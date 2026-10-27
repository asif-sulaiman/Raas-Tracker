"""Phase 1: every invoice carries its own maturity date.

At-sight LCs mature on the invoice's actual ship date; deferred LCs add their
tenor. The operator may override the computed date, but never with a date
earlier than the actual shipment. The override is audited.
"""
import uuid


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, product="MaturityProd", qty=10, price=5):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product, "quantity": qty,
                   "unit_price": price, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _invoice(admin_client, db, sale_id, number):
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in db.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json={
        "invoice_number": number, "invoice_date": "2026-01-15",
        "lines": lines})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _link_lc(admin_client, company_id, sale_id, payment_terms=None,
             tenor_days=None):
    body = {"company_id": company_id, "lc_number": f"LC-M-{_tag()}"}
    if payment_terms is not None:
        body["payment_terms"] = payment_terms
    if tenor_days is not None:
        body["tenor_days"] = tenor_days
    r = admin_client.post("/api/lcs", json=body)
    assert r.status_code == 201, r.get_json()
    lc = r.get_json()
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                          json={"sale_ids": [sale_id]})
    assert r.status_code == 200, r.get_json()
    return lc


def _book(admin_client, sale_id, invoice_id, date="2026-11-01"):
    r = admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/book",
        json={"approx_ship_date": date})
    assert r.status_code == 200, r.get_json()
    return r


def _ship(admin_client, sale_id, invoice_id, actual="2026-11-10", **extra):
    return admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/ship",
        json={"actual_ship_date": actual, **extra})


def _maturity(db, invoice_id):
    return db.execute(
        "SELECT maturity_date FROM invoices WHERE id = %s",
        (invoice_id,)).fetchone()[0]


def _status(db, invoice_id):
    return db.execute(
        "SELECT status FROM invoices WHERE id = %s",
        (invoice_id,)).fetchone()[0]


def _shipment_count(db, sale_id):
    return db.execute(
        "SELECT COUNT(*) FROM shipments WHERE sale_id = %s",
        (sale_id,)).fetchone()[0]


def _ready(admin_client, invoice_id):
    r = admin_client.get(f"/api/invoices/{invoice_id}/readiness")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _shipped_invoice(admin_client, db, produce_for, *, terms=None, tenor=None,
                     product="MaturityProd", actual="2026-11-10", **ship_extra):
    cid = _company(admin_client, db, f"Maturity Co {_tag()}")
    sid = _sale(admin_client, cid, f"PI-M-{_tag()}", product=product)
    inv = _invoice(admin_client, db, sid, f"INV-M-{_tag()}")
    if terms is not None:
        _link_lc(admin_client, cid, sid, payment_terms=terms,
                 tenor_days=tenor)
    produce_for(cid, sid, inv, _tag(), product)
    _book(admin_client, sid, inv)
    r = _ship(admin_client, sid, inv, actual=actual, **ship_extra)
    return cid, sid, inv, r


def test_at_sight_maturity_is_the_actual_ship_date(
        admin_client, db, produce_for):
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, terms="at_sight")
    assert r.status_code == 201, r.get_json()
    assert _maturity(db, inv) == "2026-11-10"

    rows = admin_client.get(f"/api/sales/{sid}/invoices").get_json()
    row = next(i for i in rows if i["invoice_id"] == inv)
    assert row["actual_ship_date"] == "2026-11-10"
    assert row["maturity_date"] == "2026-11-10"

    ready = _ready(admin_client, inv)
    assert ready["maturity_date"] == "2026-11-10"
    assert ready["payment_terms"] == "at_sight"
    assert ready["tenor_days"] is None
    assert ready["can_pay"] is True


def test_deferred_90_adds_the_tenor_to_the_ship_date(
        admin_client, db, produce_for):
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, terms="deferred", tenor=90)
    assert r.status_code == 201, r.get_json()
    assert _maturity(db, inv) == "2027-02-08"

    ready = _ready(admin_client, inv)
    assert ready["payment_terms"] == "deferred"
    assert ready["tenor_days"] == 90
    assert ready["maturity_date"] == "2027-02-08"


def test_deferred_date_math_is_leap_safe(admin_client, db, produce_for):
    """2027-12-15 + 90 days crosses 2028-02-29 and lands on 2028-03-14."""
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, terms="deferred", tenor=90,
        actual="2027-12-15")
    assert r.status_code == 201, r.get_json()
    assert _maturity(db, inv) == "2028-03-14"


def test_explicit_override_is_stored_and_audited(
        admin_client, db, produce_for):
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, terms="deferred", tenor=90,
        maturity_date="2027-03-15")
    assert r.status_code == 201, r.get_json()
    assert _maturity(db, inv) == "2027-03-15"

    row = db.execute(
        "SELECT new_value FROM audit_logs WHERE action = 'INVOICE_SHIP' "
        "AND entity_type = 'invoice' AND entity_id = %s "
        "ORDER BY id DESC LIMIT 1", (inv,)).fetchone()
    assert row is not None and "2027-03-15" in row[0]


def test_override_before_the_actual_ship_date_is_refused(
        admin_client, db, produce_for):
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, terms="deferred", tenor=90,
        maturity_date="2026-11-09")
    assert r.status_code == 400, r.get_json()
    assert _status(db, inv) == "booked"
    assert _shipment_count(db, sid) == 0, (
        "a refused maturity must not write a shipment row")
    assert _maturity(db, inv) is None


def test_malformed_maturity_is_a_400_field_error(
        admin_client, db, produce_for):
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, maturity_date="15/11/2026")
    assert r.status_code == 400, r.get_json()
    fields = {d["field"] for d in (r.get_json().get("details") or [])}
    assert "maturity_date" in fields
    assert _status(db, inv) == "booked"


def test_invoice_list_carries_the_lc_terms_for_the_ship_prefill(
        admin_client, db, produce_for):
    """The panel computes the maturity input from the row's own terms."""
    cid, sid, inv, r = _shipped_invoice(
        admin_client, db, produce_for, terms="deferred", tenor=90)
    assert r.status_code == 201, r.get_json()

    rows = admin_client.get(f"/api/sales/{sid}/invoices").get_json()
    row = next(i for i in rows if i["invoice_id"] == inv)
    assert row["payment_terms"] == "deferred"
    assert row["tenor_days"] == 90

    # A sale without an LC still reads as at-sight, never null.
    cid2, sid2, inv2, r2 = _shipped_invoice(admin_client, db, produce_for)
    assert r2.status_code == 201, r2.get_json()
    row2 = next(i for i in admin_client.get(
        f"/api/sales/{sid2}/invoices").get_json() if i["invoice_id"] == inv2)
    assert row2["payment_terms"] == "at_sight"
    assert row2["tenor_days"] is None


def test_a_sale_without_an_lc_reads_as_at_sight(
        admin_client, db, produce_for):
    cid, sid, inv, r = _shipped_invoice(admin_client, db, produce_for)
    assert r.status_code == 201, r.get_json()
    assert _maturity(db, inv) == "2026-11-10"

    ready = _ready(admin_client, inv)
    assert ready["payment_terms"] == "at_sight"
    assert ready["tenor_days"] is None
