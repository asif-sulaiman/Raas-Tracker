"""Readiness must count every recipe for a product, not one arbitrary row.

Two recipes for the same product are legal (``(company_id, name)`` is the
only unique index), and PI-based naming makes that the normal case: the same
product on two PIs yields two recipes, each produced for its own invoice.
Readiness that resolves a single ``fetchone()`` recipe can then block booking
forever because it looked at the sibling that was never run.

A run of ANY recipe for the product - linked to the invoice, or to any invoice
of the sale - satisfies readiness.
"""
import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import recipes as recipesmod  # noqa: E402
from raas_tracker import sales as salesmod  # noqa: E402
from raas_tracker._exceptions import InvoiceNotProduced  # noqa: E402

PRODUCT = "Caustic Soda 50%"


def _company(conn, name="Readiness Co"):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi):
    return salesmod.add_sale(
        conn,
        {"pi_number": pi, "client_name": "Buyer", "company_id": company_id},
        [{"product_name": PRODUCT, "quantity": 10, "unit_price": 5}],
    )


def _sale_item_id(conn, sale_id):
    return conn.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchone()[0]


def _invoice(conn, sale_id, number):
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in conn.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    return salesmod.create_invoice(conn, sale_id, number, lines=lines,
                                   invoice_date="2026-01-15")["invoice_id"]


def _recipe(conn, company_id, name):
    assert recipesmod.add_recipe(
        conn, name, total_quantity=100, company_id=company_id,
        product_name=PRODUCT)
    return recipesmod.get_recipe_by_name(conn, company_id, name)["id"]


def _run(conn, recipe_id, sale_id, invoice_id=None):
    """Insert a production run (and its link) without touching stock.

    Readiness reads ``production_runs`` + ``production_run_links``; stock
    deduction is not part of this contract.
    """
    run_id = conn.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, qty_produced)"
        " VALUES (%s, %s, %s) RETURNING id",
        (recipe_id, _sale_item_id(conn, sale_id), 1)).fetchone()[0]
    conn.execute(
        "INSERT INTO production_run_links (run_id, sale_id, invoice_id)"
        " VALUES (%s, %s, %s)", (run_id, sale_id, invoice_id))
    conn.commit()
    return run_id


# --------------------------------------------------------------------------- #
# invoice readiness
# --------------------------------------------------------------------------- #
def test_run_of_the_second_recipe_unlocks_the_invoice(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-1")
    inv = _invoice(db, sale, "INV-MR-1")
    _recipe(db, co, "PI-MR-1 - A")
    second = _recipe(db, co, "PI-MR-1 - B")
    _run(db, second, sale, inv)

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is True
    assert ready["missing_recipes"] == []
    assert ready["missing_production"] == []


def test_run_of_the_second_recipe_unlocks_booking(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-2")
    inv = _invoice(db, sale, "INV-MR-2")
    _recipe(db, co, "PI-MR-2 - A")
    second = _recipe(db, co, "PI-MR-2 - B")
    _run(db, second, sale, inv)

    assert salesmod.book_invoice(db, inv, "2026-02-01") is True
    db.commit()
    status = db.execute("SELECT status FROM invoices WHERE id = %s",
                        (inv,)).fetchone()[0]
    assert status == "booked"


def test_run_on_another_invoice_does_not_unlock_this_one(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-3")
    inv_this = _invoice(db, sale, "INV-MR-3A")
    inv_other = _invoice(db, sale, "INV-MR-3B")
    recipe_id = _recipe(db, co, "PI-MR-3 - A")
    _run(db, recipe_id, sale, inv_other)

    ready = salesmod.invoice_production_readiness(db, co, inv_this)
    assert ready["ok"] is False
    assert ready["missing_recipes"] == []
    assert ready["missing_production"] == [PRODUCT]
    # Booking is refused for the same reason the readiness reports.
    with pytest.raises(InvoiceNotProduced):
        salesmod.book_invoice(db, inv_this, "2026-02-01")
    db.rollback()


def test_no_recipe_still_reports_missing_recipe(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-4")
    inv = _invoice(db, sale, "INV-MR-4")

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_recipes"] == [PRODUCT]
    assert ready["missing_production"] == [PRODUCT]


def test_recipe_without_a_run_reports_missing_production(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-5")
    inv = _invoice(db, sale, "INV-MR-5")
    _recipe(db, co, "PI-MR-5 - A")

    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_recipes"] == []
    assert ready["missing_production"] == [PRODUCT]


def test_booking_still_refused_when_only_another_product_was_run(db):
    """Set-based matching must not degrade into "any run anywhere"."""
    other = "Other Product"
    co = _company(db)
    sale = salesmod.add_sale(
        db,
        {"pi_number": "PI-MR-6", "client_name": "Buyer", "company_id": co},
        [{"product_name": PRODUCT, "quantity": 10, "unit_price": 5},
         {"product_name": other, "quantity": 4, "unit_price": 2}],
    )
    inv = _invoice(db, sale, "INV-MR-6")
    other_recipe = recipesmod.add_recipe(
        db, "PI-MR-6 - Other", total_quantity=10, company_id=co,
        product_name=other)
    assert other_recipe
    other_id = recipesmod.get_recipe_by_name(
        db, co, "PI-MR-6 - Other")["id"]
    _run(db, other_id, sale, inv)
    ready = salesmod.invoice_production_readiness(db, co, inv)
    assert ready["ok"] is False
    assert ready["missing_recipes"] == [PRODUCT]
    assert ready["missing_production"] == [PRODUCT]


# --------------------------------------------------------------------------- #
# LC / sale readiness
# --------------------------------------------------------------------------- #
def test_lc_readiness_sees_the_sibling_recipe(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-7")
    inv = _invoice(db, sale, "INV-MR-7")
    _recipe(db, co, "PI-MR-7 - A")
    second = _recipe(db, co, "PI-MR-7 - B")
    _run(db, second, sale, inv)

    ready = salesmod.production_readiness(db, co, [sale])
    assert ready["recipes_ok"] is True
    assert ready["produced_ok"] is True
    assert ready["detail"]["missing_recipes"] == []
    assert ready["detail"]["missing_production"] == []


def test_lc_readiness_still_sees_a_missing_recipe(db):
    co = _company(db)
    sale = _sale(db, co, "PI-MR-8")
    _invoice(db, sale, "INV-MR-8")

    ready = salesmod.production_readiness(db, co, [sale])
    assert ready["recipes_ok"] is False
    assert ready["produced_ok"] is False
    assert ready["detail"]["missing_recipes"] == [PRODUCT]
