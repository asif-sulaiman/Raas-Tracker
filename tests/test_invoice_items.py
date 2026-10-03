"""Phase 2 lane A: invoice-line services + shipment-ready gate.

TDD RED first: `create_invoice_item` / `list_invoice_items` /
`check_lc_shipment_ready` / `invoiced_total_for_sale` do not exist yet.
"""
import math
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import sales as salesmod  # noqa: E402
from raas_tracker import lcs as lcsmod  # noqa: E402


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(conn, name=None):
    name = name or f"Inv Co {_tag()}"
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, company_id, pi=None, product="ProdInv", qty=10, price=5):
    return salesmod.add_sale(
        conn,
        {"pi_number": pi or f"PI-{_tag()}", "client_name": "x",
         "company_id": company_id},
        [{"product_name": product, "quantity": qty, "unit_price": price}],
    )


def _sale_item(conn, sale_id):
    return conn.execute(
        "SELECT id, quantity, unit_price FROM sale_items WHERE sale_id = %s"
        " ORDER BY id LIMIT 1",
        (sale_id,),
    ).fetchone()


def _invoice(conn, sale_id, number=None):
    """Header-only invoice for the line-level unit tests.

    ``seed_lines=False`` stops create_invoice from seeding the PI's remaining
    lines (seeding has its own tests), so these tests own the full remaining
    quantity and can exercise create_invoice_item's rules.
    """
    number = number or f"INV-{_tag()}"
    return salesmod.create_invoice(conn, sale_id, number, seed_lines=False)["invoice_id"]


def _recipe(conn, company_id, product, name=None):
    name = name or f"R-{_tag()}"
    conn.execute(
        "INSERT INTO recipes (name, company_id, product_name) VALUES (%s, %s, %s)",
        (name, company_id, product),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# create_invoice_item: price lock + qty rules
# --------------------------------------------------------------------------- #
def test_price_locked_to_sale_item(db):
    co = _company(db)
    sid = _sale(db, co, product="LockProd", qty=10, price=7.5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    line = salesmod.create_invoice_item(db, inv, si_id, 2)
    assert line["unit_price"] == pytest.approx(7.5)
    assert line["quantity"] == pytest.approx(2)
    assert line["line_total"] == pytest.approx(round(2 * 7.5, 2))
    row = db.execute(
        "SELECT unit_price, quantity, line_total FROM invoice_items WHERE id = %s",
        (line["id"],),
    ).fetchone()
    assert float(row[0]) == pytest.approx(7.5)
    assert float(row[1]) == pytest.approx(2)


def test_over_qty_rejected(db):
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 6)
    with pytest.raises(ValueError):
        salesmod.create_invoice_item(db, inv, si_id, 5)
    db.rollback()
    # only the first line persisted
    assert len(salesmod.list_invoice_items(db, inv)) == 1


def test_remaining_qty_math(db):
    co = _company(db)
    sid = _sale(db, co, qty=10, price=4)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 3)
    salesmod.create_invoice_item(db, inv, si_id, 4)
    # 7 used, 3 remain: exact fill OK ...
    salesmod.create_invoice_item(db, inv, si_id, 3)
    assert len(salesmod.list_invoice_items(db, inv)) == 3
    # ... then anything more rejects
    with pytest.raises(ValueError):
        salesmod.create_invoice_item(db, inv, si_id, 1)
    db.rollback()


def test_quantity_must_be_finite_and_positive(db):
    co = _company(db)
    sid = _sale(db, co)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    for bad in (0, -1, float("nan"), float("inf"), float("-inf"), "abc", None):
        with pytest.raises(ValueError):
            salesmod.create_invoice_item(db, inv, si_id, bad)
        db.rollback()


def test_cross_sale_item_rejected(db):
    tag = _tag()
    co = _company(db, f"Inv X Co {tag}")
    s1 = _sale(db, co, pi=f"PI-X1-{tag}")
    s2 = _sale(db, co, pi=f"PI-X2-{tag}")
    si2 = _sale_item(db, s2)[0]
    inv1 = _invoice(db, s1)
    with pytest.raises(ValueError):
        salesmod.create_invoice_item(db, inv1, si2, 1)
    db.rollback()


def test_line_total_rounding(db):
    co = _company(db)
    sid = _sale(db, co, product="RoundProd", qty=100, price=2.333)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    line = salesmod.create_invoice_item(db, inv, si_id, 3)
    assert line["line_total"] == pytest.approx(round(3 * 2.333, 2))


# --------------------------------------------------------------------------- #
# list / totals
# --------------------------------------------------------------------------- #
def test_list_invoice_items(db):
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    assert salesmod.list_invoice_items(db, inv) == []
    l1 = salesmod.create_invoice_item(db, inv, si_id, 2)
    l2 = salesmod.create_invoice_item(db, inv, si_id, 3)
    rows = salesmod.list_invoice_items(db, inv)
    assert [r["id"] for r in rows] == [l1["id"], l2["id"]]


def test_invoiced_total_for_sale(db):
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    assert salesmod.invoiced_total_for_sale(db, sid) == 0.0
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 2)  # 2*5=10
    salesmod.create_invoice_item(db, inv, si_id, 3)  # 3*5=15
    assert salesmod.invoiced_total_for_sale(db, sid) == pytest.approx(25.0)


# --------------------------------------------------------------------------- #
# check_lc_shipment_ready gate matrix
# --------------------------------------------------------------------------- #
def _lc_with_sale(db, co, product="GateProd"):
    lc = lcsmod.create_lc(db, co, f"LC-G-{_tag()}")
    sid = _sale(db, co, product=product)
    lcsmod.attach_pis(db, lc["id"], [sid])
    return lc["id"], sid


def test_shipment_ready_neither(db):
    co = _company(db, f"Gate N {_tag()}")
    lc_id, _ = _lc_with_sale(db, co, product=f"Nope-{_tag()}")
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["recipes_ok"] is False
    assert out["invoices_ok"] is False
    assert "detail" in out


def test_shipment_ready_recipe_only(db):
    tag = _tag()
    co = _company(db, f"Gate R {_tag()}")
    prod = f"RProd-{tag}"
    lc_id, _ = _lc_with_sale(db, co, product=prod)
    _recipe(db, co, prod)
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["recipes_ok"] is True
    assert out["invoices_ok"] is False


def test_shipment_ready_invoice_only(db):
    co = _company(db, f"Gate I {_tag()}")
    lc_id, sid = _lc_with_sale(db, co, product=f"IProd-{_tag()}")
    _invoice(db, sid)
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["recipes_ok"] is False
    assert out["invoices_ok"] is True


def test_shipment_ready_both(db):
    tag = _tag()
    co = _company(db, f"Gate B {_tag()}")
    prod = f"BProd-{tag}"
    lc_id, sid = _lc_with_sale(db, co, product=prod)
    _recipe(db, co, prod)
    _invoice(db, sid)
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["recipes_ok"] is True
    assert out["invoices_ok"] is True


# --------------------------------------------------------------------------- #
# Oracle gate 2 remediation: B7 / P1 / P2
# --------------------------------------------------------------------------- #
def test_sequential_double_create_boundary_second_rejected(db):
    """B7: remaining exactly consumed, second INSERT rejected (locked txn)."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 6)
    salesmod.create_invoice_item(db, inv, si_id, 4)  # exactly consumed
    assert len(salesmod.list_invoice_items(db, inv)) == 2
    with pytest.raises(ValueError):
        salesmod.create_invoice_item(db, inv, si_id, 1)
    db.rollback()
    assert len(salesmod.list_invoice_items(db, inv)) == 2


def test_invoice_amount_derived_cache(db):
    """P1: invoices.amount mirrors SUM(invoice_items.line_total)."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 2)  # 2*5=10
    row = db.execute(
        "SELECT amount FROM invoices WHERE id = %s", (inv,)).fetchone()
    assert float(row[0]) == pytest.approx(10.0)
    salesmod.create_invoice_item(db, inv, si_id, 3)  # +15 => 25
    row = db.execute(
        "SELECT amount FROM invoices WHERE id = %s", (inv,)).fetchone()
    assert float(row[0]) == pytest.approx(25.0)


def test_legacy_fallback_passes_gate(db):
    """P2: legacy company (recipes without product_name) passes the gate."""
    tag = _tag()
    co = _company(db, f"Gate Legacy {_tag()}")
    prod = f"LegacyProd-{tag}"
    lc_id, sid = _lc_with_sale(db, co, product=prod)
    _invoice(db, sid)
    # legacy-style recipe: no product link at all
    db.execute(
        "INSERT INTO recipes (name, company_id, product_name)"
        " VALUES (%s, %s, NULL)",
        (f"Legacy-R-{tag}", co),
    )
    db.commit()
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["recipes_ok"] is True
    assert out["invoices_ok"] is True




# --------------------------------------------------------------------------- #
# Gate 4 B1: invoices are never line-less by default
# --------------------------------------------------------------------------- #
def test_create_invoice_seeds_remaining_pi_lines(db):
    """A default invoice carries one line per PI line, so the report is truthful."""
    co = _company(db)
    sid = _sale(db, co, product="SeedProd", qty=10, price=7.5)
    inv = salesmod.create_invoice(db, sid, f"INV-{_tag()}")["invoice_id"]

    items = salesmod.list_invoice_items(db, inv)
    assert len(items) == 1, "default invoice must seed its PI line"
    line = items[0]
    assert line["quantity"] == pytest.approx(10)
    assert line["unit_price"] == pytest.approx(7.5)
    assert line["line_total"] == pytest.approx(75.0)
    assert line["sale_item_id"] == _sale_item(db, sid)[0]
    # amount stays the PI total, matching the pre-seeding contract.
    amount = db.execute("SELECT amount FROM invoices WHERE id = %s",
                        (inv,)).fetchone()[0]
    assert float(amount) == pytest.approx(75.0)


def test_second_invoice_seeds_only_the_remainder(db):
    """A partial first invoice leaves exactly the rest for the follow-up."""
    co = _company(db)
    sid = _sale(db, co, product="SplitSeed", qty=10, price=4)  # PI total 40
    si_id, _, _ = _sale_item(db, sid)

    first = salesmod.create_invoice(db, sid, f"INV-{_tag()}")["invoice_id"]
    # The operator trims the first invoice to 6 of the 10 units (24 of the 40).
    db.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (first,))
    db.commit()
    salesmod.create_invoice_item(db, first, si_id, 6)

    second = salesmod.create_invoice(db, sid, f"INV-{_tag()}")["invoice_id"]
    items = salesmod.list_invoice_items(db, second)
    assert len(items) == 1, "seeds only the lines that still have quantity left"
    assert items[0]["quantity"] == pytest.approx(4), "only what is left"
    assert items[0]["line_total"] == pytest.approx(16.0)
    # The derived header is the remainder, not the PI total, and it equals its
    # own lines exactly.
    second_amount = float(db.execute("SELECT amount FROM invoices WHERE id = %s",
                                     (second,)).fetchone()[0])
    assert second_amount == pytest.approx(16.0)
    assert salesmod.invoiced_total_for_sale(db, sid) == pytest.approx(40.0)


def test_seed_lines_false_creates_a_header_only_invoice(db):
    """The internal test seam still derives the money, it just skips the lines."""
    co = _company(db)
    sid = _sale(db, co, product="HeaderOnly", qty=5, price=20)  # PI total 100
    inv = salesmod.create_invoice(
        db, sid, f"INV-{_tag()}", seed_lines=False)["invoice_id"]

    assert salesmod.list_invoice_items(db, inv) == [], "no seeded lines"
    amount = float(db.execute("SELECT amount FROM invoices WHERE id = %s",
                              (inv,)).fetchone()[0])
    assert amount == pytest.approx(100.0), "still derived, never a stated 0"


def test_create_invoice_takes_no_stated_amount(db):
    """Gate 4 B2: there is no way to put a number on an invoice any more.

    A header carrying money no line backs was invisible to the invoice-driven
    report: it showed a real receivable as quantity 0 / total 0 / due 0 / Paid
    and dropped the money from gross_sales. The parameter is gone, so the bug
    cannot come back through this entry point.
    """
    import inspect

    params = inspect.signature(salesmod.create_invoice).parameters
    assert "amount" not in params, (
        "create_invoice must not accept a stated amount; the money is derived")
    assert set(params) == {"conn", "sale_id", "invoice_number", "seed_lines"}
