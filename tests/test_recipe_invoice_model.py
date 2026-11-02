"""Invoice-scoped recipes: one recipe per invoice, one formulation per product.

These pin the Phase-2 service and HTTP contract, not the readiness/report
surface (Phase 3):

* create derives a formulation slot per distinct invoice product;
* one recipe per invoice (409) and a foreign invoice is 404;
* a late invoice line appears as a *ghost* slot on read, and its first
  formulation creates the row;
* percentages are capped per product, not per recipe;
* production takes per-product ``outputs``, converts each need into the
  chemical's stock unit and deducts **once** per chemical, even when two
  products share it;
* a client cannot steer a run at another invoice — links come from the
  recipe's own invoice.
"""
from urllib.parse import quote

import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import recipes as recipesmod  # noqa: E402
from raas_tracker import sales as salesmod  # noqa: E402
from raas_tracker import stock as stockmod  # noqa: E402
from raas_tracker._exceptions import (  # noqa: E402
    InvoiceNotFound,
    LifecycleConflict,
    RecipeHasRuns,
    RecipeInvoiceConflict,
    RecipeItemConflict,
    RecipeNameConflict,
    ResourceMissing,
)

PRODUCT_A = "Soap Base"
PRODUCT_B = "Liquid Cleaner"
CHEM = "Caustic Soda"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _company(conn, name="Invoice Recipe Co"):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi, items=None):
    items = items or [
        {"product_name": PRODUCT_A, "quantity": 100, "unit_price": 2},
        {"product_name": PRODUCT_B, "quantity": 50, "unit_price": 3},
    ]
    return salesmod.add_sale(
        conn,
        {"pi_number": pi, "client_name": "Buyer", "company_id": company_id},
        items,
    )


def _sale_items(conn, sale_id):
    return conn.execute(
        "SELECT id, product_name, unit, quantity FROM sale_items "
        "WHERE sale_id = %s ORDER BY id", (sale_id,)).fetchall()


def _invoice(conn, sale_id, number):
    lines = [{"sale_item_id": r[0], "quantity": float(r[3])}
             for r in _sale_items(conn, sale_id)]
    return salesmod.create_invoice(
        conn, sale_id, number, lines=lines, invoice_date="2026-01-15"
    )["invoice_id"]


def _scenario(conn, items=None, number="INV-RCP-1"):
    co = _company(conn)
    sale = _sale(conn, co, f"PI-{number}", items)
    inv = _invoice(conn, sale, number)
    return co, sale, inv


def _recipe(conn, company_id, invoice_id, name="Batch One"):
    return recipesmod.add_recipe(conn, name, company_id, invoice_id)


def _product(detail, name):
    return next(p for p in detail["products"] if p["product_name"] == name)


def _stock(conn, name=CHEM):
    return conn.execute(
        "SELECT current_qty FROM chemicals WHERE name = %s", (name,)
    ).fetchone()[0]


def _add_late_line(conn, sale_id, invoice_id, product, qty, unit="KG"):
    item_id = salesmod.add_sale_item(conn, sale_id, product, qty, 5, unit)
    salesmod.create_invoice_item(conn, invoice_id, item_id, qty)
    return item_id


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #
def test_add_recipe_creates_a_slot_for_every_invoice_product(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)

    assert recipe["invoice_id"] == inv
    assert recipe["invoice_number"] == "INV-RCP-1"
    assert recipe["company_id"] == co
    assert recipe["product_count"] == 2
    assert recipe["formulated_count"] == 0
    assert recipe["run_count"] == 0

    detail = recipesmod.get_recipe_detail(db, recipe["id"])
    by_name = {p["product_name"]: p for p in detail["products"]}
    assert set(by_name) == {PRODUCT_A, PRODUCT_B}
    assert all(p["id"] is not None for p in by_name.values())
    assert all(p["on_invoice"] for p in by_name.values())
    assert all(p["formulated"] is False for p in by_name.values())
    assert by_name[PRODUCT_A]["invoiced_qty"] == 100
    assert by_name[PRODUCT_B]["invoiced_qty"] == 50


def test_one_recipe_per_invoice_is_409(db):
    co, sale, inv = _scenario(db)
    _recipe(db, co, inv)
    with pytest.raises(RecipeInvoiceConflict):
        _recipe(db, co, inv, name="Second Batch")


def test_foreign_invoice_is_404(db):
    co_a = _company(db, "Company A")
    co_b = _company(db, "Company B")
    sale_b = _sale(db, co_b, "PI-FOREIGN")
    inv_b = _invoice(db, sale_b, "INV-FOREIGN")

    with pytest.raises(InvoiceNotFound):
        _recipe(db, co_a, inv_b)


def test_recipe_needs_planned_or_produced_invoice(db):
    co, sale, inv = _scenario(db)
    db.execute("UPDATE invoices SET status = 'booked' WHERE id = %s", (inv,))
    db.commit()
    with pytest.raises(LifecycleConflict):
        _recipe(db, co, inv)


def test_invoice_without_lines_is_400(db):
    co, sale, _inv = _scenario(db)
    empty = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status, amount, invoice_date) "
        "VALUES (%s, 'INV-EMPTY', 'planned', 0, '2026-01-15') RETURNING id",
        (sale,)).fetchone()[0]
    db.commit()
    with pytest.raises(ValueError):
        _recipe(db, co, empty)


# --------------------------------------------------------------------------- #
# duplicate / mixed-unit invoice lines
# --------------------------------------------------------------------------- #
def test_duplicate_lines_in_one_unit_merge(db):
    items = [
        {"product_name": PRODUCT_A, "quantity": 100, "unit_price": 2},
        {"product_name": PRODUCT_A, "quantity": 50, "unit_price": 2},
    ]
    co, sale, inv = _scenario(db, items)
    recipe = _recipe(db, co, inv)

    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                       PRODUCT_A)
    assert product["invoiced_qty"] == 150
    assert product["line_count"] == 2
    assert product["unconverted"] == []


def test_mixed_unit_lines_merge_through_the_conversion_table(db):
    items = [
        {"product_name": PRODUCT_A, "quantity": 100, "unit_price": 2,
         "unit": "KG"},
        {"product_name": PRODUCT_A, "quantity": 50, "unit_price": 2,
         "unit": "L"},
    ]
    co, sale, inv = _scenario(db, items)
    # The seed ships L -> KG = 1; replace it so the sum proves the conversion
    # table is what merged the lines, not a coincidence.
    db.execute("DELETE FROM unit_conversions WHERE from_unit = 'L' "
               "AND to_unit = 'KG'")
    db.commit()
    assert stockmod.add_unit_conversion(db, "L", "KG", 1.2)
    recipe = _recipe(db, co, inv)

    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                       PRODUCT_A)
    assert product["line_count"] == 2
    assert product["unconverted"] == []
    assert product["invoiced_qty"] == pytest.approx(160)


def test_missing_conversion_is_reported_and_production_refuses(db):
    items = [
        {"product_name": PRODUCT_A, "quantity": 100, "unit_price": 2,
         "unit": "KG"},
        {"product_name": PRODUCT_A, "quantity": 50, "unit_price": 2,
         "unit": "GAL"},
    ]
    co, sale, inv = _scenario(db, items)
    recipe = _recipe(db, co, inv)
    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                       PRODUCT_A)
    assert product["unconverted"] == [{"from_unit": "GAL", "to_unit": "KG"}]
    assert product["invoiced_qty"] == 100

    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 10)

    with pytest.raises(ValueError, match="Unit Conversions"):
        recipesmod.create_production_run(
            db, co, recipe["id"], [{"product_name": PRODUCT_A, "quantity": 10}])

    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 0
    assert _stock(db) == 500


# --------------------------------------------------------------------------- #
# late products
# --------------------------------------------------------------------------- #
def test_late_product_is_a_ghost_slot_without_any_write(db):
    """A line written outside the invoice service still shows in the plan.

    ``create_invoice_item`` syncs the slot in its own transaction, so the
    ghost path is the backstop for a row that arrived by another route (a
    legacy row, or a writer that bypasses the service). Reading the detail
    must not write: the slot stays virtual — ``id: None`` — until it is
    formulated.
    """
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    before = db.execute("SELECT COUNT(*) FROM recipe_products").fetchone()[0]

    item_id = salesmod.add_sale_item(db, sale, "Late Additive", 12, 5, "KG")
    db.execute(
        "INSERT INTO invoice_items (invoice_id, sale_item_id, product_name,"
        " unit, quantity, unit_price, line_total)"
        " VALUES (%s, %s, 'Late Additive', 'KG', 12, 5, 60)",
        (inv, item_id))
    db.commit()

    detail = recipesmod.get_recipe_detail(db, recipe["id"])
    ghost = _product(detail, "Late Additive")
    assert ghost["id"] is None
    assert ghost["on_invoice"] is True
    assert ghost["formulated"] is False
    assert ghost["invoiced_qty"] == 12
    assert ghost["remaining_qty"] == 12
    assert db.execute(
        "SELECT COUNT(*) FROM recipe_products").fetchone()[0] == before

    source = recipesmod.get_invoice_production_source(db, inv)
    assert [p["product_name"] for p in source["products"]] == sorted(
        [PRODUCT_A, PRODUCT_B, "Late Additive"])


def test_late_invoice_line_syncs_its_slot_in_the_same_transaction(db):
    """The service path materialises the slot with the line, not on a GET."""
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)

    _add_late_line(db, sale, inv, "Late Additive", 12)

    slot = db.execute(
        "SELECT id FROM recipe_products WHERE recipe_id = %s"
        " AND lower(product_name) = lower(%s)",
        (recipe["id"], "Late Additive")).fetchone()
    assert slot is not None
    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                       "Late Additive")
    assert product["id"] == slot[0]
    assert product["formulated"] is False
    assert product["invoiced_qty"] == 12


def test_formulating_a_ghost_creates_its_slot(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    _add_late_line(db, sale, inv, "Late Additive", 12)
    stockmod.add_chemical(db, CHEM, 500, "KG")

    assert recipesmod.add_recipe_item(
        db, recipe["id"], "Late Additive", CHEM, 5)

    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                       "Late Additive")
    assert product["id"] is not None
    assert product["formulated"] is True
    assert product["percentage"] == 5
    assert product["items"][0]["chemical_name"] == CHEM


def test_product_not_on_the_invoice_cannot_be_formulated(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    with pytest.raises(ValueError, match="not on invoice"):
        recipesmod.add_recipe_item(
            db, recipe["id"], "Something Else", CHEM, 5)


# --------------------------------------------------------------------------- #
# formulation rules
# --------------------------------------------------------------------------- #
def test_percentage_cap_is_per_product(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    stockmod.add_chemical(db, "Perfume", 100, "KG")

    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 60)
    # A different product has its own 100% budget.
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_B, CHEM, 60)
    with pytest.raises(ValueError, match="100%"):
        recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, "Perfume", 45)

    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, "Perfume", 40)
    assert _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                    PRODUCT_A)["percentage"] == 100


def test_same_chemical_twice_in_one_product_is_409(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 20)
    with pytest.raises(RecipeItemConflict):
        recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 10)


def test_update_and_delete_items_are_id_based(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 20)
    item = _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                    PRODUCT_A)["items"][0]

    assert recipesmod.update_recipe_item(db, recipe["id"], item["id"], 25)
    assert _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                    PRODUCT_A)["percentage"] == 25

    assert recipesmod.delete_recipe_item(db, recipe["id"], item["id"])
    assert _product(recipesmod.get_recipe_detail(db, recipe["id"]),
                    PRODUCT_A)["formulated"] is False


def test_foreign_item_id_is_not_found(db):
    co, sale, inv = _scenario(db)
    first = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, first["id"], PRODUCT_A, CHEM, 20)
    item = _product(recipesmod.get_recipe_detail(db, first["id"]),
                    PRODUCT_A)["items"][0]

    # A second recipe (different invoice) must not reach the first's item.
    sale2 = _sale(db, co, "PI-RCP-2")
    inv2 = _invoice(db, sale2, "INV-RCP-2")
    second = _recipe(db, co, inv2, name="Batch Two")
    assert recipesmod.update_recipe_item(
        db, second["id"], item["id"], 30) is False
    assert recipesmod.delete_recipe_item(
        db, second["id"], item["id"]) is False


# --------------------------------------------------------------------------- #
# rename / delete
# --------------------------------------------------------------------------- #
def test_rename_touches_only_the_header(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    assert recipesmod.update_recipe(db, recipe["id"], "Renamed Batch")
    renamed = recipesmod.get_recipe_by_id(db, recipe["id"])
    assert renamed["name"] == "Renamed Batch"
    assert renamed["invoice_id"] == inv

    _recipe(db, co, _invoice(db, _sale(db, co, "PI-RCP-OTHER"),
                             "INV-RCP-OTHER"), name="Taken Name")
    with pytest.raises(RecipeNameConflict):
        recipesmod.update_recipe(db, recipe["id"], "Taken Name")


def test_case_only_rename_keeps_the_formulation(db):
    """The old items query matched ``r.name = %s`` exactly; a case-only rename
    passed the lower() lookup and then returned zero ingredients."""
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv, name="Batch A")
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 40)

    assert recipesmod.update_recipe(db, recipe["id"], "BATCH A")
    detail = recipesmod.get_recipe_detail(db, recipe["id"])
    assert detail["recipe"]["name"] == "BATCH A"
    items = _product(detail, PRODUCT_A)["items"]
    assert [(i["chemical_name"], float(i["percentage"])) for i in items] == [
        (CHEM, 40.0)]


def test_rename_audit_records_both_names(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv, name="Audit Batch")

    recipesmod.update_recipe(db, recipe["id"], "Audited Batch")
    row = db.execute(
        "SELECT old_value, new_value FROM audit_logs"
        " WHERE entity_type = 'recipe' AND entity_id = %s"
        "   AND action = 'RECIPE_UPDATE' ORDER BY id DESC LIMIT 1",
        (recipe["id"],)).fetchone()
    assert row[0] == "Audit Batch"
    assert "Audited Batch" in row[1]


def test_delete_with_runs_is_409(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 10)
    recipesmod.create_production_run(
        db, co, recipe["id"], [{"product_name": PRODUCT_A, "quantity": 10}])

    with pytest.raises(RecipeHasRuns):
        recipesmod.delete_recipe(db, recipe["id"])
    assert recipesmod.get_recipe_by_id(db, recipe["id"]) is not None


def test_delete_without_runs_removes_slots_and_items(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 10)

    assert recipesmod.delete_recipe(db, recipe["id"])
    assert recipesmod.get_recipe_by_id(db, recipe["id"]) is None
    assert db.execute("SELECT COUNT(*) FROM recipe_products").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0] == 0


def test_require_recipe_raises_404_type(db):
    with pytest.raises(ResourceMissing):
        recipesmod.get_recipe_detail(db, 999999)


# --------------------------------------------------------------------------- #
# production
# --------------------------------------------------------------------------- #
def _formulated_two_product_recipe(db, chemical=CHEM, qty=500, unit="KG",
                                   pct_a=20, pct_b=10):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, chemical, qty, unit)
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, chemical, pct_a)
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_B, chemical, pct_b)
    return co, sale, inv, recipe


def test_outputs_validation(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    for bad in ([], [{"quantity": 5}],
                [{"product_name": PRODUCT_A, "quantity": 0}],
                [{"product_name": PRODUCT_A, "quantity": -1}],
                [{"product_name": PRODUCT_A, "quantity": float("nan")}],
                [{"product_name": PRODUCT_A, "quantity": 1},
                 {"product_name": PRODUCT_A, "quantity": 2}],
                [{"product_name": "Not Invoiced", "quantity": 1}]):
        with pytest.raises(ValueError):
            recipesmod.create_production_run(db, co, recipe["id"], bad)

    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 0


def test_produce_aggregates_one_deduction_per_chemical(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)

    result = recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 100},
        {"product_name": PRODUCT_B, "quantity": 50},
    ])

    # 20% of 100 + 10% of 50 = 25, deducted once.
    assert _stock(db) == pytest.approx(475)
    assert result["production_qty"] == pytest.approx(150)
    items = db.execute(
        "SELECT chemical_name, required_qty, deducted_qty FROM "
        "production_run_items WHERE run_id = %s", (result["run_id"],)).fetchall()
    assert len(items) == 1
    assert items[0][0] == CHEM
    assert items[0][1] == pytest.approx(25)
    assert items[0][2] == pytest.approx(25)

    run_qty = db.execute(
        "SELECT qty_produced FROM production_runs WHERE id = %s",
        (result["run_id"],)).fetchone()[0]
    assert run_qty == pytest.approx(150)


def test_run_snapshots_outputs_and_chemical_ids(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    result = recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 10},
    ])
    outputs = db.execute(
        "SELECT recipe_product_id, product_name, unit, qty_produced "
        "FROM production_run_outputs WHERE run_id = %s",
        (result["run_id"],)).fetchall()
    assert len(outputs) == 1
    assert outputs[0][1] == PRODUCT_A
    assert outputs[0][3] == pytest.approx(10)
    slot_id = db.execute(
        "SELECT id FROM recipe_products WHERE recipe_id = %s "
        "AND lower(product_name) = lower(%s)", (recipe["id"], PRODUCT_A)
    ).fetchone()[0]
    assert outputs[0][0] == slot_id

    chem_id = db.execute("SELECT id FROM chemicals WHERE name = %s",
                         (CHEM,)).fetchone()[0]
    run_items = db.execute(
        "SELECT chemical_id FROM production_run_items WHERE run_id = %s",
        (result["run_id"],)).fetchall()
    assert run_items == [(chem_id,)]

    link = db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links WHERE run_id = %s",
        (result["run_id"],)).fetchone()
    assert link == (sale, inv)


def test_produce_converts_from_product_unit_to_chemical_unit(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(
        db, chemical="Solvent", qty=100, unit="L", pct_a=10, pct_b=0)
    assert stockmod.add_unit_conversion(db, "KG", "L", 1.5)

    recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 10},
    ])
    # 10% of 10 KG = 1 KG -> 1.5 L.
    assert _stock(db, "Solvent") == pytest.approx(98.5)
    unit = db.execute(
        "SELECT unit, required_qty FROM production_run_items "
        "JOIN production_runs ON production_runs.id = production_run_items.run_id "
        "WHERE production_runs.recipe_id = %s", (recipe["id"],)).fetchone()
    assert unit[0] == "L"
    assert unit[1] == pytest.approx(1.5)


def test_missing_product_to_chemical_conversion_writes_nothing(db):
    # KG -> PCS is not seeded, so this is a genuine missing conversion.
    co, sale, inv, recipe = _formulated_two_product_recipe(
        db, chemical="Solvent", qty=100, unit="PCS", pct_a=10, pct_b=0)
    with pytest.raises(ValueError, match="Solvent"):
        recipesmod.create_production_run(db, co, recipe["id"], [
            {"product_name": PRODUCT_A, "quantity": 10},
        ])
    assert _stock(db, "Solvent") == 100
    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 0


def test_partial_runs_do_not_rededuct(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)

    first = recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 100},
    ])
    assert _stock(db) == pytest.approx(480)  # 20% of 100

    second = recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_B, "quantity": 50},
    ])
    assert _stock(db) == pytest.approx(475)  # + 10% of 50 only
    assert first["run_id"] != second["run_id"]


def test_produce_refuses_an_unformulated_product(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 10)

    with pytest.raises(ValueError, match="no formulation"):
        recipesmod.create_production_run(db, co, recipe["id"], [
            {"product_name": PRODUCT_B, "quantity": 5},
        ])
    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 0


def test_produce_marks_the_invoice_produced_and_the_sale_running(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 10},
    ])
    assert db.execute("SELECT status FROM invoices WHERE id = %s",
                      (inv,)).fetchone()[0] == "produced"
    assert db.execute("SELECT shipment_status FROM sales WHERE id = %s",
                      (sale,)).fetchone()[0] == "production_done"


def test_report_sums_each_product_share(db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    report = recipesmod.generate_report(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 10},
        {"product_name": PRODUCT_B, "quantity": 5},
    ])
    assert len(report) == 1
    assert report[0]["chemical_name"] == CHEM
    assert report[0]["required_total"] == pytest.approx(2.5)
    assert report[0]["current_stock"] == 500
    assert {p["product_name"] for p in report[0]["products"]} == {
        PRODUCT_A, PRODUCT_B}


# --------------------------------------------------------------------------- #
# HTTP contract
# --------------------------------------------------------------------------- #
def _api_recipe(admin_client, company_id, invoice_id, name="API Batch"):
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": company_id, "invoice_id": invoice_id})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["recipe"]["id"]


def test_api_create_and_detail(admin_client, db):
    co, sale, inv = _scenario(db)
    rid = _api_recipe(admin_client, co, inv)

    body = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()
    assert body["recipe"]["invoice_id"] == inv
    assert {p["product_name"] for p in body["products"]} == {
        PRODUCT_A, PRODUCT_B}
    assert all(p["id"] is not None for p in body["products"])


def test_api_rename_by_id_and_conflict(admin_client, db):
    co, sale, inv = _scenario(db)
    rid = _api_recipe(admin_client, co, inv, name="API Old")

    r = admin_client.put(f"/api/recipes/by-id/{rid}", json={"name": "API New"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"success": True, "name": "API New"}

    _api_recipe(admin_client, co,
                _invoice(db, _sale(db, co, "PI-API-TAKEN"), "INV-API-TAKEN"),
                name="Taken")
    r = admin_client.put(f"/api/recipes/by-id/{rid}", json={"name": "taken"})
    assert r.status_code == 409
    assert "already exists" in r.get_json()["error"]

    r = admin_client.put(f"/api/recipes/by-id/{rid}",
                         json={"name": "X", "product_name": PRODUCT_A})
    assert r.status_code == 400
    assert admin_client.put(
        "/api/recipes/by-id/999999", json={"name": "X"}).status_code == 404


def test_api_create_rejects_legacy_product_and_quantity_fields(admin_client, db):
    co, sale, inv = _scenario(db)
    for extra in ({"product_name": PRODUCT_A},
                  {"yield_qty": 100},
                  {"water_percentage": 10}):
        r = admin_client.post("/api/recipes", json={
            "name": "Legacy", "company_id": co, "invoice_id": inv, **extra})
        assert r.status_code == 400
        assert r.get_json()["error"] == "Invalid payload"


def test_api_duplicate_and_foreign_are_typed(admin_client, db):
    co, sale, inv = _scenario(db)
    _api_recipe(admin_client, co, inv)

    r = admin_client.post("/api/recipes", json={
        "name": "Again", "company_id": co, "invoice_id": inv})
    assert r.status_code == 409

    co_b = _company(db, "Other Co")
    sale_b = _sale(db, co_b, "PI-API-FOREIGN")
    inv_b = _invoice(db, sale_b, "INV-API-FOREIGN")
    r = admin_client.post("/api/recipes", json={
        "name": "Foreign", "company_id": co, "invoice_id": inv_b})
    assert r.status_code == 404


def test_api_formulate_by_product_path(admin_client, db):
    co, sale, inv = _scenario(db)
    rid = _api_recipe(admin_client, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")

    path = f"/api/recipes/by-id/{rid}/products/{quote(PRODUCT_A)}/items"
    r = admin_client.post(path, json={"chemical": CHEM, "percentage": 20})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post(path, json={"chemical": CHEM, "percentage": 20})
    assert r.status_code == 409

    body = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()
    product = next(p for p in body["products"]
                   if p["product_name"] == PRODUCT_A)
    assert product["formulated"] is True
    assert product["percentage"] == 20
    item_id = product["items"][0]["id"]

    r = admin_client.put(f"/api/recipes/by-id/{rid}/items/{item_id}",
                         json={"percentage": 30})
    assert r.status_code == 200
    r = admin_client.delete(f"/api/recipes/by-id/{rid}/items/{item_id}")
    assert r.status_code == 200


def test_api_set_and_clear_water(admin_client, db):
    co, sale, inv = _scenario(db)
    rid = _api_recipe(admin_client, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    items_path = f"/api/recipes/by-id/{rid}/products/{quote(PRODUCT_A)}/items"
    assert admin_client.post(
        items_path, json={"chemical": CHEM, "percentage": 40}).status_code == 200

    water_path = f"/api/recipes/by-id/{rid}/products/{quote(PRODUCT_A)}/water"
    assert admin_client.put(water_path, json={"percentage": 25}).status_code == 200

    detail = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()
    product = next(p for p in detail["products"]
                   if p["product_name"] == PRODUCT_A)
    assert product["water_percentage"] == 25
    assert product["water_effective"] == 25
    assert product["grand_total"] == 65

    assert admin_client.put(water_path, json={"percentage": None}).status_code == 200
    detail = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()
    product = next(p for p in detail["products"]
                   if p["product_name"] == PRODUCT_A)
    assert product["water_percentage"] is None
    assert product["water_effective"] == 60
    assert product["grand_total"] == 100

    assert admin_client.put(water_path, json={"percentage": 100.5}).status_code == 400
    assert admin_client.put(
        f"/api/recipes/by-id/999999/products/{quote(PRODUCT_A)}/water",
        json={"percentage": 10}).status_code == 404


def test_api_set_water_requires_authentication(client):
    r = client.put(
        f"/api/recipes/by-id/1/products/{quote(PRODUCT_A)}/water",
        json={"percentage": 10})
    assert r.status_code == 401


def test_api_produce_and_validation(admin_client, db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    rid = recipe["id"]

    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={})
    assert r.status_code == 400

    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "outputs": [{"product_name": PRODUCT_A, "quantity": 10}]})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["runs"][0]["run_id"]

    # extra="forbid": an old client cannot smuggle invoice ids in.
    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "outputs": [{"product_name": PRODUCT_A, "quantity": 1}],
        "invoice_ids": [inv]})
    assert r.status_code == 400


def test_api_delete_with_runs_is_409(admin_client, db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    recipesmod.create_production_run(db, co, recipe["id"], [
        {"product_name": PRODUCT_A, "quantity": 10}])
    r = admin_client.delete(f"/api/recipes/by-id/{recipe['id']}")
    assert r.status_code == 409


def test_api_name_aliases_still_resolve(admin_client, db):
    co, sale, inv = _scenario(db)
    _api_recipe(admin_client, co, inv, name="Alias Batch")
    stockmod.add_chemical(db, CHEM, 500, "KG")

    r = admin_client.get(
        f"/api/recipes/{quote('Alias Batch')}?company_id={co}")
    assert r.status_code == 200, r.get_json()
    rid = r.get_json()["recipe"]["id"]
    assert r.get_json()["recipe"]["invoice_id"] == inv

    r = admin_client.post(
        f"/api/recipes/{quote('Alias Batch')}/items",
        json={"chemical": CHEM, "percentage": 15, "company_id": co,
              "product": PRODUCT_A})
    assert r.status_code == 200, r.get_json()

    r = admin_client.put(f"/api/recipes/{quote('Alias Batch')}",
                         json={"new_name": "Aliased", "company_id": co})
    assert r.status_code == 200, r.get_json()
    assert recipesmod.get_recipe_by_id(db, rid)["name"] == "Aliased"


def test_api_invoice_deep_link_and_production_source(admin_client, db):
    co, sale, inv = _scenario(db)
    rid = _api_recipe(admin_client, co, inv)

    body = admin_client.get(f"/api/invoices/{inv}/recipe").get_json()
    assert body["recipe"]["id"] == rid
    assert {p["product_name"] for p in body["products"]} == {
        PRODUCT_A, PRODUCT_B}

    source = admin_client.get(
        f"/api/invoices/{inv}/production-source").get_json()
    assert source["invoice_id"] == inv
    assert source["invoice_number"] == "INV-RCP-1"
    assert source["recipe"]["id"] == rid
    remaining = {p["product_name"]: p["remaining_qty"]
                 for p in source["products"]}
    assert remaining == {PRODUCT_A: 100, PRODUCT_B: 50}


def test_api_invoice_without_recipe_deep_link_is_null(admin_client, db):
    co, sale, inv = _scenario(db)
    body = admin_client.get(f"/api/invoices/{inv}/recipe").get_json()
    assert body["recipe"] is None
    assert {p["product_name"] for p in body["products"]} == {
        PRODUCT_A, PRODUCT_B}


def test_api_reports_accept_outputs(admin_client, db):
    co, sale, inv, recipe = _formulated_two_product_recipe(db)
    r = admin_client.post("/api/reports/generate", json={"recipes": [
        {"recipe_id": recipe["id"], "outputs": [
            {"product_name": PRODUCT_A, "quantity": 10},
            {"product_name": PRODUCT_B, "quantity": 5},
        ]},
    ]})
    assert r.status_code == 200, r.get_json()
    report = r.get_json()["report"]
    assert len(report) == 1
    assert report[0]["required_total"] == pytest.approx(2.5)


# --------------------------------------------------------------------------- #
# water (display only)
# --------------------------------------------------------------------------- #
def test_water_defaults_to_the_remainder_and_overrides_once_set(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 40)

    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_A)
    assert product["percentage"] == 40
    assert product["water_percentage"] is None
    assert product["water_effective"] == 60
    assert product["grand_total"] == 100

    assert recipesmod.set_product_water(
        db, recipe["id"], PRODUCT_A, 25) is True
    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_A)
    assert product["water_percentage"] == 25
    assert product["water_effective"] == 25
    assert product["grand_total"] == 65

    # Clearing (None) puts the remainder back.
    assert recipesmod.set_product_water(
        db, recipe["id"], PRODUCT_A, None) is True
    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_A)
    assert product["water_percentage"] is None
    assert product["water_effective"] == 60


def test_water_is_per_product_and_never_needs_an_ingredient(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 40)

    # An empty product has no auto remainder, but an explicit value still shows.
    empty = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_B)
    assert empty["water_effective"] is None
    assert empty["grand_total"] is None

    recipesmod.set_product_water(db, recipe["id"], PRODUCT_B, 15)
    detail = recipesmod.get_recipe_detail(db, recipe["id"])
    assert _product(detail, PRODUCT_B)["grand_total"] == 15
    assert _product(detail, PRODUCT_A)["water_effective"] == 60


def test_water_is_validated_and_audited(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)

    with pytest.raises(ValueError):
        recipesmod.set_product_water(db, recipe["id"], PRODUCT_A, -1)
    with pytest.raises(ValueError):
        recipesmod.set_product_water(db, recipe["id"], PRODUCT_A, 100.5)
    with pytest.raises(ValueError):
        recipesmod.set_product_water(db, recipe["id"], "Not On Invoice", 10)

    recipesmod.set_product_water(db, recipe["id"], PRODUCT_A, 12.5)
    row = db.execute(
        "SELECT old_value, new_value FROM audit_logs"
        " WHERE entity_type = 'recipe_product'"
        "   AND action = 'RECIPE_WATER_SET' ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "auto"
    assert row[1] == "12.5"
