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
    """Invoice with a single baseline line for the line-level unit tests.

    An invoice can no longer be created without lines (the header-only seam is
    gone), so the baseline of one unit keeps the invoice real while these tests
    own the quantities they add via create_invoice_item under the shared 5%
    aggregate ceiling.
    """
    number = number or f"INV-{_tag()}"
    si_id, _, _ = _sale_item(conn, sale_id)
    return salesmod.create_invoice(
        conn, sale_id, number,
        lines=[{"sale_item_id": si_id, "quantity": 1}],
        invoice_date="2026-01-15")["invoice_id"]


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


def test_over_qty_allowed(db):
    """Invoice quantities can exceed PI quantities up to 5% tolerance (shipment advantage)."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    # PI qty = 10, ceiling = 10.5 (5% tolerance); the baseline line uses 1
    # First added line: 6 (total 7, within PI qty)
    salesmod.create_invoice_item(db, inv, si_id, 6)
    # Second added line: 3.5 (total 10.5 = ceiling) - allowed at ceiling
    salesmod.create_invoice_item(db, inv, si_id, 3.5)
    db.commit()
    items = salesmod.list_invoice_items(db, inv)
    assert [i["quantity"] for i in items] == [1, 6, 3.5]


def test_over_qty_rejected_at_ceiling(db):
    """Invoice quantities exceeding 5% tolerance are rejected."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    # PI qty = 10, ceiling = 10.5
    salesmod.create_invoice_item(db, inv, si_id, 6)
    salesmod.create_invoice_item(db, inv, si_id, 3.5)  # total = 10.5 (at ceiling)
    # Adding more should be rejected
    with pytest.raises(ValueError, match="exceeds 10.5"):
        salesmod.create_invoice_item(db, inv, si_id, 0.1)
    db.rollback()


def test_remaining_qty_math(db):
    """Quantities can exceed PI remaining up to 5% tolerance ceiling."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=4)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 3)
    salesmod.create_invoice_item(db, inv, si_id, 4)
    # 8 used of 10; exact fill with 2 OK (total 10)
    salesmod.create_invoice_item(db, inv, si_id, 2)
    # Now 10 used, 0 remain - can add up to 0.5 more (5% tolerance)
    salesmod.create_invoice_item(db, inv, si_id, 0.5)
    db.commit()
    items = salesmod.list_invoice_items(db, inv)
    assert len(items) == 5
    assert items[4]["quantity"] == 0.5  # 5% tolerance allowance


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
    baseline = salesmod.list_invoice_items(db, inv)
    assert len(baseline) == 1, "the invoice is born with its baseline line"
    l1 = salesmod.create_invoice_item(db, inv, si_id, 2)
    l2 = salesmod.create_invoice_item(db, inv, si_id, 3)
    rows = salesmod.list_invoice_items(db, inv)
    assert [r["id"] for r in rows] == [baseline[0]["id"], l1["id"], l2["id"]]


def test_invoiced_total_for_sale(db):
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    assert salesmod.invoiced_total_for_sale(db, sid) == 0.0
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 2)  # 2*5=10
    salesmod.create_invoice_item(db, inv, si_id, 3)  # 3*5=15
    assert salesmod.invoiced_total_for_sale(db, sid) == pytest.approx(30.0)


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
    # Barrier 1 (invoices) must fail; with no invoices yet there are no
    # invoiced products, so both recipe and production checks are vacuous.
    assert out["invoices_ok"] is False
    assert out["recipes_ok"] is True
    assert out["produced_ok"] is True
    assert "detail" in out


def test_shipment_ready_recipe_only(db):
    tag = _tag()
    co = _company(db, f"Gate R {_tag()}")
    prod = f"RProd-{tag}"
    lc_id, _ = _lc_with_sale(db, co, product=prod)
    _recipe(db, co, prod)
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    # No invoicing yet, so no product is invoice-scoped: recipe/produced are
    # vacuously ok, and the invoice barrier is still what blocks the move.
    assert out["recipes_ok"] is True
    assert out["invoices_ok"] is False


def test_shipment_ready_invoice_only(db):
    co = _company(db, f"Gate I {_tag()}")
    lc_id, sid = _lc_with_sale(db, co, product=f"IProd-{_tag()}")
    # One explicit invoice line so the sale's product is actually invoiced
    # under the LC.
    _invoice(db, sid)
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["invoices_ok"] is True
    # A real recipe and an invoice-linked production run are both still owed.
    assert out["recipes_ok"] is False
    assert out["produced_ok"] is False


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
    # Recipe+big invoices exist, but no production run is linked yet.
    assert out["produced_ok"] is False


# --------------------------------------------------------------------------- #
# Oracle gate 2 remediation: B7 / P1 / P2
# --------------------------------------------------------------------------- #
def test_sequential_double_create_boundary_allowed(db):
    """B7: remaining exactly consumed, additional items allowed up to 5% tolerance."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 6)
    salesmod.create_invoice_item(db, inv, si_id, 3)  # exactly consumed (total 10)
    assert len(salesmod.list_invoice_items(db, inv)) == 3
    # Now we can add up to 0.5 more (5% tolerance ceiling = 10.5)
    salesmod.create_invoice_item(db, inv, si_id, 0.5)
    db.commit()
    items = salesmod.list_invoice_items(db, inv)
    assert len(items) == 4
    assert items[3]["quantity"] == 0.5


def test_invoice_amount_derived_cache(db):
    """P1: invoices.amount mirrors SUM(invoice_items.line_total)."""
    co = _company(db)
    sid = _sale(db, co, qty=10, price=5)
    si_id, _, _ = _sale_item(db, sid)
    inv = _invoice(db, sid)
    salesmod.create_invoice_item(db, inv, si_id, 2)  # baseline 5 + 10 = 15
    row = db.execute(
        "SELECT amount FROM invoices WHERE id = %s", (inv,)).fetchone()
    assert float(row[0]) == pytest.approx(15.0)
    salesmod.create_invoice_item(db, inv, si_id, 3)  # +15 => 30
    row = db.execute(
        "SELECT amount FROM invoices WHERE id = %s", (inv,)).fetchone()
    assert float(row[0]) == pytest.approx(30.0)


def test_legacy_recipe_without_product_link_does_not_satisfy_the_gate(db):
    """Strict product match — the NULL-product_name fallback is gone (7a7ae73).

    This used to pass vacuously: the header-only invoice carried no lines, so
    no product was invoice-scoped and the recipe check never ran. The invoice
    now carries its line, so the gate sees the product and reports the missing
    product-linked recipe; only a matching ``recipes.product_name`` satisfies
    the barrier.
    """
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
    assert out["invoices_ok"] is True
    assert out["recipes_ok"] is False, (
        "strict match: a NULL product_name is not a recipe for the product")
    assert prod in out["detail"]["missing_recipes"]

    # The product-linked recipe satisfies the recipe barrier; production is
    # still owed, so the produced barrier keeps blocking.
    _recipe(db, co, prod)
    out = salesmod.check_lc_shipment_ready(db, lc_id)
    assert out["recipes_ok"] is True
    assert out["produced_ok"] is False




# --------------------------------------------------------------------------- #
# Gate 4 B1: invoices are never line-less
# --------------------------------------------------------------------------- #
def test_create_invoice_refuses_a_line_less_invoice(db):
    """The legacy auto-seed and header-only modes are gone.

    A default create used to seed the PI's remaining lines; a header-only
    create produced an invoice the report could not see. Both modes are
    removed, so ``lines`` is required and must be non-empty.
    """
    from raas_tracker._exceptions import ServiceError

    co = _company(db)
    sid = _sale(db, co, product="SeedProd", qty=10, price=7.5)
    for bad in (None, []):
        with pytest.raises(ServiceError, match="lines are required"):
            salesmod.create_invoice(db, sid, f"INV-{_tag()}", lines=bad,
                                    invoice_date="2026-01-15")
        db.rollback()
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE sale_id = %s", (sid,)
    ).fetchone()[0] == 0, "a refused create leaves no header behind"


def test_second_invoice_takes_the_explicit_remainder(db):
    """A partial first invoice leaves the rest for the follow-up to state."""
    co = _company(db)
    sid = _sale(db, co, product="SplitSeed", qty=10, price=4)  # PI total 40
    si_id, _, _ = _sale_item(db, sid)

    first = salesmod.create_invoice(
        db, sid, f"INV-{_tag()}",
        lines=[{"sale_item_id": si_id, "quantity": 6}],
        invoice_date="2026-01-15")["invoice_id"]
    first_amount = float(db.execute("SELECT amount FROM invoices WHERE id = %s",
                                    (first,)).fetchone()[0])
    assert first_amount == pytest.approx(24.0)

    second = salesmod.create_invoice(
        db, sid, f"INV-{_tag()}",
        lines=[{"sale_item_id": si_id, "quantity": 4}],
        invoice_date="2026-01-16")["invoice_id"]
    items = salesmod.list_invoice_items(db, second)
    assert len(items) == 1, "the follow-up carries exactly its explicit line"
    assert items[0]["quantity"] == pytest.approx(4), "the remainder"
    assert items[0]["line_total"] == pytest.approx(16.0)
    # The header is its own lines exactly, and the sale totals whole.
    second_amount = float(db.execute("SELECT amount FROM invoices WHERE id = %s",
                                     (second,)).fetchone()[0])
    assert second_amount == pytest.approx(16.0)
    assert salesmod.invoiced_total_for_sale(db, sid) == pytest.approx(40.0)


def test_create_invoice_header_is_exactly_its_explicit_lines(db):
    """The header is the line sum, never a stated figure or the PI total."""
    co = _company(db)
    sid = _sale(db, co, product="HeaderOnly", qty=5, price=20)  # PI total 100
    si_id, _, _ = _sale_item(db, sid)
    out = salesmod.create_invoice(
        db, sid, f"INV-{_tag()}",
        lines=[{"sale_item_id": si_id, "quantity": 1.5}],
        invoice_date="2026-01-15")
    inv = out["invoice_id"]
    assert out["amount"] == pytest.approx(30.0), "1.5 * 20, not the PI's 100"
    amount = float(db.execute("SELECT amount FROM invoices WHERE id = %s",
                              (inv,)).fetchone()[0])
    assert amount == pytest.approx(30.0), "still derived, never a stated figure"
    assert len(salesmod.list_invoice_items(db, inv)) == 1


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
    assert set(params) == {"conn", "sale_id", "invoice_number", "lines",
                           "invoice_date"}
