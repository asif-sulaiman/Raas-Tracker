"""Schema v8: a recipe belongs to one invoice, with one slot per product."""

import psycopg
import pytest

from raas_tracker import db as dbmod


def _columns(conn, table):
    return {
        row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,)).fetchall()
    }


def _indexes(conn):
    return {
        row[0] for row in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ).fetchall()
    }


def test_schema_version_at_least_ten():
    """Floor, not equality pin: the v8 DDL must stay behind a version the fast
    path re-runs, and later migrations raising it further is not a regression."""
    assert dbmod._SCHEMA_VERSION >= 10


def test_v9_recipe_shape_is_invoice_scoped(db):
    assert _columns(db, "recipes") == {
        "id", "name", "created_date", "company_id", "invoice_id"}
    assert _columns(db, "recipe_products") == {
        "id", "recipe_id", "product_name", "unit", "water_percentage"}
    assert _columns(db, "recipe_items") == {
        "id", "recipe_product_id", "chemical_id", "percentage",
        # v14: an ingredient line may now draw from an outward purchase instead
        # of the in-house shelf. `test_recipe_material_sources.py` pins what
        # those two columns mean; this pin says they are the only additions, so
        # the v8 invoice-scoped shape itself never drifted.
        "source_type", "outward_purchase_line_id"}
    assert _columns(db, "production_run_outputs") == {
        "id", "run_id", "recipe_product_id", "product_name", "unit",
        "qty_produced"}

    indexes = _indexes(db)
    assert "idx_recipes_invoice_id" in indexes
    assert "idx_recipe_products_recipe_product" in indexes
    assert "idx_recipe_items_product_chemical" in indexes
    assert "idx_run_outputs_run_product" in indexes
    assert "idx_run_outputs_product" in indexes


def test_recipe_invoice_is_not_null_and_one_per_invoice(db):
    company = db.execute(
        "INSERT INTO companies (name) VALUES ('Shape Co') RETURNING id"
    ).fetchone()[0]
    sale = db.execute(
        "INSERT INTO sales (stage, company_id) VALUES ('pi_issued', %s) "
        "RETURNING id", (company,)).fetchone()[0]
    invoice = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status) "
        "VALUES (%s, 'INV-SHAPE', 'planned') RETURNING id",
        (sale,)).fetchone()[0]
    db.commit()

    with pytest.raises(psycopg.errors.NotNullViolation):
        db.execute("INSERT INTO recipes (name) VALUES ('No Invoice')")
    db.rollback()

    db.execute(
        "INSERT INTO recipes (name, company_id, invoice_id) "
        "VALUES ('One', %s, %s)", (company, invoice))
    db.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "INSERT INTO recipes (name, company_id, invoice_id) "
            "VALUES ('Two', %s, %s)", (company, invoice))
    db.rollback()


def test_recipe_product_slot_names_are_unique_ignoring_case(db):
    company = db.execute(
        "INSERT INTO companies (name) VALUES ('Slot Co') RETURNING id"
    ).fetchone()[0]
    sale = db.execute(
        "INSERT INTO sales (stage, company_id) VALUES ('pi_issued', %s) "
        "RETURNING id", (company,)).fetchone()[0]
    invoice = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status) "
        "VALUES (%s, 'INV-SLOT', 'planned') RETURNING id",
        (sale,)).fetchone()[0]
    recipe = db.execute(
        "INSERT INTO recipes (name, company_id, invoice_id) "
        "VALUES ('Slot Recipe', %s, %s) RETURNING id",
        (company, invoice)).fetchone()[0]
    db.execute(
        "INSERT INTO recipe_products (recipe_id, product_name) "
        "VALUES (%s, 'KNIT 100')", (recipe,))
    db.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "INSERT INTO recipe_products (recipe_id, product_name) "
            "VALUES (%s, 'knit 100')", (recipe,))
    db.rollback()


def test_migration_is_idempotent(pg_dsn):
    conn = dbmod.get_connection(pg_dsn)
    try:
        dbmod._create_tables(conn)
        dbmod._create_tables(conn)
        assert _columns(conn, "recipes") == {
            "id", "name", "created_date", "company_id", "invoice_id"}
        # The stored version follows the code constant (bumped again for the
        # PERF-3 indexes); a hardcoded literal here would fail every bump.
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (dbmod._SCHEMA_VERSION_KEY,)).fetchone()[0] == str(dbmod._SCHEMA_VERSION)
    finally:
        conn.rollback()
        conn.close()


def test_v8_refuses_to_rewrite_a_populated_old_shape(pg_dsn):
    """The demo cutover must run first; the migration says so and stops.

    Simulates the live v7 database: the old ``recipes.product_name`` column is
    back (the real one is gone), with a recipe row that has no invoice-scoped
    meaning. Nothing may be dropped in that state.
    """
    db = dbmod.get_connection(pg_dsn)
    raw = None
    try:
        db.execute("TRUNCATE recipes CASCADE")
        db.execute("ALTER TABLE recipes ADD COLUMN product_name TEXT")
        company = db.execute(
            "INSERT INTO companies (name) VALUES ('Old Shape Co') RETURNING id"
        ).fetchone()[0]
        sale = db.execute(
            "INSERT INTO sales (stage, company_id) VALUES ('pi_issued', %s) "
            "RETURNING id", (company,)).fetchone()[0]
        invoice = db.execute(
            "INSERT INTO invoices (sale_id, invoice_number, status) "
            "VALUES (%s, 'INV-OLD', 'planned') RETURNING id",
            (sale,)).fetchone()[0]
        db.execute(
            "INSERT INTO recipes (name, company_id, invoice_id, product_name) "
            "VALUES ('Demo', %s, %s, 'Legacy Product')",
            (company, invoice))
        db.commit()

        raw = psycopg.connect(pg_dsn)
        with pytest.raises(RuntimeError, match="reset_demo_recipes"):
            dbmod._create_tables(raw)
        # The failed pass rolled itself back and dropped nothing.
        assert "product_name" in _columns(db, "recipes")
        assert db.execute(
            "SELECT COUNT(*) FROM recipes").fetchone()[0] == 1
    finally:
        if raw is not None:
            raw.close()
        db.rollback()
        db.execute("TRUNCATE recipes CASCADE")
        db.execute("ALTER TABLE recipes DROP COLUMN IF EXISTS product_name")
        db.commit()
        db.close()
        dbmod._create_tables(dbmod.get_connection(pg_dsn))
