"""Production-run atomicity + invoice-route transaction & lifecycle guards.

Regression net for the N+1 transaction bug: ``update_stock`` used to COMMIT
internally, so ``create_production_run`` committed once per ingredient. A
shortage on the Nth ingredient left the first N-1 deductions, the
``production_runs`` row, its outputs, links and the invoice/sale status flips
durably committed while the API answered 400 -- and a retry double-deducted.

Every test here snapshots the FULL affected state and asserts it is
byte-identical after a rejected request.

A recipe is now the plan for one invoice, so a single run may cover several
products. That replaces the old multi-recipe atomicity: a shortage on a later
product's chemical must roll the earlier product's deduction back too.

Also covers: one-transaction booking, the forward-only produce rules
(booked stays booked; shipped/paid refuse), @admin_required on the mutating
routes, and the failure-after-write rollback path.
"""
from urllib.parse import quote

# ---------- helpers (patterns from tests/test_invoices_production.py) ----------


def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute("SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, products=("AtomProd",)):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": p, "quantity": 10, "unit_price": 5,
                   "unit": "KG"} for p in products]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


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


def _invoice(admin_client, db, sale_id, number):
    r = admin_client.post(f"/api/sales/{sale_id}/invoices", json={
        "invoice_number": number,
        "invoice_date": "2026-01-15",
        "lines": _remainder_lines(db, sale_id)})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _chemical(admin_client, name, qty):
    r = admin_client.post("/api/chemicals", json={"name": name, "qty": qty, "unit": "KG"})
    assert r.status_code == 200, r.get_json()


def _recipe(admin_client, company_id, invoice_id, name, formulations):
    """formulations: {product_name: [(chemical_name, percentage), ...]}."""
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": company_id, "invoice_id": invoice_id})
    assert r.status_code == 201, r.get_json()
    recipe_id = r.get_json()["recipe"]["id"]
    for product, ingredients in formulations.items():
        for chem, pct in ingredients:
            r = admin_client.post(
                f"/api/recipes/by-id/{recipe_id}/products/"
                f"{quote(product)}/items",
                json={"chemical": chem, "percentage": pct})
            assert r.status_code == 200, r.get_json()
    return recipe_id


def _produce(admin_client, recipe_id, outputs):
    """outputs: {product_name: quantity}."""
    return admin_client.post(
        f"/api/recipes/by-id/{recipe_id}/produce",
        json={"outputs": [{"product_name": p, "quantity": q}
                          for p, q in outputs.items()]})


def _stock(db, names):
    rows = db.execute(
        "SELECT name, current_qty FROM chemicals WHERE name = ANY(%s) ORDER BY name",
        (list(names),)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _state(db, sale_id, invoice_id):
    """Everything a produce/book request can move, as one comparable snapshot."""
    return {
        "runs": db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0],
        "outputs": db.execute(
            "SELECT COUNT(*) FROM production_run_outputs").fetchone()[0],
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
    sid = _sale(admin_client, cid, "PI-ATOMIC-1")
    chems = [f"AtomicChem-{i}" for i in range(1, 5)]
    for name in chems:
        _chemical(admin_client, name, 100)
    inv = _invoice(admin_client, db, sid, "INV-ATOMIC-1")
    rid = _recipe(admin_client, cid, inv, "AtomicRecipe",
                  {"AtomProd": [(name, 10) for name in chems]})

    # Loop order is by chemical name, so AtomicChem-3 is the third deduction
    # and cannot cover 10% * production_qty (10) = 1.0.
    db.execute("UPDATE chemicals SET current_qty = 0.5 WHERE name = %s", (chems[2],))
    db.commit()

    before_stock = _stock(db, chems)
    before_state = _state(db, sid, inv)
    assert before_state["shipment_status"] == "production_running"
    assert before_state["invoice_status"] == "planned"

    r = _produce(admin_client, rid, {"AtomProd": 10})
    assert r.status_code == 400, r.get_json()
    assert "insufficient stock" in r.get_json()["error"]

    # Every ingredient (deducted and untouched alike) must be unchanged, and no
    # run / output / link / run_item / status flip may have survived.
    assert _stock(db, chems) == before_stock
    assert _state(db, sid, inv) == before_state


def test_produce_retried_after_shortage_deducts_exactly_once(admin_client, db):
    """A retry after a shortage must not double-deduct the surviving items."""
    cid = _company(admin_client, db, "RetryCo")
    sid = _sale(admin_client, cid, "PI-RETRY-1", ("RetryProd",))
    chems = [f"RetryChem-{i}" for i in range(1, 4)]
    for name in chems:
        _chemical(admin_client, name, 100)
    inv = _invoice(admin_client, db, sid, "INV-RETRY-1")
    rid = _recipe(admin_client, cid, inv, "RetryRecipe",
                  {"RetryProd": [(name, 10) for name in chems]})
    db.execute("UPDATE chemicals SET current_qty = 0.5 WHERE name = %s", (chems[1],))
    db.commit()

    before_stock = _stock(db, chems)
    for _ in range(2):  # first fails, retry must fail identically
        r = _produce(admin_client, rid, {"RetryProd": 10})
        assert r.status_code == 400, r.get_json()

    assert _stock(db, chems) == before_stock
    assert _state(db, sid, inv)["runs"] == 0


def test_produce_two_products_short_on_the_second_rolls_back_the_first(
        admin_client, db):
    """One run, several products: the earlier product's deduction is atomic too."""
    cid = _company(admin_client, db, "MultiCo")
    sid = _sale(admin_client, cid, "PI-MULTI-1", ("MultiProdA", "MultiProdB"))
    _chemical(admin_client, "MultiChem-A", 100)
    _chemical(admin_client, "MultiChem-B", 100)
    inv = _invoice(admin_client, db, sid, "INV-MULTI-1")
    rid = _recipe(admin_client, cid, inv, "MultiRecipe", {
        "MultiProdA": [("MultiChem-A", 10)],
        "MultiProdB": [("MultiChem-B", 10)],
    })
    db.execute("UPDATE chemicals SET current_qty = 0.5 WHERE name = %s",
               ("MultiChem-B",))
    db.commit()

    before_stock = _stock(db, ["MultiChem-A", "MultiChem-B"])
    before_state = _state(db, sid, inv)

    r = _produce(admin_client, rid, {"MultiProdA": 10, "MultiProdB": 10})
    assert r.status_code == 400, r.get_json()
    assert "insufficient stock" in r.get_json()["error"]

    assert _stock(db, ["MultiChem-A", "MultiChem-B"]) == before_stock
    assert _state(db, sid, inv) == before_state


def test_produce_two_products_commits_one_run_with_two_outputs(admin_client, db):
    """Success path: one header, one output per product, one deduction each."""
    cid = _company(admin_client, db, "MultiOkCo")
    sid = _sale(admin_client, cid, "PI-MULTOK-1", ("MultiOkProdA", "MultiOkProdB"))
    _chemical(admin_client, "MultiOkChem-1", 100)
    _chemical(admin_client, "MultiOkChem-2", 100)
    inv = _invoice(admin_client, db, sid, "INV-MULTOK-1")
    rid = _recipe(admin_client, cid, inv, "MultiOkRecipe", {
        "MultiOkProdA": [("MultiOkChem-1", 10)],
        "MultiOkProdB": [("MultiOkChem-2", 10)],
    })

    r = _produce(admin_client, rid, {"MultiOkProdA": 10, "MultiOkProdB": 20})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()["runs"][0]
    assert float(body["production_qty"]) == 30.0
    assert len(body["outputs"]) == 2

    # 10% of 10 = 1.0 and 10% of 20 = 2.0 deducted.
    assert _stock(db, ["MultiOkChem-1", "MultiOkChem-2"]) == [
        ("MultiOkChem-1", 99.0), ("MultiOkChem-2", 98.0)]
    state = _state(db, sid, inv)
    assert state["runs"] == 1
    assert state["outputs"] == 2
    assert state["run_items"] == 2
    assert state["invoice_status"] == "produced"


# ---------- 2. booking is one transaction (invoice + sale together) ----------


def test_book_invoice_moves_invoice_and_sale_together(admin_client, db, produce_for):
    cid = _company(admin_client, db, "BookCo")
    sid = _sale(admin_client, cid, "PI-BOOK-AT-1", ("BookProd",))
    inv = _invoice(admin_client, db, sid, "INV-BOOK-AT-1")
    produce_for(cid, sid, inv, "BookAT", "BookProd")

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
    sid = _sale(admin_client, cid, "PI-BOOKF-1", ("BookFailProd",))
    inv = _invoice(admin_client, db, sid, "INV-BOOKF-1")
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
    sid = _sale(admin_client, cid, "PI-BOOK409-1", ("Book409Prod",))
    _chemical(admin_client, "Book409Chem", 100)
    inv = _invoice(admin_client, db, sid, "INV-BOOK409-1")
    rid = _recipe(admin_client, cid, inv, "Book409Recipe",
                  {"Book409Prod": [("Book409Chem", 10)]})

    # Booking requires production and shipping requires booking (Phase 2).
    r = _produce(admin_client, rid, {"Book409Prod": 5})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 200, r.get_json()
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


# ---------- 3. a run can never be steered at another company ----------


def test_produce_refuses_a_company_mismatch(admin_client, db):
    cid_a = _company(admin_client, db, "OwnCoA")
    sid_a = _sale(admin_client, cid_a, "PI-OWNA-1", ("OwnProdA",))
    _chemical(admin_client, "OwnChem-A", 100)
    inv_a = _invoice(admin_client, db, sid_a, "INV-OWN-A")
    rid = _recipe(admin_client, cid_a, inv_a, "OwnRecipe-A",
                  {"OwnProdA": [("OwnChem-A", 10)]})

    cid_b = _company(admin_client, db, "OwnCoB")
    sid_b = _sale(admin_client, cid_b, "PI-OWNB-1", ("OwnProdB",))
    inv_b = _invoice(admin_client, db, sid_b, "INV-OWN-B")

    before_stock = _stock(db, ["OwnChem-A"])
    before_state = _state(db, sid_b, inv_b)

    r = admin_client.post(f"/api/recipes/by-id/{rid}/produce", json={
        "company_id": cid_b,
        "outputs": [{"product_name": "OwnProdA", "quantity": 10}]})
    assert r.status_code == 404, r.get_json()

    # Customer B's sale/invoice must be untouched and no run may exist.
    assert _stock(db, ["OwnChem-A"]) == before_stock
    assert _state(db, sid_b, inv_b) == before_state


# ---------- 4. invoice lifecycle is forward-only on the produce path ----------


def test_produce_does_not_downgrade_paid_invoice(admin_client, db):
    """Commit 7726eef: producing against a paid invoice must not downgrade it."""
    cid = _company(admin_client, db, "PaidCo")
    sid = _sale(admin_client, cid, "PI-PAID-1", ("PaidProd",))   # total 50
    _chemical(admin_client, "PaidChem-1", 100)
    inv = _invoice(admin_client, db, sid, "INV-PAID-1")
    rid = _recipe(admin_client, cid, inv, "PaidRecipe",
                  {"PaidProd": [("PaidChem-1", 10)]})
    # Phase 2 order: production -> booking -> shipping -> payment.
    r = _produce(admin_client, rid, {"PaidProd": 10})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-10-20"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/ship",
                          json={"actual_ship_date": "2026-10-21"})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/pay",
                          json={"payment_amount": 50, "payment_date": "2026-10-25"})
    assert r.status_code == 201, r.get_json()
    assert _invoice_status(db, inv) == "paid"

    before_stock = _stock(db, ["PaidChem-1"])
    before_state = _state(db, sid, inv)

    r = _produce(admin_client, rid, {"PaidProd": 10})
    assert r.status_code == 409, r.get_json()
    assert "already paid" in r.get_json()["error"]

    assert _invoice_status(db, inv) == "paid"
    assert _stock(db, ["PaidChem-1"]) == before_stock
    # Refused run leaves NO orphan production_runs row behind.
    assert _state(db, sid, inv) == before_state


def test_produce_never_downgrades_shipped_invoice_or_ship_booked_sale(admin_client, db):
    cid = _company(admin_client, db, "FwdCo")
    sid = _sale(admin_client, cid, "PI-FWD-1", ("FwdProd",))
    _chemical(admin_client, "FwdChem-1", 100)

    # (a) shipped invoice -> refused, nothing moves
    inv_ship = _invoice(admin_client, db, sid, "INV-FWD-SHIPPED")
    rid = _recipe(admin_client, cid, inv_ship, "FwdRecipe",
                  {"FwdProd": [("FwdChem-1", 10)]})
    # Phase 2 order: production -> booking -> shipping.
    r = _produce(admin_client, rid, {"FwdProd": 10})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv_ship}/book",
                          json={"approx_ship_date": "2026-10-15"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv_ship}/ship",
                          json={"actual_ship_date": "2026-10-20"})
    assert r.status_code == 201, r.get_json()
    before_stock = _stock(db, ["FwdChem-1"])
    r = _produce(admin_client, rid, {"FwdProd": 10})
    assert r.status_code == 409, r.get_json()
    assert _invoice_status(db, inv_ship) == "shipped"
    assert _stock(db, ["FwdChem-1"]) == before_stock

    # (b) a booked sale (shipment_status='ship_booked') is never pushed back
    sid_book = _sale(admin_client, cid, "PI-FWD-2", ("FwdProd",))
    inv_book = _invoice(admin_client, db, sid_book, "INV-FWD-BOOKED")
    rid_book = _recipe(admin_client, cid, inv_book, "FwdRecipeBook",
                       {"FwdProd": [("FwdChem-1", 10)]})
    # Booking requires production first.
    r = _produce(admin_client, rid_book, {"FwdProd": 10})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/sales/{sid_book}/invoices/{inv_book}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()
    assert _shipment_status(db, sid_book) == "ship_booked"
    r = _produce(admin_client, rid_book, {"FwdProd": 10})
    assert r.status_code == 201, r.get_json()
    assert _shipment_status(db, sid_book) == "ship_booked"


def test_produce_keeps_booked_invoice_booked(admin_client, db):
    """Produce is forward-only: a ``booked`` invoice must NOT be pulled back
    to ``produced``.

    The lifecycle is [planned, produced, booked, shipped, paid], so 'produced'
    is EARLIER than 'booked' — writing it on produce is a backward move that
    the rest of the lifecycle (sales.update_invoice_status) refuses with a 409.
    The production still happened: the run row, its outputs, links and the
    ingredient deduction all stand, and the sale's shipment sub-step still
    reports the production. Only the invoice's lifecycle position is left alone.
    """
    cid = _company(admin_client, db, "BookedInvCo")
    sid = _sale(admin_client, cid, "PI-BKD-1", ("BkdProd",))
    _chemical(admin_client, "BkdChem-1", 100)
    inv = _invoice(admin_client, db, sid, "INV-BKD-1")
    rid = _recipe(admin_client, cid, inv, "BkdRecipe",
                  {"BkdProd": [("BkdChem-1", 10)]})
    # Booking requires production first.
    r = _produce(admin_client, rid, {"BkdProd": 10})
    assert r.status_code == 201, r.get_json()
    r = admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                          json={"approx_ship_date": "2026-11-01"})
    assert r.status_code == 200, r.get_json()
    assert _invoice_status(db, inv) == "booked"
    assert _shipment_status(db, sid) == "ship_booked"

    r = _produce(admin_client, rid, {"BkdProd": 10})
    assert r.status_code == 201, r.get_json()

    # The run really happened — run row, links and the single atomic deduction.
    run_id = r.get_json()["runs"][0]["run_id"]
    links = {(l[0], l[1]) for l in db.execute(
        "SELECT sale_id, invoice_id FROM production_run_links WHERE run_id = %s",
        (run_id,)).fetchall()}
    assert (sid, inv) in links
    assert _stock(db, ["BkdChem-1"]) == [("BkdChem-1", 98.0)]

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
    sid = _sale(admin_client, cid, "PI-RERUN-1", ("ReRunProd",))
    _chemical(admin_client, "ReRunChem-1", 100)
    inv = _invoice(admin_client, db, sid, "INV-RERUN-1")
    rid = _recipe(admin_client, cid, inv, "ReRunRecipe",
                  {"ReRunProd": [("ReRunChem-1", 10)]})
    # Booking requires production first.
    pre = _produce(admin_client, rid, {"ReRunProd": 10})
    assert pre.status_code == 201, pre.get_json()
    assert admin_client.post(f"/api/sales/{sid}/invoices/{inv}/book",
                             json={"approx_ship_date": "2026-11-01"}
                             ).status_code == 200

    for _ in range(2):
        r = _produce(admin_client, rid, {"ReRunProd": 10})
        assert r.status_code == 201, r.get_json()

    assert _invoice_status(db, inv) == "booked"
    assert _shipment_status(db, sid) == "ship_booked"
    # Three runs (the pre-book one plus two), three deductions.
    assert _stock(db, ["ReRunChem-1"]) == [("ReRunChem-1", 97.0)]
    assert _state(db, sid, inv)["runs"] == 3


# ---------- 5. @admin_required on the mutating routes ----------


def test_non_admin_gets_403_on_mutating_invoice_and_production_routes(
        admin_client, user_client, db):
    cid = _company(admin_client, db, "AuthCo")
    sid = _sale(admin_client, cid, "PI-AUTH-1", ("AuthProd",))
    _chemical(admin_client, "AuthChem-1", 100)
    inv = _invoice(admin_client, db, sid, "INV-AUTH-1")
    rid = _recipe(admin_client, cid, inv, "AuthRecipe",
                  {"AuthProd": [("AuthChem-1", 10)]})

    before_stock = _stock(db, ["AuthChem-1"])
    before_state = _state(db, sid, inv)

    attempts = [
        (f"/api/recipes/by-id/{rid}/produce",
         {"outputs": [{"product_name": "AuthProd", "quantity": 10}]}),
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


# ---------- 6. nonexistent sale on invoice create is 404, not a fake 400 ----------


def test_create_invoice_for_unknown_sale_returns_404(admin_client, db):
    cid = _company(admin_client, db, "MissingSaleCo")
    sid = _sale(admin_client, cid, "PI-MISS-1", ("MissingSaleProd",))
    item = db.execute("SELECT id FROM sale_items WHERE sale_id = %s",
                      (sid,)).fetchone()[0]
    before = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    # Valid payload so the 404 can only come from the sale lookup, not from a
    # body-validation 400.
    r = admin_client.post("/api/sales/999999/invoices",
                          json={"invoice_number": "INV-MISS-1",
                                "invoice_date": "2026-01-15",
                                "lines": [{"sale_item_id": item, "quantity": 1}]})
    assert r.status_code == 404, r.get_json()
    assert db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == before


# ---------- 7. a mixed-source run is atomic too (Slice 2) ----------
#
# Outward sourcing adds a fourth write to the run: the immutable per-run
# source evidence. A failure anywhere after it must remove that evidence with
# everything else, or a later reader would see a delivery that was never
# produced against.


def _outward_source(admin_client, db, client_id, name, quantity=25, number="PO-ATOMIC-OUT"):
    r = admin_client.post("/api/chemical-purchases", json={
        "supplier_name": "Outward Supplier", "purchase_number": number,
        "purchase_date": "2026-01-15", "delivery_type": "outward",
        "client_id": client_id,
        "lines": [{"chemical_name": name, "quantity": quantity, "unit": "KG",
                   "is_new": True}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["items"][0]["id"]


def _add_outward_item(admin_client, recipe_id, product, chemical, percentage,
                      line_id):
    r = admin_client.post(
        f"/api/recipes/by-id/{recipe_id}/products/{quote(product)}/items",
        json={"chemical": chemical, "percentage": percentage,
              "source_type": "outward", "outward_purchase_line_id": line_id})
    assert r.status_code == 200, r.get_json()


def test_mixed_source_run_rolls_back_source_evidence_when_audit_fails(
        admin_client, db, monkeypatch):
    """Audit blows up AFTER the deduction + evidence writes: nothing survives.

    ``create_production_run`` audits inside the same transaction, so a dead
    audit sink must take the run, its outputs, its links, the in-house
    deduction AND the outward source snapshot back with it.
    """
    from raas_tracker import recipes as recipes_module

    cid = _company(admin_client, db, "MixAtomicCo")
    sid = _sale(admin_client, cid, "PI-MIXAT-1", ("MixAtProdA", "MixAtProdB"))
    line_id = _outward_source(admin_client, db, cid, "MixAtOut")
    _chemical(admin_client, "MixAtIn", 100)
    out_id = db.execute("SELECT id FROM chemicals WHERE name = 'MixAtOut'"
                        ).fetchone()[0]
    in_id = db.execute("SELECT id FROM chemicals WHERE name = 'MixAtIn'"
                       ).fetchone()[0]
    assert out_id < in_id, "the evidence row must be written before the failure"

    inv = _invoice(admin_client, db, sid, "INV-MIXAT-1")
    rid = _recipe(admin_client, cid, inv, "MixAtomicRecipe",
                  {"MixAtProdB": [("MixAtIn", 10)]})
    _add_outward_item(admin_client, rid, "MixAtProdA", "MixAtOut", 10, line_id)

    before_stock = _stock(db, ["MixAtIn", "MixAtOut"])
    before_state = _state(db, sid, inv)

    # Injected only for the run under test: add_recipe and add_recipe_item audit
    # through this same sink, so patching it earlier fails the setup instead of
    # proving anything about production atomicity.
    def _boom(*args, **kwargs):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(recipes_module, "log_audit_action", _boom)

    r = _produce(admin_client, rid, {"MixAtProdA": 10, "MixAtProdB": 10})
    assert r.status_code == 500, r.get_json()

    assert _stock(db, ["MixAtIn", "MixAtOut"]) == before_stock
    assert _state(db, sid, inv) == before_state
    assert db.execute("SELECT COUNT(*) FROM production_run_items").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_logs "
                      "WHERE action = 'PRODUCTION_RUN_CREATE'").fetchone()[0] == 0


def test_all_outward_run_writes_evidence_without_touching_stock(admin_client, db):
    """The success-side twin: one transaction, evidence committed, stock still.

    Pinned here as well as in the sourcing suite because it is the atomicity
    contract -- the evidence row and the run header commit together, and no
    ``update_stock`` call is made at all when every ingredient is outward.
    """
    cid = _company(admin_client, db, "AllOutAtomicCo")
    sid = _sale(admin_client, cid, "PI-ALLAT-1", ("AllAtProd",))
    line_id = _outward_source(admin_client, db, cid, "AllAtOut")
    inv = _invoice(admin_client, db, sid, "INV-ALLAT-1")
    rid = _recipe(admin_client, cid, inv, "AllAtomicRecipe", {})
    _add_outward_item(admin_client, rid, "AllAtProd", "AllAtOut", 40, line_id)

    before_stock = _stock(db, ["AllAtOut"])
    r = _produce(admin_client, rid, {"AllAtProd": 10})
    assert r.status_code == 201, r.get_json()

    state = _state(db, sid, inv)
    assert (state["runs"], state["outputs"], state["run_items"],
            state["links"]) == (1, 1, 1, 1)
    assert _stock(db, ["AllAtOut"]) == before_stock
    row = db.execute("SELECT source_type, deducted_qty, purchase_number, "
                     "source_qty FROM production_run_items").fetchone()
    assert row == ("outward", 0, "PO-ATOMIC-OUT", 25)
    assert state["invoice_status"] == "produced"
