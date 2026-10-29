"""Revising a recipe after creation: rename, product rebind, metadata.

A recipe created by mistake must be fixable. Two invariants keep that safe:

* runs and stock history link by ``recipe_id``, never by name, so a rename
  moves the recipe without rewriting the past;
* ``recipes.product_name`` is what readiness and the commercial report key
  off, so the product may only be rebound before any production run exists.

Names are compared case-insensitively (the DB index is case-sensitive). The
service resolves the row once and updates by id, so a rename and a metadata
change in the same request cannot disagree about which row they mean.
"""
import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import recipes as recipesmod  # noqa: E402
from raas_tracker import sales as salesmod  # noqa: E402
from raas_tracker._exceptions import (  # noqa: E402
    RecipeNameConflict,
    RecipeProductLocked,
)

PRODUCT = "Caustic Soda 50%"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _company(conn, name="Revise Co"):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi="PI-REV-1", product=PRODUCT):
    return salesmod.add_sale(
        conn,
        {"pi_number": pi, "client_name": "Buyer", "company_id": company_id},
        [{"product_name": product, "quantity": 10, "unit_price": 5}],
    )


def _invoice(conn, sale_id, number="INV-REV-1"):
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in conn.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    return salesmod.create_invoice(conn, sale_id, number, lines=lines,
                                   invoice_date="2026-01-15")["invoice_id"]


def _recipe(conn, company_id, name, product=PRODUCT, total=100, water=0):
    assert recipesmod.add_recipe(conn, name, total_quantity=total,
                                 water_percentage=water, company_id=company_id,
                                 product_name=product)
    return recipesmod.get_recipe_by_name(conn, company_id, name)


def _chemical(conn, name="Caustic Flake"):
    row = conn.execute(
        "SELECT id FROM chemicals WHERE name = %s", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO chemicals (name, current_qty, unit) VALUES (%s, 500, 'KG')"
        " RETURNING id", (name,)).fetchone()[0]


def _run(conn, recipe_id, sale_id):
    item_id = conn.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchone()[0]
    run_id = conn.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, qty_produced)"
        " VALUES (%s, %s, 1) RETURNING id",
        (recipe_id, item_id)).fetchone()[0]
    conn.execute(
        "INSERT INTO production_run_links (run_id, sale_id) VALUES (%s, %s)",
        (run_id, sale_id))
    conn.commit()
    return run_id


# --------------------------------------------------------------------------- #
# rename
# --------------------------------------------------------------------------- #
def test_rename_moves_the_recipe_and_its_items(db):
    co = _company(db)
    _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Batch A", total=250, water=10)
    _chemical(db)
    assert recipesmod.add_recipe_item(db, co, recipe["name"], "Caustic Flake", 40)

    assert recipesmod.update_recipe(db, co, recipe["name"],
                                    new_name="  PI-REV-1 - Final  ") is True

    assert recipesmod.get_recipe_by_name(db, co, recipe["name"]) is None
    renamed = recipesmod.get_recipe_by_name(db, co, "PI-REV-1 - Final")
    assert renamed["id"] == recipe["id"]
    assert renamed["name"] == "PI-REV-1 - Final"

    items = recipesmod.list_recipe_items(db, co, "PI-REV-1 - Final")
    assert [(i["chemical_name"], i["percentage"]) for i in items] == [
        ("Caustic Flake", 40)]


def test_case_only_rename_keeps_the_items(db):
    """The old items query matched ``r.name = %s`` exactly; a case-only rename
    passed the lower() lookup and then returned zero ingredients."""
    co = _company(db)
    _sale(db, co)
    recipe = _recipe(db, co, "Batch A")
    _chemical(db)
    assert recipesmod.add_recipe_item(db, co, recipe["name"], "Caustic Flake", 40)

    assert recipesmod.update_recipe(db, co, recipe["name"],
                                    new_name="BATCH A") is True
    stored = recipesmod.get_recipe_by_name(db, co, "bAtCh A")
    assert stored["name"] == "BATCH A"
    assert len(recipesmod.list_recipe_items(db, co, "batch a")) == 1


def test_rename_to_an_existing_name_conflicts(db):
    co = _company(db)
    _sale(db, co)
    first = _recipe(db, co, "PI-REV-1 - One")
    _recipe(db, co, "PI-REV-1 - Two")

    with pytest.raises(RecipeNameConflict):
        recipesmod.update_recipe(db, co, first["name"],
                                 new_name="pi-rev-1 - two")
    # Nothing moved.
    assert recipesmod.get_recipe_by_name(db, co, "PI-REV-1 - One")["id"] == first["id"]
    assert recipesmod.get_recipe_by_name(db, co, "PI-REV-1 - Two")["id"] != first["id"]


def test_rename_after_production_keeps_the_run_readable(db):
    co = _company(db)
    sale = _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Produced")
    run_id = _run(db, recipe["id"], sale)

    assert recipesmod.update_recipe(db, co, recipe["name"],
                                    new_name="PI-REV-1 - Renamed") is True
    run = db.execute(
        "SELECT recipe_id FROM production_runs WHERE id = %s",
        (run_id,)).fetchone()
    assert run[0] == recipe["id"]
    assert recipesmod.list_recipe_items(db, co, "PI-REV-1 - Renamed") == []


def test_rename_audit_records_both_names(db):
    co = _company(db)
    _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Audit")

    recipesmod.update_recipe(db, co, recipe["name"],
                             new_name="PI-REV-1 - Audited")
    row = db.execute(
        "SELECT old_value, new_value FROM audit_logs"
        " WHERE entity_type = 'recipe' AND entity_id = %s"
        "   AND action = 'RECIPE_UPDATE' ORDER BY id DESC LIMIT 1",
        (recipe["id"],)).fetchone()
    assert row[0] == "PI-REV-1 - Audit"
    assert "PI-REV-1 - Audited" in row[1]


# --------------------------------------------------------------------------- #
# product rebind
# --------------------------------------------------------------------------- #
def test_product_rebind_without_runs(db):
    co = _company(db)
    _sale(db, co)
    second = "Soda Ash"
    salesmod.add_sale(
        db, {"pi_number": "PI-REV-2", "client_name": "Buyer",
             "company_id": co},
        [{"product_name": second, "quantity": 5, "unit_price": 1}])
    recipe = _recipe(db, co, "PI-REV-1 - Rebind")

    assert recipesmod.update_recipe(db, co, recipe["name"],
                                    product_name=" soda ash ") is True
    assert recipesmod.get_recipe_by_name(
        db, co, recipe["name"])["product_name"] == "soda ash"


def test_product_seen_only_on_an_invoice_is_registered(db):
    """Invoice lines are snapshots: a renamed PI line leaves the old product
    name on the invoice, and create/rebind must accept it."""
    co = _company(db)
    sale = _sale(db, co, product="Snapshot Name")
    _invoice(db, sale)
    db.execute("UPDATE sale_items SET product_name = %s WHERE sale_id = %s",
               ("Renamed On The PI", sale))
    db.commit()

    assert recipesmod.add_recipe(db, "PI-REV-1 - Snapshot", 10, 0, co,
                                 "Snapshot Name") is True
    recipe = recipesmod.get_recipe_by_name(db, co, "PI-REV-1 - Snapshot")
    assert recipe["product_name"] == "Snapshot Name"

    assert recipesmod.update_recipe(db, co, recipe["name"],
                                    product_name="snapshot name") is True


def test_product_rebind_is_locked_after_production(db):
    co = _company(db)
    sale = _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Locked")
    _run(db, recipe["id"], sale)

    with pytest.raises(RecipeProductLocked):
        recipesmod.update_recipe(db, co, recipe["name"],
                                 product_name="Soda Ash")
    assert recipesmod.get_recipe_by_name(
        db, co, recipe["name"])["product_name"] == PRODUCT


def test_unregistered_product_is_a_plain_validation_error(db):
    co = _company(db)
    _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Unregistered")

    with pytest.raises(ValueError) as exc:
        recipesmod.update_recipe(db, co, recipe["name"],
                                 product_name="Not On Any PI")
    assert not isinstance(exc.value, (RecipeNameConflict, RecipeProductLocked))


# --------------------------------------------------------------------------- #
# metadata + audit
# --------------------------------------------------------------------------- #
def test_omitted_fields_are_unchanged(db):
    co = _company(db)
    _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Meta", total=321, water=7)

    assert recipesmod.update_recipe(db, co, recipe["name"],
                                    new_name="PI-REV-1 - Meta 2") is True
    got = recipesmod.get_recipe_by_name(db, co, "PI-REV-1 - Meta 2")
    assert float(got["total_quantity"]) == 321
    assert float(got["water_percentage"]) == 7


def test_metadata_is_still_finite_checked(db):
    co = _company(db)
    _sale(db, co)
    recipe = _recipe(db, co, "PI-REV-1 - Finite")

    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            recipesmod.update_recipe(db, co, recipe["name"],
                                     total_quantity=bad)
        db.rollback()


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
def _create_recipe_row(conn, company_id, name, product=PRODUCT):
    return _recipe(conn, company_id, name, product=product)


def test_route_put_renames_and_reports_the_name(admin_client, db):
    co = _company(db)
    _sale(db, co)
    _create_recipe_row(db, co, "PI-REV-1 - Route")

    r = admin_client.put("/api/recipes/PI-REV-1 - Route", json={
        "company_id": co, "new_name": "PI-REV-1 - Route Renamed",
        "total_quantity": 88})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"success": True, "name": "PI-REV-1 - Route Renamed"}

    assert admin_client.get(
        f"/api/recipes/PI-REV-1 - Route?company_id={co}").status_code == 404
    detail = admin_client.get(
        f"/api/recipes/PI-REV-1 - Route Renamed?company_id={co}")
    assert detail.status_code == 200
    assert detail.get_json()["recipe"]["name"] == "PI-REV-1 - Route Renamed"
    assert float(detail.get_json()["recipe"]["total_quantity"]) == 88


def test_route_put_rename_conflict_is_409(admin_client, db):
    co = _company(db)
    _sale(db, co)
    _create_recipe_row(db, co, "PI-REV-1 - A")
    _create_recipe_row(db, co, "PI-REV-1 - B")

    r = admin_client.put("/api/recipes/PI-REV-1 - A", json={
        "company_id": co, "new_name": "pi-rev-1 - b"})
    assert r.status_code == 409, r.get_json()
    assert "already exists" in r.get_json()["error"]


def test_route_put_unregistered_product_is_400(admin_client, db):
    co = _company(db)
    _sale(db, co)
    _create_recipe_row(db, co, "PI-REV-1 - Bad Product")

    r = admin_client.put("/api/recipes/PI-REV-1 - Bad Product", json={
        "company_id": co, "product_name": "Ghost Product"})
    assert r.status_code == 400, r.get_json()


def test_route_put_product_after_run_is_409(admin_client, db):
    co = _company(db)
    sale = _sale(db, co)
    recipe = _create_recipe_row(db, co, "PI-REV-1 - Run Locked")
    _run(db, recipe["id"], sale)

    r = admin_client.put("/api/recipes/PI-REV-1 - Run Locked", json={
        "company_id": co, "product_name": "Soda Ash"})
    assert r.status_code == 409, r.get_json()


def test_route_detail_reports_run_count(admin_client, db):
    co = _company(db)
    sale = _sale(db, co)
    _create_recipe_row(db, co, "PI-REV-1 - No Runs")
    produced = _create_recipe_row(db, co, "PI-REV-1 - Has Runs")
    _run(db, produced["id"], sale)

    fresh = admin_client.get(
        f"/api/recipes/PI-REV-1 - No Runs?company_id={co}").get_json()
    assert fresh["recipe"]["run_count"] == 0

    used = admin_client.get(
        f"/api/recipes/PI-REV-1 - Has Runs?company_id={co}").get_json()
    assert used["recipe"]["run_count"] == 1
