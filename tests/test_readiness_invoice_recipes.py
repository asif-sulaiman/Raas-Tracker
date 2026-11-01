"""Readiness for invoice-scoped recipes: plan, formulation, production.

A recipe is the production plan for exactly one invoice, so booking one
invoice asks three questions about *that* invoice's own lines:

* is there a recipe at all (``missing_recipe``)?
* does every product have a formulation (``missing_formulation``)?
* did a run linked to this invoice output every product
  (``missing_production``, ``qty_produced > 0``)?

A sibling invoice never answers for this one, and a late invoice line joins
the plan as an empty slot that blocks booking until it is formulated and run.
"""
import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import recipes as recipesmod  # noqa: E402
from raas_tracker import sales as salesmod  # noqa: E402
from raas_tracker._exceptions import (  # noqa: E402
    InvoiceHasRuns,
    InvoiceNotProduced,
)

PRODUCT_A = "Soap Base"
PRODUCT_B = "Liquid Cleaner"
CHEM_A = "Readiness Caustic"
CHEM_B = "Readiness Soda Ash"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _company(conn, name="Readiness Co"):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi, products=(PRODUCT_A,)):
    return salesmod.add_sale(
        conn,
        {"pi_number": pi, "client_name": "Buyer", "company_id": company_id},
        [{"product_name": p, "quantity": 100, "unit_price": 2}
         for p in products],
    )


def _sale_items(conn, sale_id):
    return conn.execute(
        "SELECT id, product_name, quantity FROM sale_items "
        "WHERE sale_id = %s ORDER BY id", (sale_id,)).fetchall()


def _invoice(conn, sale_id, number, product=None, quantity=100):
    lines = [{"sale_item_id": r[0], "quantity": quantity}
             for r in _sale_items(conn, sale_id)
             if product is None or r[1] == product]
    return salesmod.create_invoice(
        conn, sale_id, number, lines=lines, invoice_date="2026-01-15"
    )["invoice_id"]


def _recipe(conn, company_id, invoice_id, name="Plan"):
    return recipesmod.add_recipe(conn, name, company_id, invoice_id)


def _chemical(conn, name, qty=500):
    row = conn.execute(
        "SELECT id FROM chemicals WHERE name = %s", (name,)).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "INSERT INTO chemicals (name, current_qty, unit) VALUES (%s, %s, 'KG')"
        " RETURNING id", (name, qty)).fetchone()
    conn.commit()
    return row[0]


def _formulate(conn, recipe, product, chemical, pct):
    return recipesmod.add_recipe_item(
        conn, recipe["id"], product, chemical, pct)


def _produce(conn, company_id, recipe, outputs):
    return recipesmod.create_production_run(
        conn, company_id, recipe["id"],
        [{"product_name": p, "quantity": q} for p, q in outputs.items()])


def _stock(conn, name):
    return conn.execute(
        "SELECT current_qty FROM chemicals WHERE name = %s", (name,)
    ).fetchone()[0]


# --------------------------------------------------------------------------- #
# invoice readiness
# --------------------------------------------------------------------------- #
def test_no_recipe_names_the_recipe_step(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-1")
    inv = _invoice(db, sale, "INV-RD-1")

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_recipe"] is True
    # Nothing can be formulated or produced without a plan.
    assert ready["missing_formulation"] == [PRODUCT_A]
    assert ready["missing_production"] == [PRODUCT_A]

    with pytest.raises(InvoiceNotProduced) as exc:
        salesmod.book_invoice(db, inv, "2026-02-01")
    assert "no recipe" in str(exc.value)
    db.rollback()


def test_recipe_without_formulation_is_missing_formulation(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-2")
    inv = _invoice(db, sale, "INV-RD-2")
    _recipe(db, co, inv)

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_recipe"] is False
    assert ready["missing_formulation"] == [PRODUCT_A]
    assert ready["missing_production"] == [PRODUCT_A]


def test_formulated_without_a_run_is_missing_production(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-3")
    inv = _invoice(db, sale, "INV-RD-3")
    recipe = _recipe(db, co, inv)
    _chemical(db, CHEM_A)
    _formulate(db, recipe, PRODUCT_A, CHEM_A, 40)

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_formulation"] == []
    assert ready["missing_production"] == [PRODUCT_A]

    with pytest.raises(InvoiceNotProduced) as exc:
        salesmod.book_invoice(db, inv, "2026-02-01")
    assert "not produced yet" in str(exc.value)
    assert PRODUCT_A in str(exc.value)
    db.rollback()


def test_production_clears_readiness_and_booking_succeeds(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-4")
    inv = _invoice(db, sale, "INV-RD-4")
    recipe = _recipe(db, co, inv)
    _chemical(db, CHEM_A)
    _formulate(db, recipe, PRODUCT_A, CHEM_A, 40)
    _produce(db, co, recipe, {PRODUCT_A: 100})

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is True
    assert ready["missing_recipe"] is False
    assert ready["missing_formulation"] == []
    assert ready["missing_production"] == []

    assert salesmod.book_invoice(db, inv, "2026-02-01") is True
    status = db.execute("SELECT status FROM invoices WHERE id = %s",
                        (inv,)).fetchone()[0]
    assert status == "booked"


def test_late_product_blocks_booking_until_formulated_and_run(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-5", products=(PRODUCT_A, PRODUCT_B))
    item_a, item_b = _sale_items(db, sale)
    inv = _invoice(db, sale, "INV-RD-5", product=PRODUCT_A)
    recipe = _recipe(db, co, inv)
    _chemical(db, CHEM_A)
    _chemical(db, CHEM_B)
    _formulate(db, recipe, PRODUCT_A, CHEM_A, 40)
    _produce(db, co, recipe, {PRODUCT_A: 100})
    assert salesmod.invoice_production_readiness(db, co, inv)["ok"] is True

    # The late line auto-joins the plan as an empty slot (same transaction).
    salesmod.create_invoice_item(db, inv, item_b[0], 50)
    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_recipe"] is False
    assert ready["missing_formulation"] == [PRODUCT_B]
    assert ready["missing_production"] == [PRODUCT_B]
    with pytest.raises(InvoiceNotProduced) as exc:
        salesmod.book_invoice(db, inv, "2026-02-01")
    assert PRODUCT_B in str(exc.value)
    db.rollback()

    stock_a = _stock(db, CHEM_A)
    _formulate(db, recipe, PRODUCT_B, CHEM_B, 20)
    _produce(db, co, recipe, {PRODUCT_B: 50})

    # Producing the late product must not deduct the first product again.
    assert _stock(db, CHEM_A) == pytest.approx(stock_a)
    assert _stock(db, CHEM_B) == pytest.approx(500 - 10)
    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is True
    assert salesmod.book_invoice(db, inv, "2026-02-01") is True


def test_run_on_another_invoice_does_not_unlock_this_one(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-6")
    inv_a = _invoice(db, sale, "INV-RD-6A", quantity=60)
    inv_b = _invoice(db, sale, "INV-RD-6B", quantity=40)
    recipe_a = _recipe(db, co, inv_a, name="Plan A")
    recipe_b = _recipe(db, co, inv_b, name="Plan B")
    _chemical(db, CHEM_A)
    _formulate(db, recipe_a, PRODUCT_A, CHEM_A, 10)
    _formulate(db, recipe_b, PRODUCT_A, CHEM_A, 10)
    _produce(db, co, recipe_a, {PRODUCT_A: 60})

    assert salesmod.invoice_production_readiness(db, co, inv_a)["ok"] is True
    ready_b = salesmod.invoice_production_readiness(db, co, inv_b)
    assert ready_b["ok"] is False
    assert ready_b["missing_recipe"] is False
    assert ready_b["missing_formulation"] == []
    assert ready_b["missing_production"] == [PRODUCT_A]


# --------------------------------------------------------------------------- #
# LC / sale rollup
# --------------------------------------------------------------------------- #
def test_sale_readiness_rolls_up_each_invoice(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-7", products=(PRODUCT_A, PRODUCT_B))
    inv_a = _invoice(db, sale, "INV-RD-7A", product=PRODUCT_A)
    inv_b = _invoice(db, sale, "INV-RD-7B", product=PRODUCT_B)
    recipe_a = _recipe(db, co, inv_a, name="Plan A")
    recipe_b = _recipe(db, co, inv_b, name="Plan B")
    _chemical(db, CHEM_A)
    _chemical(db, CHEM_B)
    _formulate(db, recipe_a, PRODUCT_A, CHEM_A, 40)
    _formulate(db, recipe_b, PRODUCT_B, CHEM_B, 20)
    _produce(db, co, recipe_a, {PRODUCT_A: 100})

    ready = salesmod.production_readiness(db, co, [sale])
    assert ready["recipes_ok"] is True
    assert ready["produced_ok"] is False
    assert ready["detail"]["products"] == [PRODUCT_B, PRODUCT_A]  # sorted
    assert ready["detail"]["missing_recipes"] == []
    assert ready["detail"]["missing_formulation"] == []
    assert ready["detail"]["missing_production"] == [PRODUCT_B]


def test_sale_readiness_flags_an_invoice_without_a_recipe(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-8", products=(PRODUCT_A, PRODUCT_B))
    inv_a = _invoice(db, sale, "INV-RD-8A", product=PRODUCT_A)
    _invoice(db, sale, "INV-RD-8B", product=PRODUCT_B)
    _recipe(db, co, inv_a, name="Plan A")

    ready = salesmod.production_readiness(db, co, [sale])
    assert ready["recipes_ok"] is False
    assert ready["produced_ok"] is False
    assert ready["detail"]["missing_recipes"] == [PRODUCT_B]


# --------------------------------------------------------------------------- #
# tampering backstops
# --------------------------------------------------------------------------- #
def test_ship_rechecks_readiness_and_refuses_an_emptied_formulation(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-9")
    inv = _invoice(db, sale, "INV-RD-9")
    recipe = _recipe(db, co, inv)
    _chemical(db, CHEM_A)
    _formulate(db, recipe, PRODUCT_A, CHEM_A, 40)
    _produce(db, co, recipe, {PRODUCT_A: 100})
    assert salesmod.book_invoice(db, inv, "2026-02-01") is True

    # Booking proved production; emptying the formulation afterwards must not
    # ship on that stale proof.
    db.execute(
        """DELETE FROM recipe_items WHERE recipe_product_id IN
               (SELECT id FROM recipe_products WHERE recipe_id = %s)""",
        (recipe["id"],))
    db.commit()

    with pytest.raises(InvoiceNotProduced):
        salesmod.ship_invoice(db, inv, "2026-02-02", invoice_number="INV-RD-9")
    db.rollback()
    status = db.execute("SELECT status FROM invoices WHERE id = %s",
                        (inv,)).fetchone()[0]
    assert status == "booked"


def test_void_invoice_with_runs_is_refused(db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-10")
    inv = _invoice(db, sale, "INV-RD-10")
    recipe = _recipe(db, co, inv)
    _chemical(db, CHEM_A)
    _formulate(db, recipe, PRODUCT_A, CHEM_A, 40)
    _produce(db, co, recipe, {PRODUCT_A: 100})

    with pytest.raises(InvoiceHasRuns):
        salesmod.void_invoice(db, sale, inv)
    db.rollback()
    assert db.execute("SELECT 1 FROM invoices WHERE id = %s",
                      (inv,)).fetchone() is not None


# --------------------------------------------------------------------------- #
# route shape
# --------------------------------------------------------------------------- #
def test_api_readiness_reports_the_three_steps(admin_client, db):
    co = _company(db)
    sale = _sale(db, co, "PI-RD-11")
    inv = _invoice(db, sale, "INV-RD-11")

    body = admin_client.get(f"/api/invoices/{inv}/readiness").get_json()
    assert body["can_book"] is False
    assert body["recipes_ok"] is False
    assert body["produced_ok"] is False
    assert "no recipe" in body["book_blocked_reason"]
    assert body["detail"]["missing_recipe"] is True
    assert body["detail"]["missing_formulation"] == [PRODUCT_A]
    assert body["detail"]["missing_production"] == [PRODUCT_A]

    recipe = _recipe(db, co, inv)
    _chemical(db, CHEM_A)
    _formulate(db, recipe, PRODUCT_A, CHEM_A, 40)
    _produce(db, co, recipe, {PRODUCT_A: 100})

    body = admin_client.get(f"/api/invoices/{inv}/readiness").get_json()
    assert body["can_book"] is True
    assert body["recipes_ok"] is True
    assert body["produced_ok"] is True
    assert body["book_blocked_reason"] is None
    assert body["detail"]["missing_formulation"] == []
    assert body["detail"]["missing_production"] == []
