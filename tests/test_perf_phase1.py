"""Phase 1 performance regressions: probes, identity fast path, edge config.

Three per-request costs dominated the measured latency:

1. Every `get_connection()` checkout ran a schema probe + two seed COUNTs on a
   database ~130 ms away. Those probes belong on connection creation (the
   pool's `configure` callback), not on every checkout.
2. Every `/api/*` request resolved identity against the database even when the
   caller presented no credential at all — an anonymous 401 paid a full round
   trip to learn there was nothing to validate.
3. The function defaulted to `iad1` while the database lives in Tokyo, and
   Vercel served hashed bundles without an immutable cache policy.

These tests pin the first two behaviours; the static/edge halves live beside
the other Vercel assertions (`tests/test_spa_fallback.py` for Flask's own
headers, this file for `vercel.json`).
"""
import json
import re
from pathlib import Path

import psycopg
import pytest

import flask_app
from raas_tracker import db as dbmod


_ROOT = Path(flask_app.__file__).resolve().parent
_VERCEL = json.loads((_ROOT / "vercel.json").read_text(encoding="utf-8"))
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


# ==================== schema/seed probes ====================

def test_checkouts_do_not_reprobe_a_warm_pool(pg_dsn, monkeypatch):
    """A warm pool must not probe on checkout.

    The pool may open more than one physical connection while a checkout is
    waiting (that is psycopg_pool's growth rule), so the deterministic
    invariant is asserted after `wait()` settles the pool: subsequent
    checkouts issue no schema query and no seed COUNTs. The old per-checkout
    path counted 2/2 here.
    """
    monkeypatch.delenv("RAAS_SCHEMA_CHECK", raising=False)
    dbmod.close_pool()
    try:
        dbmod.get_connection(pg_dsn).close()  # creates and configures the pool
        dbmod._get_pool(pg_dsn).wait(timeout=30)  # let background growth settle

        counts = {"schema": 0, "seeds": 0}
        real_schema = dbmod._schema_current
        real_seeds = dbmod._ensure_seeds

        def counting_schema(conn):
            counts["schema"] += 1
            return real_schema(conn)

        def counting_seeds(conn):
            counts["seeds"] += 1
            return real_seeds(conn)

        monkeypatch.setattr(dbmod, "_schema_current", counting_schema)
        monkeypatch.setattr(dbmod, "_ensure_seeds", counting_seeds)
        dbmod.get_connection(pg_dsn).close()
        dbmod.get_connection(pg_dsn).close()
        assert counts == {"schema": 0, "seeds": 0}, counts
    finally:
        dbmod.close_pool()


def test_forced_schema_check_still_probes_every_checkout(pg_dsn, monkeypatch):
    """The test-only escape hatch stays exact.

    `RAAS_SCHEMA_CHECK=always` (set for the whole suite) is what lets tests
    truncate or drop tables and have the next checkout repair them. If that
    path silently stopped probing, test isolation would fail in confusing
    ways rather than here.
    """
    monkeypatch.setenv("RAAS_SCHEMA_CHECK", "always")
    dbmod.close_pool()
    counts = {"schema": 0, "seeds": 0}
    real_schema = dbmod._schema_current
    real_seeds = dbmod._ensure_seeds

    def counting_schema(conn):
        counts["schema"] += 1
        return real_schema(conn)

    def counting_seeds(conn):
        counts["seeds"] += 1
        return real_seeds(conn)

    monkeypatch.setattr(dbmod, "_schema_current", counting_schema)
    monkeypatch.setattr(dbmod, "_ensure_seeds", counting_seeds)
    try:
        dbmod.get_connection(pg_dsn).close()
        dbmod.get_connection(pg_dsn).close()
        assert counts == {"schema": 2, "seeds": 2}, counts
    finally:
        dbmod.close_pool()


def test_pool_default_cap_is_four(pg_dsn, monkeypatch):
    """The session pooler admits 15 clients; one instance may use at most 4."""
    monkeypatch.delenv("RAAS_POOL_MAX_SIZE", raising=False)
    dbmod.close_pool()
    try:
        assert dbmod._get_pool(pg_dsn).max_size == 4
    finally:
        dbmod.close_pool()


def test_prepare_connection_closes_the_connection_when_setup_fails(pg_dsn, monkeypatch):
    """A failed migration must not leak the freshly opened physical connection.

    psycopg_pool abandons a connection whose `configure` callback raises,
    without closing it. Under a failing deployment that retries forever, each
    retry would leak a client against the pooler's 15-client cap.
    """
    monkeypatch.delenv("RAAS_SCHEMA_CHECK", raising=False)

    def boom(conn):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(dbmod, "_schema_current", boom)
    conn = psycopg.connect(pg_dsn)
    with pytest.raises(RuntimeError):
        dbmod._prepare_connection(conn)
    assert conn.closed


# ==================== identity fast path ====================

@pytest.mark.parametrize("path", ["/api/chemicals", "/api/auth/me", "/api/notifications"])
def test_anonymous_api_request_never_opens_a_database_connection(client, monkeypatch, path):
    """No cookie and no API key means there is nothing to validate.

    Before the change this raised the 500 from the catch-all handler because
    `_resolve_identity` opened a connection unconditionally; the honest answer
    is the gate's 401, reached without touching the database.
    """
    def _boom():
        raise AssertionError("anonymous request must not open a database connection")

    monkeypatch.setattr(flask_app, "get_db", _boom)
    r = client.get(path)
    assert r.status_code == 401, r.get_json()
    assert r.get_json() == {"error": "authentication required"}


@pytest.mark.parametrize("kind", ["cookie", "api-key"])
def test_a_presented_credential_is_still_validated_against_the_database(client, monkeypatch, kind):
    """The fast path keys on *absence*, never on a value.

    A credential that is present but invalid must still be rejected by the
    database-backed check — an attacker cannot skip validation by inventing a
    session id or key.
    """
    if kind == "cookie":
        client.set_cookie(flask_app.SESSION_COOKIE, "not-a-real-session")
        headers = {}
    else:
        headers = {flask_app.API_KEY_HEADER: "raas_not_a_real_key"}
    real_get_db = flask_app.get_db
    calls = []

    def counting_get_db():
        calls.append(1)
        return real_get_db()

    monkeypatch.setattr(flask_app, "get_db", counting_get_db)
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 401, r.get_json()
    assert calls, "a presented credential must be validated against the database"


# ==================== edge configuration ====================

def test_function_region_is_pinned_to_the_database_region():
    """Supabase is in ap-northeast-1; the function defaulted to iad1 (~130 ms RTT)."""
    assert _VERCEL.get("regions") == ["hnd1"], (
        "functions must run in hnd1 (Tokyo), next to the Supabase project"
    )


def _matches(source: str, path: str) -> bool:
    return re.match("^" + source + r"\Z", path) is not None


def _cache_entries():
    return [
        entry for entry in _VERCEL.get("headers", [])
        if any(h["key"] == "Cache-Control" for h in entry.get("headers", []))
    ]


def _cache_value(entry) -> str:
    return next(h["value"] for h in entry["headers"] if h["key"] == "Cache-Control")


def test_edge_caches_hashed_assets_immutably():
    """Vercel serves `/assets/*` from its CDN, bypassing Flask's cache headers.

    Vite content-hashes every file under `assets/`, so a one-year immutable
    policy is safe: a new build changes the name, and `index.html` (which is
    never cached) points at the new name.
    """
    entries = _cache_entries()
    assert entries, "vercel.json declares no Cache-Control header at all"
    for asset in ("/assets/index-abc123.js", "/assets/index-abc123.css"):
        matching = [e for e in entries if _matches(e["source"], asset)]
        assert matching, f"no Cache-Control entry covers {asset!r}"
        assert any(_cache_value(e) == IMMUTABLE_CACHE for e in matching), (
            f"{asset!r} is not immutably cached: "
            f"{[(_cache_value(e), e['source']) for e in matching]}"
        )


def test_edge_does_not_immutably_cache_the_document_or_unhashed_files():
    """`index.html` names the current bundle hash, so caching it would pin users
    to a stale bundle (or a deleted one). Unhashed root files must not inherit
    the asset policy either."""
    entries = _cache_entries()
    for path in ("/", "/login", "/reports", "/index.html", "/favicon.svg"):
        assert not any(_matches(e["source"], path) for e in entries), (
            f"a Cache-Control entry covers {path!r}; only /assets/* is content-hashed"
        )
