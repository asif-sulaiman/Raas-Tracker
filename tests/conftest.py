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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from chem_stock import get_connection, create_first_admin, ensure_setup_token, create_user


_TABLES = (
    "api_key_rate_limits api_keys approval_workflow audit_logs "
    "chemicals login_attempts notification_reads notifications reason_codes "
    "recipe_items recipes reconciliation_periods sale_items sale_payments sales "
    "sales_stage_history sessions unit_conversions upload_rows uploads users"
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
