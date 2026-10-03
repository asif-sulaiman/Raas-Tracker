"""Invoice amount + auto-paid rule (invoices.amount NUMERIC(14,2)).

invoices.amount is a DERIVED cache of SUM(invoice_items.line_total). A create
states only WHICH invoice this is: the money is the sale's remaining uninvoiced
PI (PI total minus what the invoice lines already carry), seeded one line per
remaining uninvoiced PI line, and the header is restated in ::numeric from those
lines. Covered here: a stated amount in the body being ignored, a second invoice
deriving the remainder, header == its lines on a multi-line PI, awkward money
never overstating the PI, partial vs full payments flipping status to 'paid', a
0-derived amount -> paid at creation, legacy NULL amount rows, the no-downgrade
guard, completion totals (total_amount / paid_amount), list row shape
(amount + paid_amount), and the 409 guard on deleting an invoiced PI line.

A test that wants an invoice SMALLER than the remainder has no amount to state:
it creates the invoice and then adjusts its lines (_trim_invoice), exactly as
the operator's line editor would.

Patterns/fixtures mirror tests/test_invoices_production.py.
"""
from decimal import Decimal

import pytest

from flask_app import InvoiceCreateIn
from raas_tracker.sales import _sync_invoice_paid_status, create_invoice_item


# ---------- helpers (patterns from tests/test_invoices_production.py) ----------

def _company(admin_client, db, name="AmtCo"):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale_lines(admin_client, company_id, pi, items):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": items})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _sale(admin_client, company_id, pi, product="ProdAmt", qty=10, unit_price=5):
    """One-line PI. qty x unit_price IS the total an invoice derives."""
    return _sale_lines(admin_client, company_id, pi,
                       [{"product_name": product, "quantity": qty,
                         "unit_price": unit_price, "unit": "KG"}])


def _create_invoice(admin_client, sale_id, number, body_amount=None):
    """POST a create. ``body_amount`` is only ever there to be IGNORED."""
    payload = {"invoice_number": number}
    if body_amount is not None:
        payload["amount"] = body_amount
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json=payload)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _invoice(admin_client, sale_id, number):
    return _create_invoice(admin_client, sale_id, number)["invoice_id"]


def _sale_item_ids(db, sale_id):
    return [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchall()]


def _trim_invoice(db, invoice_id, sale_item_id, quantity):
    """Drop a seeded invoice's lines and re-add ONE partial line.

    The create seeds the whole remainder; a test that needs an invoice smaller
    than the remainder (so a follow-up invoice has something left to derive)
    says so by adjusting the lines. create_invoice_item restates the header
    from the lines in the same write.
    """
    db.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (invoice_id,))
    db.commit()
    create_invoice_item(db, invoice_id, sale_item_id, quantity)


def _pi_total(db, sale_id):
    """PI total in exact ::numeric, straight from SQL."""
    return db.execute(
        "SELECT COALESCE(ROUND(SUM(quantity * unit_price)::numeric, 2), 0) "
        "FROM sale_items WHERE sale_id = %s", (sale_id,)).fetchone()[0]


def _header(db, invoice_id):
    """invoices.amount exactly as stored (Decimal/NULL)."""
    return db.execute(
        "SELECT amount FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


def _lines_total(db, invoice_id):
    """SUM(invoice_items.line_total) exactly as stored."""
    return db.execute(
        "SELECT COALESCE(SUM(line_total), 0) FROM invoice_items "
        "WHERE invoice_id = %s", (invoice_id,)).fetchone()[0]


def _pay(admin_client, sale_id, invoice_id, amount):
    r = admin_client.post(f"/api/sales/{sale_id}/invoices/{invoice_id}/pay",
                          json={"payment_amount": amount,
                                "payment_date": "2026-10-30"})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _rows(admin_client, sale_id):
    return admin_client.get(f"/api/sales/{sale_id}/invoices").get_json()


def _row(admin_client, sale_id, invoice_id):
    return [r for r in _rows(admin_client, sale_id)
            if r["invoice_id"] == invoice_id][0]


def _status(db, invoice_id):
    return db.execute(
        "SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


# ---------- 1. create: money is derived, never stated ----------

def test_create_invoice_ignores_stated_amount_and_derives_from_pi(admin_client, db):
    """A client still sending `amount` has it dropped, not honoured.

    There is no amount field any more. A header carrying money that no line
    backs is invisible to the invoice-driven report, so the only honest amount
    is the PI remainder the lines were seeded with.
    """
    assert "amount" not in InvoiceCreateIn.model_fields

    cid = _company(admin_client, db, name="AmtStated")
    sid = _sale(admin_client, cid, "PI-AMT-1", qty=20)  # 20 x 5 = 100

    body = _create_invoice(admin_client, sid, "INV-AMT-1", body_amount=40)
    assert body["status"] == "planned"
    assert body["invoice_id"]
    assert body["amount"] == pytest.approx(100.0)   # derived, not the stated 40

    inv = body["invoice_id"]
    assert _header(db, inv) == Decimal("100.00")
    assert _lines_total(db, inv) == Decimal("100.00")
    assert len(_rows(admin_client, sid)) == 1

    row = _row(admin_client, sid, inv)
    assert row["amount"] == pytest.approx(100.0)
    assert row["paid_amount"] == pytest.approx(0.0)
    assert row["status"] == "planned"


def test_create_invoice_derives_amount_from_whole_pi(admin_client, db):
    cid = _company(admin_client, db, name="AmtDefault")
    sid = _sale(admin_client, cid, "PI-AMT-2")  # 10 x 5 = 50

    body = _create_invoice(admin_client, sid, "INV-AMT-2")
    assert body["amount"] == pytest.approx(50.0)
    assert body["status"] == "planned"

    row = _row(admin_client, sid, body["invoice_id"])
    assert row["amount"] == pytest.approx(50.0)
    assert row["paid_amount"] == pytest.approx(0.0)


# ---------- 2. partial payment: status stays, paid_amount reflected ----------

def test_partial_payment_keeps_status_and_reports_paid_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtPartial")
    sid = _sale(admin_client, cid, "PI-AMT-4", qty=20)  # 20 x 5 = 100
    inv = _invoice(admin_client, sid, "INV-AMT-4")

    _pay(admin_client, sid, inv, 40)

    assert _status(db, inv) == "planned"
    row = _row(admin_client, sid, inv)
    assert row["amount"] == pytest.approx(100.0)
    assert row["paid_amount"] == pytest.approx(40.0)
    assert row["status"] == "planned"


# ---------- 3. reaching the amount flips status to 'paid' ----------

def test_full_payment_flips_status_to_paid(admin_client, db):
    cid = _company(admin_client, db, name="AmtFull")
    sid = _sale(admin_client, cid, "PI-AMT-5", qty=20)  # 20 x 5 = 100
    inv = _invoice(admin_client, sid, "INV-AMT-5")

    _pay(admin_client, sid, inv, 60)
    assert _status(db, inv) == "planned", "partial coverage must not flip"

    _pay(admin_client, sid, inv, 40)  # 100 >= 100
    assert _status(db, inv) == "paid"

    row = _row(admin_client, sid, inv)
    assert row["status"] == "paid"
    assert row["paid_amount"] == pytest.approx(100.0)


# ---------- 4. derived amount 0 -> paid at creation ----------

def test_zero_derived_amount_paid_at_creation(admin_client, db):
    """A zero-priced PI line derives 0 -> born 'paid' (covered with no payment)."""
    cid = _company(admin_client, db, name="AmtZero")
    sid = _sale(admin_client, cid, "PI-AMT-6", qty=10, unit_price=0)  # 10 x 0 = 0

    body = _create_invoice(admin_client, sid, "INV-AMT-6")
    assert body["amount"] == pytest.approx(0.0)
    assert body["status"] == "paid"
    assert _status(db, body["invoice_id"]) == "paid"

    row = _row(admin_client, sid, body["invoice_id"])
    assert row["status"] == "paid"
    assert row["amount"] == pytest.approx(0.0)
    assert row["paid_amount"] == pytest.approx(0.0)


# ---------- 5. legacy NULL amount: any payment marks it paid ----------

def test_legacy_null_amount_payment_marks_paid(admin_client, db):
    cid = _company(admin_client, db, name="AmtLegacy")
    sid = _sale(admin_client, cid, "PI-AMT-7", qty=40)  # 40 x 5 = 200

    paid_inv = _invoice(admin_client, sid, "INV-AMT-7")          # seeds 200
    _trim_invoice(db, paid_inv, _sale_item_ids(db, sid)[0], 20)  # -> 100
    unpaid_inv = _invoice(admin_client, sid, "INV-AMT-8")        # -> the rest
    assert _header(db, paid_inv) == Decimal("100.00")
    assert _header(db, unpaid_inv) == Decimal("100.00")
    db.execute("UPDATE invoices SET amount = NULL WHERE sale_id = %s", (sid,))
    db.commit()

    # NULL amount with NO payment -> old rule does not fire
    assert _status(db, unpaid_inv) == "planned"

    _pay(admin_client, sid, paid_inv, 10)
    assert _status(db, paid_inv) == "paid", "legacy NULL amount: any payment pays"

    row = _row(admin_client, sid, paid_inv)
    assert row["amount"] is None
    assert row["paid_amount"] == pytest.approx(10.0)
    assert row["status"] == "paid"


# ---------- 6. no-downgrade: a paid invoice can never be un-paid ----------

def test_paid_status_never_downgrades(admin_client, db):
    cid = _company(admin_client, db, name="AmtNoDown")

    # (a) a 0-derived invoice is paid with zero payments -> rule still "met",
    #     but the guarded UPDATE must not touch an already-'paid' row.
    zero_sid = _sale(admin_client, cid, "PI-AMT-9", qty=10, unit_price=0)
    zero_inv = _invoice(admin_client, zero_sid, "INV-AMT-9")
    assert _status(db, zero_inv) == "paid"
    assert _sync_invoice_paid_status(db, zero_inv) is False
    assert _status(db, zero_inv) == "paid"

    # (b) fully covered invoice: drop the payments, re-run the sync ->
    #     rule no longer met, so no UPDATE may fire (never un-pays).
    sid = _sale(admin_client, cid, "PI-AMT-10", qty=20)  # 20 x 5 = 100
    inv = _invoice(admin_client, sid, "INV-AMT-10")
    _pay(admin_client, sid, inv, 100)
    assert _status(db, inv) == "paid"
    assert _sync_invoice_paid_status(db, inv) is False
    assert _status(db, inv) == "paid"

    db.execute("DELETE FROM sale_payments WHERE invoice_id = %s", (inv,))
    db.commit()
    assert _sync_invoice_paid_status(db, inv) is False
    assert _status(db, inv) == "paid"
    assert _row(admin_client, sid, inv)["status"] == "paid"


# ---------- 7. completion totals ----------

def test_completion_carries_total_amount_and_paid_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtComp")
    sid = _sale(admin_client, cid, "PI-AMT-11", qty=50)  # 50 x 5 = 250

    # no invoices yet -> legacy shape (exact keys) is preserved
    r = admin_client.get(f"/api/sales/{sid}/completion")
    assert r.status_code == 200
    assert r.get_json() == {"status": "none", "paid": 0, "total": 0,
                            "invoices": []}

    si = _sale_item_ids(db, sid)[0]
    a = _invoice(admin_client, sid, "INV-AMT-11")   # seeds 250
    _trim_invoice(db, a, si, 20)                    # -> 100
    b = _invoice(admin_client, sid, "INV-AMT-12")   # -> the rest
    _trim_invoice(db, b, si, 10)                    # -> 50
    c = _invoice(admin_client, sid, "INV-AMT-13")   # -> the rest
    _trim_invoice(db, c, si, 6)                     # -> 30
    assert [_header(db, i) for i in (a, b, c)] == [Decimal("100.00"),
                                                   Decimal("50.00"),
                                                   Decimal("30.00")]
    db.execute("UPDATE invoices SET amount = NULL WHERE id = %s", (c,))
    db.commit()

    _pay(admin_client, sid, a, 100)   # paid
    _pay(admin_client, sid, b, 20)    # partial
    _pay(admin_client, sid, c, 30)    # legacy NULL -> paid by any payment

    body = admin_client.get(f"/api/sales/{sid}/completion").get_json()
    # existing keys unchanged
    assert body["total"] == 3
    assert body["paid"] == 2
    assert body["status"] == "partial"
    assert len(body["invoices"]) == 3
    # new keys
    assert body["total_amount"] == pytest.approx(150.0)   # NULL amount excluded
    assert body["paid_amount"] == pytest.approx(150.0)    # 100 + 20 + 30

    # invoices inside completion carry amount + paid_amount too
    by_id = {i["invoice_id"]: i for i in body["invoices"]}
    assert by_id[a]["amount"] == pytest.approx(100.0)
    assert by_id[a]["paid_amount"] == pytest.approx(100.0)
    assert by_id[b]["amount"] == pytest.approx(50.0)
    assert by_id[b]["paid_amount"] == pytest.approx(20.0)
    assert by_id[c]["amount"] is None and by_id[c]["paid_amount"] == pytest.approx(30.0)


# ---------- 8. list row shape ----------

def test_list_rows_carry_amount_and_paid_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtShape")
    sid = _sale(admin_client, cid, "PI-AMT-14", qty=15)  # 15 x 5 = 75
    inv = _invoice(admin_client, sid, "INV-AMT-14")

    rows = _rows(admin_client, sid)
    assert len(rows) == 1
    assert isinstance(rows[0]["amount"], (int, float))
    assert isinstance(rows[0]["paid_amount"], (int, float))
    assert rows[0]["amount"] == pytest.approx(75.0)
    assert rows[0]["paid_amount"] == pytest.approx(0.0)

    _pay(admin_client, sid, inv, 25.5)
    row = _row(admin_client, sid, inv)
    assert row["amount"] == pytest.approx(75.0)
    assert row["paid_amount"] == pytest.approx(25.5)
    assert row["status"] == "planned"


# ---------- 9. integrity: forward-only, sale scoping, derived money, void ----------

def _detail(admin_client, sale_id):
    r = admin_client.get(f"/api/sales/{sale_id}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _shipments(db, sale_id):
    return db.execute(
        "SELECT COUNT(*) FROM shipments WHERE sale_id = %s", (sale_id,)).fetchone()[0]


def _payments(db, sale_id):
    return db.execute("SELECT COUNT(*) FROM sale_payments WHERE sale_id = %s", (sale_id,)).fetchone()[0]


def test_ship_on_paid_refused_without_phantom_shipment(admin_client, db):
    cid = _company(admin_client, db, name="IntShipPaid")
    sid = _sale(admin_client, cid, "PI-INT-1")  # 10 x 5 = 50
    inv = _invoice(admin_client, sid, "INV-INT-1")
    _pay(admin_client, sid, inv, 50)
    assert _status(db, inv) == "paid"

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-11-05"})
    assert r.status_code == 409, r.get_json()
    assert "invoice already paid" in r.get_json()["error"]

    assert _status(db, inv) == "paid"
    assert _shipments(db, sid) == 0, "refused ship must not leave a shipment row"


def test_book_ship_pay_delete_reject_cross_sale_invoice(admin_client, db):
    cid = _company(admin_client, db, name="IntScope")
    sid_a = _sale(admin_client, cid, "PI-INT-A")
    sid_b = _sale(admin_client, cid, "PI-INT-B")
    inv = _invoice(admin_client, sid_b, "INV-INT-SC")

    r = admin_client.post(f"/api/sales/{sid_a}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 404, r.get_json()
    r = admin_client.post(f"/api/sales/{sid_a}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-11-02"})
    assert r.status_code == 404, r.get_json()
    r = admin_client.post(f"/api/sales/{sid_a}/invoices/{inv}/pay",
                          json={"payment_amount": 10, "payment_date": "2026-11-03"})
    assert r.status_code == 404, r.get_json()
    r = admin_client.delete(f"/api/sales/{sid_a}/invoices/{inv}")
    assert r.status_code == 404, r.get_json()

    # No side effects anywhere.
    assert _status(db, inv) == "planned"
    assert _payments(db, sid_a) == 0
    assert _payments(db, sid_b) == 0
    assert _shipments(db, sid_a) == 0
    assert _shipments(db, sid_b) == 0
    assert _detail(admin_client, sid_a)["shipment_status"] is None
    assert _detail(admin_client, sid_b)["shipment_status"] == "production_running"


def test_book_ship_pay_reject_missing_invoice(admin_client, db):
    cid = _company(admin_client, db, name="IntMissing")
    sid = _sale(admin_client, cid, "PI-INT-M")

    r = admin_client.post("/api/sales/999999/invoices/999999/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 404, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/999999/ship",
                          json={"actual_ship_date": "2026-11-02"})
    assert r.status_code == 404, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/999999/pay",
                          json={"payment_amount": 10, "payment_date": "2026-11-03"})
    assert r.status_code == 404, r.get_json()
    r = admin_client.delete(f"/api/sales/{sid}/invoices/999999")
    assert r.status_code == 404, r.get_json()


def test_partial_pay_returns_actual_status(admin_client, db):
    cid = _company(admin_client, db, name="IntHonest")
    sid = _sale(admin_client, cid, "PI-INT-H", qty=40)  # 40 x 5 = 200
    inv = _invoice(admin_client, sid, "INV-INT-H")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-11-04"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "planned"
    assert _status(db, inv) == "planned"

    db.execute("UPDATE invoices SET status = 'produced' WHERE id = %s", (inv,))
    db.commit()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-11-05"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "produced"
    assert _status(db, inv) == "produced"

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-10"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-11-06"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "booked"
    assert _status(db, inv) == "booked"

    # Full coverage flips and reports paid.
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-11-07"})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "paid"
    assert _status(db, inv) == "paid"


def test_book_on_shipped_refused_and_sale_untouched(admin_client, db):
    cid = _company(admin_client, db, name="IntBookShip")
    sid = _sale(admin_client, cid, "PI-INT-BS")
    inv = _invoice(admin_client, sid, "INV-INT-BS")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-11-08"})
    assert r.status_code == 201, r.get_json()
    before = _detail(admin_client, sid)["shipment_status"]

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-09"})
    assert r.status_code == 409, r.get_json()
    assert "invoice already shipped" in r.get_json()["error"]

    assert _status(db, inv) == "shipped"
    assert _detail(admin_client, sid)["shipment_status"] == before


def test_produce_on_paid_refused(admin_client, db):
    cid = _company(admin_client, db, name="IntProdPaid")
    sid = _sale(admin_client, cid, "PI-INT-PP")
    r = admin_client.post("/api/chemicals",
                          json={"name": "Chem-IntPP", "qty": 500, "unit": "KG"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post("/api/recipes", json={
        "name": "Recipe-IntPP", "yield": 100, "company_id": cid,
        "product_name": "ProdAmt"})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post("/api/recipes/Recipe-IntPP/items", json={
        "chemical": "Chem-IntPP", "percentage": 40, "company_id": cid})
    assert r.status_code == 200, r.get_json()
    inv = _invoice(admin_client, sid, "INV-INT-PP")
    _pay(admin_client, sid, inv, 50)
    assert _status(db, inv) == "paid"

    r = admin_client.post("/api/recipes/Recipe-IntPP/produce", json={
        "production_qty": 5, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 409, r.get_json()
    assert "invoice already paid" in r.get_json()["error"]
    assert _status(db, inv) == "paid"


def test_second_invoice_derives_the_remainder(admin_client, db):
    """No amount to state: invoice 2 IS the PI total minus invoice 1's lines."""
    cid = _company(admin_client, db, name="IntAmtReq")
    sid = _sale(admin_client, cid, "PI-INT-AR", qty=40)  # 40 x 5 = 200

    first = _invoice(admin_client, sid, "INV-INT-AR1")
    assert _header(db, first) == Decimal("200.00")
    _trim_invoice(db, first, _sale_item_ids(db, sid)[0], 20)   # -> 100

    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-INT-AR2"})
    assert r.status_code == 201, r.get_json()
    second = r.get_json()["invoice_id"]
    assert r.get_json()["amount"] == pytest.approx(100.0)

    assert _header(db, second) == Decimal("100.00")
    assert _lines_total(db, second) == Decimal("100.00")
    assert (_header(db, second)
            == _pi_total(db, sid) - _lines_total(db, first))
    assert _row(admin_client, sid, second)["amount"] == pytest.approx(100.0)
    assert len(_rows(admin_client, sid)) == 2


def test_derived_invoices_never_overstate_pi_for_awkward_money(admin_client, db):
    """Half-KG quantities + 2dp prices: the PI total and the line sum differ.

    2.5 x 33.33 = 83.325 and 1.5 x 11.11 = 16.665, so per-line 2dp rounding
    gives 83.33 + 16.67 = 100.00 against a PI total of 99.99. The old
    "no-overstatement" cap rejected a creation like this outright (it fired on
    7.7% of legitimate creates); the derived amount must instead absorb the
    cent, so the sale can never be invoiced above its own PI.
    """
    cid = _company(admin_client, db, name="IntCap")
    sid = _sale_lines(admin_client, cid, "PI-INT-CAP", [
        {"product_name": "AwkA", "quantity": 2.5, "unit_price": 33.33,
         "unit": "KG"},
        {"product_name": "AwkB", "quantity": 1.5, "unit_price": 11.11,
         "unit": "KG"}])
    total = _pi_total(db, sid)
    assert total == Decimal("99.99")

    first = _invoice(admin_client, sid, "INV-INT-CAP1")
    assert _header(db, first) == _lines_total(db, first)
    assert _header(db, first) <= total

    # A follow-up invoice over the rounding remainder must not tip it over.
    _invoice(admin_client, sid, "INV-INT-CAP2")
    summed = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM invoices WHERE sale_id = %s",
        (sid,)).fetchone()[0]
    assert summed <= total, f"invoiced {summed} > PI total {total}"
    assert summed == Decimal("99.99"), "the derived amount must absorb the cent"
    assert len(_rows(admin_client, sid)) == 2


def test_null_amount_legacy_does_not_block_next_derived_create(admin_client, db):
    """A legacy NULL header is unverifiable: it must neither block nor skew the
    next derived amount, which comes from the LINES, not from the headers."""
    cid = _company(admin_client, db, name="IntNullSkip")
    sid = _sale(admin_client, cid, "PI-INT-NS", qty=20)  # 20 x 5 = 100
    first = _invoice(admin_client, sid, "INV-INT-NS1")   # seeds 100
    _trim_invoice(db, first, _sale_item_ids(db, sid)[0], 6)   # -> 30
    db.execute("UPDATE invoices SET amount = NULL WHERE id = %s", (first,))
    db.commit()

    second = _invoice(admin_client, sid, "INV-INT-NS2")   # derives 100 - 30
    assert _header(db, second) == Decimal("70.00")
    assert _header(db, second) == _lines_total(db, second)
    assert len(_rows(admin_client, sid)) == 2


def test_void_paid_invoice_keeps_sale_payment_truth(admin_client, db):
    cid = _company(admin_client, db, name="IntVoid")
    sid = _sale(admin_client, cid, "PI-INT-V")  # 10 x 5 = 50
    inv = _invoice(admin_client, sid, "INV-INT-V")
    _pay(admin_client, sid, inv, 50)
    assert _status(db, inv) == "paid"

    r = admin_client.delete(f"/api/sales/{sid}/invoices/{inv}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"success": True}

    assert _rows(admin_client, sid) == []
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE id = %s", (inv,)).fetchone()[0] == 0

    # Payments detach (invoice_id NULL) but stay counted in sale totals.
    rows = db.execute(
        "SELECT invoice_id, payment_amount FROM sale_payments WHERE sale_id = %s",
        (sid,)).fetchall()
    assert len(rows) == 1
    assert rows[0][0] is None
    assert round(float(rows[0][1]), 2) == pytest.approx(50.0)
    detail = _detail(admin_client, sid)
    assert round(float(detail["total_paid"]), 2) == pytest.approx(50.0)
    assert round(float(detail["payment_amount"]), 2) == pytest.approx(50.0)

    audit = db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = %s AND entity_id = %s",
        ("INVOICE_VOID", inv)).fetchone()[0]
    assert audit >= 1


def test_double_pay_sums_without_loss(admin_client, db):
    cid = _company(admin_client, db, name="IntDoublePay")
    sid = _sale(admin_client, cid, "PI-INT-DP", qty=40)  # 40 x 5 = 200
    inv = _invoice(admin_client, sid, "INV-INT-DP")

    _pay(admin_client, sid, inv, 120)
    assert _status(db, inv) == "planned"
    _pay(admin_client, sid, inv, 80)
    assert _status(db, inv) == "paid"

    row = _row(admin_client, sid, inv)
    assert row["paid_amount"] == pytest.approx(200.0)
    assert row["status"] == "paid"


# ---------- 10. derived money: one source of truth ----------

def test_derived_header_equals_sum_of_lines_on_multi_line_pi(admin_client, db):
    """Header == its lines on a MULTI-line PI, exactly (no float drift).

    The single-line case is covered above; more than one line is where per-line
    rounding could otherwise make the header disagree with what the
    invoice-driven report sums.
    """
    cid = _company(admin_client, db, name="AmtMulti")
    sid = _sale_lines(admin_client, cid, "PI-AMT-15", [
        {"product_name": "MultiA", "quantity": 3, "unit_price": 12.5,
         "unit": "KG"},
        {"product_name": "MultiB", "quantity": 2, "unit_price": 7.25,
         "unit": "KG"}])
    assert _pi_total(db, sid) == Decimal("52.00")   # 37.50 + 14.50

    inv = _invoice(admin_client, sid, "INV-AMT-15")
    assert _header(db, inv) == Decimal("52.00")
    assert _lines_total(db, inv) == Decimal("52.00")
    assert _header(db, inv) == _lines_total(db, inv)
    # One seeded line per remaining uninvoiced PI line.
    assert db.execute(
        "SELECT COUNT(*) FROM invoice_items WHERE invoice_id = %s",
        (inv,)).fetchone()[0] == 2

    # A line write restates the header from the lines in the same write: this
    # invoice now carries only MultiA x 1, so the header follows it down.
    _trim_invoice(db, inv, _sale_item_ids(db, sid)[0], 1)
    assert _header(db, inv) == Decimal("12.50")
    assert _lines_total(db, inv) == Decimal("12.50")
    assert _header(db, inv) == _lines_total(db, inv)


def test_deleting_invoiced_pi_line_refused_and_sale_unchanged(admin_client, db):
    """409: the report's grain is the PI line but its money is the invoice line.

    invoice_items.sale_item_id is ON DELETE SET NULL, so deleting an invoiced
    PI line would drop the row out of the grain while its money stayed in the
    sale's total. The refused delete must leave the sale byte-for-byte intact.
    """
    cid = _company(admin_client, db, name="AmtDelGuard")
    sid = _sale_lines(admin_client, cid, "PI-AMT-16", [
        {"product_name": "DelA", "quantity": 4, "unit_price": 25, "unit": "KG"},
        {"product_name": "DelB", "quantity": 2, "unit_price": 15, "unit": "KG"}])
    item_id = _sale_item_ids(db, sid)[0]
    inv = _invoice(admin_client, sid, "INV-AMT-16")
    before_items = _detail(admin_client, sid)["items"]
    before_amount = _header(db, inv)
    assert before_amount == Decimal("130.00")   # 100.00 + 30.00

    r = admin_client.delete(f"/api/sales/{sid}/items/{item_id}")
    assert r.status_code == 409, r.get_json()
    assert "invoice lines" in r.get_json()["error"]

    assert _detail(admin_client, sid)["items"] == before_items
    assert item_id in [i["id"] for i in before_items]
    assert _header(db, inv) == before_amount
    assert _lines_total(db, inv) == before_amount
    assert len(_rows(admin_client, sid)) == 1


def test_editing_invoiced_pi_line_quantity_or_price_refused(admin_client, db):
    """409: the next invoice is sized from the PI total, so it must not move.

    The next invoice's amount is derived from the PI total minus what the
    invoice lines already carry. Re-basing an invoiced PI line's quantity or
    price would size that invoice off a total the invoice set no longer matches,
    while invoice_items.line_total kept the old money.
    """
    cid = _company(admin_client, db, name="AmtEditGuard")
    sid = _sale_lines(admin_client, cid, "PI-AMT-17", [
        {"product_name": "EditA", "quantity": 4, "unit_price": 25, "unit": "KG"}])
    item_id = _sale_item_ids(db, sid)[0]
    inv = _invoice(admin_client, sid, "INV-AMT-17")
    before_amount = _header(db, inv)
    assert before_amount == Decimal("100.00")

    for patch in ({"quantity": 8}, {"unit_price": 30}):
        r = admin_client.put(f"/api/sales/{sid}/items/{item_id}", json=patch)
        assert r.status_code == 409, (patch, r.get_json())
        assert "invoice lines" in r.get_json()["error"]

    # The PI line and its money are untouched, so the next invoice still derives
    # from an unchanged PI total.
    assert _header(db, inv) == before_amount
    assert _lines_total(db, inv) == before_amount
    assert _detail(admin_client, sid)["items"][0]["quantity"] == 4


def test_bulk_update_removing_invoiced_pi_line_refused_with_409(admin_client, db):
    """The bulk PUT maps the same rule to 409, not 400.

    A client has to be able to tell "this line already has money against it"
    from "your payload was invalid", whichever route it used.
    """
    cid = _company(admin_client, db, name="AmtBulkGuard")
    sid = _sale_lines(admin_client, cid, "PI-AMT-18", [
        {"product_name": "BulkA", "quantity": 4, "unit_price": 25, "unit": "KG"},
        {"product_name": "BulkB", "quantity": 2, "unit_price": 15, "unit": "KG"}])
    ids = _sale_item_ids(db, sid)
    inv = _invoice(admin_client, sid, "INV-AMT-18")
    before_amount = _header(db, inv)
    before_items = _detail(admin_client, sid)["items"]

    detail = admin_client.get(f"/api/sales/{sid}").get_json()
    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"client_name": detail["client_name"], "pi_number": detail["pi_number"],
                   "pi_date": detail["pi_date"], "company_id": cid},
        "items": [{"id": ids[1], "product_name": "BulkB", "quantity": 2,
                   "unit_price": 15, "unit": "KG"}],
        "removedIds": [ids[0]],
    })
    assert r.status_code == 409, r.get_json()
    assert "invoice lines" in r.get_json()["error"]
    assert _detail(admin_client, sid)["items"] == before_items
    assert _header(db, inv) == before_amount


def test_concurrent_creates_cannot_over_invoice_the_sale(admin_client, db, pg_dsn):
    """Best-effort invariant check under real concurrency, NOT a race reproducer.

    `log_audit_action` defaults to atomic=True, which COMMITs. Calling it per
    seeded line therefore committed on the first line, dropping the sales and
    sale_items locks before the remaining lines were inserted; a concurrent
    create could then derive the same remainder and the sale was invoiced to
    162% of its PI. Whether the interleaving happens depends on timing, so this
    asserts the invariant the lock exists to protect - the sale is never billed
    past its PI and no invoice header disagrees with its own lines - while
    `test_seeded_line_audit_never_commits_mid_seed` is the deterministic guard
    that actually pins the cause.
    """
    import threading

    from raas_tracker.sales import create_invoice
    from chem_stock import get_connection

    cid = _company(admin_client, db, name="AmtConcurrent")
    sid = _sale_lines(admin_client, cid, "PI-AMT-19", [
        {"product_name": "Conc1", "quantity": 5, "unit_price": 25, "unit": "KG"},
        {"product_name": "Conc2", "quantity": 3, "unit_price": 40, "unit": "KG"},
        {"product_name": "Conc3", "quantity": 8, "unit_price": 12.5, "unit": "KG"},
        {"product_name": "Conc4", "quantity": 2, "unit_price": 99.99, "unit": "KG"},
])
    pi_total = _pi_total(db, sid)
    assert pi_total == Decimal("544.98")   # 125 + 120 + 100 + 199.98

    errors = []

    def _create(number):
        conn = get_connection(pg_dsn)
        try:
            create_invoice(conn, sid, number)
        except Exception as e:            # noqa: BLE001 - recorded, asserted below
            errors.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=_create, args=(f"INV-RACE-{i}",))
               for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Whatever interleaving occurred, the sale can never be billed past its PI,
    # and every created invoice is fully backed by its own lines.
    billed = db.execute(
        "SELECT COALESCE(SUM(line_total), 0) FROM invoice_items ii "
        "JOIN invoices i ON i.id = ii.invoice_id WHERE i.sale_id = %s",
        (sid,)).fetchone()[0]
    assert billed <= pi_total, f"over-invoiced: billed {billed} > PI {pi_total}"

    rows = db.execute(
        "SELECT i.id, i.amount, "
        "  (SELECT COALESCE(SUM(line_total), 0) FROM invoice_items WHERE invoice_id = i.id) "
        "FROM invoices i WHERE i.sale_id = %s", (sid,)).fetchall()
    assert rows, "at least one invoice must have been created"
    for invoice_id, amount, lines in rows:
        assert Decimal(str(amount or 0)) == Decimal(str(lines)), (
            f"invoice {invoice_id} header {amount} != its lines {lines}")


def test_seeded_line_audit_never_commits_mid_seed(admin_client, db, monkeypatch):
    """Deterministic guard for the race the threaded test only sometimes wins.

    `log_audit_action` commits when `atomic=True`. A commit inside
    `_seed_invoice_lines` releases the `sales` and `sale_items` row locks the
    seed depends on, so a concurrent create derives the same remainder and bills
    the sale twice - and a mid-seed failure would persist a header that no line
    backs. Asserting the call is non-atomic is deterministic; provoking the race
    is not.
    """
    from raas_tracker import sales as salesmod

    cid = _company(admin_client, db, name="AmtAuditAtomic")
    sid = _sale_lines(admin_client, cid, "PI-AMT-20", [
        {"product_name": "Aud1", "quantity": 4, "unit_price": 25, "unit": "KG"},
        {"product_name": "Aud2", "quantity": 3, "unit_price": 40, "unit": "KG"},
    ])
    seen = []
    real = salesmod.log_audit_action

    def spy(conn, action, *args, **kwargs):
        seen.append((action, kwargs.get("atomic")))
        return real(conn, action, *args, **kwargs)

    monkeypatch.setattr(salesmod, "log_audit_action", spy)
    salesmod.create_invoice(db, sid, "INV-AMT-20")

    seeded = [flag for action, flag in seen if action == "INVOICE_ITEM_ADD"]
    assert len(seeded) == 2, f"both seeded lines must be audited: {seen}"
    assert all(flag is False for flag in seeded), (
        "seeding must not commit mid-transaction; it would release the row locks"
        f" the derivation depends on (saw atomic={seeded})")


def test_bulk_update_rebasing_an_invoiced_pi_line_refused(admin_client, db):
    """The bulk PUT's in-place item rewrite needs the same 409 guard as removals.

    `removedIds` was guarded but `items[].id` was not, so the full-edit form could
    raise an invoiced line's price or cut its quantity: the PI total (which the
    NEXT invoice is derived from) moved while invoice_items.line_total kept the
    old money, leaving a phantom receivable that can never be billed.
    """
    cid = _company(admin_client, db, name="AmtBulkRebase")
    sid = _sale_lines(admin_client, cid, "PI-AMT-21", [
        {"product_name": "Reb1", "quantity": 4, "unit_price": 25, "unit": "KG"},
        {"product_name": "Reb2", "quantity": 2, "unit_price": 15, "unit": "KG"}])
    ids = _sale_item_ids(db, sid)
    inv = _invoice(admin_client, sid, "INV-AMT-21")
    before_amount = _header(db, inv)
    before_items = _detail(admin_client, sid)["items"]

    detail = admin_client.get(f"/api/sales/{sid}").get_json()
    base_header = {"client_name": detail["client_name"], "pi_number": detail["pi_number"],
                   "pi_date": detail["pi_date"], "company_id": cid}
    # Untouched line + the invoiced line re-priced -> refused.
    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": base_header,
        "items": [{"id": ids[1], "product_name": "Reb2", "quantity": 2,
                   "unit_price": 15, "unit": "KG"},
                  {"id": ids[0], "product_name": "Reb1", "quantity": 4,
                   "unit_price": 99, "unit": "KG"}],
        "removedIds": [],
    })
    assert r.status_code == 409, r.get_json()
    assert "invoice lines" in r.get_json()["error"]

    # A descriptive-only change to the same invoiced line stays allowed.
    ok = admin_client.put(f"/api/sales/{sid}", json={
        "header": base_header,
        "items": [{"id": ids[0], "product_name": "Reb1 renamed", "quantity": 4,
                   "unit_price": 25, "unit": "KG"},
                  {"id": ids[1], "product_name": "Reb2", "quantity": 2,
                   "unit_price": 15, "unit": "KG"}],
        "removedIds": [],
    })
    assert ok.status_code == 200, ok.get_json()
    assert _header(db, inv) == before_amount, "money must be untouched"
    assert _detail(admin_client, sid)["items"][0]["product_name"] == "Reb1 renamed"
