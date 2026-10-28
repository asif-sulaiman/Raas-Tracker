"""Phase 2 of the invoice-maturity plan: per-invoice Payment Due + board rows.

An invoice reaches Payment Due when it gets an **actual ship date**. Booking
proves production, but it is not a shipment: a booked invoice no longer opens
the sale's Payment Due stage. Shipping moves its own sale in the same commit,
and an unshipped sibling invoice on the same sale stays behind.

An LC is a container, not a shipment: it advances only when every non-void
invoice under it has shipped. Until then the LC (and any PI still waiting on an
invoice) stays in Shipment Ongoing, so the board can ask whether more invoices
are coming; once the last invoice ships the LC advances and carries a PI whose
product was never invoiced along with it.
"""


def _company_id(db, name):
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, products):
    """products: [(name, qty, price), ...] — one PI line each."""
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [
            {"product_name": name, "quantity": qty,
             "unit_price": price, "unit": "KG"}
            for name, qty, price in products
        ]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _line_ids(db, sale_id):
    return [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchall()]


def _invoice(admin_client, db, sale_id, number, quantities=None):
    """Create one invoice; ``quantities[i] is None`` skips PI line ``i``."""
    lines = []
    for i, item_id in enumerate(_line_ids(db, sale_id)):
        if quantities is None:
            qty = float(db.execute(
                "SELECT quantity FROM sale_items WHERE id = %s",
                (item_id,)).fetchone()[0])
        else:
            qty = quantities[i]
        if qty is None:
            continue
        lines.append({"sale_item_id": item_id, "quantity": qty})
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json={
        "invoice_number": number, "invoice_date": "2026-01-15", "lines": lines})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _move(admin_client, sale_id):
    return admin_client.post(f"/api/sales/{sale_id}/move", json={})


def _book(admin_client, sale_id, invoice_id, date="2026-11-01"):
    r = admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/book",
        json={"approx_ship_date": date})
    assert r.status_code == 200, r.get_json()
    return r


def _ship(admin_client, sale_id, invoice_id, date="2026-11-10"):
    return admin_client.post(
        f"/api/sales/{sale_id}/invoices/{invoice_id}/ship",
        json={"actual_ship_date": date})


def _stage(db, sale_id):
    return db.execute(
        "SELECT stage FROM sales WHERE id = %s", (sale_id,)).fetchone()[0]


def _ready_invoice(admin_client, db, produce_for, sale_id, number, tag,
                   product):
    """Create, produce and book one invoice; returns its id."""
    inv = _invoice(admin_client, db, sale_id, number)
    produce_for(_company_of(db, sale_id), sale_id, inv, tag, product)
    _book(admin_client, sale_id, inv)
    return inv


def _company_of(db, sale_id):
    return db.execute(
        "SELECT company_id FROM sales WHERE id = %s", (sale_id,)).fetchone()[0]


def _lc(admin_client, company_id, number, sale_ids):
    r = admin_client.post("/api/lcs", json={
        "company_id": company_id, "lc_number": number})
    assert r.status_code in (200, 201), r.get_json()
    lc = r.get_json()
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                          json={"sale_ids": sale_ids})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()
    return lc


def _lc_stage(admin_client, lc_id):
    return admin_client.get(f"/api/lcs/{lc_id}").get_json()["stage"]


# --------------------------------------------------------------------------- #
# Shipping is what opens Payment Due
# --------------------------------------------------------------------------- #
def test_shipping_moves_the_sale_to_payment_due(admin_client, db, produce_for):
    admin_client.post("/api/companies", json={"name": "PD Move Co"})
    cid = _company_id(db, "PD Move Co")
    sid = _sale(admin_client, cid, "PI-PD-MOVE", [("PdMove", 10, 5)])
    assert _move(admin_client, sid).status_code == 200  # lc_received
    assert _move(admin_client, sid).status_code == 200  # shipment_ongoing
    inv = _ready_invoice(admin_client, db, produce_for, sid, "INV-PD-MOVE",
                         "PDMoveTag", "PdMove")

    r = _ship(admin_client, sid, inv)
    assert r.status_code == 201, r.get_json()
    assert _stage(db, sid) == "payment_due"

    row = db.execute(
        "SELECT to_stage, notes FROM sales_stage_history WHERE sale_id = %s "
        "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    assert row[0] == "payment_due"
    assert "INV-PD-MOVE" in row[1]
    audit = db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'SALE_MOVE' "
        "AND entity_type = 'sale' AND entity_id = %s "
        "AND new_value = 'payment_due'", (sid,)).fetchone()[0]
    assert audit == 1


def test_a_booked_invoice_alone_is_not_enough(admin_client, db, produce_for):
    admin_client.post("/api/companies", json={"name": "PD Booked Co"})
    cid = _company_id(db, "PD Booked Co")
    sid = _sale(admin_client, cid, "PI-PD-BOOKED", [("PdBooked", 10, 5)])
    assert _move(admin_client, sid).status_code == 200
    assert _move(admin_client, sid).status_code == 200
    _ready_invoice(admin_client, db, produce_for, sid, "INV-PD-BOOKED",
                   "PDBookedTag", "PdBooked")

    r = _move(admin_client, sid)
    assert r.status_code == 409, r.get_json()
    assert "ship" in r.get_json()["error"].lower()
    assert _stage(db, sid) == "shipment_ongoing"


def test_an_unshipped_sibling_invoice_stays_behind(admin_client, db,
                                                   produce_for):
    admin_client.post("/api/companies", json={"name": "PD Sibling Co"})
    cid = _company_id(db, "PD Sibling Co")
    sid = _sale(admin_client, cid, "PI-PD-SIB",
                [("PdSibA", 10, 5), ("PdSibB", 4, 5)])
    assert _move(admin_client, sid).status_code == 200
    assert _move(admin_client, sid).status_code == 200
    # One invoice per PI line so only A ships.
    inv_a = _invoice(admin_client, db, sid, "INV-PD-SIB-A",
                     quantities=[10, None])
    inv_b = _invoice(admin_client, db, sid, "INV-PD-SIB-B",
                     quantities=[None, 4])
    produce_for(cid, sid, inv_a, "PdSibATag", "PdSibA")
    produce_for(cid, sid, inv_b, "PdSibBTag", "PdSibB")
    _book(admin_client, sid, inv_a)
    _book(admin_client, sid, inv_b)

    assert _ship(admin_client, sid, inv_a).status_code == 201
    assert _stage(db, sid) == "payment_due"
    statuses = dict(db.execute(
        "SELECT invoice_number, status FROM invoices WHERE sale_id = %s",
        (sid,)).fetchall())
    assert statuses["INV-PD-SIB-A"] == "shipped"
    assert statuses["INV-PD-SIB-B"] == "booked", (
        "the sibling invoice must not travel with the shipped one")


# --------------------------------------------------------------------------- #
# An LC advances only when every invoice under it has shipped
# --------------------------------------------------------------------------- #
def test_lc_readiness_reports_shipped_and_all_shipped(admin_client, db,
                                                     produce_for):
    admin_client.post("/api/companies", json={"name": "PD Ready Co"})
    cid = _company_id(db, "PD Ready Co")
    s1 = _sale(admin_client, cid, "PI-PD-R1", [("PdReady1", 10, 5)])
    s2 = _sale(admin_client, cid, "PI-PD-R2", [("PdReady2", 10, 5)])
    lc = _lc(admin_client, cid, "LC-PD-READY", [s1, s2])
    inv1 = _ready_invoice(admin_client, db, produce_for, s1, "INV-PD-R1",
                          "PdReady1Tag", "PdReady1")
    inv2 = _ready_invoice(admin_client, db, produce_for, s2, "INV-PD-R2",
                          "PdReady2Tag", "PdReady2")

    out = admin_client.get(f"/api/lcs/{lc['id']}/readiness").get_json()
    assert out["shipped_ok"] is False
    assert out["all_shipped_ok"] is False

    assert _ship(admin_client, s1, inv1).status_code == 201
    out = admin_client.get(f"/api/lcs/{lc['id']}/readiness").get_json()
    assert out["shipped_ok"] is True
    assert out["all_shipped_ok"] is False, (
        "one shipped invoice must not advance an LC that still has one unshipped")

    assert _ship(admin_client, s2, inv2).status_code == 201
    out = admin_client.get(f"/api/lcs/{lc['id']}/readiness").get_json()
    assert out["all_shipped_ok"] is True


def test_lc_advances_only_after_every_invoice_ships(admin_client, db,
                                                    produce_for):
    admin_client.post("/api/companies", json={"name": "PD LcGate Co"})
    cid = _company_id(db, "PD LcGate Co")
    s1 = _sale(admin_client, cid, "PI-PD-G1", [("PdGate1", 10, 5)])
    s2 = _sale(admin_client, cid, "PI-PD-G2", [("PdGate2", 10, 5)])
    lc = _lc(admin_client, cid, "LC-PD-GATE", [s1, s2])
    inv1 = _ready_invoice(admin_client, db, produce_for, s1, "INV-PD-G1",
                          "PdGate1Tag", "PdGate1")
    inv2 = _ready_invoice(admin_client, db, produce_for, s2, "INV-PD-G2",
                          "PdGate2Tag", "PdGate2")

    assert _ship(admin_client, s1, inv1).status_code == 201
    assert _stage(db, s1) == "payment_due"
    assert _stage(db, s2) == "shipment_ongoing"
    assert _lc_stage(admin_client, lc["id"]) == "shipment_ongoing"

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body["shipped_ok"] is True
    assert body["all_shipped_ok"] is False
    assert "ship" in body["error"].lower()
    assert _lc_stage(admin_client, lc["id"]) == "shipment_ongoing"

    assert _ship(admin_client, s2, inv2).status_code == 201
    assert _lc_stage(admin_client, lc["id"]) == "payment_due"
    assert _stage(db, s2) == "payment_due"


def test_lc_advance_carries_a_pi_that_was_never_invoiced(admin_client, db,
                                                         produce_for):
    admin_client.post("/api/companies", json={"name": "PD LcCarry Co"})
    cid = _company_id(db, "PD LcCarry Co")
    s1 = _sale(admin_client, cid, "PI-PD-C1", [("PdCarry1", 10, 5)])
    s2 = _sale(admin_client, cid, "PI-PD-C2", [("PdCarry2", 7, 5)])
    lc = _lc(admin_client, cid, "LC-PD-CARRY", [s1, s2])
    # Only s1 is invoiced; s2 waits for an invoice the operator may never make.
    inv1 = _ready_invoice(admin_client, db, produce_for, s1, "INV-PD-C1",
                          "PdCarry1Tag", "PdCarry1")

    assert _ship(admin_client, s1, inv1).status_code == 201
    assert _lc_stage(admin_client, lc["id"]) == "payment_due", (
        "the last invoice under the LC shipped, so the LC advances")
    assert _stage(db, s2) == "payment_due", (
        "an uninvoiced PI travels with its LC once every invoice has shipped")

    rows = admin_client.get("/api/sales").get_json()["sales"]
    leftover = next(r for r in rows if r["id"] == s2)
    assert leftover["has_uninvoiced_remainder"] is True, (
        "the board must be able to ask whether more invoices are coming")
    shipped = next(r for r in rows if r["id"] == s1)
    assert shipped["has_uninvoiced_remainder"] is False


# --------------------------------------------------------------------------- #
# The board list carries each sale's invoices
# --------------------------------------------------------------------------- #
def test_sales_list_attaches_invoice_summaries(admin_client, db, produce_for):
    admin_client.post("/api/companies", json={"name": "PD List Co"})
    cid = _company_id(db, "PD List Co")
    sid = _sale(admin_client, cid, "PI-PD-LIST", [("PdListA", 10, 5),
                                                  ("PdListB", 4, 5)])
    assert _move(admin_client, sid).status_code == 200
    assert _move(admin_client, sid).status_code == 200
    inv_a = _invoice(admin_client, db, sid, "INV-PD-LIST-A",
                     quantities=[6, None])
    produce_for(cid, sid, inv_a, "PdListATag", "PdListA")
    _book(admin_client, sid, inv_a)
    assert _ship(admin_client, sid, inv_a).status_code == 201

    row = next(r for r in admin_client.get("/api/sales").get_json()["sales"]
               if r["id"] == sid)
    assert [i["invoice_number"] for i in row["invoices"]] == ["INV-PD-LIST-A"]
    inv = row["invoices"][0]
    assert inv["status"] == "shipped"
    assert inv["amount"] == 30.0
    assert inv["paid_amount"] == 0.0
    assert inv["maturity_date"] == "2026-11-10", "at sight matures on the ship date"
    assert inv["actual_ship_date"] == "2026-11-10"
    # 6 of 10 A and none of the 4 B are invoiced.
    assert row["has_uninvoiced_remainder"] is True


def test_sales_list_summary_carries_lc_terms_for_the_ship_form(
        admin_client, db, produce_for):
    """The board's Ship form prefills deferred maturity from the summary."""
    admin_client.post("/api/companies", json={"name": "PD Terms Co"})
    cid = _company_id(db, "PD Terms Co")
    sid = _sale(admin_client, cid, "PI-PD-TERMS", [("PdTerms", 10, 5)])
    r = admin_client.post("/api/lcs", json={
        "company_id": cid, "lc_number": "LC-PD-TERMS",
        "payment_terms": "deferred", "tenor_days": 90})
    assert r.status_code in (200, 201), r.get_json()
    lc_id = r.get_json()["id"]
    assert admin_client.post(f"/api/lcs/{lc_id}/pis",
                             json={"sale_ids": [sid]}).status_code == 200
    assert admin_client.post(
        f"/api/lcs/{lc_id}/move",
        json={"new_stage": "shipment_ongoing"}).status_code == 200
    inv = _ready_invoice(admin_client, db, produce_for, sid, "INV-PD-TERMS",
                         "PDTermsTag", "PdTerms")
    assert _ship(admin_client, sid, inv).status_code == 201

    row = next(r for r in admin_client.get("/api/sales").get_json()["sales"]
               if r["id"] == sid)
    summary = row["invoices"][0]
    assert summary["payment_terms"] == "deferred"
    assert summary["tenor_days"] == 90
    # 2026-11-10 + 90 days.
    assert summary["maturity_date"] == "2027-02-08"


def test_a_sale_without_an_lc_advances_on_its_own_ship(admin_client, db,
                                                       produce_for):
    admin_client.post("/api/companies", json={"name": "PD Solo Co"})
    cid = _company_id(db, "PD Solo Co")
    sid = _sale(admin_client, cid, "PI-PD-SOLO", [("PdSolo", 10, 5)])
    assert _move(admin_client, sid).status_code == 200
    assert _move(admin_client, sid).status_code == 200
    inv = _ready_invoice(admin_client, db, produce_for, sid, "INV-PD-SOLO",
                         "PdSoloTag", "PdSolo")

    assert _ship(admin_client, sid, inv).status_code == 201
    assert _stage(db, sid) == "payment_due"
