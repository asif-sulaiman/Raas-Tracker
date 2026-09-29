"""Production-run atomicity + invoice-route transaction & lifecycle guards.

Regression net for the N+1 transaction bug: ``update_stock`` used to COMMIT
internally, so ``create_production_run`` committed once per ingredient. A
shortage on the Nth ingredient left the first N-1 deductions, the
``production_runs`` row, the ``production_run_links`` rows and the
invoice/sale status flips durably committed while the API answered 400 --
and a retry double-deducted.

Every test here snapshots the FULL affected state and asserts it is
byte-identical after a rejected request.

Also covers: multi-recipe produce atomicity, one-transaction booking,
cross-company link rejection (fail closed), the "never downgrade a paid /
shipped invoice" rule from commit 7726eef, and @admin_required on the five
new mutating routes.
"""

# ---------- helpers (patterns from tests/test_invoices_production.py) ----------


def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute("SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, product):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": product, "quantity": 10, "unit_price": 5, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _invoice(admin_client, sale_id, number):
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json={"invoice_number": number})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _chemical(admin_client, name, qty):
    r = admin_client.post("/api/chemicals", json={"name": name, "qty": qty, "unit": "KG"})
    assert r.status_code == 200, r.get_json()


def _recipe_with_items(admin_client, company_id, product, recipe_name, ingredients):
    """ingredients: list of (chemical_name, percentage)."""
    r = admin_client.post("/api/recipes", json={
        "name": recipe_name, "yield": 100, "company_id": company_id,
        "product_name": product})
    assert r.status_code == 201, r.get_json()
    for chem, pct in ingredients:
        r = admin_client.post(f"/api/recipes/{recipe_name}/items", json={
            "chemical": chem, "percentage": pct, "company_id": company_id})
        assert r.status_code == 200, r.get_json()


def _stock(db, names):
    rows = db.execute(
        "SELECT name, current_qty FROM chemicals WHERE name = ANY(%s) ORDER BY name",
        (list(names),)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _state(db, sale_id, invoice_id):
    """Everything a produce/book request can move, as one comparable snapshot."""
    return {
        "runs": db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0],
        "run_items": db.execute("SELECT COUNT(*) FROM production_run_items").fetchone()[0],
        "links": db.execute("SELECT COUNT(*) FROM production_run_links").fetchone()[0],
        "shipment_status": db.execute(
            "SELECT shipment_status FROM sales WHERE id = %s", (sale_id,)).fetchone()[0],
        "invoice_status": db.execute(
            "SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0],
    }


def _invoice_status(db, invoice_id):
    return db.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,)).fetchone()[0]


def _shipment_status(db, sale_id):
    return db.execute(
        "SELECT shipment_status FROM sales WHERE id = %s", (sale_id,)).fetchone()[0]


# ---------- 1. one production run == one transaction ----------


def test_produce_short_on_third_ingredient_rolls_everything_back(admin_client, db):
    """4-ingredient recipe, ingredient 3 short: nothing may survive the 400."""
    cid = _company(admin_client, db, "AtomicCo")
    sid = _sale(admin_client, cid, "PI-ATOMIC-1", "AtomicProd")
    chems = [f"AtomicChem-{i}" for i in range(1, 5)]
    for name in chems:
        _chemical(admin_client, name, 100)
    _recipe_with_items(admin_client, cid, "AtomicProd", "AtomicRecipe",
                       [(name, 10) for name in chems])
    inv = _invoice(admin_client, sid, "INV-ATOMIC-1")

    # Loop order is by chemical name, so AtomicChem-3 is the third deduction
    # and cannot cover required_per_unit (0.1) * production_qty (10) = 1.0.
    db.execute("UPDATE chemicals SET current_qty = 0.5 WHERE name = %s", (chems[2],))
    db.commit()

    before_stock = _stock(db, chems)
    before_state = _state(db, sid, inv)
    assert before_state["shipment_status"] == "production_running"
    assert before_state["invoice_status"] == "planned"

    r = admin_client.post("/api/recipes/AtomicRecipe/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 400, r.get_json()
    assert "insufficient stock" in r.get_json()["error"]

    # Every ingredient (deducted and untouched alike) must be unchanged, and no
    # run / link / run_item / status flip may have survived.
    assert _stock(db, chems) == before_stock
    assert _state(db, sid, inv) == before_state


def test_produce_retried_after_shortage_deducts_exactly_once(admin_client, db):
    """A retry after a shortage must not double-deduct the surviving items."""
    cid = _company(admin_client, db, "RetryCo")
    sid = _sale(admin_client, cid, "PI-RETRY-1", "RetryProd")
    chems = [f"RetryChem-{i}" for i in range(1, 4)]
    for name in chems:
        _chemical(admin_client, name, 100)
    _recipe_with_items(admin_client, cid, "RetryProd", "RetryRecipe",
                       [(name, 10) for name in chems])
    inv = _invoice(admin_client, sid, "INV-RETRY-1")
    db.execute("UPDATE chemicals SET current_qty = 0.5 WHERE name = %s", (chems[1],))
    db.commit()

    payload = {"production_qty": 10, "company_id": cid,
               "sale_ids": [sid], "invoice_ids": [inv]}
    before_stock = _stock(db, chems)

    for _ in range(2):  # first fails, retry must fail identically
        r = admin_client.post("/api/recipes/RetryRecipe/produce", json=payload)
        assert r.status_code == 400, r.get_json()

    assert _stock(db, chems) == before_stock
    assert _state(db, sid, inv)["runs"] == 0


# ---------- 2. multi-recipe produce is ONE transaction ----------


def test_produce_multi_recipe_second_invalid_rolls_back_first(admin_client, db):
    """Recipe 1 produced + recipe 2 invalid must leave recipe 1 untouched."""
    cid = _company(admin_client, db, "MultiCo")
    sid = _sale(admin_client, cid, "PI-MULTI-1", "MultiProd")
    _chemical(admin_client, "MultiChem-1", 100)
    _recipe_with_items(admin_client, cid, "MultiProd", "MultiRecipe-1",
                       [("MultiChem-1", 10)])
    inv = _invoice(admin_client, sid, "INV-MULTI-1")

    before_stock = _stock(db, ["MultiChem-1"])
    before_state = _state(db, sid, inv)

    r = admin_client.post("/api/recipes/MultiRecipe-1/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv],
        "recipes": [{"recipe_name": "MultiRecipe-1", "qty": 10},
                    {"recipe_name": "MultiRecipe-Missing", "qty": 10}]})
    assert r.status_code == 400, r.get_json()

    assert _stock(db, ["MultiChem-1"]) == before_stock
    assert _state(db, sid, inv) == before_state


def test_produce_multi_recipe_all_valid_commits_once(admin_client, db):
    """Success path unchanged: both recipes produced, one 201."""
    cid = _company(admin_client, db, "MultiOkCo")
    sid = _sale(admin_client, cid, "PI-MULTOK-1", "MultiOkProd")
    _chemical(admin_client, "MultiOkChem-1", 100)
    _chemical(admin_client, "MultiOkChem-2", 100)
    _recipe_with_items(admin_client, cid, "MultiOkProd", "MultiOkRecipe-1",
                       [("MultiOkChem-1", 10)])
    _recipe_with_items(admin_client, cid, "MultiOkProd", "MultiOkRecipe-2",
                       [("MultiOkChem-2", 10)])
    inv = _invoice(admin_client, sid, "INV-MULTOK-1")

    r = admin_client.post("/api/recipes/MultiOkRecipe-1/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv],
        "recipes": [{"recipe_name": "MultiOkRecipe-1", "qty": 10},
                    {"recipe_name": "MultiOkRecipe-2", "qty": 20}]})
    assert r.status_code == 201, r.get_json()
    runs = r.get_json()["runs"]
    assert [x["recipe_name"] for x in runs] == ["MultiOkRecipe-1", "MultiOkRecipe-2"]
    assert [x["run_id"] for x in runs] == sorted(x["run_id"] for x in runs)

    # 10% of 10 = 1.0 and 10% of 20 = 2.0 deducted.
    assert _stock(db, ["MultiOkChem-1", "MultiOkChem-2"]) == [
        ("MultiOkChem-1", 99.0), ("MultiOkChem-2", 98.0)]
    state = _state(db, sid, inv)
    assert state["runs"] == 2
    assert state["invoice_status"] == "produced"


# ---------- 3. booking is one transaction (invoice + sale together) ----------


def test_book_invoice_moves_invoice_and_sale_together(admin_client, db):
    cid = _company(admin_client, db, "BookCo")
    sid = _sale(admin_client, cid, "PI-BOOK-AT-1", "BookProd")
    inv = _invoice(admin_client, sid, "INV-BOOK-AT-1")

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "booked"
    assert _invoice_status(db, inv) == "booked"
    assert _shipment_status(db, sid) == "ship_booked"


def test_book_invoice_second_write_failure_leaves_both_unchanged(
        admin_client, db, monkeypatch):
    """A failure after the invoice row was touched must roll the sale back too."""
    import flask_app

    cid = _company(admin_client, db, "BookFailCo")
    sid = _sale(admin_client, cid, "PI-BOOKF-1", "BookFailProd")
    inv = _invoice(admin_client, sid, "INV-BOOKF-1")
    before = _state(db, sid, inv)

    def _book_then_fail(conn, invoice_id, approx_ship_date):
        conn.execute(
            "UPDATE invoices SET status = 'booked', approx_ship_date = %s WHERE id = %s",
            (approx_ship_date, invoice_id))
        raise RuntimeError("forced failure in the booking write")

    monkeypatch.setattr(flask_app, "book_invoice", _book_then_fail)

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 500, r.get_json()

    assert _invoice_status(db, inv) == before["invoice_status"]
    assert _shipment_status(db, sid) == before["shipment_status"]
    assert _state(db, sid, inv) == before


def test_book_invoice_refused_transition_does_not_move_sale(admin_client, db):
    """Book refused (409) after a ship: the sale badge must not advance."""
    cid = _company(admin_client, db, "Book409Co")
    sid = _sale(admin_client, cid, "PI-BOOK409-1", "Book409Prod")
    inv = _invoice(admin_client, sid, "INV-BOOK409-1")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-10-20"})
    assert r.status_code == 201, r.get_json()
    db.execute("UPDATE sales SET shipment_status = 'production_done' WHERE id = %s",
               (sid,))
    db.commit()

    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 409, r.get_json()
    assert _invoice_status(db, inv) == "shipped"
    assert _shipment_status(db, sid) == "production_done"


# ---------- 4. produce links are company-scoped (fail closed) ----------


def test_produce_refuses_links_belonging_to_another_company(admin_client, db):
    cid_a = _company(admin_client, db, "OwnCoA")
    sid_a = _sale(admin_client, cid_a, "PI-OWNA-1", "OwnProdA")
    _chemical(admin_client, "OwnChem-A", 100)
    _recipe_with_items(admin_client, cid_a, "OwnProdA", "OwnRecipe-A",
                       [("OwnChem-A", 10)])

    cid_b = _company(admin_client, db, "OwnCoB")
    sid_b = _sale(admin_client, cid_b, "PI-OWNB-1", "OwnProdB")
    inv_b = _invoice(admin_client, sid_b, "INV-OWN-B")

    before_stock = _stock(db, ["OwnChem-A"])
    before_state = _state(db, sid_b, inv_b)

    r = admin_client.post("/api/recipes/OwnRecipe-A/produce", json={
        "production_qty": 10, "company_id": cid_a,
        "sale_ids": [sid_b], "invoice_ids": [inv_b]})
    assert r.status_code == 404, r.get_json()

    # Customer B's sale/invoice must be untouched and no run may exist.
    assert _stock(db, ["OwnChem-A"]) == before_stock
    assert _state(db, sid_b, inv_b) == before_state


# ---------- 5. invoice lifecycle is forward-only on the produce path ----------


def test_produce_does_not_downgrade_paid_invoice(admin_client, db):
    """Commit 7726eef: producing against a paid invoice must not downgrade it."""
    cid = _company(admin_client, db, "PaidCo")
    sid = _sale(admin_client, cid, "PI-PAID-1", "PaidProd")   # total 50
    _chemical(admin_client, "PaidChem-1", 100)
    _recipe_with_items(admin_client, cid, "PaidProd", "PaidRecipe",
                       [("PaidChem-1", 10)])
    inv = _invoice(admin_client, sid, "INV-PAID-1")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-10-25"})
    assert r.status_code == 201, r.get_json()
    assert _invoice_status(db, inv) == "paid"

    before_stock = _stock(db, ["PaidChem-1"])
    before_state = _state(db, sid, inv)

    r = admin_client.post("/api/recipes/PaidRecipe/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 409, r.get_json()
    assert "already paid" in r.get_json()["error"]

    assert _invoice_status(db, inv) == "paid"
    assert _stock(db, ["PaidChem-1"]) == before_stock
    # Refused run leaves NO orphan production_runs row behind.
    assert _state(db, sid, inv) == before_state


def test_produce_never_downgrades_shipped_invoice_or_ship_booked_sale(admin_client, db):
    cid = _company(admin_client, db, "FwdCo")
    sid = _sale(admin_client, cid, "PI-FWD-1", "FwdProd")
    _chemical(admin_client, "FwdChem-1", 100)
    _recipe_with_items(admin_client, cid, "FwdProd", "FwdRecipe",
                       [("FwdChem-1", 10)])

    # (a) shipped invoice -> refused, nothing moves
    inv_ship = _invoice(admin_client, sid, "INV-FWD-SHIPPED")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv_ship}/ship",
                          json={"actual_ship_date": "2026-10-20"})
    assert r.status_code == 201, r.get_json()
    before_stock = _stock(db, ["FwdChem-1"])
    r = admin_client.post("/api/recipes/FwdRecipe/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv_ship]})
    assert r.status_code == 409, r.get_json()
    assert _invoice_status(db, inv_ship) == "shipped"
    assert _stock(db, ["FwdChem-1"]) == before_stock

    # (b) a booked sale (shipment_status='ship_booked') is never pushed back
    sid_book = _sale(admin_client, cid, "PI-FWD-2", "FwdProd")
    inv_book = _invoice(admin_client, sid_book, "INV-FWD-BOOKED")
    r = admin_client.post(f"/api/sales/{sid_book}/invoices/{inv_book}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()
    assert _shipment_status(db, sid_book) == "ship_booked"
    r = admin_client.post("/api/recipes/FwdRecipe/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid_book], "invoice_ids": [inv_book]})
    assert r.status_code == 201, r.get_json()
    assert _shipment_status(db, sid_book) == "ship_booked"


def test_produce_keeps_booked_invoice_booked(admin_client, db):
    """Produce is forward-only: a ``booked`` invoice must NOT be pulled back
    to ``produced``.

    The lifecycle is [planned, produced, booked, shipped, paid], so 'produced'
    is EARLIER than 'booked' — writing it on produce is a backward move that
    the rest of the lifecycle (sales.update_invoice_status) refuses with a 409.
    The production still happened: the run row, its links and the ingredient
    deduction all stand, and the sale's shipment sub-step still reports the
    production. Only the invoice's lifecycle position is left alone.
    """
    cid = _company(admin_client, db, "BookedInvCo")
    sid = _sale(admin_client, cid, "PI-BKD-1", "BkdProd")
    _chemical(admin_client, "BkdChem-1", 100)
    _recipe_with_items(admin_client, cid, "BkdProd", "BkdRecipe",
                       [("BkdChem-1", 10)])
    inv = _invoice(admin_client, sid, "INV-BKD-1")
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()
    assert _invoice_status(db, inv) == "booked"
    assert _shipment_status(db, sid) == "ship_booked"

    r = admin_client.post("/api/recipes/BkdRecipe/produce", json={
        "production_qty": 10, "company_id": cid,
        "sale_ids": [sid], "invoice_ids": [inv]})
    assert r.status_code == 201, r.get_json()

    # The run really happened — run row, links and the single atomic deduction.
    run_id = r.get_json()["runs"][0]["run_id"]
    links = {(l[0], l[1]) for l in db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links WHERE run_id = %s",
        (run_id,)).fetchall()}
    assert (sid, inv) in links
    assert _stock(db, ["BkdChem-1"]) == [("BkdChem-1", 99.0)]

    # The invoice lifecycle position did not regress...
    assert _invoice_status(db, inv) == "booked"
    # ...and the production sub-step on the sale is still intact.
    assert _shipment_status(db, sid) == "ship_booked"


def test_produce_on_booked_invoice_is_idempotent_on_re_run(admin_client, db):
    """Re-producing against the same booked invoice leaves it booked again.

    Pins that the guard is a no-op for an at-or-past 'produced' invoice rather
    than a one-shot check, and that a second run still deducts its stock.
    """
    cid = _company(admin_client, db, "ReRunBookedCo")
    sid = _sale(admin_client, cid, "PI-RERUN-1", "ReRunProd")
    _chemical(admin_client, "ReRunChem-1", 100)
    _recipe_with_items(admin_client, cid, "ReRunProd", "ReRunRecipe",
                       [("ReRunChem-1", 10)])
    inv = _invoice(admin_client, sid, "INV-RERUN-1")
    assert admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                             json={"approx_ship_date": "2026-11-01"}
                             ).status_code == 200

    payload = {"production_qty": 10, "company_id": cid,
               "sale_ids": [sid], "invoice_ids": [inv]}
    for _ in range(2):
        r = admin_client.post("/api/recipes/ReRunRecipe/produce", json=payload)
        assert r.status_code == 201, r.get_json()

    assert _invoice_status(db, inv) == "booked"
    assert _shipment_status(db, sid) == "ship_booked"
    # Two runs, two deductions — atomicity intact across the re-run.
    assert _stock(db, ["ReRunChem-1"]) == [("ReRunChem-1", 98.0)]
    assert _state(db, sid, inv)["runs"] == 2


# ---------- 6. @admin_required on the five mutating routes ----------


def test_non_admin_gets_403_on_mutating_invoice_and_production_routes(
        admin_client, user_client, db):
    cid = _company(admin_client, db, "AuthCo")
    sid = _sale(admin_client, cid, "PI-AUTH-1", "AuthProd")
    _chemical(admin_client, "AuthChem-1", 100)
    _recipe_with_items(admin_client, cid, "AuthProd", "AuthRecipe",
                       [("AuthChem-1", 10)])
    inv = _invoice(admin_client, sid, "INV-AUTH-1")

    before_stock = _stock(db, ["AuthChem-1"])
    before_state = _state(db, sid, inv)

    attempts = [
        ("/api/recipes/AuthRecipe/produce",
         {"production_qty": 10, "company_id": cid, "sale_ids": [sid],
          "invoice_ids": [inv]}),
        (f"/api/sales/{sid}/invoices", {"invoice_number": "INV-AUTH-2"}),
        (f"/api/sales/{sid}/invoices/{inv}/book", {"approx_ship_date": "2026-10-15"}),
        (f"/api/sales/{sid}/invoices/{inv}/ship", {"actual_ship_date": "2026-10-20"}),
        (f"/api/sales/{sid}/invoices/{inv}/pay",
         {"payment_amount": 1, "payment_date": "2026-10-25"}),
    ]
    for url, payload in attempts:
        r = user_client.post(url, json=payload)
        assert r.status_code == 403, (url, r.status_code, r.get_json())

    assert _stock(db, ["AuthChem-1"]) == before_stock
    assert _state(db, sid, inv) == before_state


# ---------- 7. nonexistent sale on invoice create is 404, not a fake 400 ----------


def test_create_invoice_for_unknown_sale_returns_404(admin_client, db):
    cid = _company(admin_client, db, "MissingSaleCo")
    _sale(admin_client, cid, "PI-MISS-1", "MissingSaleProd")
    before = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    r = admin_client.post("/api/sales/999999/invoices",
                          json={"invoice_number": "INV-MISS-1"})
    assert r.status_code == 404, r.get_json()
    assert db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == before