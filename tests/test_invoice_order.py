"""Phase 2 of the shipment-first pipeline: strict Book -> Ship -> Pay.

An invoice created in Shipment Ongoing matures independently:

  planned -> produced -> booked -> shipped -> paid

Shipping REQUIRES ``booked`` (booking is where production is proven), and
paying REQUIRES ``shipped``. A refusal writes nothing: no shipment row, no
payment row, no status move.
"""


# ---------- helpers ----------

def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, product="OrderProd", qty=10, price=5):
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


def _status(db, invoice_id):
    return db.execute(
        "SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


def _shipments(db, sale_id):
    return db.execute(
        "SELECT COUNT(*) FROM shipments WHERE sale_id = %s", (sale_id,)).fetchone()[0]


def _payments(db, sale_id):
    return db.execute(
        "SELECT COUNT(*) FROM sale_payments WHERE sale_id = %s",
        (sale_id,)).fetchone()[0]


def _book(admin_client, sale_id, invoice_id, date="2026-11-01"):
    return admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/book",
        json={"approx_ship_date": date})


def _ship(admin_client, sale_id, invoice_id, date="2026-11-10"):
    return admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/ship",
        json={"actual_ship_date": date})


def _pay(admin_client, sale_id, invoice_id, amount, date="2026-11-20"):
    return admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/pay",
        json={"payment_amount": amount, "payment_date": date})


# ---------- 1. shipping requires booking ----------

def test_ship_refused_for_a_planned_invoice(admin_client, db):
    cid = _company(admin_client, db, "OrderShipPlanCo")
    sid = _sale(admin_client, cid, "PI-ORDER-SP")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-SP")

    r = _ship(admin_client, sid, inv)
    assert r.status_code == 409, r.get_json()
    assert "book" in r.get_json()["error"].lower()
    assert _status(db, inv) == "planned"
    assert _shipments(db, sid) == 0, "a refused ship must not write a shipment row"


def test_ship_refused_for_a_produced_but_unbooked_invoice(
        admin_client, db, produce_for):
    cid = _company(admin_client, db, "OrderShipProdCo")
    sid = _sale(admin_client, cid, "PI-ORDER-SPR", product="OrderProdR")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-SPR")
    produce_for(cid, sid, inv, "OrderShipProd", "OrderProdR")
    assert _status(db, inv) == "produced"

    r = _ship(admin_client, sid, inv)
    assert r.status_code == 409, r.get_json()
    assert "book" in r.get_json()["error"].lower()
    assert _status(db, inv) == "produced"
    assert _shipments(db, sid) == 0


def test_book_then_ship_succeeds_and_stamps_the_invoice_date(
        admin_client, db, produce_for):
    cid = _company(admin_client, db, "OrderShipOkCo")
    sid = _sale(admin_client, cid, "PI-ORDER-OK", product="OrderProdOk")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-OK")
    produce_for(cid, sid, inv, "OrderShipOk", "OrderProdOk")

    r = _book(admin_client, sid, inv)
    assert r.status_code == 200, r.get_json()
    r = _ship(admin_client, sid, inv)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "shipped"
    assert _status(db, inv) == "shipped"

    row = db.execute(
        "SELECT invoice_date, invoice_number FROM shipments WHERE sale_id = %s",
        (sid,)).fetchone()
    assert row is not None
    assert row[0] == "2026-01-15", "the shipment must carry the invoice's own date"
    assert row[1] == "INV-ORDER-OK"


# ---------- 2. paying requires shipping ----------

def test_pay_refused_for_a_booked_invoice(admin_client, db, produce_for):
    cid = _company(admin_client, db, "OrderPayBookedCo")
    sid = _sale(admin_client, cid, "PI-ORDER-PB", product="OrderProdPB")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-PB")
    produce_for(cid, sid, inv, "OrderPayBooked", "OrderProdPB")
    assert _book(admin_client, sid, inv).status_code == 200

    r = _pay(admin_client, sid, inv, 10)
    assert r.status_code == 409, r.get_json()
    assert "ship" in r.get_json()["error"].lower()
    assert _status(db, inv) == "booked"
    assert _payments(db, sid) == 0, "a refused pay must not write a payment row"


def test_pay_refused_for_a_planned_invoice(admin_client, db):
    cid = _company(admin_client, db, "OrderPayPlanCo")
    sid = _sale(admin_client, cid, "PI-ORDER-PP")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-PP")

    r = _pay(admin_client, sid, inv, 10)
    assert r.status_code == 409, r.get_json()
    assert "ship" in r.get_json()["error"].lower()
    assert _status(db, inv) == "planned"
    assert _payments(db, sid) == 0


def test_partial_payments_keep_the_invoice_shipped_until_covered(
        admin_client, db, produce_for):
    """Partial money is recorded; the status only moves once it is covered.

    10 x 5 = 50. Two partial payments report 'shipped'; the third covers the
    invoice and reports 'paid'.
    """
    cid = _company(admin_client, db, "OrderPayPartCo")
    sid = _sale(admin_client, cid, "PI-ORDER-PART", product="OrderProdPart")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-PART")
    produce_for(cid, sid, inv, "OrderPayPart", "OrderProdPart")
    assert _book(admin_client, sid, inv).status_code == 200
    assert _ship(admin_client, sid, inv).status_code == 201

    for amount in (10, 10):
        r = _pay(admin_client, sid, inv, amount)
        assert r.status_code == 201, r.get_json()
        assert r.get_json()["status"] == "shipped"
        assert _status(db, inv) == "shipped"

    r = _pay(admin_client, sid, inv, 30)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "paid"
    assert _status(db, inv) == "paid"
    assert _payments(db, sid) == 3


def test_pay_refused_once_the_invoice_is_paid(admin_client, db, produce_for):
    cid = _company(admin_client, db, "OrderPayPaidCo")
    sid = _sale(admin_client, cid, "PI-ORDER-PAID", product="OrderProdPaid")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-PAID")
    produce_for(cid, sid, inv, "OrderPayPaid", "OrderProdPaid")
    assert _book(admin_client, sid, inv).status_code == 200
    assert _ship(admin_client, sid, inv).status_code == 201
    assert _pay(admin_client, sid, inv, 50).status_code == 201
    assert _status(db, inv) == "paid"

    r = _pay(admin_client, sid, inv, 1)
    assert r.status_code == 409, r.get_json()
    assert "already paid" in r.get_json()["error"]
    assert _payments(db, sid) == 1


# ---------- 3. readiness mirrors the service order ----------

def _readiness(admin_client, invoice_id):
    r = admin_client.get(f"/api/invoices/{invoice_id}/readiness")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_readiness_opens_pay_only_after_shipping(admin_client, db, produce_for):
    cid = _company(admin_client, db, "OrderReadyCo")
    sid = _sale(admin_client, cid, "PI-ORDER-RDY", product="OrderProdRdy")
    inv = _invoice(admin_client, db, sid, "INV-ORDER-RDY")

    body = _readiness(admin_client, inv)
    assert body["can_book"] is False and body["can_ship"] is False
    assert body["can_pay"] is False

    produce_for(cid, sid, inv, "OrderReady", "OrderProdRdy")
    body = _readiness(admin_client, inv)
    assert body["can_book"] is True
    assert body["can_ship"] is False
    assert body["can_pay"] is False

    assert _book(admin_client, sid, inv).status_code == 200
    body = _readiness(admin_client, inv)
    assert body["can_book"] is False
    assert body["can_ship"] is True
    assert body["can_pay"] is False, "booked is not payable any more"

    assert _ship(admin_client, sid, inv).status_code == 201
    body = _readiness(admin_client, inv)
    assert body["can_ship"] is False
    assert body["can_pay"] is True
