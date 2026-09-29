"""Go for Production (S1-S3): invoice lifecycle, shipment_status, production links.

Covers POST/GET invoices, book/ship/pay, sale completion, recipe produce with
sale/invoice links, stage move -> shipment_status, and payload shapes
(list shipment_status, detail item_no/invoices, production-source recipes).
"""


# ---------- helpers (patterns from tests/test_recipes_register.py) ----------

def _company(admin_client, db, name="InvCo"):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, product="ProdInv", item_no=None):
    item = {"product_name": product, "quantity": 10, "unit_price": 5, "unit": "KG"}
    if item_no is not None:
        item["item_no"] = item_no
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [item]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _invoice(admin_client, sale_id, number):
    r = admin_client.post(f"/api/sales/{sale_id}/invoices",
                          json={"invoice_number": number})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _detail(admin_client, sale_id):
    r = admin_client.get(f"/api/sales/{sale_id}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _invoice_status(db, invoice_id):
    return db.execute(
        "SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


# ---------- 1. invoice create + duplicate rules ----------

def test_invoice_create_list_duplicate_and_cross_sale_reuse(admin_client, db):
    cid = _company(admin_client, db, name="DupCo")
    s1 = _sale(admin_client, cid, "PI-DUP-1")
    s2 = _sale(admin_client, cid, "PI-DUP-2")

    inv1 = _invoice(admin_client, s1, "INV-SAME")

    lst = admin_client.get(f"/api/sales/{s1}/invoices").get_json()
    assert len(lst) == 1
    assert lst[0]["invoice_id"] == inv1
    assert lst[0]["invoice_number"] == "INV-SAME"
    assert lst[0]["status"] == "planned"

    # Duplicate invoice number on the SAME sale -> 400 with error
    r = admin_client.post(f"/api/sales/{s1}/invoices",
                          json={"invoice_number": "INV-SAME"})
    assert r.status_code == 400
    body = r.get_json()
    assert "error" in body and "already exists" in body["error"]
    assert len(admin_client.get(f"/api/sales/{s1}/invoices").get_json()) == 1

    # A DIFFERENT sale may reuse the same number
    inv2 = _invoice(admin_client, s2, "INV-SAME")
    assert inv2 != inv1
    lst2 = admin_client.get(f"/api/sales/{s2}/invoices").get_json()
    assert len(lst2) == 1 and lst2[0]["invoice_number"] == "INV-SAME"


# ---------- 2. shipment_status on first invoice, no downgrade ----------

def test_first_invoice_sets_production_running_and_never_downgrades(
        admin_client, db):
    cid = _company(admin_client, db, name="RunCo")

    s_run = _sale(admin_client, cid, "PI-RUN-1")
    assert _detail(admin_client, s_run)["shipment_status"] is None
    _invoice(admin_client, s_run, "INV-RUN-1")
    assert _detail(admin_client, s_run)["shipment_status"] == "production_running"

    s_done = _sale(admin_client, cid, "PI-DONE-1")
    db.execute("UPDATE sales SET shipment_status = 'production_done' "
               "WHERE id = %s", (s_done,))
    db.commit()
    _invoice(admin_client, s_done, "INV-DONE-1")
    assert _detail(admin_client, s_done)["shipment_status"] == "production_done"

    s_book = _sale(admin_client, cid, "PI-BOOK-1")
    db.execute("UPDATE sales SET shipment_status = 'ship_booked' "
               "WHERE id = %s", (s_book,))
    db.commit()
    _invoice(admin_client, s_book, "INV-BOOK-1")
    assert _detail(admin_client, s_book)["shipment_status"] == "ship_booked"


# ---------- 3. book ----------

def test_book_requires_approx_date_and_sets_ship_booked(admin_client, db):
    cid = _company(admin_client, db, name="BookCo")
    sid = _sale(admin_client, cid, "PI-BK-1")
    inv = _invoice(admin_client, sid, "INV-BK-1")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "   "})
    assert r.status_code == 400
    assert "error" in r.get_json()

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "booked"

    assert _detail(admin_client, sid)["shipment_status"] == "ship_booked"
    invs = admin_client.get(f"/api/sales/{sid}/invoices").get_json()
    assert invs[0]["status"] == "booked"
    assert invs[0]["approx_ship_date"] == "2026-10-15"


# ---------- 4. ship ----------

def test_ship_creates_shipment_row_and_marks_shipped(admin_client, db):
    cid = _company(admin_client, db, name="ShipCo")
    sid = _sale(admin_client, cid, "PI-SH-1")
    inv = _invoice(admin_client, sid, "INV-SH-1")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-10-20"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["status"] == "shipped"
    assert body["shipment_id"]

    detail = _detail(admin_client, sid)
    assert len(detail["shipments"]) == 1
    assert detail["shipments"][0]["ship_date"] == "2026-10-20"

    invs = admin_client.get(f"/api/sales/{sid}/invoices").get_json()
    assert invs[0]["status"] == "shipped"
    assert invs[0]["actual_ship_date"] == "2026-10-20"


# ---------- 5. pay ----------

def test_pay_records_payment_and_syncs_sale_totals(admin_client, db):
    cid = _company(admin_client, db, name="PayCo")
    sid = _sale(admin_client, cid, "PI-PAY-1")  # 10 x 5 = 50
    inv = _invoice(admin_client, sid, "INV-PAY-1")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 25.5,
                                "payment_date": "2026-10-25"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["payment_id"]
    assert body["status"] == "paid"

    rows = db.execute(
        "SELECT invoice_id, payment_amount FROM sale_payments "
        "WHERE sale_id = %s", (sid,)).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == inv
    assert round(float(rows[0][1]), 2) == 25.5

    detail = _detail(admin_client, sid)
    assert round(float(detail["total_paid"]), 2) == 25.5
    assert round(float(detail["payment_amount"]), 2) == 25.5
    assert round(float(detail["balance"]), 2) == 24.5


# ---------- 6. completion ----------

def test_completion_reflects_invoice_counts(admin_client, db):
    cid = _company(admin_client, db, name="CompCo")
    sid = _sale(admin_client, cid, "PI-CMP-1")

    r = admin_client.get(f"/api/sales/{sid}/completion")
    assert r.status_code == 200
    assert r.get_json() == {"status": "none", "paid": 0, "total": 0,
                            "invoices": []}

    _invoice(admin_client, sid, "INV-CMP-1")
    _invoice(admin_client, sid, "INV-CMP-2")
    body = admin_client.get(f"/api/sales/{sid}/completion").get_json()
    assert body["total"] == 2
    assert body["paid"] == 0
    assert body["status"] == "none"
    assert len(body["invoices"]) == 2

    # One invoice carries status 'paid' -> completion counts it as partial.
    db.execute("UPDATE invoices SET status = 'paid' "
               "WHERE sale_id = %s AND invoice_number = %s",
               (sid, "INV-CMP-1"))
    db.commit()
    body = admin_client.get(f"/api/sales/{sid}/completion").get_json()
    assert body["total"] == 2
    assert body["paid"] == 1
    assert body["status"] == "partial"


# ---------- 7. produce ----------

def _production_setup(admin_client, db, tag):
    """Company + sale + chemical + registered recipe + one invoice."""
    cid = _company(admin_client, db, name=f"ProdCo-{tag}")
    sid = _sale(admin_client, cid, pi=f"PI-PR-{tag}", product=f"Prod-{tag}")
    r = admin_client.post("/api/chemicals",
                          json={"name": f"Chem-{tag}", "qty": 500, "unit": "KG"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post("/api/recipes", json={
        "name": f"Recipe-{tag}", "yield": 100, "company_id": cid,
        "product_name": f"Prod-{tag}"})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/recipes/Recipe-{tag}/items", json={
        "chemical": f"Chem-{tag}", "percentage": 40, "company_id": cid})
    assert r.status_code == 200, r.get_json()
    inv = _invoice(admin_client, sid, f"INV-PR-{tag}")
    return cid, sid, inv


def test_produce_links_run_invoices_sales_and_new_columns(admin_client, db):
    cid, sid, inv = _production_setup(admin_client, db, "A")

    r = admin_client.post("/api/recipes/Recipe-A/produce", json={
        "production_qty": 5, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv],
        "material_number": "MAT-77", "packing": "25KG drum",
        "invoice_number": "INV-NUM-A"})
    assert r.status_code == 201, r.get_json()
    runs = r.get_json()["runs"]
    assert len(runs) == 1
    run_id = runs[0]["run_id"]
    assert runs[0]["sale_ids"] == [sid]
    assert runs[0]["invoice_ids"] == [inv]

    # invoice status -> produced
    assert _invoice_status(db, inv) == "produced"
    # linked sale advances production_running -> production_done
    assert _detail(admin_client, sid)["shipment_status"] == "production_done"

    # production_run_links rows
    links = db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links "
        "WHERE run_id = %s", (run_id,)).fetchall()
    assert (sid, inv) in [(l[0], l[1]) for l in links]

    # production_runs carries the new columns
    row = db.execute(
        "SELECT material_number, packing, invoice_number "
        "FROM production_runs WHERE id = %s", (run_id,)).fetchone()
    assert (row[0], row[1], row[2]) == ("MAT-77", "25KG drum", "INV-NUM-A")


def test_produce_never_downgrades_ship_booked(admin_client, db):
    cid, sid, inv = _production_setup(admin_client, db, "B")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200
    assert _detail(admin_client, sid)["shipment_status"] == "ship_booked"

    r = admin_client.post("/api/recipes/Recipe-B/produce", json={
        "production_qty": 5, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 201, r.get_json()

    assert _detail(admin_client, sid)["shipment_status"] == "ship_booked"
    assert _invoice_status(db, inv) == "produced"


def test_produce_cross_sale_invoice_uses_invoices_own_sale(admin_client, db):
    cid, sid_a, _ = _production_setup(admin_client, db, "C")
    sid_b = _sale(admin_client, cid, pi="PI-PR-C2", product="Prod-C")
    inv_b = _invoice(admin_client, sid_b, "INV-PR-C2")

    r = admin_client.post("/api/recipes/Recipe-C/produce", json={
        "production_qty": 5, "company_id": cid,
        "sale_ids": [sid_a], "invoice_ids": [inv_b]})
    assert r.status_code == 201, r.get_json()
    run_id = r.get_json()["runs"][0]["run_id"]

    pairs = {(l[0], l[1]) for l in db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links "
        "WHERE run_id = %s", (run_id,)).fetchall()}
    assert (sid_a, None) in pairs          # sale_ids link, no invoice
    assert (sid_b, inv_b) in pairs         # invoice's OWN sale_id
    assert (sid_a, inv_b) not in pairs
    assert _invoice_status(db, inv_b) == "produced"
    assert _detail(admin_client, sid_b)["shipment_status"] == "production_done"


# ---------- 8. stage move ----------

def test_move_to_shipment_ongoing_sets_production_running(admin_client, db):
    cid = _company(admin_client, db, name="MoveCo")
    sid = _sale(admin_client, cid, "PI-MOVE-1")
    assert _detail(admin_client, sid)["stage"] == "pi_issued"
    assert _detail(admin_client, sid)["shipment_status"] is None

    r = admin_client.post(f"/api/sales/{sid}/move", json={"notes": "to LC"})
    assert r.status_code == 200
    assert r.get_json()["new_stage"] == "lc_received"
    assert _detail(admin_client, sid)["shipment_status"] is None

    r = admin_client.post(f"/api/sales/{sid}/move", json={"notes": "to ship"})
    assert r.status_code == 200
    assert r.get_json()["new_stage"] == "shipment_ongoing"
    sale = _detail(admin_client, sid)
    assert sale["stage"] == "shipment_ongoing"
    assert sale["shipment_status"] == "production_running"


# ---------- 9. payload shapes ----------

def test_sales_list_has_shipment_status_detail_has_item_no(admin_client, db):
    cid = _company(admin_client, db, name="ShapeCo")
    sid = _sale(admin_client, cid, "PI-SHAPE-1", item_no="ITM-7")

    def _row():
        rows = admin_client.get("/api/sales").get_json()
        return [x for x in rows if x["id"] == sid][0]

    row = _row()
    assert "shipment_status" in row
    assert row["shipment_status"] is None

    _invoice(admin_client, sid, "INV-SHAPE-1")
    assert _row()["shipment_status"] == "production_running"

    detail = _detail(admin_client, sid)
    assert detail["shipment_status"] == "production_running"
    assert detail["items"][0]["item_no"] == "ITM-7"
    assert isinstance(detail["invoices"], list)
    assert detail["invoices"][0]["invoice_number"] == "INV-SHAPE-1"


# ---------- 10. production-source ----------

def test_production_source_products_with_recipes(admin_client, db):
    cid = _company(admin_client, db, name="SrcCo")
    sid = _sale(admin_client, cid, "PI-SRC-1", product="SrcProd",
                item_no="ITM-SRC")
    r = admin_client.post("/api/recipes", json={
        "name": "SrcRecipe", "yield": 100, "company_id": cid,
        "product_name": "SrcProd"})
    assert r.status_code == 201, r.get_json()

    r = admin_client.get(f"/api/production-source?sale_id={sid}")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data["sale_id"] == sid
    assert data["products"], "expected at least one product"
    products = {p["product_name"]: p for p in data["products"]}
    prod = products["SrcProd"]
    assert prod["item_no"] == "ITM-SRC"
    assert isinstance(prod["recipes"], list)
    assert prod["recipes"], "expected recipes for the matching product"
    assert any(rcp["name"] == "SrcRecipe" for rcp in prod["recipes"])
