"""RAAS Tracker database backend: PostgreSQL.

Single connection factory for the whole app (Flask handlers, CLI, tests).
Connects via DATABASE_URL (psycopg v3, transactional mode — explicit
conn.commit()/conn.rollback() behave exactly like the former SQLite backend).

Column style is intentionally conservative: datetimes stay TEXT
('YYYY-MM-DD HH:MM:SS', via to_char(NOW(), ...)), flags stay INTEGER 0/1,
so all Python-side comparisons and sorting work unchanged.
"""

import atexit
import logging as _logging
import os
import threading
from typing import Optional, Set

import psycopg
from psycopg_pool import ConnectionPool


class _PooledConnection:
    """Wrapper that returns connection to pool on close() instead of closing it."""

    __slots__ = ("_conn", "_pool")

    def __init__(self, conn: psycopg.Connection, pool: ConnectionPool):
        self._conn = conn
        self._pool = pool

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self):
        """Return connection to pool instead of closing it. Commit any pending transaction first.

        Explicit close() is the *success* path: callers across the app rely on it
        to make their writes durable (transactional mode, no autocommit). An
        exceptional exit must go through rollback() instead - see __exit__.
        """
        if self._conn is not None:
            try:
                # Commit any pending transaction to avoid "INTRANS" rollback warnings
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            self._pool.putconn(self._conn)
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Never commit a block that raised. Before connection pooling,
        # psycopg.Connection.close() on an open transaction performed a
        # server-side ROLLBACK; the pooled wrapper's close() commits instead,
        # so `with get_connection() as conn:` used to make half-written work
        # durable on every error path. Roll back on exception, commit on
        # success, and return the connection to the pool either way.
        if exc_type is None:
            self.close()
        else:
            conn, self._conn = self._conn, None
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                self._pool.putconn(conn)
        return False

logger = _logging.getLogger("raas")
if not logger.handlers:
    _handler = _logging.StreamHandler()
    _handler.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(os.getenv("RAAS_LOG_LEVEL", "INFO").upper() or "INFO")


DEFAULT_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/raas"
DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/raas_test"

# app_settings key holding the schema signature (see _schema_signature).
_SCHEMA_SIG_KEY = "raas_schema_sig"

# app_settings key holding the code-owned schema version.
_SCHEMA_VERSION_KEY = "raas_schema_version"

# Code-owned, monotonically increasing schema version.
#
# The stored signature is only ever a copy of the *live* schema, so it can
# never detect a *code-side* schema change: adding a column to
# _create_tables() leaves stored == live (both still lack the column) and the
# fast path would skip the migration forever. The old defence was a hand-typed
# _REQUIRED_SIG_TOKENS tuple that nothing enforced, so additive migrations
# that forgot their token silently never applied and no later deploy recovered.
#
# This integer is the primary, self-maintaining gate: _schema_current() returns
# False whenever stored != _SCHEMA_VERSION, and _create_tables() re-stamps the
# current value in the same transaction as the signature.
#
# BUMP RULE: increment this whenever _create_tables() gains DDL (new table,
# new column, new index) or a data migration that must run once. A database
# that predates this key has no row at all, which counts as "not current" and
# runs the full path exactly once.
#
_SCHEMA_VERSION = 2

# Frozen legacy tokens from builds that shipped them — do NOT add
# per-migration entries. _SCHEMA_VERSION is authoritative; these only preserve
# the old gate for databases upgraded from those builds. A mistyped token
# forces the full DDL path on every connection.
_REQUIRED_SIG_TOKENS: tuple = ("invoices.amount:numeric",)

# Advisory lock key serialising the migration across pool connections, so a
# cold start with --threads=N runs the DDL once instead of N times racing.
_MIGRATION_LOCK_KEY = 0x52414153  # "RAAS"

# Guards lazy pool construction (double-checked locking around _pool/_pool_dsn).
_pool_lock = threading.Lock()

# Connection pool (initialized lazily on first get_connection call)
_pool: Optional[ConnectionPool] = None
_pool_dsn: Optional[str] = None


def data_dir() -> str:
    """Writable data directory: RAAS_DATA_DIR (Docker volume) or repo root."""
    override = os.getenv("RAAS_DATA_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Archive path of the legacy SQLite database (read-only source for the
# one-off Phase-4 migration). No longer used as a live backend.
DB_PATH = os.path.join(data_dir(), "chem_stock.db")
JSON_PATH = os.path.join(data_dir(), "stock_data.json")


def resolve_dsn(dsn: Optional[str] = None) -> str:
    """Return the effective PostgreSQL DSN: explicit arg, env, then default."""
    return dsn or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


def _get_pool(dsn: Optional[str] = None) -> ConnectionPool:
    """Get or create the global connection pool.

    Double-checked locking: on a cold start several threads (waitress
    --threads=4) reach this at once. Without the lock each builds its own
    ConnectionPool, and only the winner is reachable from close_pool/atexit,
    so every loser's pool (plus its min_size=1 background connection) leaked.
    """
    global _pool, _pool_dsn
    effective_dsn = resolve_dsn(dsn)
    pool = _pool
    if pool is not None and _pool_dsn == effective_dsn:
        return pool
    with _pool_lock:
        if _pool is None or _pool_dsn != effective_dsn:
            if _pool is not None:
                _pool.close()
            _pool = ConnectionPool(
                conninfo=effective_dsn,
                min_size=1,
                max_size=10,
                kwargs={"prepare_threshold": None},
                open=True,
            )
            _pool_dsn = effective_dsn
        return _pool


def get_connection(dsn: Optional[str] = None) -> psycopg.Connection:
    """Get a connection from the pool, ensuring schema/migrations/seeds.

    The connection runs in transactional mode: DML requires conn.commit()
    (or rolls back with conn.rollback()), matching previous backend semantics.
    Server-side statement preparation is disabled (prepare_threshold=None)
    so the app also works through transaction-mode poolers (e.g. Supabase).

    Schema setup runs only when the recorded schema version differs from the
    code's _SCHEMA_VERSION, or the live schema drifts from the recorded
    signature (missing tables/columns after an upgrade or a manual DROP):
    the common case is a single probe query, which keeps per-request (and
    per-test) overhead to one round trip instead of the full DDL.

    The returned connection is wrapped so that close() returns it to the pool
    instead of closing it, maintaining compatibility with existing code.
    """
    pool = _get_pool(dsn)
    conn = pool.getconn()
    # Run schema check and seeds on each connection (cheap: one probe + two COUNTs)
    # This maintains the original behavior where schema drift is detected per-request.
    try:
        if not _schema_current(conn):
            _create_tables(conn)
        _ensure_seeds(conn)
    except Exception:
        # If schema check/migration fails, discard the transaction (a failed
        # migration leaves the connection aborted) and return it to the pool,
        # then re-raise. The caller sees the same exception as before.
        try:
            conn.rollback()
        except Exception:
            pass
        pool.putconn(conn)
        raise
    return _PooledConnection(conn, pool)


def close_pool() -> None:
    """Close the global connection pool. Called at shutdown."""
    global _pool, _pool_dsn
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
            _pool_dsn = None


atexit.register(close_pool)


def _schema_signature_live(conn: psycopg.Connection) -> str:
    """Canonical columns-plus-indexes listing of the public schema."""
    cur = conn.execute(
        """SELECT (SELECT coalesce(string_agg(table_name || '.' || column_name || ':'
                                               || data_type, ',' ORDER BY table_name,
                                               column_name, data_type), '')
                   FROM information_schema.columns WHERE table_schema = 'public')
                  || '|idx:' ||
                  (SELECT coalesce(string_agg(indexname, ',' ORDER BY indexname), '')
                   FROM pg_indexes WHERE schemaname = 'public')""")
    return cur.fetchone()[0]


def _version_is_current(stored_version: Optional[str]) -> bool:
    """True only when the stored schema version parses to the code version.

    A missing key (database predating _SCHEMA_VERSION) or any unparseable
    value is "not current": the full DDL path must run once, which is exactly
    the migration behaviour we want.
    """
    if stored_version is None:
        return False
    try:
        return int(str(stored_version).strip()) == _SCHEMA_VERSION
    except (TypeError, ValueError):
        return False


def _schema_current(conn: psycopg.Connection) -> bool:
    """True when the live schema matches the code version + recorded signature.

    Single round trip. The version is the primary gate: it is the only signal
    that can see a code-side change to _create_tables(), since the signature is
    just a copy of the live schema. Any error (fresh database without
    app_settings, missing tables) means "not current" and triggers the full path.
    """
    try:
        cur = conn.execute(
            "SELECT (SELECT value FROM app_settings WHERE key = %s), "
            "(SELECT value FROM app_settings WHERE key = %s), "
            "(SELECT (SELECT coalesce(string_agg(table_name || '.' || column_name || ':'"
            " || data_type, ',' ORDER BY table_name, column_name, data_type), '') "
            "FROM information_schema.columns WHERE table_schema = 'public') "
            "|| '|idx:' || "
            "(SELECT coalesce(string_agg(indexname, ',' ORDER BY indexname), '') "
            "FROM pg_indexes WHERE schemaname = 'public'))",
            (_SCHEMA_SIG_KEY, _SCHEMA_VERSION_KEY),
        )
        stored, stored_version, live = cur.fetchone()
        ready = (_version_is_current(stored_version)
                 and bool(stored) and stored == live
                 and all(tok in live for tok in _REQUIRED_SIG_TOKENS))
    except Exception:
        ready = False
    try:
        conn.rollback()
    except Exception:
        pass
    return ready


def _table_columns(conn: psycopg.Connection, table: str) -> Set[str]:
    """Column names of a table in the public schema (empty set if missing)."""
    cur = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s""",
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


def _create_tables(conn: psycopg.Connection) -> None:
    """Migrate the schema, atomically.

    The whole migration is ONE transaction that commits exactly once, at the
    end, together with the signature + version upsert. Any failure rolls the
    lot back, so a database is never left half-migrated: the recorded version
    stays behind, the next get_connection() retries cleanly, and no partial DDL
    is ever durable. Nothing below this function may commit or rollback.

    Concurrency: a transaction-scoped advisory lock serialises the body, so on a
    cold start with --threads=N only one connection migrates. xact-scoped means
    PostgreSQL releases it at COMMIT *or* ROLLBACK, so it cannot leak onto a
    pooled connection even if this function raises.
    """
    try:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
        _run_migration(conn)
        _record_schema_state(conn)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def _record_schema_state(conn: psycopg.Connection) -> None:
    """Stamp the live signature and the code schema version (same transaction)."""
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (_SCHEMA_SIG_KEY, _schema_signature_live(conn)),
    )
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (_SCHEMA_VERSION_KEY, str(_SCHEMA_VERSION)),
    )


def backfill_lc_links(conn: psycopg.Connection) -> int:
    """Group legacy sales.lc_number mirrors into letters_of_credit rows.

    Idempotent: sales sharing (company_id, trimmed lc_number) share ONE lc
    row; singletons get their own row; sales with no lc_number (or no
    company) stay unlinked (lc_id NULL). Re-running links only still-NULL
    rows and reuses existing lc rows, so ids are stable and no dupes appear.

    Transaction control belongs to the caller: no commit/rollback in here,
    mirroring backfill_company_links (which must stay commit-free so
    _create_tables remains ONE atomic transaction).
    """
    groups = conn.execute(
        """SELECT DISTINCT company_id, trim(lc_number) AS lc
           FROM sales
           WHERE lc_id IS NULL AND company_id IS NOT NULL
             AND lc_number IS NOT NULL AND trim(lc_number) <> ''"""
    ).fetchall()
    linked = 0
    for company_id, lc_number in groups:
        row = conn.execute(
            "SELECT id FROM letters_of_credit "
            "WHERE company_id = %s AND lc_number = %s",
            (company_id, lc_number),
        ).fetchone()
        if row is None:
            lc_date = conn.execute(
                "SELECT MIN(lc_date) FROM sales "
                "WHERE lc_id IS NULL AND company_id = %s "
                "AND trim(lc_number) = %s",
                (company_id, lc_number),
            ).fetchone()[0]
            row = conn.execute(
                "INSERT INTO letters_of_credit (lc_number, company_id, lc_date) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (company_id, lc_number) DO NOTHING RETURNING id",
                (lc_number, company_id, lc_date),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id FROM letters_of_credit "
                    "WHERE company_id = %s AND lc_number = %s",
                    (company_id, lc_number),
                ).fetchone()
        if row is None:
            logger.warning("LC race on (%s, %s); skipping group for next run.", company_id, lc_number)
            continue
        cursor = conn.execute(
            "UPDATE sales SET lc_id = %s "
            "WHERE lc_id IS NULL AND company_id = %s AND trim(lc_number) = %s",
            (row[0], company_id, lc_number),
        )
        linked += cursor.rowcount or 0
    if linked:
        logger.info("Backfilled %s sales rows to letters_of_credit.", linked)
    return linked


def _run_migration(conn: psycopg.Connection) -> None:
    """Every DDL statement of the schema. NO commit/rollback in here.

    Runs inside _create_tables' transaction, under its advisory lock. Every
    statement must be idempotent so it is safe on a fresh *and* an existing
    production database.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chemicals (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            current_qty REAL NOT NULL DEFAULT 0,
            balance_last_month REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'KG',
            last_updated TEXT,
            reorder_level REAL NOT NULL DEFAULT 0
        );
        -- P0: company master (customers). sales.company_id links legacy
        -- free-text clients; client_name stays as compat/display fallback.
        -- Declared before recipes (which references it).
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            code TEXT,
            country TEXT,
            address TEXT,
            contact_person TEXT,
            swift TEXT,
            lc_bank TEXT,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL,
            total_quantity REAL NOT NULL DEFAULT 1,
            water_percentage REAL NOT NULL DEFAULT 0,
            created_date TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD')),
            company_id INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
            product_name TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_company_name
            ON recipes (company_id, name);
        CREATE TABLE IF NOT EXISTS recipe_items (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            recipe_id INTEGER NOT NULL,
            chemical_id INTEGER NOT NULL,
            percentage REAL NOT NULL DEFAULT 0,
            required_qty_per_unit REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY (chemical_id) REFERENCES chemicals(id) ON DELETE CASCADE
        );
    """)
    # Migration for existing databases - add total_quantity column and drop product_yield
    columns = _table_columns(conn, "recipes")
    if "total_quantity" not in columns:
        if "product_yield" in columns:
            # Migrate: copy product_yield data to total_quantity, then drop product_yield
            conn.execute("ALTER TABLE recipes ADD COLUMN total_quantity REAL DEFAULT 0")
            conn.execute("UPDATE recipes SET total_quantity = product_yield WHERE total_quantity = 0")
            conn.execute("ALTER TABLE recipes DROP COLUMN product_yield")
        else:
            # Add new column if neither exists
            conn.execute("ALTER TABLE recipes ADD COLUMN total_quantity REAL DEFAULT 1")
    # Migration for existing databases - add water_percentage to recipes
    if "water_percentage" not in _table_columns(conn, "recipes"):
        conn.execute("ALTER TABLE recipes ADD COLUMN water_percentage REAL DEFAULT 0")
    # Migration for existing databases
    if "percentage" not in _table_columns(conn, "recipe_items"):
        conn.execute("ALTER TABLE recipe_items ADD COLUMN percentage REAL DEFAULT 0")
        conn.execute("UPDATE recipe_items SET percentage = required_qty_per_unit * 100 WHERE percentage = 0")
    # Migration for existing databases - add balance_last_month to chemicals
    if "balance_last_month" not in _table_columns(conn, "chemicals"):
        conn.execute("ALTER TABLE chemicals ADD COLUMN balance_last_month REAL DEFAULT 0")
    # Migration - per-chemical reorder level (0 = feature off, only exact-zero counts)
    if "reorder_level" not in _table_columns(conn, "chemicals"):
        conn.execute("ALTER TABLE chemicals ADD COLUMN reorder_level REAL NOT NULL DEFAULT 0")
    # Create upload tracking tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            filename TEXT NOT NULL,
            upload_date TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            file_type TEXT,
            status TEXT DEFAULT 'uploaded',
            total_chemicals INTEGER DEFAULT 0,
            matched INTEGER DEFAULT 0,
            last_month_mismatches INTEGER DEFAULT 0,
            this_month_mismatches INTEGER DEFAULT 0,
            both_mismatches INTEGER DEFAULT 0,
            not_in_db INTEGER DEFAULT 0,
            not_in_upload INTEGER DEFAULT 0,
            match_percentage REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS upload_rows (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            upload_id INTEGER NOT NULL,
            chemical_name TEXT,
            batch_number TEXT,
            expiry_date TEXT,
            upload_unit TEXT,
            balance_last_month REAL,
            balance_this_month REAL,
            matched_in_db INTEGER DEFAULT 0,
            unit_match INTEGER DEFAULT 1,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS unit_conversions (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            from_unit TEXT NOT NULL,
            to_unit TEXT NOT NULL,
            factor REAL NOT NULL,
            UNIQUE(from_unit, to_unit)
        );
        CREATE TABLE IF NOT EXISTS reason_codes (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            description TEXT,
            category TEXT
        );
        CREATE TABLE IF NOT EXISTS approval_workflow (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            upload_id INTEGER NOT NULL,
            upload_row_id INTEGER,
            status TEXT DEFAULT 'pending',
            reason_code TEXT,
            comments TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE,
            FOREIGN KEY (upload_row_id) REFERENCES upload_rows(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            user_id TEXT DEFAULT 'system',
            old_value TEXT,
            new_value TEXT,
            timestamp TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            ip_address TEXT
        );
        CREATE TABLE IF NOT EXISTS reconciliation_periods (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            period_name TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            status TEXT DEFAULT 'open',
            locked_by TEXT,
            locked_at TEXT,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            stage TEXT NOT NULL DEFAULT 'pi_issued',
            pi_number TEXT,
            pi_date TEXT,
            client_name TEXT,
            pi_file_path TEXT,
            lc_number TEXT,
            lc_date TEXT,
            shipment_date TEXT,
            payment_date TEXT,
            payment_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            updated_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            sale_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sales_stage_history (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            sale_id INTEGER NOT NULL,
            from_stage TEXT,
            to_stage TEXT NOT NULL,
            changed_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            notes TEXT,
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sale_payments (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            sale_id INTEGER NOT NULL,
            payment_date TEXT,
            payment_amount REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
        );
        -- P1: partial shipments (one row per actual shipment event).
        CREATE TABLE IF NOT EXISTS shipments (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            ship_date TEXT NOT NULL,
            invoice_number TEXT,
            invoice_date TEXT,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        -- P1: production runs (P3 executes). Run items snapshot the formula
        -- actually deducted, so master edits never rewrite batch history.
        CREATE TABLE IF NOT EXISTS production_runs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE RESTRICT,
            sale_item_id INTEGER NOT NULL REFERENCES sale_items(id) ON DELETE CASCADE,
            order_number TEXT,
            batch_number TEXT,
            production_date TEXT,
            qty_produced REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        CREATE TABLE IF NOT EXISTS production_run_items (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
            chemical_id INTEGER REFERENCES chemicals(id) ON DELETE RESTRICT,
            chemical_name TEXT NOT NULL,
            required_qty REAL NOT NULL DEFAULT 0,
            deducted_qty REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'KG'
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            expires_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT,
            ip_address TEXT,
            attempted_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            success INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            key_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            expires_at TEXT,
            allowed_ips TEXT DEFAULT '',
            revoked INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            last_used_ip TEXT,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS api_key_rate_limits (
            key_id INTEGER NOT NULL,
            hit_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            FOREIGN KEY (key_id) REFERENCES api_keys(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            severity TEXT NOT NULL DEFAULT 'info',
            role_scope TEXT NOT NULL DEFAULT 'all',
            entity_type TEXT,
            entity_id INTEGER,
            dedupe_key TEXT,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key);
        -- Chemical identity is case-insensitive application-wide: forbid
        -- 'Acid' vs 'acid' duplicates that would split reconciliation.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_chemicals_name_lower
            ON chemicals (lower(name));
        CREATE INDEX IF NOT EXISTS idx_audit_chemical_time
            ON audit_logs (entity_type, entity_id, timestamp);
        -- Performance indexes for dashboard / sales pipeline queries
        CREATE INDEX IF NOT EXISTS idx_sales_stage ON sales(stage);
        CREATE INDEX IF NOT EXISTS idx_sales_created_at ON sales(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sale_items_sale_id ON sale_items(sale_id);
        CREATE INDEX IF NOT EXISTS idx_sale_items_product_name ON sale_items(product_name);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC);
        CREATE TABLE IF NOT EXISTS notification_reads (
            user_id INTEGER NOT NULL,
            notification_id INTEGER NOT NULL,
            read_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            PRIMARY KEY (user_id, notification_id),
            FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE
        );
    """)
    # Migration for existing databases - add batch/unit columns to upload_rows
    if "batch_number" not in _table_columns(conn, "upload_rows"):
        conn.execute("ALTER TABLE upload_rows ADD COLUMN batch_number TEXT")
        conn.execute("ALTER TABLE upload_rows ADD COLUMN expiry_date TEXT")
        conn.execute("ALTER TABLE upload_rows ADD COLUMN upload_unit TEXT")
        conn.execute("ALTER TABLE upload_rows ADD COLUMN unit_match INTEGER DEFAULT 1")
    # P0: wire legacy free-text clients to the companies master (idempotent).
    if "company_id" not in _table_columns(conn, "sales"):
        conn.execute("ALTER TABLE sales ADD COLUMN company_id INTEGER "
                     "REFERENCES companies(id) ON DELETE RESTRICT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_company_id ON sales(company_id)")
    if "comments" not in _table_columns(conn, "sales"):
        conn.execute("ALTER TABLE sales ADD COLUMN comments TEXT")
    if "maturity_date" not in _table_columns(conn, "sales"):
        conn.execute("ALTER TABLE sales ADD COLUMN maturity_date TEXT")
    if "unit" not in _table_columns(conn, "sale_items"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN unit TEXT NOT NULL DEFAULT 'KG'")
    # M10: password reset/change (no email column, no new table).
    if "reset_token_hash" not in _table_columns(conn, "users"):
        conn.execute("ALTER TABLE users ADD COLUMN reset_token_hash TEXT")
    if "reset_token_expires_at" not in _table_columns(conn, "users"):
        conn.execute("ALTER TABLE users ADD COLUMN reset_token_expires_at TEXT")
    if "must_change_password" not in _table_columns(conn, "users"):
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    # P2: master recipe linkage (company x product, no auto-create).
    if "company_id" not in _table_columns(conn, "recipes"):
        conn.execute("ALTER TABLE recipes ADD COLUMN company_id INTEGER "
                     "REFERENCES companies(id) ON DELETE RESTRICT")
    if "product_name" not in _table_columns(conn, "recipes"):
        conn.execute("ALTER TABLE recipes ADD COLUMN product_name TEXT")
    # Global name uniqueness gives way to one master per company x product.
    conn.execute("ALTER TABLE recipes DROP CONSTRAINT IF EXISTS recipes_name_key")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_company_name "
                 "ON recipes (company_id, name)")
    from .companies import backfill_company_links  # local: avoids circular import
    backfill_company_links(conn)

    # S1: Production & Invoice schema
    # Fix production_runs.sale_item_id (broken NOT NULL).
    # Probe is_nullable instead of catching the ALTER's error: a failed
    # statement aborts the whole transaction, and the migration is now one
    # transaction, so a swallowed error here would poison everything after it.
    cur = conn.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'production_runs' "
        "AND column_name = 'sale_item_id'")
    nullable = cur.fetchone()
    if nullable and nullable[0] == "NO":
        conn.execute("ALTER TABLE production_runs ALTER COLUMN sale_item_id DROP NOT NULL")
    # production_runs new columns
    for col, ddl in [
        ("material_number", "ADD COLUMN material_number TEXT"),
        ("packing", "ADD COLUMN packing TEXT"),
        ("invoice_number", "ADD COLUMN invoice_number TEXT"),
    ]:
        if col not in _table_columns(conn, "production_runs"):
            conn.execute(f"ALTER TABLE production_runs {ddl}")

    # invoices table (idempotent: no existence SELECT, so no check-then-act race)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            invoice_number TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            approx_ship_date TEXT,
            actual_ship_date TEXT,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_sale_number "
                 "ON invoices (sale_id, invoice_number)")

    # production_run_links table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_run_links (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
            sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL
        )
    """)

    # sales.shipment_status
    if "shipment_status" not in _table_columns(conn, "sales"):
        conn.execute("ALTER TABLE sales ADD COLUMN shipment_status TEXT")

    # sale_items.item_no
    if "item_no" not in _table_columns(conn, "sale_items"):
        conn.execute("ALTER TABLE sale_items ADD COLUMN item_no TEXT")

    # sale_payments.invoice_id
    if "invoice_id" not in _table_columns(conn, "sale_payments"):
        conn.execute("ALTER TABLE sale_payments ADD COLUMN invoice_id INTEGER "
                     "REFERENCES invoices(id) ON DELETE SET NULL")

    # invoices.amount (NULL = legacy/unknown; drives invoice auto-paid rule)
    if "amount" not in _table_columns(conn, "invoices"):
        conn.execute("ALTER TABLE invoices ADD COLUMN amount NUMERIC(14,2)")

    # Phase 1: LC foundation. sales.lc_number/lc_date TEXT mirrors stay
    # untouched (they become display mirrors); sales.lc_id is the new link.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS letters_of_credit (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            lc_number TEXT NOT NULL,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
            lc_date TEXT,
            expiry_date TEXT,
            bank_ref TEXT,
            stage TEXT NOT NULL DEFAULT 'lc_received',
            notes TEXT,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
            updated_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_letters_of_credit_company_lc "
                 "ON letters_of_credit (company_id, lc_number)")
    if "lc_id" not in _table_columns(conn, "sales"):
        conn.execute("ALTER TABLE sales ADD COLUMN lc_id INTEGER "
                     "REFERENCES letters_of_credit(id) ON DELETE RESTRICT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sales_lc_id ON sales(lc_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            sale_item_id INTEGER REFERENCES sale_items(id) ON DELETE SET NULL,
            product_name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'KG',
            quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            line_total NUMERIC(14,2)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice_id "
                 "ON invoice_items(invoice_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_sale_item_id "
                 "ON invoice_items(sale_item_id)")
    backfill_lc_links(conn)

    # Global name uniqueness gives way to one master per company x product.
    conn.execute("ALTER TABLE recipes DROP CONSTRAINT IF EXISTS recipes_name_key")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_company_name "
                 "ON recipes (company_id, name)")
    # Second pass so companies created for legacy free-text clients also get
    # the unique index / constraint cleanup applied to the same rows.
    from .companies import backfill_company_links  # local: avoids circular import
    backfill_company_links(conn)
    # Signature + schema version are stamped by _create_tables, in this same
    # transaction: nothing below commits.


def _ensure_seeds(conn: psycopg.Connection) -> None:
    """Insert default reason codes / unit conversions when their tables are empty.

    Runs on every connection (cheap: two COUNT probes): test isolation
    truncates seed tables, and operators may delete rows, so seeds cannot
    be gated behind the schema version.
    """
    # Seed default reason codes
    cursor = conn.execute("SELECT COUNT(*) FROM reason_codes")
    if cursor.fetchone()[0] == 0:
        default_reasons = [
            ("MEASUREMENT_ERROR", "Small measurement difference", "quantity"),
            ("UNIT_CONVERSION", "Unit conversion discrepancy", "unit"),
            ("DATA_ENTRY_ERROR", "Data entry mistake", "quantity"),
            ("NEW_PRODUCT", "New product not in system", "missing"),
            ("DISPOSED", "Product was disposed/expired", "missing"),
            ("TRANSFERRED", "Product transferred to another location", "missing"),
            ("COUNTED_WRONG", "Physical count was incorrect", "quantity"),
            ("SYSTEM_ERROR", "System calculation error", "system"),
            ("BATCH_SPLIT", "Batch was split", "batch"),
            ("EXPIRY_UPDATED", "Expiry date was updated", "expiry"),
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO reason_codes (code, description, category) VALUES (%s, %s, %s) "
                "ON CONFLICT (code) DO NOTHING",
                default_reasons
            )
    # Seed default unit conversions
    cursor = conn.execute("SELECT COUNT(*) FROM unit_conversions")
    if cursor.fetchone()[0] == 0:
        default_conversions = [
            ("KG", "G", 1000), ("G", "KG", 0.001),
            ("L", "ML", 1000), ("ML", "L", 0.001),
            ("KG", "L", 1), ("L", "KG", 1),
            ("DRUM", "L", 200), ("DRUM", "KG", 200),
            ("BOTTLE", "L", 2.5), ("BOTTLE", "KG", 2.5),
            ("BOX", "PCS", 12), ("PCS", "BOX", 1/12),
        ]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO unit_conversions (from_unit, to_unit, factor) VALUES (%s, %s, %s) "
                "ON CONFLICT (from_unit, to_unit) DO NOTHING",
                default_conversions
            )
    conn.commit()
