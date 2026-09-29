"""Invoice amount + auto-paid rule (invoices.amount NUMERIC(14,2)).

Covers POST invoice with/without amount (default = sale invoice total),
partial vs full payments flipping status to 'paid', amount 0 -> paid at
creation, legacy NULL amount rows, the no-downgrade guard, completion
totals (total_amount / paid_amount), and list row shape (amount + paid_amount).

Patterns/fixtures mirror tests/test_invoices_production.py.
"""
from raas_tracker.sales import _sync_invoice_paid_status


# ---------- helpers (patterns from tests/test_invoices_production.py) ----------

def _company(admin_client, db, name="AmtCo"):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, product="ProdAmt", qty=10):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product, "quantity": qty,
                   "unit_price": 5, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _create_invoice(admin_client, sale_id, number, amount=None):
    payload = {"invoice_number": number}
    if amount is not None:
        payload["amount"] = amount
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json=payload)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _invoice(admin_client, sale_id, number, amount=None):
    return _create_invoice(admin_client, sale_id, number, amount)["invoice_id"]


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


# ---------- 1. create: explicit amount / default amount / validation ----------

def test_create_invoice_with_explicit_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtExplicit")
    sid = _sale(admin_client, cid, "PI-AMT-1", qty=30)  # 30 x 5 = 150

    body = _create_invoice(admin_client, sid, "INV-AMT-1", amount=120.55)
    assert body["amount"] == 120.55
    assert body["status"] == "planned"
    assert body["invoice_id"]

    row = _row(admin_client, sid, body["invoice_id"])
    assert row["amount"] == 120.55
    assert row["paid_amount"] == 0.0
    assert row["status"] == "planned"


def test_create_invoice_defaults_amount_to_sale_invoice_total(admin_client, db):
    cid = _company(admin_client, db, name="AmtDefault")
    sid = _sale(admin_client, cid, "PI-AMT-2")  # 10 x 5 = 50

    body = _create_invoice(admin_client, sid, "INV-AMT-2")
    assert body["amount"] == 50.0
    assert body["status"] == "planned"

    row = _row(admin_client, sid, body["invoice_id"])
    assert row["amount"] == 50.0
    assert row["paid_amount"] == 0.0


def test_create_invoice_rejects_negative_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtNeg")
    sid = _sale(admin_client, cid, "PI-AMT-3")

    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-AMT-3", "amount": -1})
    assert r.status_code == 400
    body = r.get_json()
    assert "error" in body and "details" in body
    assert _rows(admin_client, sid) == []


# ---------- 2. partial payment: status stays, paid_amount reflected ----------

def test_partial_payment_keeps_status_and_reports_paid_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtPartial")
    sid = _sale(admin_client, cid, "PI-AMT-4", qty=30)  # 30 x 5 = 150
    inv = _invoice(admin_client, sid, "INV-AMT-4", amount=100)

    _pay(admin_client, sid, inv, 40)

    assert _status(db, inv) == "planned"
    row = _row(admin_client, sid, inv)
    assert row["amount"] == 100
    assert row["paid_amount"] == 40.0
    assert row["status"] == "planned"


# ---------- 3. reaching the amount flips status to 'paid' ----------

def test_full_payment_flips_status_to_paid(admin_client, db):
    cid = _company(admin_client, db, name="AmtFull")
    sid = _sale(admin_client, cid, "PI-AMT-5", qty=30)  # 30 x 5 = 150
    inv = _invoice(admin_client, sid, "INV-AMT-5", amount=100)

    _pay(admin_client, sid, inv, 60)
    assert _status(db, inv) == "planned", "partial coverage must not flip"

    _pay(admin_client, sid, inv, 40)  # 100 >= 100
    assert _status(db, inv) == "paid"

    row = _row(admin_client, sid, inv)
    assert row["status"] == "paid"
    assert row["paid_amount"] == 100.0


# ---------- 4. amount 0 -> paid at creation ----------

def test_zero_amount_invoice_paid_at_creation(admin_client, db):
    cid = _company(admin_client, db, name="AmtZero")
    sid = _sale(admin_client, cid, "PI-AMT-6")

    body = _create_invoice(admin_client, sid, "INV-AMT-6", amount=0)
    assert body["amount"] == 0
    assert body["status"] == "paid"
    assert _status(db, body["invoice_id"]) == "paid"

    row = _row(admin_client, sid, body["invoice_id"])
    assert row["status"] == "paid"
    assert row["amount"] == 0
    assert row["paid_amount"] == 0.0


# ---------- 5. legacy NULL amount: any payment marks it paid ----------

def test_legacy_null_amount_payment_marks_paid(admin_client, db):
    cid = _company(admin_client, db, name="AmtLegacy")
    sid = _sale(admin_client, cid, "PI-AMT-7", qty=50)  # 50 x 5 = 250

    paid_inv = _invoice(admin_client, sid, "INV-AMT-7", amount=100)
    unpaid_inv = _invoice(admin_client, sid, "INV-AMT-8", amount=100)
    db.execute("UPDATE invoices SET amount = NULL WHERE sale_id = %s", (sid,))
    db.commit()

    # NULL amount with NO payment -> old rule does not fire
    assert _status(db, unpaid_inv) == "planned"

    _pay(admin_client, sid, paid_inv, 10)
    assert _status(db, paid_inv) == "paid", "legacy NULL amount: any payment pays"

    row = _row(admin_client, sid, paid_inv)
    assert row["amount"] is None
    assert row["paid_amount"] == 10.0
    assert row["status"] == "paid"


# ---------- 6. no-downgrade: a paid invoice can never be un-paid ----------

def test_paid_status_never_downgrades(admin_client, db):
    cid = _company(admin_client, db, name="AmtNoDown")
    sid = _sale(admin_client, cid, "PI-AMT-9", qty=30)  # 30 x 5 = 150

    # (a) amount 0 invoice is paid with zero payments -> rule still "met",
    #     but the guarded UPDATE must not touch an already-'paid' row.
    zero_inv = _invoice(admin_client, sid, "INV-AMT-9", amount=0)
    assert _status(db, zero_inv) == "paid"
    assert _sync_invoice_paid_status(db, zero_inv) is False
    assert _status(db, zero_inv) == "paid"

    # (b) fully covered invoice: drop the payments, re-run the sync ->
    #     rule no longer met, so no UPDATE may fire (never un-pays).
    inv = _invoice(admin_client, sid, "INV-AMT-10", amount=100)
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

    a = _invoice(admin_client, sid, "INV-AMT-11", amount=100)
    b = _invoice(admin_client, sid, "INV-AMT-12", amount=50)
    c = _invoice(admin_client, sid, "INV-AMT-13", amount=30)
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
    assert body["total_amount"] == 150      # NULL amount excluded
    assert body["paid_amount"] == 150       # 100 + 20 + 30

    # invoices inside completion carry amount + paid_amount too
    by_id = {i["invoice_id"]: i for i in body["invoices"]}
    assert by_id[a]["amount"] == 100 and by_id[a]["paid_amount"] == 100
    assert by_id[b]["amount"] == 50 and by_id[b]["paid_amount"] == 20
    assert by_id[c]["amount"] is None and by_id[c]["paid_amount"] == 30


# ---------- 8. list row shape ----------

def test_list_rows_carry_amount_and_paid_amount(admin_client, db):
    cid = _company(admin_client, db, name="AmtShape")
    sid = _sale(admin_client, cid, "PI-AMT-14", qty=30)  # 30 x 5 = 150
    inv = _invoice(admin_client, sid, "INV-AMT-14", amount=75)

    rows = _rows(admin_client, sid)
    assert len(rows) == 1
    assert isinstance(rows[0]["amount"], (int, float))
    assert isinstance(rows[0]["paid_amount"], (int, float))
    assert rows[0]["amount"] == 75
    assert rows[0]["paid_amount"] == 0.0

    _pay(admin_client, sid, inv, 25.5)
    row = _row(admin_client, sid, inv)
    assert row["amount"] == 75
    assert row["paid_amount"] == 25.5
    assert row["status"] == "planned"


# ---------- 9. integrity: forward-only, sale scoping, amount caps, void ----------

def _detail(admin_client, sale_id):
    r = admin_client.get(f"/api/sales/{sale_id}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _shipments(db, sale_id):
    return db.execute(
        "SELECT COUNT(*) FROM shipments WHERE sale_id = %s", (sale_id,)).fetchone()[0]


def _payments(db, sale_id):
    return db.execute(
        "SELECT COUNT(*) FROM sale_payments WHERE sale_id = %s", (sale_id,)).fetchone()[0]


def test_ship_on_paid_refused_without_phantom_shipment(admin_client, db):
    cid = _company(admin_client, db, name="IntShipPaid")
    sid = _sale(admin_client, cid, "PI-INT-1")  # 10 x 5 = 50
    inv = _invoice(admin_client, sid, "INV-INT-1", amount=50)
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
    inv = _invoice(admin_client, sid_b, "INV-INT-SC", amount=50)

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
    inv = _invoice(admin_client, sid, "INV-INT-H", amount=200)

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
    inv = _invoice(admin_client, sid, "INV-INT-BS", amount=50)

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
    inv = _invoice(admin_client, sid, "INV-INT-PP", amount=50)
    _pay(admin_client, sid, inv, 50)
    assert _status(db, inv) == "paid"

    r = admin_client.post("/api/recipes/Recipe-IntPP/produce", json={
        "production_qty": 5, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 409, r.get_json()
    assert "invoice already paid" in r.get_json()["error"]
    assert _status(db, inv) == "paid"


def test_second_invoice_without_amount_rejected(admin_client, db):
    cid = _company(admin_client, db, name="IntAmtReq")
    sid = _sale(admin_client, cid, "PI-INT-AR")
    _invoice(admin_client, sid, "INV-INT-AR1")

    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-INT-AR2"})
    assert r.status_code == 400, r.get_json()
    assert "amount" in r.get_json()["error"]
    assert len(_rows(admin_client, sid)) == 1


def test_invoice_amounts_capped_at_sale_total(admin_client, db):
    cid = _company(admin_client, db, name="IntCap")
    sid = _sale(admin_client, cid, "PI-INT-CAP")  # 10 x 5 = 50

    # First invoice alone above the sale total is rejected.
    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-INT-BIG", "amount": 60})
    assert r.status_code == 400, r.get_json()
    assert len(_rows(admin_client, sid)) == 0

    _invoice(admin_client, sid, "INV-INT-C1", amount=30)
    # 30 + 30 > 50 -> rejected.
    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-INT-C2", "amount": 30})
    assert r.status_code == 400, r.get_json()
    assert "exceed" in r.get_json()["error"]
    # Exact fit (30 + 20 == 50) is accepted.
    _invoice(admin_client, sid, "INV-INT-C3", amount=20)
    assert len(_rows(admin_client, sid)) == 2


def test_null_amount_legacy_skips_sum_check(admin_client, db):
    cid = _company(admin_client, db, name="IntNullSkip")
    sid = _sale(admin_client, cid, "PI-INT-NS")  # 10 x 5 = 50
    first = _invoice(admin_client, sid, "INV-INT-NS1", amount=30)
    db.execute("UPDATE invoices SET amount = NULL WHERE id = %s", (first,))
    db.commit()

    # Legacy NULL amount is unverifiable -> no cap rejection.
    _invoice(admin_client, sid, "INV-INT-NS2", amount=50)
    assert len(_rows(admin_client, sid)) == 2


def test_void_paid_invoice_keeps_sale_payment_truth(admin_client, db):
    cid = _company(admin_client, db, name="IntVoid")
    sid = _sale(admin_client, cid, "PI-INT-V")  # 10 x 5 = 50
    inv = _invoice(admin_client, sid, "INV-INT-V", amount=50)
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
    assert round(float(rows[0][1]), 2) == 50.0
    detail = _detail(admin_client, sid)
    assert round(float(detail["total_paid"]), 2) == 50.0
    assert round(float(detail["payment_amount"]), 2) == 50.0

    audit = db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = %s AND entity_id = %s",
        ("INVOICE_VOID", inv)).fetchone()[0]
    assert audit >= 1


def test_double_pay_sums_without_loss(admin_client, db):
    cid = _company(admin_client, db, name="IntDoublePay")
    sid = _sale(admin_client, cid, "PI-INT-DP", qty=40)  # 40 x 5 = 200
    inv = _invoice(admin_client, sid, "INV-INT-DP", amount=200)

    _pay(admin_client, sid, inv, 120)
    assert _status(db, inv) == "planned"
    _pay(admin_client, sid, inv, 80)
    assert _status(db, inv) == "paid"

    row = _row(admin_client, sid, inv)
    assert row["paid_amount"] == 200.0
    assert row["status"] == "paid"
