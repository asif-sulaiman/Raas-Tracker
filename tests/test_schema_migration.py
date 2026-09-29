"""Migration-safety regressions for `raas_tracker/db.py`.

Five defects are pinned here, each with a test that fails on the old code:

1. `_PooledConnection.__exit__` committed the work of a `with get_connection()`
   block that raised, because pooled `close()` commits where psycopg's
   non-pooled `close()` used to ROLLBACK.
2. The DDL fast path could not see a *code-side* schema change: the stored
   signature is a copy of the live schema, so an additive migration was skipped
   forever unless someone remembered to hand-type a `_REQUIRED_SIG_TOKENS`
   entry. Replaced by an explicit, code-owned `_SCHEMA_VERSION`.
3. `_create_tables` was not one transaction: `backfill_company_links` committed
   in the middle, so a later failure left a half-applied migration and the DDL
   retried on every connection forever.
4. `invoices` / `production_run_links` used check-then-act `CREATE TABLE` instead
   of the `CREATE TABLE IF NOT EXISTS` every other table uses -> DuplicateTable
   under a cold-start pool.
5. `_get_pool` had an unsynchronised `if _pool is None` -> two cold-start
   threads each built a pool and the loser's leaked.

Runs against the conftest `pg_dsn` (TEST_DATABASE_URL or the embedded pgserver).
"""
import re
import threading

import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import db as dbmod

SCRATCH = "schema_mig_scratch"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class RecordingConn:
    """Proxy that records every SQL statement the migration executes."""

    def __init__(self, conn):
        self._conn = conn
        self.statements = []

    def execute(self, query, params=None):
        self.statements.append(" ".join(str(query).split()))
        if params is None:
            return self._conn.execute(query)
        return self._conn.execute(query, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class NoCommitConn:
    """Proxy whose commit() explodes: proves a helper never commits."""

    def __init__(self, conn):
        self._conn = conn

    def commit(self):
        raise AssertionError("this helper must not commit")

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def scratch(pg_dsn):
    """A real throwaway table, created and dropped around the test."""
    conn = dbmod.get_connection(pg_dsn)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH}")
        conn.execute(
            f"CREATE TABLE {SCRATCH} (id INTEGER PRIMARY KEY, note TEXT)")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = dbmod.get_connection(pg_dsn)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {SCRATCH}")
        conn.commit()
    finally:
        conn.close()


def _raw(dsn):
    """Autocommit connection that bypasses get_connection (no migration)."""
    return psycopg.connect(dsn, autocommit=True)


def _scratch_rows(dsn):
    raw = _raw(dsn)
    try:
        return raw.execute(f"SELECT id, note FROM {SCRATCH} "
                           "ORDER BY id").fetchall()
    finally:
        raw.close()


def _public_tables(dsn):
    raw = _raw(dsn)
    try:
        return {r[0] for r in raw.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()}
    finally:
        raw.close()


def _setting(dsn, key):
    raw = _raw(dsn)
    try:
        return raw.execute(
            "SELECT value FROM app_settings WHERE key = %s", (key,)
        ).fetchone()
    finally:
        raw.close()


# --------------------------------------------------------------------------- #
# Task 1 — __exit__ must roll back on exception
# --------------------------------------------------------------------------- #
def test_with_block_rolls_back_on_exception(pg_dsn, scratch):
    """`with get_connection() as conn:` + raise => work is discarded.

    Pre-pooling, psycopg's Connection.close() on an open transaction issued a
    server-side ROLLBACK. The pooled wrapper committed instead, so a half-written
    request silently became durable.
    """
    with pytest.raises(ValueError, match="boom"):
        with dbmod.get_connection(pg_dsn) as conn:
            conn.execute(
                f"INSERT INTO {SCRATCH} (id, note) VALUES (%s, %s)",
                (1, "should-be-discarded"))
            raise ValueError("boom")

    assert _scratch_rows(pg_dsn) == [], \
        "exceptional __exit__ committed the block's work"


def test_with_block_commits_on_success(pg_dsn, scratch):
    """The success path of __exit__ must still COMMIT (callers depend on it)."""
    with dbmod.get_connection(pg_dsn) as conn:
        conn.execute(f"INSERT INTO {SCRATCH} (id, note) VALUES (%s, %s)",
                     (1, "kept"))

    assert _scratch_rows(pg_dsn) == [(1, "kept")]


def test_explicit_close_still_commits(pg_dsn, scratch):
    """Plain conn.close() (no `with`, no exception) keeps committing."""
    conn = dbmod.get_connection(pg_dsn)
    conn.execute(f"INSERT INTO {SCRATCH} (id, note) VALUES (%s, %s)",
                 (1, "kept"))
    conn.close()

    assert _scratch_rows(pg_dsn) == [(1, "kept")]


def test_connection_reusable_after_exceptional_exit(pg_dsn, scratch):
    """A rolled-back connection goes back to the pool clean, not INTRANS."""
    with pytest.raises(RuntimeError):
        with dbmod.get_connection(pg_dsn) as conn:
            conn.execute(f"INSERT INTO {SCRATCH} (id, note) VALUES (%s, %s)",
                         (1, "x"))
            raise RuntimeError("nope")

    with dbmod.get_connection(pg_dsn) as conn:
        conn.execute(f"INSERT INTO {SCRATCH} (id, note) VALUES (%s, %s)",
                     (2, "next-connection-fine"))

    assert _scratch_rows(pg_dsn) == [(2, "next-connection-fine")]


# --------------------------------------------------------------------------- #
# Task 2 — explicit, code-owned schema version gates the fast path
# --------------------------------------------------------------------------- #
def test_schema_version_is_an_int_constant():
    assert isinstance(dbmod._SCHEMA_VERSION, int)
    assert not isinstance(dbmod._SCHEMA_VERSION, bool)
    assert dbmod._SCHEMA_VERSION >= 1
    assert isinstance(dbmod._SCHEMA_VERSION_KEY, str)


def test_stale_stored_version_forces_full_ddl(pg_dsn, monkeypatch):
    """A database stamped behind _SCHEMA_VERSION must not skip the DDL."""
    dbmod.get_connection(pg_dsn).close()  # ensure fully migrated
    assert _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY) is not None

    conn = dbmod.get_connection(pg_dsn)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (dbmod._SCHEMA_VERSION_KEY, str(dbmod._SCHEMA_VERSION - 1)))
    conn.commit()
    conn.close()

    calls = []
    real = dbmod._create_tables
    monkeypatch.setattr(dbmod, "_create_tables",
                        lambda c: (calls.append(c), real(c))[1])
    dbmod.get_connection(pg_dsn).close()

    assert calls, "stored version behind code must NOT skip _create_tables"
    assert int(_setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)[0]) \
        == dbmod._SCHEMA_VERSION, "migration must re-stamp the current version"


def test_missing_version_key_forces_full_ddl(pg_dsn, monkeypatch):
    """An absent key (pre-upgrade database) means "not current", never skip."""
    dbmod.get_connection(pg_dsn).close()
    conn = dbmod.get_connection(pg_dsn)
    conn.execute("DELETE FROM app_settings WHERE key = %s",
                 (dbmod._SCHEMA_VERSION_KEY,))
    conn.commit()
    conn.close()

    calls = []
    real = dbmod._create_tables
    monkeypatch.setattr(dbmod, "_create_tables",
                        lambda c: (calls.append(c), real(c))[1])
    dbmod.get_connection(pg_dsn).close()

    assert calls, "absent version key must not silently skip DDL"
    assert _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY) is not None


def test_non_numeric_version_forces_full_ddl(pg_dsn, monkeypatch):
    """A corrupt/unparseable stored version must not skip DDL either."""
    dbmod.get_connection(pg_dsn).close()
    conn = dbmod.get_connection(pg_dsn)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (dbmod._SCHEMA_VERSION_KEY, "not-a-number"))
    conn.commit()
    conn.close()

    calls = []
    real = dbmod._create_tables
    monkeypatch.setattr(dbmod, "_create_tables",
                        lambda c: (calls.append(c), real(c))[1])
    dbmod.get_connection(pg_dsn).close()

    assert calls, "unparseable stored version must not skip DDL"
    assert int(_setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)[0]) \
        == dbmod._SCHEMA_VERSION


def test_fast_path_is_a_noop_once_current(pg_dsn, monkeypatch):
    """After the migration, the version gate is satisfied => DDL never runs."""
    dbmod.get_connection(pg_dsn).close()  # migrate + stamp

    def _boom(conn):
        raise AssertionError("full DDL must not run on a current database")

    monkeypatch.setattr(dbmod, "_create_tables", _boom)
    dbmod.get_connection(pg_dsn).close()


# --------------------------------------------------------------------------- #
# Task 3 — _create_tables is ONE transaction
# --------------------------------------------------------------------------- #
def test_create_tables_is_atomic_on_failure(pg_dsn, monkeypatch):
    """A mid-migration failure must leave the database untouched.

    `reconciliation_periods` is created before the first backfill call, so the
    old code made it durable (companies.backfill_company_links committed at
    that point) even though the rest of the migration then blew up.
    """
    from raas_tracker import companies as companies_mod

    dbmod.get_connection(pg_dsn).close()  # ensure fully migrated
    raw = _raw(pg_dsn)
    try:
        raw.execute("DROP TABLE IF EXISTS reconciliation_periods CASCADE")
    finally:
        raw.close()
    assert "reconciliation_periods" not in _public_tables(pg_dsn)

    seen = []
    real_backfill = companies_mod.backfill_company_links

    def _boom_after_first_backfill(conn):
        # Let the real helper run (it used to commit right here), then fail on
        # the second call site - i.e. after most of the DDL has been issued.
        real_backfill(conn)
        seen.append(1)
        if len(seen) >= 2:
            raise RuntimeError("mid-migration failure")

    monkeypatch.setattr(companies_mod, "backfill_company_links",
                        _boom_after_first_backfill)

    with pytest.raises(RuntimeError, match="mid-migration failure"):
        dbmod.get_connection(pg_dsn)

    # Nothing before the failure may be durable.
    assert "reconciliation_periods" not in _public_tables(pg_dsn), \
        "DDL before the failure was committed => migration is not atomic"
    # And the failure must not have advanced the recorded version.
    stored = _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)
    assert stored is None or int(stored[0]) == dbmod._SCHEMA_VERSION

    # DB must still be usable and still migratable.
    monkeypatch.undo()
    dbmod.get_connection(pg_dsn).close()
    assert "reconciliation_periods" in _public_tables(pg_dsn)


def test_backfill_company_links_does_not_commit(pg_dsn, monkeypatch):
    """backfill_company_links is a pure helper: no transaction control.

    _create_tables owns the commit; a commit inside the helper splits the
    migration into two durable halves.
    """
    from raas_tracker import companies as companies_mod

    dbmod.get_connection(pg_dsn).close()
    conn = dbmod.get_connection(pg_dsn)
    try:
        conn.execute("DELETE FROM sales")
        conn.execute("INSERT INTO sales (pi_number, client_name) "
                     "VALUES ('PI-ATOMIC', 'Atomic Co')")
        conn.commit()
        no_commit = NoCommitConn(conn)
        companies_mod.backfill_company_links(no_commit)
        assert conn.execute(
            "SELECT company_id FROM sales WHERE pi_number = 'PI-ATOMIC'"
        ).fetchone()[0] is not None
    finally:
        monkeypatch.undo()
        conn.rollback()
        conn.close()

# --------------------------------------------------------------------------- #
# Task 4 — idempotent DDL + one migrator at a time
# --------------------------------------------------------------------------- #
def test_migrated_tables_use_create_table_if_not_exists(pg_dsn):
    """Every CREATE TABLE in the migration must be idempotent.

    `invoices` and `production_run_links` used a bare CREATE TABLE guarded by a
    separate information_schema SELECT: two cold-start threads both pass the
    check and one dies with DuplicateTable.
    """
    dbmod.get_connection(pg_dsn).close()
    conn = dbmod.get_connection(pg_dsn)
    try:
        rec = RecordingConn(conn)
        dbmod._create_tables(rec)
        conn.commit()
    finally:
        conn.close()

    sql = "\n".join(rec.statements)
    for table in ("invoices", "production_run_links"):
        assert re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", sql), \
            f"{table} is still created with a bare CREATE TABLE"

    # No check-then-act table creation left behind.
    assert "information_schema.tables" not in sql, \
        "existence-check SELECT still gates a CREATE TABLE"


def test_create_tables_is_serialised_with_an_advisory_lock(pg_dsn):
    """The whole migration body is guarded by a (transaction-scoped) lock."""
    dbmod.get_connection(pg_dsn).close()
    conn = dbmod.get_connection(pg_dsn)
    try:
        rec = RecordingConn(conn)
        dbmod._create_tables(rec)
        conn.commit()
    finally:
        conn.close()

    sql = "\n".join(rec.statements)
    assert re.search(r"pg_advisory_xact_lock", sql), \
        "migration body is not guarded by an advisory lock"


# --------------------------------------------------------------------------- #
# Task 5 — _get_pool is thread-safe
# --------------------------------------------------------------------------- #
def test_get_pool_builds_one_pool_on_cold_start(pg_dsn, monkeypatch):
    """Concurrent cold-start callers must construct exactly one pool.

    The loser's pool (and its min_size=1 background connection) used to leak,
    because only the winner was reachable from close_pool/atexit.
    """
    real_pool_cls = dbmod.ConnectionPool
    built = []
    built_lock = threading.Lock()

    class _CountingPool(real_pool_cls):
        def __init__(self, *args, **kwargs):
            with built_lock:
                built.append(1)
            super().__init__(*args, **kwargs)

    dbmod.close_pool()
    monkeypatch.setattr(dbmod, "ConnectionPool", _CountingPool)
    monkeypatch.setenv("DATABASE_URL", pg_dsn)

    workers = 8
    barrier = threading.Barrier(workers)
    results = []
    errors = []

    def _worker():
        try:
            barrier.wait(timeout=30)
            results.append(dbmod._get_pool(pg_dsn))
        except Exception as exc:  # pragma: no cover - diagnostic only
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert len(built) == 1, \
        f"pool constructed {len(built)}x on a cold start (losers leak)"
    assert all(r is results[0] for r in results), \
        "concurrent callers got different pools"
    dbmod.close_pool()


def test_get_pool_is_stable_for_one_dsn(pg_dsn):
    """Unchanged DSN behaviour: the same pool object is reused."""
    assert dbmod._get_pool(pg_dsn) is dbmod._get_pool(pg_dsn)
