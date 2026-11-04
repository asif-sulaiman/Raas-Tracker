"""Shared fixtures: isolated PG database + seeded admin/user + Flask test client.

PostgreSQL for every run. Target resolution:
1. TEST_DATABASE_URL env (CI service container, Supabase proof) — used as-is.
2. Otherwise an in-process embedded server (pgserver: zero-install, offline).
"""
import io
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stderr

import pytest

# In-process rate limits would throttle the suite itself (hundreds of
# requests per minute across two users): disable globally here. Targeted
# rate-limit tests re-enable the limiter explicitly.
os.environ["RAAS_RATE_LIMITS"] = "off"

# Production probes schema + seeds once per physical pooled connection (in the
# pool's `configure` callback). Tests truncate and drop tables between cases
# while reusing those same connections, so they must keep the old per-checkout
# probe. The one test that exercises the cached path deletes this key itself.
os.environ["RAAS_SCHEMA_CHECK"] = "always"

# Production caps one pool at 4 connections (waitress has 4 threads).
# test_concurrency.py holds 8 connections live at once across threads, so the
# suite raises the cap; the production default is asserted separately.
os.environ["RAAS_POOL_MAX_SIZE"] = "10"

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from chem_stock import get_connection, create_first_admin, ensure_setup_token, create_user


_TABLES = (
    "api_key_rate_limits api_keys approval_workflow audit_logs "
    "chemicals companies invoices invoice_items letters_of_credit login_attempts "
    "notification_reads notifications packaging_items packaging_movements reason_codes "
    "recipe_items recipe_products recipes reconciliation_periods production_run_items "
    "production_run_links production_run_outputs production_runs "
    "sale_items sale_payments sales sales_stage_history sessions shipments "
    "unit_conversions upload_rows uploads users"
).split()
# app_settings is deliberately NOT truncated: it carries the schema version
# that lets get_connection skip the full DDL (1 probe instead of ~40
# statements). Setup keys are removed explicitly below instead.


@pytest.fixture(scope="session")
def pg_dsn():
    """Session-wide Postgres DSN: TEST_DATABASE_URL or an embedded server."""
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        yield url
        return
    import pgserver
    import psycopg

    workdir = tempfile.mkdtemp(prefix="raaspg_")
    server = pgserver.get_server(os.path.join(workdir, "data"))
    try:
        admin = psycopg.connect(server.get_uri("postgres"), autocommit=True)
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'raas_test'").fetchone()
        if not exists:
            admin.execute("CREATE DATABASE raas_test")
        admin.close()
        yield server.get_uri("raas_test")
    finally:
        server.cleanup()
        shutil.rmtree(workdir, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _pin_database_url(pg_dsn):
    """Pin DATABASE_URL to the test DSN for the whole session.

    Tests that bypass the client fixture (bare test_client()) must never
    resolve the real remote DSN via env.
    """
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_dsn
    yield
    if old is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    """Lower bcrypt cost for tests only (hash plumbing identical, ~10x faster).

    Narrower test runtimes also shrink wall-clock exposure for the
    time-window throttle tests.
    """
    import raas_tracker.auth as _auth
    monkeypatch.setattr(_auth, "BCRYPT_ROUNDS", 4)
    _auth._DUMMY_HASH = None


@pytest.fixture()
def db(pg_dsn):
    conn = get_connection(pg_dsn)
    tables = ", ".join(f'"{t}"' for t in _TABLES)
    conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    conn.execute("DELETE FROM app_settings WHERE key IN ('setup_token_hash', 'setup_completed')")
    conn.commit()
    conn.close()
    conn = get_connection(pg_dsn)
    with redirect_stderr(io.StringIO()):
        ensure_setup_token(conn)
    create_first_admin(conn, "admin", "admin-pass-123")
    create_user(conn, "user", "user-pass-123", role="user")
    conn.execute("DELETE FROM app_settings WHERE key = 'setup_token_hash'")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def client(pg_dsn, db, monkeypatch):
    import flask_app
    from chem_stock import get_connection as _gc
    monkeypatch.setattr(flask_app, "get_db", lambda: _gc(pg_dsn))
    return flask_app.app.test_client()


@pytest.fixture()
def admin_client(client, db):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200
    return client


@pytest.fixture()
def user_client(pg_dsn, db, monkeypatch):
    import flask_app
    from chem_stock import get_connection as _gc
    monkeypatch.setattr(flask_app, "get_db", lambda: _gc(pg_dsn))
    c = flask_app.app.test_client()
    r = c.post("/api/auth/login", json={"username": "user", "password": "user-pass-123"})
    assert r.status_code == 200
    return c


@pytest.fixture()
def produce_for(admin_client):
    """Formulate and produce every product on one invoice.

    A recipe is the production plan for ONE invoice, so booking needs every
    invoiced product to be formulated *and* covered by a run linked to that
    invoice. Call as ``produce_for(company_id, sale_id, invoice_id, tag, product)``;
    the ``product`` argument is kept for call-site compatibility and asserted to
    be on the invoice.
    """
    from urllib.parse import quote

    def _produce(company_id, sale_id, invoice_id, tag, product):
        chem = f"GateChem-{tag}"
        recipe = f"GateRecipe-{tag}"
        source = admin_client.get(
            f"/api/invoices/{invoice_id}/production-source").get_json()
        assert source and source["products"], source
        names = [p["product_name"] for p in source["products"]]
        assert product in names, (product, names)

        r = admin_client.post("/api/chemicals",
                              json={"name": chem, "qty": 500, "unit": "KG"})
        assert r.status_code == 200, r.get_json()
        r = admin_client.post("/api/recipes", json={
            "name": recipe, "company_id": company_id,
            "invoice_id": invoice_id})
        assert r.status_code == 201, r.get_json()
        rid = r.get_json()["recipe"]["id"]
        for name in names:
            r = admin_client.post(
                f"/api/recipes/by-id/{rid}/products/{quote(name)}/items",
                json={"chemical": chem, "percentage": 40})
            assert r.status_code == 200, r.get_json()

        outputs = [{"product_name": p["product_name"],
                    "quantity": max(float(p["remaining_qty"]), 0.0001)}
                   for p in source["products"]]
        r = admin_client.post(f"/api/recipes/by-id/{rid}/produce",
                              json={"outputs": outputs})
        assert r.status_code == 201, r.get_json()
        return r.get_json()
    return _produce


@pytest.fixture()
def proxy_trust(monkeypatch):
    """Re-evaluate ProxyFix's `x_for` for this test's env, then restore it.

    `TRUSTED_PROXY` is otherwise read once at import, which would leave this
    security control with no black-box evidence in either state.
    Pass None to model "no proxy declared".
    """
    import flask_app
    original = flask_app.app.wsgi_app

    def _apply(value):
        if value is None:
            monkeypatch.delenv("TRUSTED_PROXY", raising=False)
        else:
            monkeypatch.setenv("TRUSTED_PROXY", value)
        flask_app._apply_proxy_fix()

    yield _apply
    flask_app.app.wsgi_app = original
