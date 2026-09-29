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


def _sale(admin_client, company_id, pi, product="ProdAmt"):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product, "quantity": 10,
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
    sid = _sale(admin_client, cid, "PI-AMT-1")  # 10 x 5 = 50

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
    sid = _sale(admin_client, cid, "PI-AMT-4")
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
    sid = _sale(admin_client, cid, "PI-AMT-5")
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
    sid = _sale(admin_client, cid, "PI-AMT-7")

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
    sid = _sale(admin_client, cid, "PI-AMT-9")

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
    sid = _sale(admin_client, cid, "PI-AMT-11")

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
    sid = _sale(admin_client, cid, "PI-AMT-14")
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
