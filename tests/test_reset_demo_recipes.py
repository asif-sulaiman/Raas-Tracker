"""One-off cutover: deleting the demo recipes and runs before schema v8.

These are v7-shaped tests on purpose: the script runs against the live v7
database *before* the v8 migration, and its whole value is the preview it
prints for that one cutover. Phase 1 replaces this module's schema, at which
point the deletion path is dead code and only the version refusal remains.
"""

import json
from pathlib import Path

import pytest

from raas_tracker.demo_reset import (
    DEMO_SCHEMA_VERSION,
    DemoResetRefused,
    reset_demo_recipes,
)


def _seed(conn, *, statuses=("produced",), deducted=4.0, chemical_qty=100.0):
    """Insert one demo company/recipe/run/invoice set. Returns ids."""
    company = conn.execute(
        "INSERT INTO companies (name) VALUES ('Demo Co') RETURNING id"
    ).fetchone()[0]
    chemical = conn.execute(
        "INSERT INTO chemicals (name, current_qty, unit, last_updated) "
        "VALUES ('Acetone', %s, 'KG', '2026-01-01') RETURNING id",
        (chemical_qty,)).fetchone()[0]
    sale = conn.execute(
        "INSERT INTO sales (stage, company_id, shipment_status, pi_number, client_name) "
        "VALUES ('shipment_ongoing', %s, 'production_running', 'PI-1', 'Client') "
        "RETURNING id",
        (company,)).fetchone()[0]
    recipe = conn.execute(
        "INSERT INTO recipes (name, company_id, product_name, total_quantity) "
        "VALUES ('Demo Recipe', %s, 'KNIT 100', 100) RETURNING id",
        (company,)).fetchone()[0]
    conn.execute(
        "INSERT INTO recipe_items (recipe_id, chemical_id, percentage, required_qty_per_unit) "
        "VALUES (%s, %s, 40, 0.4)",
        (recipe, chemical))
    invoices = []
    for i, status in enumerate(statuses):
        invoices.append(conn.execute(
            "INSERT INTO invoices (sale_id, invoice_number, status) "
            "VALUES (%s, %s, %s) RETURNING id",
            (sale, f"INV-{i + 1}", status)).fetchone()[0])
    run = conn.execute(
        "INSERT INTO production_runs (recipe_id, qty_produced, production_date) "
        "VALUES (%s, 25, '2026-01-02') RETURNING id",
        (recipe,)).fetchone()[0]
    conn.execute(
        "INSERT INTO production_run_items "
        "(run_id, chemical_id, chemical_name, required_qty, deducted_qty, unit) "
        "VALUES (%s, %s, 'Acetone', %s, %s, 'KG')",
        (run, chemical, deducted, deducted))
    for invoice in invoices:
        conn.execute(
            "INSERT INTO production_run_links (run_id, sale_id, invoice_id) "
            "VALUES (%s, %s, %s)",
            (run, sale, invoice))
    conn.commit()
    return {"company": company, "chemical": chemical, "sale": sale,
            "recipe": recipe, "run": run, "invoices": invoices}


def _counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("recipes", "recipe_items", "production_runs",
                      "production_run_items", "production_run_links")
    }


def _chemical_qty(conn, chemical_id):
    return conn.execute(
        "SELECT current_qty FROM chemicals WHERE id = %s", (chemical_id,)
    ).fetchone()[0]


def test_dry_run_previews_without_writing(db):
    seed = _seed(db, deducted=4.0)
    before = _counts(db)

    report = reset_demo_recipes(db, apply=False)

    assert report["apply"] is False
    assert report["schema_version"] == DEMO_SCHEMA_VERSION
    assert report["counts"] == before
    assert report["report_path"] is None
    assert [row["quantity"] for row in report["stock_delta"]] == [4.0]
    assert report["stock_delta"][0]["chemical_name"] == "Acetone"
    assert _counts(db) == before
    assert _chemical_qty(db, seed["chemical"]) == 100.0
    assert db.execute(
        "SELECT status FROM invoices WHERE id = %s", (seed["invoices"][0],)
    ).fetchone()[0] == "produced"
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'DEMO_DATA_RESET'"
    ).fetchone()[0] == 0


def test_apply_refuses_without_an_existing_backup(db):
    seed = _seed(db)
    before = _counts(db)

    with pytest.raises(DemoResetRefused, match="backup"):
        reset_demo_recipes(db, apply=True)

    assert _counts(db) == before
    assert _chemical_qty(db, seed["chemical"]) == 100.0
    db.rollback()


def test_restore_stock_requires_apply(db):
    _seed(db)
    with pytest.raises(DemoResetRefused, match="restor"):
        reset_demo_recipes(db, apply=False, restore_stock=True)
    db.rollback()


def test_apply_deletes_in_fk_order_and_restores_derived_state(db, tmp_path):
    seed = _seed(db, statuses=("produced", "produced"), deducted=4.0)
    backup = tmp_path / "dump.sql"
    backup.write_text("-- pg_dump\n")

    report = reset_demo_recipes(
        db, apply=True, backup_file=str(backup), report_dir=str(tmp_path))

    assert report["apply"] is True
    assert report["counts"]["recipes"] == 1
    assert report["counts"]["production_run_links"] == 2
    assert _counts(db) == {
        "recipes": 0, "recipe_items": 0, "production_runs": 0,
        "production_run_items": 0, "production_run_links": 0}
    assert db.execute(
        "SELECT status FROM invoices WHERE sale_id = %s", (seed["sale"],)
    ).fetchall() == [("planned",), ("planned",)]
    assert db.execute(
        "SELECT shipment_status, stage FROM sales WHERE id = %s", (seed["sale"],)
    ).fetchone() == ("production_running", "shipment_ongoing")
    # Stock is untouched unless explicitly restored.
    assert _chemical_qty(db, seed["chemical"]) == 100.0
    # One run snapshot and one recipe row are exported before deletion.
    assert len(report["deleted"]["production_runs"]) == 1
    assert report["deleted"]["recipes"][0]["name"] == "Demo Recipe"
    assert report["invoices_reset"] == sorted(seed["invoices"])
    assert report["sales_updated"] == [
        {"sale_id": seed["sale"], "shipment_status": "production_running"}]
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'DEMO_DATA_RESET'"
    ).fetchone()[0] == 1

    path = Path(report["report_path"])
    assert path.parent == tmp_path
    assert path.name.startswith("reset_report_") and path.suffix == ".json"
    saved = json.loads(path.read_text())
    assert saved["counts"] == report["counts"]
    assert saved["deleted"]["recipe_items"][0]["percentage"] == 40
    assert saved["backup_file"] == str(backup)
    assert saved["stock_delta"][0]["quantity"] == 4.0


def test_apply_leaves_a_booked_invoice_booked_and_marks_ship_booked(db, tmp_path):
    seed = _seed(db, statuses=("booked", "produced"))
    backup = tmp_path / "dump.sql"
    backup.write_text("-- pg_dump\n")

    report = reset_demo_recipes(
        db, apply=True, backup_file=str(backup), report_dir=str(tmp_path))

    assert db.execute(
        "SELECT status FROM invoices ORDER BY id").fetchall() == [
            ("booked",), ("planned",)]
    assert db.execute(
        "SELECT shipment_status FROM sales WHERE id = %s", (seed["sale"],)
    ).fetchone()[0] == "ship_booked"
    assert report["sales_updated"] == [
        {"sale_id": seed["sale"], "shipment_status": "ship_booked"}]
    # Only the produced invoice is reported as reset.
    assert report["invoices_reset"] == [seed["invoices"][1]]


def test_restore_stock_adds_the_deducted_quantity_and_audits(db, tmp_path):
    seed = _seed(db, deducted=4.0, chemical_qty=100.0)
    backup = tmp_path / "dump.sql"
    backup.write_text("-- pg_dump\n")

    report = reset_demo_recipes(
        db, apply=True, restore_stock=True,
        backup_file=str(backup), report_dir=str(tmp_path))

    assert _chemical_qty(db, seed["chemical"]) == 104.0
    assert report["stock_restored"] == [{
        "chemical_id": seed["chemical"], "chemical_name": "Acetone",
        "unit": "KG", "quantity": 4.0, "restored": True}]
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'ADJUST_STOCK'"
        " AND entity_id = %s", (seed["chemical"],)).fetchone()[0] == 1


def test_stock_delta_covers_run_items_without_a_chemical_id(db, tmp_path):
    seed = _seed(db)
    # The historical bug: list_recipe_items_by_id never selected chemical_id,
    # so existing snapshots carry NULL. The restore must still find the row by
    # its snapshot name instead of silently dropping the chemical.
    db.execute("UPDATE production_run_items SET chemical_id = NULL")
    db.commit()
    backup = tmp_path / "dump.sql"
    backup.write_text("-- pg_dump\n")

    report = reset_demo_recipes(
        db, apply=True, restore_stock=True,
        backup_file=str(backup), report_dir=str(tmp_path))

    assert report["stock_delta"][0]["chemical_name"] == "Acetone"
    assert report["stock_delta"][0]["quantity"] == 4.0
    assert _chemical_qty(db, seed["chemical"]) == 104.0


def test_refuses_a_database_that_is_not_v7(db):
    seed = _seed(db)
    before = _counts(db)
    db.execute(
        "UPDATE app_settings SET value = %s WHERE key = 'raas_schema_version'",
        (str(DEMO_SCHEMA_VERSION + 1),))
    db.commit()
    try:
        with pytest.raises(DemoResetRefused, match="schema"):
            reset_demo_recipes(db, apply=True,
                               backup_file=__file__, report_dir="/tmp")
        assert _counts(db) == before
        assert _chemical_qty(db, seed["chemical"]) == 100.0
    finally:
        db.rollback()
        db.execute(
            "UPDATE app_settings SET value = %s WHERE key = 'raas_schema_version'",
            (str(DEMO_SCHEMA_VERSION),))
        db.commit()


def test_script_refuses_apply_without_a_real_backup_file(db, tmp_path):
    import importlib.util

    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "reset_demo_recipes_script", project / "scripts" / "reset_demo_recipes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seed = _seed(db)
    before = _counts(db)

    code = module.main(["--yes", "--backup-file", str(tmp_path / "missing.dump")])

    assert code == 2
    assert _counts(db) == before
    assert _chemical_qty(db, seed["chemical"]) == 100.0
