"""Pipeline gate contract tests.

``lc_received -> shipment_ongoing`` requires no invoice: invoices are created
*in* Shipment Ongoing, from the PI lines (one or many, partial quantities).

``shipment_ongoing -> payment_due`` requires at least one invoice that has been
**booked** (or already shipped/paid). Booking is the gate that proves the
invoice's product was produced, so the payment gate no longer re-derives
production from recipe/run content.
"""
import uuid

from chem_stock import add_recipe, add_sale
from raas_tracker.sales import book_invoice, create_invoice
from raas_tracker.recipes import create_production_run


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(db, name):
    row = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    db.commit()
    return row[0]


def _sale(db, pi_number, company_id=None, product="Widget", qty=10,
          price=5.0):
    data = {"pi_number": pi_number, "client_name": "T Co"}
    if company_id is not None:
        data["company_id"] = company_id
    return add_sale(db, data,
                    [{"product_name": product, "quantity": qty,
                      "unit_price": price}])


def _sale_item_id(db, sale_id):
    return db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchone()[0]


def _invoice(db, sale_id, number):
    """Create an invoice covering the sale's PI lines in full."""
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in db.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    return create_invoice(db, sale_id, number, lines=lines,
                          invoice_date="2026-01-15")


def _mk_lc(admin_client, company_id, lc_number, **kw):
    r = admin_client.post("/api/lcs", json={
        "company_id": company_id,
        "lc_number": lc_number,
        **kw,
    })
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()


# --------------------------------------------------------------------------- #
# Shipment entry: no invoice required
# --------------------------------------------------------------------------- #
def test_shipment_ongoing_allowed_without_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"G1 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G1-{tag}")
    sid = _sale(db, f"PI-G1-{tag}", company_id=co, product=f"G1Prod-{tag}")
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "shipment_ongoing"
    assert db.execute(
        "SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0] == (
            "shipment_ongoing")


def test_shipment_ongoing_passes_with_invoice_only(admin_client, db):
    tag = _tag()
    co = _company(db, f"G2 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G2-{tag}")
    product = f"G2Prod-{tag}"
    sid = _sale(db, f"PI-G2-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    # No recipe and no production run — the old combined gate would reject
    # this move. The new split gate allows it with only an invoice.
    _invoice(db, sid, f"INV-G2-{tag}")

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "shipment_ongoing"


# --------------------------------------------------------------------------- #
# Barrier 2: payment_due needs a shipped invoice
# --------------------------------------------------------------------------- #
def test_payment_due_blocked_without_a_shipped_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"G3 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G3-{tag}")
    product = f"G3Prod-{tag}"
    sid = _sale(db, f"PI-G3-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    _invoice(db, sid, f"INV-G3-{tag}")
    # An invoice exists but is only 'planned'. Move to shipment_ongoing first.
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert body["all_shipped_ok"] is False
    assert any("ship" in str(m).lower() for m in body["missing"])


def test_payment_due_blocked_with_recipe_but_no_shipped_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"G4 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G4-{tag}")
    product = f"G4Prod-{tag}"
    sid = _sale(db, f"PI-G4-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    _invoice(db, sid, f"INV-G4-{tag}")
    assert add_recipe(db, f"R-G4-{tag}", 100, 0, co, product) is not False
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert any("ship" in str(m).lower() for m in body["missing"])


def test_payment_due_ignores_a_sale_linked_run_that_never_shipped(admin_client, db):
    """Production alone is not enough any more: the invoice must ship."""
    tag = _tag()
    co = _company(db, f"G5 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G5-{tag}")
    product = f"G5Prod-{tag}"
    sid = _sale(db, f"PI-G5-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    _invoice(db, sid, f"INV-G5-{tag}")
    assert add_recipe(db, f"R-G5-{tag}", 100, 0, co, product) is not False
    # Production run attached to the sale but NOT to its invoice.
    recipe_id = db.execute(
        "SELECT id FROM recipes WHERE company_id = %s AND lower(name) = lower(%s)",
        (co, f"R-G5-{tag}")).fetchone()[0]
    sale_item_id = _sale_item_id(db, sid)
    run_id = db.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, order_number, batch_number, production_date, qty_produced, created_by) VALUES (%s, %s, NULL, NULL, NULL, 42.5, NULL) RETURNING id",
        (recipe_id, sale_item_id)).fetchone()[0]
    db.execute(
        "INSERT INTO production_run_links (run_id, sale_id, invoice_id) VALUES (%s, %s, NULL)",
        (run_id, sid))
    db.commit()
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()


def test_payment_due_passes_with_a_shipped_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"G6 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G6-{tag}")
    product = f"G6Prod-{tag}"
    sid = _sale(db, f"PI-G6-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    _invoice(db, sid, f"INV-G6-{tag}")
    # Get the invoice id just created.
    inv_id = db.execute(
        "SELECT id FROM invoices WHERE sale_id = %s AND invoice_number = %s",
        (sid, f"INV-G6-{tag}")).fetchone()[0]
    assert add_recipe(db, f"R-G6-{tag}", 100, 0, co, product) is not False
    recipe_id = db.execute(
        "SELECT id FROM recipes WHERE company_id = %s AND lower(name) = lower(%s)",
        (co, f"R-G6-{tag}")).fetchone()[0]
    sale_item_id = _sale_item_id(db, sid)
    run_id = db.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, order_number, batch_number, production_date, qty_produced, created_by) VALUES (%s, %s, NULL, NULL, NULL, 42.5, NULL) RETURNING id",
        (recipe_id, sale_item_id)).fetchone()[0]
    db.execute(
        "INSERT INTO production_run_links (run_id, sale_id, invoice_id) VALUES (%s, %s, %s)",
        (run_id, sid, inv_id))
    db.commit()
    assert book_invoice(db, inv_id, "2026-11-01") is True
    # Shipment is the gate (status-only here: this test is about the LC gate).
    db.execute(
        "UPDATE invoices SET status = 'shipped', actual_ship_date = %s "
        "WHERE id = %s", ("2026-11-10", inv_id))
    db.commit()
    assert admin_client.post(f"/api/lcs/{lc['id']}/move",
                             json={"new_stage": "shipment_ongoing"}).status_code == 200

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 200, r.get_json()


def test_direct_jump_from_lc_received_to_payment_due_requires_a_shipped_invoice(
        admin_client, db):
    tag = _tag()
    co = _company(db, f"G7 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G7-{tag}")
    product = f"G7Prod-{tag}"
    sid = _sale(db, f"PI-G7-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    _invoice(db, sid, f"INV-G7-{tag}")
    assert add_recipe(db, f"R-G7-{tag}", 100, 0, co, product) is not False

    # A direct overshoot to payment_due still has to clear the shipped gate.
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert any("ship" in str(m).lower() for m in body["missing"])
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "lc_received"


def test_readiness_endpoint_reports_booked_state(admin_client, db):
    tag = _tag()
    co = _company(db, f"G8 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G8-{tag}")
    product = f"G8Prod-{tag}"
    sid = _sale(db, f"PI-G8-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200

    # Nothing yet: invoices_ok/booked_ok/shipped_ok are false; production
    # checks are vacuous-truth and informational only.
    r = admin_client.get(f"/api/lcs/{lc['id']}/readiness")
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["invoices_ok"] is False
    assert out["booked_ok"] is False
    assert out["shipped_ok"] is False
    assert out["all_shipped_ok"] is False
    assert out["recipes_ok"] is True
    assert out["produced_ok"] is True
    assert out["pis"][0]["id"] == sid
    # The barrier-only invoice-draft payload is gone with the invoice gate.
    assert "invoice_drafts" not in out

    # After invoicing, the informational flag flips...
    _invoice(db, sid, f"INV-G8-{tag}")
    out = admin_client.get(f"/api/lcs/{lc['id']}/readiness").get_json()
    assert out["invoices_ok"] is True
    # ...but the payment barrier still wants a shipped invoice.
    assert out["booked_ok"] is False
    assert out["shipped_ok"] is False
    assert out["all_shipped_ok"] is False
