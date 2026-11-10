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


def _table_columns(conn, table: str):
    """Column names of a table in the public schema (empty set if missing)."""
    cur = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s""",
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


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


def test_migration_never_invents_invoice_lines(db, pg_dsn):
    """No connection-time migration may write ``invoice_items`` on its own.

    Phase 2 deleted ``backfill_invoice_lines``, which seeded lines for legacy
    line-less invoices every time the schema ran. The only writers left are the
    three operator endpoints, so a full migration pass must leave a line-less
    invoice exactly as it found it.
    """
    dbmod.get_connection(pg_dsn).close()  # ensure fully migrated
    sid = db.execute(
        "INSERT INTO sales (stage, pi_number, pi_date, client_name) "
        "VALUES ('pi_issued', 'PI-NOBACKFILL', '2026-01-15', 'No Backfill Co') "
        "RETURNING id").fetchone()[0]
    db.execute(
        "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
        "VALUES (%s, 'NoBackfill', 10, 5, 'KG')", (sid,))
    inv = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status, amount) "
        "VALUES (%s, 'INV-NOBACKFILL', 'planned', 50) RETURNING id",
        (sid,)).fetchone()[0]
    # A stale stamp forces the next connection through the full DDL + migration
    # path, which is where the removed backfill used to run.
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (dbmod._SCHEMA_VERSION_KEY, str(dbmod._SCHEMA_VERSION - 1)))
    db.commit()

    dbmod.get_connection(pg_dsn).close()

    assert db.execute(
        "SELECT COUNT(*) FROM invoice_items WHERE invoice_id = %s",
        (inv,)).fetchone()[0] == 0, "a migration must never invent invoice lines"


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
# Task 6 — INV-REVISION schema upgrade (v4 → v5)
# --------------------------------------------------------------------------- #
def test_inv_revision_columns_added_on_v4_to_v5_upgrade(pg_dsn):
    """INV-REVISION v5 migration adds three new columns with correct defaults,
    and an old stored version replays the whole chain up to the current one."""
    # Start from a fully migrated v4 database
    dbmod.get_connection(pg_dsn).close()

    # Force version back to 4
    conn = dbmod.get_connection(pg_dsn)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (dbmod._SCHEMA_VERSION_KEY, "4"))
    conn.commit()
    conn.close()

    # Now get a new connection - should trigger the v4->current migration chain
    conn = dbmod.get_connection(pg_dsn)
    try:
        # Check all three new columns exist
        inv_cols = _table_columns(conn, "invoices")
        assert "shipment_status" in inv_cols, "invoices.shipment_status missing"
        # ...and the later revision rode along: the forced-old version runs the
        # whole migration chain, not just v5.
        assert "invoice_date" in inv_cols, "invoices.invoice_date missing"

        lc_cols = _table_columns(conn, "letters_of_credit")
        assert "shipment_gate" in lc_cols, "letters_of_credit.shipment_gate missing"

        ii_cols = _table_columns(conn, "invoice_items")
        assert "source_type" in ii_cols, "invoice_items.source_type missing"

        # Check defaults by inserting test rows
        # 1. invoices.shipment_status default 'pending'
        sale_id = conn.execute(
            "INSERT INTO sales (pi_number, client_name) VALUES ('PI-TEST', 'Test Co') RETURNING id"
        ).fetchone()[0]
        inv_id = conn.execute(
            "INSERT INTO invoices (sale_id, invoice_number) VALUES (%s, 'TEST-SHIP') RETURNING id",
            (sale_id,)
        ).fetchone()[0]
        status = conn.execute("SELECT shipment_status FROM invoices WHERE id = %s", (inv_id,)).fetchone()[0]
        assert status == "pending", f"invoices.shipment_status default != 'pending', got {status}"

        # 2. letters_of_credit.shipment_gate default 'per_invoice'
        co_id = conn.execute(
            "INSERT INTO companies (name) VALUES ('Test Co') RETURNING id"
        ).fetchone()[0]
        lc_id = conn.execute(
            "INSERT INTO letters_of_credit (lc_number, company_id) VALUES ('LC-TEST', %s) RETURNING id",
            (co_id,)
        ).fetchone()[0]
        gate = conn.execute("SELECT shipment_gate FROM letters_of_credit WHERE id = %s", (lc_id,)).fetchone()[0]
        assert gate == "per_invoice", f"letters_of_credit.shipment_gate default != 'per_invoice', got {gate}"

        # 3. invoice_items.source_type default 'pi_line'
        inv_id2 = conn.execute(
            "INSERT INTO invoices (sale_id, invoice_number) VALUES (%s, 'TEST-SRC') RETURNING id",
            (sale_id,)
        ).fetchone()[0]
        si_id = conn.execute(
            "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price) VALUES (%s, 'Test', 10, 5) RETURNING id",
            (sale_id,)
        ).fetchone()[0]
        ii_id = conn.execute(
            "INSERT INTO invoice_items (invoice_id, sale_item_id, product_name, quantity, unit_price) "
            "VALUES (%s, %s, 'Test', 5, 10) RETURNING id",
            (inv_id2, si_id)
        ).fetchone()[0]
        src = conn.execute("SELECT source_type FROM invoice_items WHERE id = %s", (ii_id,)).fetchone()[0]
        assert src == "pi_line", f"invoice_items.source_type default != 'pi_line', got {src}"

        # Verify the migration stamped the current schema version
        conn2 = dbmod.get_connection(pg_dsn)
        conn2.close()
        stored = _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)
        assert stored is not None and int(stored[0]) == dbmod._SCHEMA_VERSION, \
            "schema version not updated to current"
    finally:
        conn.close()


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


# --------------------------------------------------------------------------- #
# v13 — CHEMICAL-PURCHASES: tables, column, constraints, RLS, version bump
# --------------------------------------------------------------------------- #
def test_v13_chemical_purchases_schema(db, pg_dsn):
    """The receipt register migrates in: both tables, the in_house_registered
    column, the delivery/client + quantity + version + unique CHECKs, RLS on the
    backend-owned tables, and the stamped version. The CHECKs are probed by
    insert so they are the final authority, not just the Pydantic model."""
    assert dbmod._SCHEMA_VERSION >= 13
    tables = _public_tables(pg_dsn)
    assert "chemical_purchases" in tables
    assert "chemical_purchase_items" in tables
    assert "in_house_registered" in _table_columns(db, "chemicals")

    stored = _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)
    assert stored is not None and int(stored[0]) == dbmod._SCHEMA_VERSION

    # RLS enabled (no policies => no anon/authenticated reach; the Flask owner
    # role bypasses it) and the named constraints are present.
    raw = _raw(pg_dsn)
    try:
        for t in ("chemical_purchases", "chemical_purchase_items"):
            row = raw.execute(
                "SELECT relrowsecurity FROM pg_class WHERE relname = %s",
                (t,)).fetchone()
            assert row is not None and row[0] is True, f"{t} RLS not enabled"
        names = {r[0] for r in raw.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid IN "
            "('chemical_purchases'::regclass, 'chemical_purchase_items'::regclass)"
        ).fetchall()}
        assert "chk_purchase_delivery_client" in names
        assert "uq_purchase_item_material" in names
    finally:
        raw.close()

    # Setup: a company, two chemicals, a valid outward receipt and one item.
    cid = db.execute("INSERT INTO companies (name) VALUES ('V13 Co') "
                     "RETURNING id").fetchone()[0]
    chem_a = db.execute("INSERT INTO chemicals (name, unit) VALUES ('V13 A', 'KG') "
                        "RETURNING id").fetchone()[0]
    chem_b = db.execute("INSERT INTO chemicals (name, unit) VALUES ('V13 B', 'KG') "
                        "RETURNING id").fetchone()[0]
    pid = db.execute(
        "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
        "purchase_date, delivery_type, client_id, created_by) "
        "VALUES ('S', 'PO-V13', '2026-01-15', 'outward', %s, 'admin') RETURNING id",
        (cid,)).fetchone()[0]
    db.execute("INSERT INTO chemical_purchase_items (purchase_id, chemical_id, "
               "quantity, unit) VALUES (%s, %s, 10, 'KG')", (pid, chem_a))
    db.commit()

    # in_house must reject a client.
    with pytest.raises(psycopg.IntegrityError):
        db.execute(
            "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
            "purchase_date, delivery_type, client_id, created_by) "
            "VALUES ('S', 'PO-X1', '2026-01-15', 'in_house', %s, 'admin')", (cid,))
    db.rollback()
    # outward must reject a null client.
    with pytest.raises(psycopg.IntegrityError):
        db.execute(
            "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
            "purchase_date, delivery_type, client_id, created_by) "
            "VALUES ('S', 'PO-X2', '2026-01-15', 'outward', NULL, 'admin')")
    db.rollback()
    # version must be > 0.
    with pytest.raises(psycopg.IntegrityError):
        db.execute(
            "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
            "purchase_date, delivery_type, client_id, created_by, version) "
            "VALUES ('S', 'PO-X3', '2026-01-15', 'outward', %s, 'admin', 0)", (cid,))
    db.rollback()
    # quantity must be > 0 (a different chemical isolates the CHECK from the
    # unique constraint).
    with pytest.raises(psycopg.IntegrityError):
        db.execute("INSERT INTO chemical_purchase_items (purchase_id, chemical_id, "
                   "quantity, unit) VALUES (%s, %s, 0, 'KG')", (pid, chem_b))
    db.rollback()
    # one item per (purchase, chemical).
    with pytest.raises(psycopg.IntegrityError):
        db.execute("INSERT INTO chemical_purchase_items (purchase_id, chemical_id, "
                   "quantity, unit) VALUES (%s, %s, 5, 'KG')", (pid, chem_a))
    db.rollback()


def test_v13_in_house_registered_default_true_and_idempotent_rerun(db, pg_dsn):
    """in_house_registered defaults TRUE (ordinary chemicals stay visible) and
    re-running the migration twice is a no-op — the DDL is idempotent and the
    version stays stamped at the code constant."""
    # An insert that omits the column lands registered (default TRUE).
    chem_id = db.execute(
        "INSERT INTO chemicals (name, unit) VALUES ('V13 Default', 'KG') "
        "RETURNING id").fetchone()[0]
    db.commit()
    assert db.execute("SELECT in_house_registered FROM chemicals WHERE id = %s",
                      (chem_id,)).fetchone()[0] is True
    # Release the read transaction's ACCESS SHARE lock so the DDL below can take
    # its ACCESS EXCLUSIVE lock on chemicals without blocking behind this conn.
    db.commit()
    # The default is declared in the catalog, not merely backfilled onto rows.
    raw = _raw(pg_dsn)
    try:
        default = raw.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'chemicals' "
            "AND column_name = 'in_house_registered'").fetchone()[0]
        assert default is not None and "true" in default.lower()
    finally:
        raw.close()

    # Idempotent re-run: two consecutive _create_tables calls on a migrated DB
    # raise nothing and leave both new tables present and the version stamped.
    conn = dbmod.get_connection(pg_dsn)
    try:
        dbmod._create_tables(conn)
        dbmod._create_tables(conn)
    finally:
        conn.close()
    stored = _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)
    assert stored is not None and int(stored[0]) == dbmod._SCHEMA_VERSION
    tables = _public_tables(pg_dsn)
    assert "chemical_purchases" in tables
    assert "chemical_purchase_items" in tables


# --------------------------------------------------------------------------- #
# v14 — MATERIAL SOURCES: source columns, pairing CHECKs, run evidence, defaults
# --------------------------------------------------------------------------- #
def _v14_chain(db):
    """Raw parent chain for the v14 probes: company, chemicals, an outward
    purchase + line, and an invoiced recipe with one product slot and one run.

    Built with SQL only (no Flask, no service layer) so the migration is the
    thing under test. Returns the ids the probes need.
    """
    cid = db.execute("INSERT INTO companies (name) VALUES ('V14 Co') "
                    "RETURNING id").fetchone()[0]
    chem_a = db.execute("INSERT INTO chemicals (name, unit) VALUES ('V14 A', 'KG') "
                        "RETURNING id").fetchone()[0]
    chem_b = db.execute("INSERT INTO chemicals (name, unit) VALUES ('V14 B', 'KG') "
                        "RETURNING id").fetchone()[0]
    pid = db.execute(
        "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
        "purchase_date, delivery_type, client_id, created_by) "
        "VALUES ('S', 'PO-V14', '2026-02-01', 'outward', %s, 'admin') RETURNING id",
        (cid,)).fetchone()[0]
    line_id = db.execute(
        "INSERT INTO chemical_purchase_items (purchase_id, chemical_id, quantity, "
        "unit) VALUES (%s, %s, 12, 'KG') RETURNING id", (pid, chem_a)).fetchone()[0]
    sale_id = db.execute("INSERT INTO sales (stage, client_name) "
                        "VALUES ('invoiced', 'V14 Client') RETURNING id").fetchone()[0]
    sale_item_id = db.execute(
        "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
        "VALUES (%s, 'V14 Product', 100, 5, 'KG') RETURNING id", (sale_id,)).fetchone()[0]
    invoice_id = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status) "
        "VALUES (%s, 'INV-V14', 'planned') RETURNING id", (sale_id,)).fetchone()[0]
    recipe_id = db.execute(
        "INSERT INTO recipes (name, company_id, invoice_id) "
        "VALUES ('V14 Recipe', %s, %s) RETURNING id", (cid, invoice_id)).fetchone()[0]
    product_id = db.execute(
        "INSERT INTO recipe_products (recipe_id, product_name, unit) "
        "VALUES (%s, 'V14 Product', 'KG') RETURNING id", (recipe_id,)).fetchone()[0]
    run_id = db.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, qty_produced) "
        "VALUES (%s, %s, 100) RETURNING id", (recipe_id, sale_item_id)).fetchone()[0]
    db.commit()
    return {"company_id": cid, "chem_a": chem_a, "chem_b": chem_b,
            "purchase_id": pid, "line_id": line_id, "sale_id": sale_id,
            "sale_item_id": sale_item_id, "invoice_id": invoice_id,
            "recipe_id": recipe_id, "product_id": product_id, "run_id": run_id}


def test_v14_source_columns_constraints_and_index(db, pg_dsn):
    """v14 lands the source columns on both tables, the two named pairing
    CHECKs, the source_type domain CHECKs, the NUMERIC(18,6) evidence column and
    the FK-support index -- and the version is stamped."""
    assert dbmod._SCHEMA_VERSION >= 14

    assert {"source_type", "outward_purchase_line_id"} <= _table_columns(
        db, "recipe_items")
    assert {"source_type", "outward_purchase_line_id", "purchase_number",
            "purchase_date", "supplier_name", "client_name", "source_qty",
            "source_unit"} <= _table_columns(db, "production_run_items")

    raw = _raw(pg_dsn)
    try:
        stored = _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)
        assert stored is not None and int(stored[0]) == dbmod._SCHEMA_VERSION

        # New numeric evidence column is NUMERIC(18,6); existing REAL stays REAL.
        col = raw.execute(
            "SELECT data_type, numeric_precision, numeric_scale "
            "FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'production_run_items' "
            "AND column_name = 'source_qty'").fetchone()
        assert col is not None and col[0] == "numeric", col
        assert (col[1], col[2]) == (18, 6), col

        names = {r[0] for r in raw.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid IN "
            "('recipe_items'::regclass, 'production_run_items'::regclass)"
        ).fetchall()}
        assert "chk_recipe_item_source" in names
        assert "chk_run_item_source" in names
        # The domain CHECKs are separately named, so the pairing probe below
        # cannot pass by accident on an out-of-domain value.
        assert "chk_recipe_item_source_type" in names
        assert "chk_run_item_source_type" in names

        # The new index supports is_line_referenced()'s reverse lookup.
        defs = {r[0] for r in raw.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = "
            "'production_run_items'").fetchall()}
        assert any("outward_purchase_line_id" in d for d in defs), defs
    finally:
        raw.close()


def test_v14_pairing_checks_reject_invalid_source_rows(db, pg_dsn):
    """Both named CHECKs are the final authority, not the Pydantic model: the two
    invalid pairings (in_house+line, outward+NULL) and an out-of-domain
    source_type are refused on BOTH tables, while the valid pairings land and a
    referenced line is RESTRICT-protected from deletion."""
    ids = _v14_chain(db)

    def add_item(chemical_id, source_type, line_id):
        db.execute(
            "INSERT INTO recipe_items (recipe_product_id, chemical_id, percentage, "
            "source_type, outward_purchase_line_id) VALUES (%s, %s, 50, %s, %s)",
            (ids["product_id"], chemical_id, source_type, line_id))

    def add_run_item(chemical_id, source_type, line_id):
        db.execute(
            "INSERT INTO production_run_items (run_id, chemical_id, chemical_name, "
            "required_qty, unit, source_type, outward_purchase_line_id) "
            "VALUES (%s, %s, 'V14', 10, 'KG', %s, %s)",
            (ids["run_id"], chemical_id, source_type, line_id))

    # recipe_items: in_house must not carry a line.
    with pytest.raises(psycopg.IntegrityError):
        add_item(ids["chem_b"], "in_house", ids["line_id"])
    db.rollback()
    # recipe_items: outward must carry a line.
    with pytest.raises(psycopg.IntegrityError):
        add_item(ids["chem_b"], "outward", None)
    db.rollback()
    # recipe_items: only the two Slice-2 sources exist (no pi_demos value yet).
    with pytest.raises(psycopg.IntegrityError):
        add_item(ids["chem_b"], "pi_demos", ids["line_id"])
    db.rollback()
    # production_run_items: the same three refusals.
    with pytest.raises(psycopg.IntegrityError):
        add_run_item(ids["chem_a"], "in_house", ids["line_id"])
    db.rollback()
    with pytest.raises(psycopg.IntegrityError):
        add_run_item(ids["chem_a"], "outward", None)
    db.rollback()
    with pytest.raises(psycopg.IntegrityError):
        add_run_item(ids["chem_a"], "supplier", ids["line_id"])
    db.rollback()

    add_item(ids["chem_a"], "in_house", None)
    add_item(ids["chem_b"], "outward", ids["line_id"])
    add_run_item(ids["chem_a"], "outward", ids["line_id"])
    db.commit()

    # ON DELETE RESTRICT: a line a recipe already uses cannot be dropped.
    with pytest.raises(psycopg.IntegrityError):
        db.execute("DELETE FROM chemical_purchase_items WHERE id = %s",
                   (ids["line_id"],))
    db.rollback()


def test_v14_legacy_rows_default_to_in_house_and_rerun_is_a_noop(db, pg_dsn):
    """A legacy-shaped INSERT that omits the new columns is in_house with no line
    and NULL evidence, and re-running the migration twice changes nothing."""
    ids = _v14_chain(db)
    db.execute("INSERT INTO recipe_items (recipe_product_id, chemical_id, percentage) "
               "VALUES (%s, %s, 50)", (ids["product_id"], ids["chem_a"]))
    db.execute("INSERT INTO production_run_items (run_id, chemical_id, chemical_name, "
               "required_qty, deducted_qty, unit) VALUES (%s, %s, 'V14', 10, 10, 'KG')",
               (ids["run_id"], ids["chem_a"]))
    db.commit()

    item = db.execute(
        "SELECT source_type, outward_purchase_line_id FROM recipe_items "
        "WHERE recipe_product_id = %s", (ids["product_id"],)).fetchone()
    assert item[0] == "in_house" and item[1] is None
    run_item = db.execute(
        "SELECT source_type, outward_purchase_line_id, purchase_number, "
        "purchase_date, supplier_name, client_name, source_qty, source_unit "
        "FROM production_run_items WHERE run_id = %s", (ids["run_id"],)).fetchone()
    assert run_item[0] == "in_house"
    assert all(value is None for value in run_item[1:]), run_item

    # The DEFAULT is declared in the catalog, not merely backfilled onto rows.
    raw = _raw(pg_dsn)
    try:
        for table in ("recipe_items", "production_run_items"):
            default = raw.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s "
                "AND column_name = 'source_type'", (table,)).fetchone()[0]
            assert default is not None and "in_house" in default, table
    finally:
        raw.close()

    # Idempotent re-run: nothing raises, the version stays stamped, and rows
    # already written keep their source values. The fixture's own read
    # transaction must be closed first -- it holds ACCESS SHARE on the tables
    # the migration re-runs ALTER against, which would cancel on lock_timeout
    # rather than prove anything about the DDL (same reason the tests above
    # commit before forcing the migration path).
    db.commit()
    conn = dbmod.get_connection(pg_dsn)
    try:
        dbmod._create_tables(conn)
        dbmod._create_tables(conn)
    finally:
        conn.close()
    assert int(_setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)[0]) == dbmod._SCHEMA_VERSION
    assert db.execute("SELECT COUNT(*) FROM recipe_items WHERE source_type = "
                      "'in_house' AND outward_purchase_line_id IS NULL "
                      "AND recipe_product_id = %s",
                      (ids["product_id"],)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM production_run_items "
                      "WHERE source_type = 'in_house'").fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# v15 — PI DEMOS: draft/active planning formulations, expression identity,
#       bounds, RLS, and the idempotent re-run
# --------------------------------------------------------------------------- #
def _v15_chain(db, pi="PI-V15"):
    """Raw parent chain for the v15 probes: one PI with a line, one chemical,
    one demo, one draft product slot and one ingredient.

    SQL only (no Flask, no service layer) so the migration itself is the thing
    under test: the CHECKs and the expression index must be the final authority
    even for a writer that bypasses every Pydantic model.
    """
    sale_id = db.execute("INSERT INTO sales (stage, client_name) "
                         "VALUES ('pi_issued', %s) RETURNING id", (pi,)).fetchone()[0]
    db.execute("INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
               "VALUES (%s, 'V15 Product', 100, 5, 'KG')", (sale_id,))
    chem_id = db.execute("INSERT INTO chemicals (name, unit) VALUES ('V15 A', 'KG') "
                         "RETURNING id").fetchone()[0]
    demo_id = db.execute("INSERT INTO pi_demos (sale_id) VALUES (%s) RETURNING id",
                         (sale_id,)).fetchone()[0]
    product_id = db.execute(
        "INSERT INTO pi_demo_products (demo_id, scope, product_name, unit, "
        "water_percentage) VALUES (%s, 'draft', 'V15 Product', 'KG', 40) "
        "RETURNING id", (demo_id,)).fetchone()[0]
    db.execute("INSERT INTO pi_demo_items (demo_product_id, chemical_id, percentage) "
               "VALUES (%s, %s, 60)", (product_id, chem_id))
    db.commit()
    return {"sale_id": sale_id, "chem_id": chem_id, "demo_id": demo_id,
            "product_id": product_id}


def _add_demo_product(db, demo_id, scope, name, unit="KG", water=None):
    return db.execute(
        "INSERT INTO pi_demo_products (demo_id, scope, product_name, unit, "
        "water_percentage) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (demo_id, scope, name, unit, water)).fetchone()[0]


def test_v15_pi_demo_tables_columns_constraints_and_rls(db, pg_dsn):
    """v15 lands the three demo tables with the prescribed shape: the version is
    bumped and stamped, there is no active_formula JSONB column anywhere, the new
    numerics are NUMERIC(9,6) (existing REAL recipe columns stay REAL), the named
    constraints exist, and all three tables are RLS-enabled with no policy."""
    assert dbmod._SCHEMA_VERSION == 15
    tables = _public_tables(pg_dsn)
    assert {"pi_demos", "pi_demo_products", "pi_demo_items"} <= tables

    stored = _setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)
    assert stored is not None and int(stored[0]) == dbmod._SCHEMA_VERSION

    assert {"sale_id", "draft_revision", "active_revision", "activated_at",
            "activated_by"} <= _table_columns(db, "pi_demos")
    assert {"demo_id", "scope", "product_name", "unit",
            "water_percentage"} <= _table_columns(db, "pi_demo_products")
    assert {"demo_product_id", "chemical_id", "percentage"} <= _table_columns(
        db, "pi_demo_items")
    # The active scope's child rows are the SOLE operational snapshot: a JSON
    # copy of the same facts is free to disagree with the rows it describes.
    assert "active_formula" not in _table_columns(db, "pi_demos")

    raw = _raw(pg_dsn)
    try:
        for table in ("pi_demos", "pi_demo_products", "pi_demo_items"):
            row = raw.execute(
                "SELECT relrowsecurity FROM pg_class WHERE relname = %s",
                (table,)).fetchone()
            assert row is not None and row[0] is True, f"{table} RLS not enabled"
            # No policy at all is the protection: a browser role sees nothing.
            assert raw.execute(
                "SELECT COUNT(*) FROM pg_policies WHERE tablename = %s",
                (table,)).fetchone()[0] == 0, table

        for table, column in (("pi_demo_products", "water_percentage"),
                              ("pi_demo_items", "percentage")):
            col = raw.execute(
                "SELECT data_type, numeric_precision, numeric_scale "
                "FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = %s AND column_name = %s",
                (table, column)).fetchone()
            assert col is not None and col[0] == "numeric", (table, col)
            assert (col[1], col[2]) == (9, 6), (table, col)

        names = {r[0] for r in raw.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid IN "
            "('pi_demos'::regclass, 'pi_demo_products'::regclass, "
            "'pi_demo_items'::regclass)").fetchall()}
        assert "chk_pi_demo_active_water" in names
        assert "uq_pi_demo_item_chemical" in names

        # The product identity is an EXPRESSION index: lower(trim(...)) cannot
        # live inside a table-level UNIQUE constraint, so it must be a real
        # unique index over the expression.
        defs = {r[0] for r in raw.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'pi_demo_products'").fetchall()}
        identity = [d for d in defs if "idx_pi_demo_products_identity" in d]
        assert identity, defs
        # PostgreSQL renders the expression as lower(TRIM(BOTH FROM ...)), so the
        # probe reads the folded definition rather than a literal spelling.
        folded = re.sub(r"\s+", "", identity[0]).lower()
        assert "lower(trim(bothfromproduct_name))" in folded, identity
        assert "demo_id,scope," in folded, identity
        assert "uniqueindex" in folded, identity

        # pi_demo_items.chemical_id is RESTRICT-protected, so the reverse lookup
        # needs its own index; demo_id is covered by the identity index's
        # leading column and must not be duplicated.
        item_defs = {r[0] for r in raw.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'pi_demo_items'").fetchall()}
        assert any("chemical_id" in d for d in item_defs), item_defs
    finally:
        raw.close()

    # The Flask role (table owner, RLS-bypassing) still reads them: enabling RLS
    # with no policies must not lock the service out of its own tables.
    for table in ("pi_demos", "pi_demo_products", "pi_demo_items"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_v15_pi_demo_checks_and_identity_are_enforced(db, pg_dsn):
    """Every v15 rule is a real constraint, probed by writing against it: one
    demo per PI, the scope domain, the 0..100 bounds on percentage and water, the
    active-water NOT NULL CHECK, a draft percentage of exactly zero (legal: only
    activation requires positive), the normalized product identity (case and
    whitespace only), the one-ingredient-per-chemical uniqueness, and the two
    FK directions."""
    ids = _v15_chain(db)
    demo_id = ids["demo_id"]

    # one demo per PI (the unique sale_id, so no extra index is needed).
    with pytest.raises(psycopg.IntegrityError):
        db.execute("INSERT INTO pi_demos (sale_id) VALUES (%s)", (ids["sale_id"],))
    db.rollback()

    # scope domain.
    with pytest.raises(psycopg.IntegrityError):
        _add_demo_product(db, demo_id, "archived", "V15 Product")
    db.rollback()
    # water above the bound.
    with pytest.raises(psycopg.IntegrityError):
        _add_demo_product(db, demo_id, "draft", "Over Water", water=100.5)
    db.rollback()
    # water below the bound.
    with pytest.raises(psycopg.IntegrityError):
        _add_demo_product(db, demo_id, "draft", "Negative Water", water=-1)
    db.rollback()
    # an active row may not carry a NULL/auto water: the stored value must be the
    # RESOLVED one, so no projection can re-derive it differently per reader.
    with pytest.raises(psycopg.IntegrityError):
        _add_demo_product(db, demo_id, "active", "V15 Product", water=None)
    db.rollback()
    # a draft row may: NULL water means "auto" while the plan is unfinished.
    assert _add_demo_product(db, demo_id, "draft", "Auto Water", water=None)
    db.rollback()

    # ingredient percentage bounds (0 is legal on a draft, 100.5 and -1 are not).
    def add_item(chem_id, percentage):
        db.execute("INSERT INTO pi_demo_items (demo_product_id, chemical_id, "
                   "percentage) VALUES (%s, %s, %s)",
                   (ids["product_id"], chem_id, percentage))

    other_chem = db.execute("INSERT INTO chemicals (name, unit) VALUES ('V15 B', 'KG') "
                            "RETURNING id").fetchone()[0]
    db.commit()
    with pytest.raises(psycopg.IntegrityError):
        add_item(other_chem, 100.5)
    db.rollback()
    with pytest.raises(psycopg.IntegrityError):
        add_item(other_chem, -0.5)
    db.rollback()
    add_item(other_chem, 0)
    db.commit()

    # one ingredient per chemical per product.
    with pytest.raises(psycopg.IntegrityError):
        add_item(other_chem, 10)
    db.rollback()

    # the expression identity: same name differing only in case/whitespace is
    # refused within one scope, and the SAME name in the OTHER scope is legal --
    # the draft and the activated snapshot are two working sets, not one.
    with pytest.raises(psycopg.IntegrityError):
        _add_demo_product(db, demo_id, "draft", "  V15 product  ")
    db.rollback()
    active_id = _add_demo_product(db, demo_id, "active", "V15 Product", water=40)
    db.commit()
    assert active_id != ids["product_id"]
    # a different PI's demo may use the same product name: identity is scoped.
    other_sale = db.execute("INSERT INTO sales (stage, client_name) "
                            "VALUES ('pi_issued', 'PI-V15-OTHER') RETURNING id").fetchone()[0]
    other_demo = db.execute("INSERT INTO pi_demos (sale_id) VALUES (%s) RETURNING id",
                            (other_sale,)).fetchone()[0]
    assert _add_demo_product(db, other_demo, "draft", "V15 Product", water=None)
    db.commit()

    # an unknown demo / product is refused, and a chemical a formulation uses is
    # RESTRICT-protected while an unused product cascades away with its demo.
    with pytest.raises(psycopg.IntegrityError):
        db.execute("INSERT INTO pi_demo_items (demo_product_id, chemical_id, "
                   "percentage) VALUES (999999, %s, 10)", (ids["chem_id"],))
    db.rollback()
    with pytest.raises(psycopg.IntegrityError):
        db.execute("DELETE FROM chemicals WHERE id = %s", (ids["chem_id"],))
    db.rollback()
    db.execute("DELETE FROM pi_demos WHERE id = %s", (other_demo,))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM pi_demo_products WHERE demo_id = %s",
                      (other_demo,)).fetchone()[0] == 0


def test_v15_rerun_is_a_noop_and_preserves_rows(db, pg_dsn):
    """Re-running the migration twice on a migrated database raises nothing,
    keeps the stamped version, and leaves the demo rows -- and their revisions --
    exactly as written. The activation copy is business state; a DDL retry must
    never reset it."""
    ids = _v15_chain(db)
    before = db.execute(
        "SELECT scope, product_name, unit, water_percentage FROM pi_demo_products "
        "WHERE demo_id = %s ORDER BY id", (ids["demo_id"],)).fetchall()
    revision = db.execute("SELECT draft_revision, active_revision FROM pi_demos "
                          "WHERE id = %s", (ids["demo_id"],)).fetchone()
    # Close the fixture's read transaction: it holds ACCESS SHARE on tables the
    # migration re-runs DDL against (same reason as the v13/v14 re-run tests).
    db.commit()

    conn = dbmod.get_connection(pg_dsn)
    try:
        dbmod._create_tables(conn)
        dbmod._create_tables(conn)
    finally:
        conn.close()

    assert int(_setting(pg_dsn, dbmod._SCHEMA_VERSION_KEY)[0]) == dbmod._SCHEMA_VERSION
    after = db.execute(
        "SELECT scope, product_name, unit, water_percentage FROM pi_demo_products "
        "WHERE demo_id = %s ORDER BY id", (ids["demo_id"],)).fetchall()
    assert after == before
    assert db.execute("SELECT draft_revision, active_revision FROM pi_demos "
                      "WHERE id = %s", (ids["demo_id"],)).fetchone() == revision
