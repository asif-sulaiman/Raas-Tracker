"""Go for Production (S1-S3): invoice lifecycle, shipment_status, production links.

Covers POST/GET invoices, book/ship/pay, sale completion, recipe produce with
sale/invoice links, stage move -> shipment_status, and payload shapes
(list shipment_status, detail item_no/invoices, production-source recipes).
"""
from urllib.parse import quote


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


from raas_tracker.sales import create_invoice_item


def _remainder_lines(db, sale_id):
    """The PI remainder as explicit create lines (one per uninvoiced PI line)."""
    rows = db.execute(
        """SELECT si.id,
                  si.quantity - COALESCE(SUM(ii.quantity), 0) AS remaining
             FROM sale_items si
             LEFT JOIN invoice_items ii ON ii.sale_item_id = si.id
            WHERE si.sale_id = %s
            GROUP BY si.id, si.quantity
            ORDER BY si.id""", (sale_id,)).fetchall()
    return [{"sale_item_id": r[0], "quantity": float(r[1])}
            for r in rows if r[1] is not None and float(r[1]) > 0]


def _invoice(admin_client, db, sale_id, number, amount=None, lines=None):
    """Create an invoice via the API with explicit lines (default: remainder).

    ``amount`` is only ever there to be IGNORED by the server.
    """
    payload = {"invoice_number": number, "invoice_date": "2026-01-15",
               "lines": lines if lines is not None
                        else _remainder_lines(db, sale_id)}
    if amount is not None:
        payload["amount"] = amount
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json=payload)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _detail(admin_client, sale_id):
    r = admin_client.get(f"/api/sales/{sale_id}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _invoice_status(db, invoice_id):
    return db.execute(
        "SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


def _stock(db, names):
    rows = db.execute(
        "SELECT name, current_qty FROM chemicals WHERE name = ANY(%s) ORDER BY name",
        (list(names),)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _recipe_id(db, name, company_id):
    return db.execute(
        "SELECT id FROM recipes WHERE name = %s AND company_id = %s",
        (name, company_id)).fetchone()[0]


def _create_recipe(admin_client, cid, inv, name, formulations):
    """Create the invoice's recipe and formulate each listed product."""
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": cid, "invoice_id": inv})
    assert r.status_code == 201, r.get_json()
    rid = r.get_json()["recipe"]["id"]
    for product, chemical, pct in formulations:
        r = admin_client.post(
            f"/api/recipes/by-id/{rid}/products/{quote(product)}/items",
            json={"chemical": chemical, "percentage": pct})
        assert r.status_code == 200, r.get_json()
    return rid


def _produce(admin_client, db, company_id, recipe_name, sale_id, invoice_id,
             qty=5, product=None):
    """One production run whose links derive from the recipe's own invoice.

    Booking is gated on production, so any test that books must satisfy this
    first (conftest's ``produce_for`` fixture does the same in one call).
    """
    rid = _recipe_id(db, recipe_name, company_id)
    if product is None:
        products = [row[0] for row in db.execute(
            "SELECT product_name FROM invoice_items WHERE invoice_id = %s"
            " GROUP BY product_name ORDER BY product_name",
            (invoice_id,)).fetchall()]
    else:
        products = [product]
    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "company_id": company_id,
        "outputs": [{"product_name": p, "quantity": qty} for p in products]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


# ---------- 1. invoice create + duplicate rules ----------

def test_invoice_create_list_duplicate_and_cross_sale_reuse(admin_client, db):
    cid = _company(admin_client, db, name="DupCo")
    s1 = _sale(admin_client, cid, "PI-DUP-1")
    s2 = _sale(admin_client, cid, "PI-DUP-2")

    inv1 = _invoice(admin_client, db, s1, "INV-SAME")

    lst = admin_client.get(f"/api/sales/{s1}/invoices").get_json()
    assert len(lst) == 1
    assert lst[0]["invoice_id"] == inv1
    assert lst[0]["invoice_number"] == "INV-SAME"
    assert lst[0]["status"] == "planned"

    # Duplicate invoice number on the SAME sale -> 400 with error
    dup_item = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s", (s1,)).fetchone()[0]
    r = admin_client.post(f"/api/sales/{s1}/invoices",
                          json={"invoice_number": "INV-SAME",
                                "invoice_date": "2026-01-15",
                                "lines": [{"sale_item_id": dup_item,
                                           "quantity": 1}]})
    assert r.status_code == 400
    body = r.get_json()
    assert "error" in body and "already exists" in body["error"]
    assert len(admin_client.get(f"/api/sales/{s1}/invoices").get_json()) == 1

    # A DIFFERENT sale may reuse the same number
    inv2 = _invoice(admin_client, db, s2, "INV-SAME")
    assert inv2 != inv1
    lst2 = admin_client.get(f"/api/sales/{s2}/invoices").get_json()
    assert len(lst2) == 1 and lst2[0]["invoice_number"] == "INV-SAME"


# ---------- 2. shipment_status on first invoice, no downgrade ----------

def test_first_invoice_sets_production_running_and_never_downgrades(
        admin_client, db):
    cid = _company(admin_client, db, name="RunCo")

    s_run = _sale(admin_client, cid, "PI-RUN-1")
    assert _detail(admin_client, s_run)["shipment_status"] is None
    _invoice(admin_client, db, s_run, "INV-RUN-1")
    assert _detail(admin_client, s_run)["shipment_status"] == "production_running"

    s_done = _sale(admin_client, cid, "PI-DONE-1")
    db.execute("UPDATE sales SET shipment_status = 'production_done' "
               "WHERE id = %s", (s_done,))
    db.commit()
    _invoice(admin_client, db, s_done, "INV-DONE-1")
    assert _detail(admin_client, s_done)["shipment_status"] == "production_done"

    s_book = _sale(admin_client, cid, "PI-BOOK-1")
    db.execute("UPDATE sales SET shipment_status = 'ship_booked' "
               "WHERE id = %s", (s_book,))
    db.commit()
    _invoice(admin_client, db, s_book, "INV-BOOK-1")
    assert _detail(admin_client, s_book)["shipment_status"] == "ship_booked"


# ---------- 3. book ----------

def test_book_requires_approx_date_and_sets_ship_booked(admin_client, db):
    cid, sid, inv = _production_setup(admin_client, db, "Book")

    # The date is validated BEFORE the production gate, so a bad payload still
    # reports the missing date rather than the missing production.
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book", json={})
    assert r.status_code == 400
    assert "error" in r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "   "})
    assert r.status_code == 400
    assert "error" in r.get_json()

    _produce(admin_client, db, cid, "Recipe-Book", sid, inv)

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
    cid, sid, inv = _production_setup(admin_client, db, "Ship")
    _produce(admin_client, db, cid, "Recipe-Ship", sid, inv)
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 200, r.get_json()

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
    cid, sid, inv = _production_setup(admin_client, db, "Pay")  # 10 x 5 = 50
    _produce(admin_client, db, cid, "Recipe-Pay", sid, inv)
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-10-20"})
    assert r.status_code == 201, r.get_json()

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 25.5,
                                "payment_date": "2026-10-25"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["payment_id"]
    # Partial coverage (25.5 < 50) must report the ACTUAL post-sync status,
    # not a hardcoded "paid".
    assert body["status"] == "shipped"

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

    # Two invoices, neither paid. The first derives the whole 50 remainder, so it
    # is trimmed to 30 to leave a real remainder for the second, keeping each
    # invoice's money non-zero and the assertion below about real coverage.
    first = _invoice(admin_client, db, sid, "INV-CMP-1")
    unit_price, qty = db.execute(
        "SELECT unit_price, quantity FROM sale_items WHERE sale_id = %s "
        "ORDER BY id LIMIT 1", (sid,)).fetchone()
    sale_item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sid,)).fetchone()[0]
    db.execute("DELETE FROM invoice_items WHERE invoice_id = %s", (first,))
    db.commit()
    create_invoice_item(db, first, sale_item_id, 30 / float(unit_price))
    _invoice(admin_client, db, sid, "INV-CMP-2")  # derives the remaining 20
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


# ---------- 11. booking availability is reported per invoice ----------

def _rows_for(admin_client, sale_id):
    return {r["invoice_id"]: r for r in
            admin_client.get(f"/api/sales/{sale_id}/invoices").get_json()}


def _readiness(admin_client, invoice_id):
    r = admin_client.get(f"/api/invoices/{invoice_id}/readiness")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_invoice_list_reports_cannot_book_before_production(admin_client, db):
    """The panel must be able to disable Book without guessing from status."""
    cid, sid, inv = _production_setup(admin_client, db, "ListGate")

    row = _rows_for(admin_client, sid)[inv]
    assert row["can_book"] is False
    assert row["book_blocked_reason"]
    assert "recipe" in row["book_blocked_reason"].lower()

    _produce(admin_client, db, cid, "Recipe-ListGate", sid, inv)

    row = _rows_for(admin_client, sid)[inv]
    assert row["can_book"] is True
    # null means exactly one thing: bookable.
    assert row["book_blocked_reason"] is None


def test_partial_production_reports_cannot_book_despite_produced_status(
        admin_client, db):
    """The status flag alone says 'produced'; the content does not agree.

    This is the UI/server desync the list payload exists to close: a
    status-driven button would be enabled and then refused with a 409.
    """
    cid = _company(admin_client, db, name="ListPartCo")
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-LISTP-1", "client_name": "x", "company_id": cid},
        "items": [
            {"product_name": "ListProdA", "quantity": 10, "unit_price": 5, "unit": "KG"},
            {"product_name": "ListProdB", "quantity": 10, "unit_price": 5, "unit": "KG"},
        ]})
    assert r.status_code == 201, r.get_json()
    sid = r.get_json()["id"]

    for chem in ("ListChemA", "ListChemB"):
        admin_client.post("/api/chemicals",
                          json={"name": chem, "qty": 500, "unit": "KG"})

    item_a, item_b = [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchall()]
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-LISTP-1",
        "invoice_date": "2026-01-15",
        "lines": [{"sale_item_id": item_a, "quantity": 10},
                  {"sale_item_id": item_b, "quantity": 10}]})
    assert r.status_code == 201, r.get_json()
    inv = r.get_json()["invoice_id"]

    # One plan for the invoice; only the first product is formulated.
    _create_recipe(admin_client, cid, inv, "ListRecipe",
                   [("ListProdA", "ListChemA", 40)])

    _produce(admin_client, db, cid, "ListRecipe", sid, inv, product="ListProdA")
    assert _invoice_status(db, inv) == "produced", "the flag really is 'produced'"

    row = _rows_for(admin_client, sid)[inv]
    assert row["can_book"] is False
    # The reason must name the product that is still unbuilt.
    assert "ListProdB" in row["book_blocked_reason"]

    # And the endpoint agrees with the list payload.
    body = _readiness(admin_client, inv)
    assert body["produced_ok"] is False
    assert body["can_book"] is False


def test_invoice_readiness_ignores_a_produced_sibling(admin_client, db):
    """Readiness is per invoice, not per sale.

    Used to be sale-scoped, so producing one invoice's product made a sibling
    look produced too.
    """
    cid = _company(admin_client, db, name="ListSibCo")
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-LISTS-1", "client_name": "x", "company_id": cid},
        "items": [
            {"product_name": "ListSibA", "quantity": 5, "unit_price": 5, "unit": "KG"},
            {"product_name": "ListSibB", "quantity": 5, "unit_price": 5, "unit": "KG"},
        ]})
    assert r.status_code == 201, r.get_json()
    sid = r.get_json()["id"]

    for chem in ("ListSibChemA", "ListSibChemB"):
        admin_client.post("/api/chemicals",
                          json={"name": chem, "qty": 500, "unit": "KG"})

    item_a, item_b = [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchall()]
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-LISTS-A",
        "invoice_date": "2026-01-15",
        "lines": [{"sale_item_id": item_a, "quantity": 5}]})
    inv_a = r.get_json()["invoice_id"]
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-LISTS-B",
        "invoice_date": "2026-01-16",
        "lines": [{"sale_item_id": item_b, "quantity": 5}]})
    inv_b = r.get_json()["invoice_id"]

    _create_recipe(admin_client, cid, inv_a, "ListSibRecipeA",
                   [("ListSibA", "ListSibChemA", 40)])
    _create_recipe(admin_client, cid, inv_b, "ListSibRecipeB",
                   [("ListSibB", "ListSibChemB", 40)])

    _produce(admin_client, db, cid, "ListSibRecipeA", sid, inv_a)

    assert _readiness(admin_client, inv_a)["produced_ok"] is True
    # B's own product was never produced, even though its sibling's was.
    b = _readiness(admin_client, inv_b)
    assert b["produced_ok"] is False
    assert b["can_book"] is False


def test_booked_invoice_reports_cannot_book_without_a_blocked_reason(
        admin_client, db):
    """A closed invoice is simply not bookable — that is not a 'blockage'."""
    cid, sid, inv = _production_setup(admin_client, db, "ListClosed")
    _produce(admin_client, db, cid, "Recipe-ListClosed", sid, inv)
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()

    row = _rows_for(admin_client, sid)[inv]
    assert row["can_book"] is False
    assert row["book_blocked_reason"] is None

    assert _readiness(admin_client, inv)["can_book"] is False


def test_invoice_detail_does_not_pay_for_readiness(admin_client, db):
    """The heaviest endpoints must not compute readiness nobody reads.

    ``get_sale_by_id`` and ``get_sale_completion`` embed the invoice list but
    not its booking flags.
    """
    cid, sid, inv = _production_setup(admin_client, db, "ListCost")
    detail = _detail(admin_client, sid)
    assert detail["invoices"][0].get("can_book") is None
    comp = admin_client.get(f"/api/sales/{sid}/completion").get_json()
    assert comp["invoices"][0].get("can_book") is None


def test_book_is_refused_until_the_invoice_has_been_produced(admin_client, db):
    """Production is mandatory before a ship can be booked for an invoice.

    The recipe exists but nothing has been produced yet, so the invoice must
    not be bookable: booking is the step that commits to a real shipment.
    """
    cid, sid, inv = _production_setup(admin_client, db, "Gate")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 409, r.get_json()
    assert "produced" in r.get_json()["error"].lower()
    assert _invoice_status(db, inv) == "planned"
    # Booking must not have advanced the sale's shipment sub-step either.
    assert _detail(admin_client, sid)["shipment_status"] != "ship_booked"

    _produce(admin_client, db, cid, "Recipe-Gate", sid, inv)

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()
    assert _invoice_status(db, inv) == "booked"


def test_book_is_refused_when_only_some_invoiced_products_are_produced(
        admin_client, db):
    """A 'produced' status alone is not enough — every product must be.

    ``record_production`` marks an invoice 'produced' on the FIRST linked run.
    On a two-product invoice that leaves the gate looking satisfied while the
    second product was never produced, so booking must re-check the content.
    """
    cid = _company(admin_client, db, name="PartCo")
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-PART-1", "client_name": "x", "company_id": cid},
        "items": [
            {"product_name": "ProdA", "quantity": 10, "unit_price": 5, "unit": "KG"},
            {"product_name": "ProdB", "quantity": 10, "unit_price": 5, "unit": "KG"},
        ]})
    assert r.status_code == 201, r.get_json()
    sid = r.get_json()["id"]

    admin_client.post("/api/chemicals", json={"name": "ChemA", "qty": 500, "unit": "KG"})
    admin_client.post("/api/chemicals", json={"name": "ChemB", "qty": 500, "unit": "KG"})

    inv = _invoice(admin_client, db, sid, "INV-PART-1")
    assert len(_detail(admin_client, sid)["invoices"]) == 1
    _create_recipe(admin_client, cid, inv, "Recipe",
                   [("ProdA", "ChemA", 40), ("ProdB", "ChemB", 40)])

    # Produce ProdA only. This does flip the invoice to 'produced'...
    _produce(admin_client, db, cid, "Recipe", sid, inv, product="ProdA")
    assert _invoice_status(db, inv) == "produced"

    # ...but booking must still refuse: ProdB was never produced.
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 409, r.get_json()
    assert "prodb" in r.get_json()["error"].lower()

    # Produce ProdB too — now the whole invoice is covered.
    _produce(admin_client, db, cid, "Recipe", sid, inv, product="ProdB")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()


def test_producing_a_sibling_invoice_does_not_unlock_this_one(admin_client, db):
    """Invoices proceed independently; coverage is not shared between them."""
    cid = _company(admin_client, db, name="SibCo")
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-SIB-1", "client_name": "x", "company_id": cid},
        "items": [
            {"product_name": "SibProdA", "quantity": 5, "unit_price": 5, "unit": "KG"},
            {"product_name": "SibProdB", "quantity": 5, "unit_price": 5, "unit": "KG"},
        ]})
    sid = r.get_json()["id"]

    for chem in ("SibChemA", "SibChemB"):
        admin_client.post("/api/chemicals",
                          json={"name": chem, "qty": 500, "unit": "KG"})

    # Two invoices, one product each (so both products stay visible to the
    # per-invoice readiness checks).
    item_a, item_b = [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id", (sid,)).fetchall()]
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-SIB-A", "invoice_date": "2026-01-15",
        "lines": [{"sale_item_id": item_a, "quantity": 5}]})
    assert r.status_code == 201, r.get_json()
    inv_a = r.get_json()["invoice_id"]
    r = admin_client.post(f"/api/sales/{sid}/invoices", json={
        "invoice_number": "INV-SIB-B", "invoice_date": "2026-01-16",
        "lines": [{"sale_item_id": item_b, "quantity": 5}]})
    assert r.status_code == 201, r.get_json()
    inv_b = r.get_json()["invoice_id"]

    _create_recipe(admin_client, cid, inv_a, "SibRecipeA",
                   [("SibProdA", "SibChemA", 40)])
    _create_recipe(admin_client, cid, inv_b, "SibRecipeB",
                   [("SibProdB", "SibChemB", 40)])

    _produce(admin_client, db, cid, "SibRecipeA", sid, inv_a)

    # A is covered and bookable...
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv_a}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()
    # ...while B, whose product has never been produced, is still refused.
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv_b}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 409, r.get_json()

def _production_setup(admin_client, db, tag):
    """Company + sale + chemical + invoice + its formulated recipe."""
    cid = _company(admin_client, db, name=f"ProdCo-{tag}")
    sid = _sale(admin_client, cid, pi=f"PI-PR-{tag}", product=f"Prod-{tag}")
    r = admin_client.post("/api/chemicals",
                          json={"name": f"Chem-{tag}", "qty": 500, "unit": "KG"})
    assert r.status_code == 200, r.get_json()
    inv = _invoice(admin_client, db, sid, f"INV-PR-{tag}")
    _create_recipe(admin_client, cid, inv, f"Recipe-{tag}",
                   [(f"Prod-{tag}", f"Chem-{tag}", 40)])
    return cid, sid, inv


def test_produce_links_run_invoices_sales_and_new_columns(admin_client, db):
    cid, sid, inv = _production_setup(admin_client, db, "A")
    rid = _recipe_id(db, "Recipe-A", cid)

    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "company_id": cid,
        "outputs": [{"product_name": "Prod-A", "quantity": 5}],
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


def test_produce_never_downgrades_ship_booked_sale_or_booked_invoice(
        admin_client, db):
    """Produce is forward-only on BOTH tracks.

    A booked invoice has already moved PAST 'produced' in the lifecycle
    [planned, produced, booked, shipped, paid], so a successful production run
    must not set it back to 'produced'. Likewise the sale's shipment sub-step
    stays at 'ship_booked' rather than reverting to 'production_done'. The
    production itself is still recorded (201, stock deducted, run + links
    written) — only the lifecycle positions are left alone.
    """
    cid, sid, inv = _production_setup(admin_client, db, "B")

    # Booking requires production first, so produce before booking.
    _produce(admin_client, db, cid, "Recipe-B", sid, inv)
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200
    assert _detail(admin_client, sid)["shipment_status"] == "ship_booked"
    assert _invoice_status(db, inv) == "booked"

    r = admin_client.post(f"/api/recipes/by-id/{_recipe_id(db, 'Recipe-B', cid)}"
                          "/produce", json={
                              "company_id": cid,
                              "outputs": [{"product_name": "Prod-B",
                                           "quantity": 5}]})
    assert r.status_code == 201, r.get_json()

    # The sale's shipment sub-step is not pushed back to production_done...
    assert _detail(admin_client, sid)["shipment_status"] == "ship_booked"
    # ...and the invoice is not dragged back from 'booked' to 'produced'.
    assert _invoice_status(db, inv) == "booked"

    # The production still happened and is linked.
    run_id = r.get_json()["runs"][0]["run_id"]
    pairs = {(l[0], l[1]) for l in db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links "
        "WHERE run_id = %s", (run_id,)).fetchall()}
    assert (sid, inv) in pairs
    # Two runs happened (the one that unlocked booking, plus this one):
    # 40% of 5 = 2.0 KG each, so 500 -> 496.
    assert _stock(db, ["Chem-B"]) == [("Chem-B", 496.0)]


def test_produce_links_only_the_recipes_own_invoice(admin_client, db):
    cid, sid_a, inv_a = _production_setup(admin_client, db, "C")
    sid_b = _sale(admin_client, cid, pi="PI-PR-C2", product="Prod-C")
    inv_b = _invoice(admin_client, db, sid_b, "INV-PR-C2")
    rid = _recipe_id(db, "Recipe-C", cid)

    # A request cannot steer the run at another invoice: extra fields are
    # rejected, and the links derive from the recipe's own invoice.
    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "company_id": cid,
        "outputs": [{"product_name": "Prod-C", "quantity": 5}],
        "invoice_ids": [inv_b]})
    assert r.status_code == 400, r.get_json()

    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "company_id": cid,
        "outputs": [{"product_name": "Prod-C", "quantity": 5}]})
    assert r.status_code == 201, r.get_json()
    run_id = r.get_json()["runs"][0]["run_id"]

    pairs = {(l[0], l[1]) for l in db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links "
        "WHERE run_id = %s", (run_id,)).fetchall()}
    assert pairs == {(sid_a, inv_a)}
    assert _invoice_status(db, inv_a) == "produced"
    # The sibling invoice was never touched.
    assert _invoice_status(db, inv_b) == "planned"
    assert _detail(admin_client, sid_b)["shipment_status"] == "production_running"


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

    # Phase 3-A: need an invoice to advance to shipment_ongoing
    _invoice(admin_client, db, sid, "INV-MOVE-1")

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
        rows = admin_client.get("/api/sales").get_json()["sales"]
        return [x for x in rows if x["id"] == sid][0]

    row = _row()
    assert "shipment_status" in row
    assert row["shipment_status"] is None

    _invoice(admin_client, db, sid, "INV-SHAPE-1")
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

    # Production products are invoice-scoped: a sale with no invoice offers
    # nothing, so create one (it seeds lines for SrcProd) and its recipe.
    inv = _invoice(admin_client, db, sid, "INV-SRC-1")
    _create_recipe(admin_client, cid, inv, "SrcRecipe", [])

    r = admin_client.get(f"/api/production-source?sale_id={sid}")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data["sale_id"] == sid
    assert data["products"], "expected at least one product"
    products = {p["product_name"]: p for p in data["products"]}
    prod = products["SrcProd"]
    assert prod["item_no"] == "ITM-SRC"
    assert prod["invoice_id"] == inv
    assert isinstance(prod["recipes"], list)
    assert prod["recipes"], "expected recipes for the matching product"
    assert any(rcp["name"] == "SrcRecipe" for rcp in prod["recipes"])
