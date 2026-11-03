"""Connection pool sizing and database timeouts (the 2026-11-02 outage fix).

Two separate failures made production hang and then 500:

1. The pool kept `min_size=1` and psycopg-pool's defaults (`timeout=30`,
   `max_idle=600`, `max_lifetime=3600`). Every Vercel instance therefore pinned
   a client against Supabase's session pooler for as long as it lived, and an
   idle one kept it for ten minutes. Fifteen warm instances exhaust the cap;
   new instances are refused (`EMAXCONNSESSION`) while their requests wait the
   full 30-second acquire timeout and land in the catch-all 500.
2. The database's own `statement_timeout` default is 2 minutes, and BOTH
   Supabase poolers ignore connect-time `options` (verified against the live
   project: passing `-c statement_timeout=25000` still reports `2min`). So a
   stalled read outlives the function's 60-second budget and the browser just
   hangs. The timeouts therefore have to be set per transaction with
   `SET LOCAL`, which is also the only spelling that is safe under a
   transaction-mode pooler (a session `SET` would leak onto the next tenant of
   that backend).

These tests pin the pool shape, the timeout bundle, and the honest 503.
"""
import re
import threading

import psycopg
import pytest
from psycopg_pool import PoolTimeout

import flask_app
from raas_tracker import db as dbmod


# --------------------------------------------------------------------------- #
# Pool shape
# --------------------------------------------------------------------------- #
def _fresh_pool(pg_dsn, monkeypatch):
    """Build the production pool against the test database and hand it back."""
    dbmod.close_pool()
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    return dbmod._get_pool(pg_dsn)


def test_pool_does_not_pin_a_connection_while_idle(pg_dsn, monkeypatch):
    """`min_size=0`: an instance that never touches the database holds nothing.

    With min_size=1 every cold lambda took a pooler client slot for its whole
    life, which is exactly how fifteen of them starved the project.
    """
    monkeypatch.delenv("RAAS_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    pool = _fresh_pool(pg_dsn, monkeypatch)
    try:
        assert pool.min_size == 0, "an idle instance must not hold a pooler client"
    finally:
        pool.close()
        dbmod.close_pool()


def test_pool_reclaims_idle_connections_quickly(pg_dsn, monkeypatch):
    """Idle connections must be released long before a lambda is recycled."""
    monkeypatch.delenv("RAAS_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    pool = _fresh_pool(pg_dsn, monkeypatch)
    try:
        assert pool.timeout == 10, "acquire must fail fast, not wait the 30s default"
        assert 0 < pool.max_idle <= 60, f"max_idle={pool.max_idle} keeps slots pinned"
        assert 0 < pool.max_lifetime <= 600, f"max_lifetime={pool.max_lifetime}"
    finally:
        pool.close()
        dbmod.close_pool()


def test_connect_timeout_is_bounded(pg_dsn, monkeypatch):
    """A TCP connect to a dead host must not outlive the request budget."""
    monkeypatch.delenv("RAAS_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    pool = _fresh_pool(pg_dsn, monkeypatch)
    try:
        assert int(pool.kwargs["connect_timeout"]) <= 10
        assert pool.kwargs["prepare_threshold"] is None, (
            "prepared statements break under a transaction-mode pooler"
        )
    finally:
        pool.close()
        dbmod.close_pool()


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, 4),
        ({"RAAS_POOL_MAX_SIZE": "7"}, 7),
        ({"DB_POOL_MAX": "6"}, 6),                       # documented knob, now real
        ({"DB_POOL_MAX": "6", "RAAS_POOL_MAX_SIZE": "7"}, 7),  # RAAS_ wins
        ({"RAAS_POOL_MAX_SIZE": "not-a-number"}, 4),
        ({"RAAS_POOL_MAX_SIZE": "0"}, 4),                # must admit >= 1 connection
        ({"RAAS_POOL_MAX_SIZE": "500"}, 4),              # cannot exceed the pooler
    ],
)
def test_pool_cap_is_configurable_but_bounded(pg_dsn, monkeypatch, env, expected):
    monkeypatch.delenv("RAAS_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    pool = _fresh_pool(pg_dsn, monkeypatch)
    try:
        assert pool.max_size == expected, (env, pool.max_size)
    finally:
        pool.close()
        dbmod.close_pool()


# --------------------------------------------------------------------------- #
# Per-transaction timeouts
# --------------------------------------------------------------------------- #
def test_timeout_bundle_is_one_parameterless_round_trip():
    """`SET a = 1, b = 2` does not exist in SQL, and a parameter would force the
    extended protocol, which refuses multiple commands in one statement."""
    sql = dbmod._timeout_statements()
    assert sql.count("SET LOCAL") == 3, sql
    for name in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"):
        assert re.search(rf"SET LOCAL {name} TO \d+\b", sql), sql
    assert "%s" not in sql and ";" in sql


def test_checkout_applies_the_timeouts(pg_dsn, monkeypatch):
    monkeypatch.setenv("RAAS_STATEMENT_TIMEOUT_MS", "7000")
    monkeypatch.setenv("RAAS_LOCK_TIMEOUT_MS", "2000")
    monkeypatch.setenv("RAAS_IDLE_TX_TIMEOUT_MS", "9000")
    dbmod.close_pool()
    conn = dbmod.get_connection(pg_dsn)
    try:
        got = {name: conn.execute("SHOW " + name).fetchone()[0] for name in (
            "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")}
    finally:
        conn.close()
        dbmod.close_pool()
    assert got == {"statement_timeout": "7s", "lock_timeout": "2s",
                   "idle_in_transaction_session_timeout": "9s"}, got


def test_a_stalled_query_is_cancelled_rather_than_hanging(pg_dsn, monkeypatch):
    """The behavioural point: 1s cap, 2s query, and the caller is not stuck."""
    monkeypatch.setenv("RAAS_STATEMENT_TIMEOUT_MS", "1000")
    dbmod.close_pool()
    conn = dbmod.get_connection(pg_dsn)
    try:
        with pytest.raises(psycopg.errors.QueryCanceled):
            conn.execute("SELECT pg_sleep(2)")
    finally:
        conn.rollback()
        conn.close()
        dbmod.close_pool()


def test_timeouts_do_not_leak_onto_a_bare_connection(pg_dsn, monkeypatch):
    """`SET LOCAL` is transaction-scoped, which is what makes it pooler-safe."""
    monkeypatch.setenv("RAAS_STATEMENT_TIMEOUT_MS", "5000")
    dbmod.close_pool()
    dbmod.get_connection(pg_dsn).close()
    dbmod.close_pool()
    with psycopg.connect(pg_dsn) as control:
        assert control.execute("SHOW statement_timeout").fetchone()[0] != "5s"


def test_a_bad_timeout_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("RAAS_STATEMENT_TIMEOUT_MS", "; DROP TABLE users; --")
    sql = dbmod._timeout_statements()
    assert "DROP" not in sql
    assert re.search(r"SET LOCAL statement_timeout TO \d+", sql), sql


def test_migration_gives_itself_longer_than_a_request_query(pg_dsn):
    """The v10 DDL queue measured 4.9s mean under the advisory lock; the 25s
    request cap must not cut a deployment migration in half."""
    seen = []

    class _Recording:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            seen.append(sql if isinstance(sql, str) else str(sql))
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    with psycopg.connect(pg_dsn) as raw:
        dbmod._create_tables(_Recording(raw))

    widened = [s for s in seen if s.startswith("SET LOCAL statement_timeout TO")]
    assert widened, "_create_tables never widened its own statement_timeout"
    assert int(widened[0].rsplit(None, 1)[1]) >= 60000, widened[0]


# --------------------------------------------------------------------------- #
# Honest failure surface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "exc",
    [
        PoolTimeout("connection pool exhausted"),
        psycopg.OperationalError("connection failed: server closed the connection"),
        # 53300 — what Supabase's pooler raises when the project is out of
        # client slots; the 2026-11-02 outage in one class name.
        psycopg.errors.TooManyConnections("too many connections for pool"),
    ],
    ids=["pool-timeout", "operational", "too-many-connections"],
)
def test_database_unavailability_is_a_503_with_retry_after(admin_client, monkeypatch, exc):
    """Before: 30 seconds of nothing, then a bare `internal server error` 500.

    A busy pool or an unreachable database is transient and not the app's
    fault, so it answers 503 with `Retry-After` and never claims the request
    will succeed if retried blindly — mutating routes stay non-idempotent.
    """
    def _boom():
        raise exc

    monkeypatch.setattr(flask_app, "get_db", _boom)
    r = admin_client.get("/api/chemicals")
    assert r.status_code == 503, r.get_json()
    assert r.get_json() == {"error": "database unavailable, retry shortly"}
    assert r.headers.get("Retry-After") == "5"


def test_the_503_does_not_describe_the_database(admin_client, monkeypatch):
    secret = "postgresql://postgres:super-secret@pooler.example.com:5432/postgres"
    monkeypatch.setattr(flask_app, "get_db",
                        lambda: (_ for _ in ()).throw(
                            psycopg.OperationalError(f"could not connect to {secret}")))
    body = admin_client.get("/api/chemicals").get_data(as_text=True).lower()
    for leak in ("postgresql", "pooler", "secret", "password", "operationalerror", "psycopg"):
        assert leak not in body, leak


def test_an_ordinary_bug_is_still_a_500(admin_client, monkeypatch):
    """The 503 must not become a catch-all that hides real defects."""
    class _LogicError(RuntimeError):
        pass

    def _boom():
        raise _LogicError("a real defect")

    monkeypatch.setattr(flask_app, "get_db", _boom)
    r = admin_client.get("/api/chemicals")
    assert r.status_code == 500
    assert r.get_json() == {"error": "internal server error"}
    assert "Retry-After" not in r.headers


def test_concurrent_checkouts_survive_the_lazy_pool(pg_dsn, monkeypatch):
    """min_size=0 means the first checkout of a cold process grows the pool
    under contention; it must still hand out working connections."""
    monkeypatch.delenv("RAAS_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    dbmod.close_pool()
    monkeypatch.setenv("DATABASE_URL", pg_dsn)
    results, errors = [], []
    barrier = threading.Barrier(4)

    def _worker():
        try:
            barrier.wait(timeout=30)
            conn = dbmod.get_connection()
            try:
                results.append(conn.execute("SELECT 1").fetchone()[0])
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - diagnostic
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    dbmod.close_pool()
    assert not errors, errors
    assert results == [1, 1, 1, 1]
