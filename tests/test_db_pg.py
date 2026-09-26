"""Phase-1 gate: PostgreSQL schema, defaults, migrations, and seeds on live PG.

Requires the local portable cluster (pg_dev\\start-pg.bat) with a
raas_test database. Skipped automatically when the server is unreachable,
so CI without PG still collects cleanly (Phase 3 wires PG into CI).
"""
import os
import re

import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import db as dbmod


TEST_DSN = os.getenv("TEST_DATABASE_URL", dbmod.DEFAULT_TEST_DATABASE_URL)

EXPECTED_TABLES = {
    "api_key_rate_limits", "api_keys", "app_settings", "approval_workflow",
    "audit_logs", "chemicals", "login_attempts", "notification_reads",
    "notifications", "reason_codes", "recipe_items", "recipes",
    "reconciliation_periods", "sale_items", "sale_payments", "sales",
    "sales_stage_history", "sessions", "unit_conversions", "upload_rows",
    "uploads", "users",
}

DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _server_up() -> bool:
    # Generous timeout: cloud projects (Supabase free tier) can take
    # several seconds to wake from idle on first contact.
    try:
        conn = psycopg.connect(TEST_DSN, connect_timeout=30)
        conn.close()
        return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(not _server_up(), reason="local PG not running (pg_dev/start-pg.bat)")


@pytest.fixture()
def clean_pg():
    """Wipe all public tables so each test builds schema from scratch."""
    admin = psycopg.connect(TEST_DSN, autocommit=True)
    tables = [r[0] for r in admin.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    for table in tables:
        admin.execute(f'DROP TABLE "{table}" CASCADE')
    admin.close()
    yield


@requires_pg
def test_schema_tables_created(clean_pg):
    conn = dbmod.get_connection(TEST_DSN)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        assert EXPECTED_TABLES <= tables
    finally:
        conn.close()


@requires_pg
def test_column_defaults_format(clean_pg):
    conn = dbmod.get_connection(TEST_DSN)
    try:
        conn.execute("INSERT INTO chemicals (name) VALUES ('T-Chem')")
        row = conn.execute(
            "SELECT current_qty, balance_last_month, unit, reorder_level "
            "FROM chemicals WHERE name = 'T-Chem'").fetchone()
        assert row == (0.0, 0.0, "KG", 0.0)
        conn.execute("INSERT INTO uploads (filename) VALUES ('f.pdf')")
        upload_date = conn.execute(
            "SELECT upload_date FROM uploads WHERE filename = 'f.pdf'").fetchone()[0]
        assert DATETIME_RE.match(upload_date), upload_date
        conn.execute("INSERT INTO recipes (name) VALUES ('R')")
        created = conn.execute(
            "SELECT created_date FROM recipes WHERE name = 'R'").fetchone()[0]
        assert DATE_RE.match(created), created
        conn.commit()
    finally:
        conn.close()


@requires_pg
def test_seeds_idempotent(clean_pg):
    dbmod.get_connection(TEST_DSN).close()
    conn = dbmod.get_connection(TEST_DSN)
    try:
        assert conn.execute("SELECT COUNT(*) FROM reason_codes").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM unit_conversions").fetchone()[0] == 12
    finally:
        conn.close()


@requires_pg
def test_migration_readds_dropped_column(clean_pg):
    conn = dbmod.get_connection(TEST_DSN)
    conn.execute("ALTER TABLE chemicals DROP COLUMN reorder_level")
    conn.commit()
    conn.close()
    conn2 = dbmod.get_connection(TEST_DSN)
    try:
        cols = {r[0] for r in conn2.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'chemicals'")}
        assert "reorder_level" in cols
    finally:
        conn2.close()


@requires_pg
def test_identity_sequence_starts_at_one(clean_pg):
    conn = dbmod.get_connection(TEST_DSN)
    try:
        new_id = conn.execute(
            "INSERT INTO chemicals (name) VALUES ('A') RETURNING id").fetchone()[0]
        assert new_id == 1
        conn.commit()
    finally:
        conn.close()


@requires_pg
def test_schema_signature_recorded(clean_pg):
    conn = dbmod.get_connection(TEST_DSN)
    try:
        val = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'raas_schema_sig'").fetchone()[0]
        assert val == dbmod._schema_signature_live(conn)
    finally:
        conn.close()


@requires_pg
def test_chemical_name_unique_index(clean_pg):
    conn = dbmod.get_connection(TEST_DSN)
    try:
        idx = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            "AND indexname = 'idx_chemicals_name_lower'").fetchone()
        assert idx is not None
        conn.execute("INSERT INTO chemicals (name) VALUES ('Acid')")
        with pytest.raises(psycopg.IntegrityError):
            conn.execute("INSERT INTO chemicals (name) VALUES ('ACID')")
        conn.rollback()
    finally:
        conn.close()


@requires_pg
def test_schema_fast_path_skips_ddl(clean_pg, monkeypatch):
    dbmod.get_connection(TEST_DSN).close()

    def _boom(conn):
        raise AssertionError("full DDL must not run on a current database")

    monkeypatch.setattr(dbmod, "_create_tables", _boom)
    conn = dbmod.get_connection(TEST_DSN)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0] == 0
    finally:
        conn.close()


def test_resolve_dsn_prefers_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-host/raas")
    assert dbmod.resolve_dsn() == "postgresql://env-host/raas"
    assert dbmod.resolve_dsn("postgresql://arg/db") == "postgresql://arg/db"


def test_resolve_dsn_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert dbmod.resolve_dsn() == dbmod.DEFAULT_DATABASE_URL
