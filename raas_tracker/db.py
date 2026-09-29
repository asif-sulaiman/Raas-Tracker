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
        """Return connection to pool instead of closing it. Commit any pending transaction first."""
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
        self.close()
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
    """Get or create the global connection pool."""
    global _pool, _pool_dsn
    effective_dsn = resolve_dsn(dsn)
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

    Schema setup runs only when the live schema drifts from the recorded
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
        # If schema check fails, return connection to pool and re-raise
        pool.putconn(conn)
        raise
    return _PooledConnection(conn, pool)


def close_pool() -> None:
    """Close the global connection pool. Called at shutdown."""
    global _pool, _pool_dsn
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


def _schema_current(conn: psycopg.Connection) -> bool:
    """True when the live schema matches the recorded signature.

    Single round trip. Any error (fresh database without app_settings,
    missing tables) means "not current" and triggers the full path.
    """
    try:
        cur = conn.execute(
            "SELECT (SELECT value FROM app_settings WHERE key = %s), "
            "(SELECT (SELECT coalesce(string_agg(table_name || '.' || column_name || ':'"
            " || data_type, ',' ORDER BY table_name, column_name, data_type), '') "
            "FROM information_schema.columns WHERE table_schema = 'public') "
            "|| '|idx:' || "
            "(SELECT coalesce(string_agg(indexname, ',' ORDER BY indexname), '') "
            "FROM pg_indexes WHERE schemaname = 'public'))",
            (_SCHEMA_SIG_KEY,),
        )
        stored, live = cur.fetchone()
        ready = bool(stored) and stored == live
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
    """Create all required tables if they don't exist, then migrate + seed."""
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
    # Fix production_runs.sale_item_id (broken NOT NULL)
    if "sale_item_id" in _table_columns(conn, "production_runs"):
        try:
            conn.execute("ALTER TABLE production_runs ALTER COLUMN sale_item_id DROP NOT NULL")
        except Exception:
            pass  # already nullable
    # production_runs new columns
    for col, ddl in [
        ("material_number", "ADD COLUMN material_number TEXT"),
        ("packing", "ADD COLUMN packing TEXT"),
        ("invoice_number", "ADD COLUMN invoice_number TEXT"),
    ]:
        if col not in _table_columns(conn, "production_runs"):
            conn.execute(f"ALTER TABLE production_runs {ddl}")

    # invoices table
    if "invoices" not in [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()]:
        conn.execute("""
            CREATE TABLE invoices (
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
    if "production_run_links" not in [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()]:
        conn.execute("""
            CREATE TABLE production_run_links (
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

    # Global name uniqueness gives way to one master per company x product.
    conn.execute("ALTER TABLE recipes DROP CONSTRAINT IF EXISTS recipes_name_key")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_company_name "
                 "ON recipes (company_id, name)")
    from .companies import backfill_company_links  # local: avoids circular import
    backfill_company_links(conn)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (_SCHEMA_SIG_KEY, _schema_signature_live(conn)),
    )
    conn.commit()


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
