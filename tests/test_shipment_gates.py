"""Phase 1 gate contract tests.

Barrier 1 (``lc_received -> shipment_ongoing``) requires only that at least
one invoice exists. Previously it also required every product to have a
recipe, so an invoice would not unlock production.

Barrier 2 (``shipment_ongoing -> payment_due``) requires, for every distinct
invoiced product, both:
  * a company recipe for that product_name, and
  * an invoice-linked production run for that recipe.
"""
import uuid

import pytest

from chem_stock import add_recipe, add_sale
from raas_tracker.sales import create_invoice
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


def _mk_lc(admin_client, company_id, lc_number, **kw):
    r = admin_client.post("/api/lcs", json={
        "company_id": company_id,
        "lc_number": lc_number,
        **kw,
    })
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()


# --------------------------------------------------------------------------- #
# Barrier 1: shipment_ongoing needs an invoice, nothing else
# --------------------------------------------------------------------------- #
def test_shipment_ongoing_blocked_without_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"G1 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G1-{tag}")
    sid = _sale(db, f"PI-G1-{tag}", company_id=co, product=f"G1Prod-{tag}")
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert "invoice" in str(body["missing"])
    assert any("invoice" in str(m).lower() for m in body["missing"])
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "lc_received"


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
    create_invoice(db, sid, f"INV-G2-{tag}")

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "shipment_ongoing"


# --------------------------------------------------------------------------- #
# Barrier 2: payment_due needs product recipe + matching invoice-run
# --------------------------------------------------------------------------- #
def test_payment_due_blocked_without_recipe_or_run(admin_client, db):
    tag = _tag()
    co = _company(db, f"G3 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G3-{tag}")
    product = f"G3Prod-{tag}"
    sid = _sale(db, f"PI-G3-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    create_invoice(db, sid, f"INV-G3-{tag}")
    # No recipe and no production yet. Move to shipment_ongoing first.
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    missing = body["missing"]
    assert any("recipe" in str(m).lower() for m in missing) or any(
        "production" in str(m).lower() for m in missing)


def test_payment_due_blocked_with_recipe_but_no_run(admin_client, db):
    tag = _tag()
    co = _company(db, f"G4 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G4-{tag}")
    product = f"G4Prod-{tag}"
    sid = _sale(db, f"PI-G4-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    create_invoice(db, sid, f"INV-G4-{tag}")
    assert add_recipe(db, f"R-G4-{tag}", 100, 0, co, product) is not False
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert any("production" in str(m).lower() for m in body["missing"])


def test_payment_due_blocked_when_run_linked_only_to_sale_not_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"G5 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G5-{tag}")
    product = f"G5Prod-{tag}"
    sid = _sale(db, f"PI-G5-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    create_invoice(db, sid, f"INV-G5-{tag}")
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


def test_payment_due_passes_with_invoice_linked_run(admin_client, db):
    tag = _tag()
    co = _company(db, f"G6 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G6-{tag}")
    product = f"G6Prod-{tag}"
    sid = _sale(db, f"PI-G6-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    create_invoice(db, sid, f"INV-G6-{tag}")
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
    assert admin_client.post(f"/api/lcs/{lc['id']}/move",
                             json={"new_stage": "shipment_ongoing"}).status_code == 200

    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 200, r.get_json()


def test_direct_jump_from_lc_received_to_payment_due_requires_both_gates(admin_client, db):
    tag = _tag()
    co = _company(db, f"G7 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-G7-{tag}")
    product = f"G7Prod-{tag}"
    sid = _sale(db, f"PI-G7-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200

    # No invoice, no recipe, no run: both barriers fail.
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    missing = body["missing"]
    assert any("invoices" in str(m).lower() for m in missing)
