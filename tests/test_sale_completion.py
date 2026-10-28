"""Phase 3 — completion is defined by invoices, not by the PI proposal.

Locked decision: a sale completes when it has at least one invoice and every
invoice is ``paid``; the money compared is ``SUM(invoice_items.line_total)``.
A completed sale reopens when a correction leaves an invoice unpaid, and an LC
follows its children (auto-complete on the last, revert on any reopen).

Phase 4 adds the forward case: **late invoicing**. Creating an invoice (single
or batch) on a completed sale reopens it and its LC, because the new open
invoice is money still to be collected. Other stages are left alone.

Booking/shipping order is pinned in ``tests/test_invoice_order.py``. These
tests stand invoices at the status they need with direct UPDATEs so the
completion logic is what is under test; the one end-to-end test builds real
production rows because ``book_invoice`` re-reads them.
"""
import uuid

import pytest

from chem_stock import add_recipe
from raas_tracker import lcs as lcsmod
from raas_tracker._exceptions import (
    LcNotReadyForCompletion,
    SaleNotReadyForCompletion,
)
from raas_tracker.sales import (
    add_sale,
    book_invoice,
    create_invoice,
    create_invoices_batch,
    delete_sale_payment_record,
    get_sale_by_id,
    invoice_settlement,
    mark_invoice_paid,
    move_sale_to_stage,
    ship_invoice,
    update_sale_payment_record,
    void_invoice,
)


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(conn, name=None):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (name or f"Done Co {_tag()}",)).fetchone()
    conn.commit()
    return row[0]


def _sale(conn, pi, company_id=None, qty=10, price=10):
    return add_sale(conn, {"pi_number": pi, "client_name": "Done Co",
                           "company_id": company_id},
                    [{"product_name": f"{pi}-P", "quantity": qty,
                      "unit_price": price}])


def _invoice(conn, sale_id, number, quantities=None):
    """Invoice the PI line(s), optionally with explicit quantities."""
    rows = conn.execute(
        "SELECT id, quantity FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchall()
    if quantities is None:
        quantities = [float(r[1]) for r in rows]
    lines = [{"sale_item_id": r[0], "quantity": q}
             for r, q in zip(rows, quantities)]
    return create_invoice(conn, sale_id, number, lines=lines,
                          invoice_date="2026-01-15")["invoice_id"]


def _book(conn, invoice_id):
    conn.execute(
        "UPDATE invoices SET status = 'booked', approx_ship_date = %s"
        " WHERE id = %s", ("2026-11-01", invoice_id))
    conn.commit()


def _ship(conn, invoice_id):
    conn.execute(
        "UPDATE invoices SET status = 'shipped', actual_ship_date = %s"
        " WHERE id = %s", ("2026-10-20", invoice_id))
    conn.commit()


def _stage(conn, sale_id):
    return conn.execute("SELECT stage FROM sales WHERE id = %s",
                        (sale_id,)).fetchone()[0]


def _lc_stage(conn, lc_id):
    return conn.execute("SELECT stage FROM letters_of_credit WHERE id = %s",
                        (lc_id,)).fetchone()[0]


def _lc_with_two_sales(conn, tag):
    company_id = _company(conn)
    lc = lcsmod.create_lc(conn, company_id, f"LC-DONE-{tag}")
    s1 = _sale(conn, f"PI-D1-{tag}", company_id=company_id)
    s2 = _sale(conn, f"PI-D2-{tag}", company_id=company_id)
    lcsmod.attach_pis(conn, lc["id"], [s1, s2])
    return lc["id"], s1, s2


# --------------------------------------------------------------------------- #
# settlement
# --------------------------------------------------------------------------- #
def test_invoice_settlement_counts_amount_and_paid_state(db):
    sid = _sale(db, f"PI-STL-{_tag()}")
    empty = invoice_settlement(db, sid)
    assert empty["invoice_count"] == 0
    assert empty["amount"] == 0
    # "every invoice is paid" is vacuously true with no invoices at all — which
    # is precisely why the completion predicate is `settled`, not `all_paid`.
    assert empty["all_paid"] is True
    assert empty["settled"] is False

    inv1 = _invoice(db, sid, f"INV-STL-A-{_tag()}", quantities=[6.0])
    inv2 = _invoice(db, sid, f"INV-STL-B-{_tag()}", quantities=[4.0])
    part = invoice_settlement(db, sid)
    assert part["invoice_count"] == 2
    assert part["paid_count"] == 0
    assert part["open_count"] == 2
    assert part["amount"] == 100.0
    assert part["settled"] is False

    _ship(db, inv1)
    mark_invoice_paid(db, inv1, 60, "2026-10-25")
    half = invoice_settlement(db, sid)
    assert half["paid_count"] == 1
    assert half["open_count"] == 1
    assert half["all_paid"] is False
    assert half["settled"] is False

    _ship(db, inv2)
    mark_invoice_paid(db, inv2, 40, "2026-10-26")
    done = invoice_settlement(db, sid)
    assert done["paid_count"] == 2
    assert done["open_count"] == 0
    assert done["all_paid"] is True
    assert done["settled"] is True
    assert done["amount"] == 100.0


def test_money_shown_on_the_sale_is_invoice_money(db):
    sid = _sale(db, f"PI-SHOW-{_tag()}")  # PI proposal is 100
    _invoice(db, sid, f"INV-SHOW-{_tag()}", quantities=[4.0])  # invoiced 40

    sale = get_sale_by_id(db, sid)
    assert sale["invoice_total"] == 40.0
    assert sale["balance"] == 40.0


# --------------------------------------------------------------------------- #
# the completion gate
# --------------------------------------------------------------------------- #
def test_zero_invoice_sale_cannot_complete(db):
    sid = _sale(db, f"PI-NONE-{_tag()}")
    with pytest.raises(SaleNotReadyForCompletion, match="no invoices"):
        move_sale_to_stage(db, sid, "completed")
    assert _stage(db, sid) == "pi_issued"


def test_unpaid_invoice_blocks_completion(db):
    sid = _sale(db, f"PI-OPEN-{_tag()}")
    inv = _invoice(db, sid, f"INV-OPEN-{_tag()}")
    _book(db, inv)
    _ship(db, inv)
    move_sale_to_stage(db, sid, "payment_due")

    with pytest.raises(SaleNotReadyForCompletion, match="1 invoice"):
        move_sale_to_stage(db, sid, "completed")
    assert _stage(db, sid) == "payment_due"


def test_sale_completes_only_when_every_invoice_is_paid(db):
    sid = _sale(db, f"PI-TWO-{_tag()}")
    inv1 = _invoice(db, sid, f"INV-TWO-A-{_tag()}", quantities=[6.0])
    inv2 = _invoice(db, sid, f"INV-TWO-B-{_tag()}", quantities=[4.0])
    _book(db, inv1)
    _ship(db, inv1)
    move_sale_to_stage(db, sid, "payment_due")

    mark_invoice_paid(db, inv1, 60, "2026-10-25")
    assert _stage(db, sid) == "payment_due", "one unpaid invoice completed the sale"

    _ship(db, inv2)
    mark_invoice_paid(db, inv2, 40, "2026-10-26")
    assert _stage(db, sid) == "completed"
    sale = get_sale_by_id(db, sid)
    assert sale["invoice_total"] == 100.0
    assert sale["balance"] == 0


def test_completion_uses_invoice_total_not_the_pi(db):
    sid = _sale(db, f"PI-PART-{_tag()}")  # PI proposal 100
    inv = _invoice(db, sid, f"INV-PART-{_tag()}", quantities=[4.0])  # 40 billed
    _book(db, inv)
    _ship(db, inv)
    move_sale_to_stage(db, sid, "payment_due")

    mark_invoice_paid(db, inv, 40, "2026-10-25")

    assert _stage(db, sid) == "completed", "the unbilled PI remainder blocked completion"
    assert get_sale_by_id(db, sid)["invoice_total"] == 40.0


def test_entering_payment_due_completes_an_already_paid_sale(db):
    sid = _sale(db, f"PI-EARLY-{_tag()}")
    inv = _invoice(db, sid, f"INV-EARLY-{_tag()}")
    move_sale_to_stage(db, sid, "shipment_ongoing")
    _ship(db, inv)
    mark_invoice_paid(db, inv, 100, "2026-10-25")
    assert _stage(db, sid) == "shipment_ongoing", (
        "a payment must not drag the stage forward on its own")

    move_sale_to_stage(db, sid, "payment_due")
    assert _stage(db, sid) == "completed"


# --------------------------------------------------------------------------- #
# corrections reopen a completed sale
# --------------------------------------------------------------------------- #
def _completed_sale(db, tag):
    sid = _sale(db, f"PI-COR-{tag}")
    inv = _invoice(db, sid, f"INV-COR-{tag}")
    _book(db, inv)
    _ship(db, inv)
    move_sale_to_stage(db, sid, "payment_due")
    paid = mark_invoice_paid(db, inv, 100, "2026-10-25")
    assert _stage(db, sid) == "completed"
    return sid, inv, paid


def test_editing_a_payment_down_reopens_the_sale(db):
    tag = _tag()
    sid, inv, paid = _completed_sale(db, tag)

    update_sale_payment_record(db, sid, paid["payment_id"], payment_amount=50)

    assert db.execute("SELECT status FROM invoices WHERE id = %s",
                      (inv,)).fetchone()[0] == "shipped"
    assert _stage(db, sid) == "payment_due"


def test_deleting_a_payment_reopens_the_sale(db):
    tag = _tag()
    sid, _, paid = _completed_sale(db, tag)

    delete_sale_payment_record(db, sid, paid["payment_id"])

    assert _stage(db, sid) == "payment_due"


def test_voiding_a_paid_invoice_keeps_a_paid_set_completed(db):
    tag = _tag()
    sid = _sale(db, f"PI-VOID-{tag}")
    inv1 = _invoice(db, sid, f"INV-VOID-A-{tag}", quantities=[6.0])
    inv2 = _invoice(db, sid, f"INV-VOID-B-{tag}", quantities=[4.0])
    _book(db, inv1)
    _ship(db, inv1)
    move_sale_to_stage(db, sid, "payment_due")
    _ship(db, inv2)
    mark_invoice_paid(db, inv1, 60, "2026-10-25")
    mark_invoice_paid(db, inv2, 40, "2026-10-26")
    assert _stage(db, sid) == "completed"

    assert void_invoice(db, sid, inv1) is True
    assert _stage(db, sid) == "completed", "a still-paid invoice no longer counted"
    assert invoice_settlement(db, sid)["invoice_count"] == 1

    assert void_invoice(db, sid, inv2) is True
    assert _stage(db, sid) == "payment_due", (
        "a sale with no invoices left stayed completed")


# --------------------------------------------------------------------------- #
# the LC follows its children
# --------------------------------------------------------------------------- #
def _two_child_lc_at_payment_due(db, tag):
    lc_id, s1, s2 = _lc_with_two_sales(db, tag)
    inv1 = _invoice(db, s1, f"INV-LC-A-{tag}")
    inv2 = _invoice(db, s2, f"INV-LC-B-{tag}")
    _book(db, inv1)
    _book(db, inv2)
    assert lcsmod.move_lc_stage(db, lc_id, "shipment_ongoing") is True
    # The LC advances only once every invoice under it has shipped.
    _ship(db, inv1)
    _ship(db, inv2)
    assert lcsmod.move_lc_stage(db, lc_id, "payment_due") is True
    return lc_id, s1, s2, inv1, inv2


def test_lc_autocompletes_with_its_last_child_and_reverts_on_reopen(db):
    tag = _tag()
    lc_id, s1, s2, inv1, inv2 = _two_child_lc_at_payment_due(db, tag)

    _ship(db, inv1)
    mark_invoice_paid(db, inv1, 100, "2026-10-25")
    assert _stage(db, s1) == "completed"
    assert _lc_stage(db, lc_id) == "payment_due", (
        "the LC completed while a child was still open")

    _ship(db, inv2)
    paid2 = mark_invoice_paid(db, inv2, 100, "2026-10-26")
    assert _stage(db, s2) == "completed"
    assert _lc_stage(db, lc_id) == "completed"

    update_sale_payment_record(db, s2, paid2["payment_id"], payment_amount=50)
    assert _stage(db, s2) == "payment_due"
    assert _lc_stage(db, lc_id) == "payment_due", (
        "the LC stayed completed after a child reopened")

    moves = {r[0] for r in db.execute(
        "SELECT new_value FROM audit_logs"
        " WHERE action = 'LC_MOVE' AND entity_id = %s", (lc_id,)).fetchall()}
    assert {"completed", "payment_due"} <= moves


def test_lc_manual_completion_refused_while_a_child_invoice_is_unpaid(db):
    tag = _tag()
    lc_id, s1, s2, inv1, inv2 = _two_child_lc_at_payment_due(db, tag)
    _ship(db, inv1)
    mark_invoice_paid(db, inv1, 100, "2026-10-25")
    assert _stage(db, s1) == "completed"

    with pytest.raises(LcNotReadyForCompletion, match="1 sale"):
        lcsmod.move_lc_stage(db, lc_id, "completed")

    assert _lc_stage(db, lc_id) == "payment_due"
    assert _stage(db, s2) == "payment_due"


def test_lc_manual_completion_succeeds_when_every_child_is_settled(db):
    """A child paid while still in Shipment Ongoing is settled but not yet
    completed — the LC route must still be able to close the group."""
    tag = _tag()
    company_id = _company(db)
    lc = lcsmod.create_lc(db, company_id, f"LC-MAN-{tag}")
    sid = _sale(db, f"PI-MAN-{tag}", company_id=company_id)
    lcsmod.attach_pis(db, lc["id"], [sid])

    inv = _invoice(db, sid, f"INV-MAN-{tag}")
    _book(db, inv)
    _ship(db, inv)
    mark_invoice_paid(db, inv, 100, "2026-10-25")
    assert _stage(db, sid) == "lc_received"

    assert lcsmod.move_lc_stage(db, lc["id"], "shipment_ongoing") is True
    assert lcsmod.move_lc_stage(db, lc["id"], "payment_due") is True
    assert _stage(db, sid) == "payment_due"

    assert lcsmod.move_lc_stage(db, lc["id"], "completed") is True
    assert _lc_stage(db, lc["id"]) == "completed"
    assert _stage(db, sid) == "completed"


# --------------------------------------------------------------------------- #
# late invoicing reopens the completed sale (Phase 4)
# --------------------------------------------------------------------------- #
def test_creating_an_invoice_on_a_completed_sale_reopens_it_to_payment_due(db):
    tag = _tag()
    sid, _, _ = _completed_sale(db, tag)

    late = _invoice(db, sid, f"INV-LATE-{tag}", quantities=[2.0])

    assert db.execute("SELECT status FROM invoices WHERE id = %s",
                      (late,)).fetchone()[0] == "planned"
    assert _stage(db, sid) == "payment_due"
    move = db.execute(
        "SELECT from_stage, to_stage, notes FROM sales_stage_history"
        " WHERE sale_id = %s ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    assert move[0] == "completed"
    assert move[1] == "payment_due"
    assert "reopened" in move[2]


def test_batch_create_on_a_completed_sale_reopens_it_once(db):
    tag = _tag()
    sid, _, _ = _completed_sale(db, tag)
    sale_item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sid,)).fetchone()[0]

    create_invoices_batch(db, sid, [
        {"invoice_number": f"INV-BATCH-A-{tag}", "invoice_date": "2026-07-01",
         "lines": [{"sale_item_id": sale_item_id, "quantity": 1}]},
        {"invoice_number": f"INV-BATCH-B-{tag}", "invoice_date": "2026-07-02",
         "lines": [{"sale_item_id": sale_item_id, "quantity": 2}]},
    ])

    assert _stage(db, sid) == "payment_due"
    reopens = db.execute(
        "SELECT COUNT(*) FROM sales_stage_history WHERE sale_id = %s"
        " AND from_stage = 'completed' AND to_stage = 'payment_due'",
        (sid,)).fetchone()[0]
    assert reopens == 1, "the batch reopened the sale more than once"


def test_create_invoice_leaves_shipment_ongoing_and_payment_due_untouched(db):
    tag = _tag()
    # Shipment Ongoing: no payment clock yet, so a new invoice is just another
    # shipment in planning.
    so = _sale(db, f"PI-SO-{tag}")
    _invoice(db, so, f"INV-SO-{tag}")
    assert move_sale_to_stage(db, so, "shipment_ongoing") is True
    _invoice(db, so, f"INV-SO2-{tag}", quantities=[1.0])
    assert _stage(db, so) == "shipment_ongoing"

    # Payment Due: already open, so the new invoice changes nothing.
    pd = _sale(db, f"PI-PD-{tag}")
    inv = _invoice(db, pd, f"INV-PD-{tag}")
    _ship(db, inv)
    assert move_sale_to_stage(db, pd, "payment_due") is True
    _invoice(db, pd, f"INV-PD2-{tag}", quantities=[1.0])
    assert _stage(db, pd) == "payment_due"


def test_late_invoice_on_a_completed_lc_child_reopens_sale_and_lc(db):
    tag = _tag()
    lc_id, s1, s2, inv1, inv2 = _two_child_lc_at_payment_due(db, tag)
    mark_invoice_paid(db, inv1, 100, "2026-10-25")
    mark_invoice_paid(db, inv2, 100, "2026-10-26")
    assert _stage(db, s1) == "completed"
    assert _lc_stage(db, lc_id) == "completed"

    _invoice(db, s1, f"INV-LC-LATE-{tag}", quantities=[2.0])

    assert _stage(db, s1) == "payment_due"
    assert _lc_stage(db, lc_id) == "payment_due", (
        "the LC stayed completed while a child was reopened by a late invoice")


def test_late_invoice_books_ships_pays_and_recompletes(db):
    """The reopened sale walks the full lifecycle again, then closes."""
    tag = _tag()
    company_id = _company(db, f"Late Co {tag}")
    product = f"PI-LATE-{tag}-P"
    sid = _sale(db, f"PI-LATE-{tag}", company_id=company_id)
    first = _invoice(db, sid, f"INV-LATE-1-{tag}")
    _book(db, first)
    _ship(db, first)
    move_sale_to_stage(db, sid, "payment_due")
    mark_invoice_paid(db, first, 100, "2026-10-25")
    assert _stage(db, sid) == "completed"

    late = _invoice(db, sid, f"INV-LATE-2-{tag}", quantities=[2.0])
    assert _stage(db, sid) == "payment_due"

    # Booking reads production content, not the invoice status: build the
    # recipe and the run linked to THIS invoice, as the recipe page does.
    sale_item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sid,)).fetchone()[0]
    assert add_recipe(db, f"R-LATE-{tag}", 100, 0, company_id, product) is not False
    recipe_id = db.execute(
        "SELECT id FROM recipes WHERE company_id = %s AND lower(name) = lower(%s)",
        (company_id, f"R-LATE-{tag}")).fetchone()[0]
    run_id = db.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, order_number,"
        " batch_number, production_date, qty_produced, created_by)"
        " VALUES (%s, %s, NULL, NULL, NULL, 2.0, NULL) RETURNING id",
        (recipe_id, sale_item_id)).fetchone()[0]
    db.execute(
        "INSERT INTO production_run_links (run_id, sale_id, invoice_id)"
        " VALUES (%s, %s, %s)", (run_id, sid, late))
    db.commit()

    assert book_invoice(db, late, "2026-11-01") is True
    ship_invoice(db, late, "2026-10-20", invoice_number=f"LATE-SHIP-{tag}")
    mark_invoice_paid(db, late, 20, "2026-10-27")

    assert _stage(db, sid) == "completed", (
        "paying the late invoice did not settle the reopened sale")


def test_voiding_the_late_invoice_recompletes_the_sale(db):
    tag = _tag()
    sid, _, _ = _completed_sale(db, tag)
    late = _invoice(db, sid, f"INV-VOID-LATE-{tag}", quantities=[1.0])
    assert _stage(db, sid) == "payment_due"

    assert void_invoice(db, sid, late) is True

    assert _stage(db, sid) == "completed"
