"""Phase 2 lane B (API routes) TDD tests.

Routes in flask_app.py against the parallel-lane service contract:
  create_lc / get_lc / list_lcs / attach_pis / detach_pi / move_lc_stage /
  create_invoice_item / list_invoice_items / check_lc_shipment_ready.

Covers: LC CRUD, attach/detach (404/400 paths), move (409 gate + 404),
batch atomicity (second-PI-fails rolls back first), invoice-item 409
over-qty. Rate-limit presence is covered by tests/test_rate_limits.py —
routes only need the decorators.
"""
import uuid

import pytest

from chem_stock import add_recipe, add_sale
from raas_tracker.sales import create_invoice


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
    body = {"company_id": company_id, "lc_number": lc_number, **kw}
    r = admin_client.post("/api/lcs", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


# --------------------------------------------------------------------------- #
# LC CRUD
# --------------------------------------------------------------------------- #
def test_lc_create_and_get(admin_client, db):
    co = _company(db, f"LC API Co {_tag()}")
    created = _mk_lc(admin_client, co, f"LC-{_tag()}", notes="hello")
    assert created["lc_number"].startswith("LC-")
    assert created["company_id"] == co

    r = admin_client.get(f"/api/lcs/{created['id']}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["lc_number"] == created["lc_number"]
    assert body["pis"] == []
    assert body["notes"] == "hello"


def test_lc_list_filters_by_company(admin_client, db):
    tag = _tag()
    co1 = _company(db, f"LC L1 {tag}")
    co2 = _company(db, f"LC L2 {tag}")
    _mk_lc(admin_client, co1, f"LC-A-{tag}")
    _mk_lc(admin_client, co2, f"LC-B-{tag}")
    all_rows = admin_client.get("/api/lcs").get_json()
    assert len(all_rows) >= 2
    only1 = admin_client.get(f"/api/lcs?company_id={co1}").get_json()
    assert {row["company_id"] for row in only1} == {co1}
    assert any(row["lc_number"] == f"LC-A-{tag}" for row in only1)


def test_lc_create_validation_400_shape(admin_client, db):
    co = _company(db, f"LC V {_tag()}")
    r = admin_client.post("/api/lcs", json={"company_id": co})
    assert r.status_code == 400
    body = r.get_json()
    assert "error" in body
    assert isinstance(body.get("details"), list) and body["details"]
    assert all("field" in d and "message" in d for d in body["details"])


def test_lc_create_unknown_company_400(admin_client, db):
    r = admin_client.post("/api/lcs",
                          json={"company_id": 999999,
                                "lc_number": f"LC-{_tag()}"})
    assert r.status_code == 400


def test_lc_create_duplicate_409(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC D {_tag()}")
    _mk_lc(admin_client, co, f"LC-DUP-{tag}")
    r = admin_client.post("/api/lcs",
                          json={"company_id": co,
                                "lc_number": f"LC-DUP-{tag}"})
    assert r.status_code == 409


def test_lc_get_404(admin_client, db):
    assert admin_client.get("/api/lcs/999999").status_code == 404


def test_lc_put_mutable_only(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC P {_tag()}")
    created = _mk_lc(admin_client, co, f"LC-P-{tag}")
    r = admin_client.put(f"/api/lcs/{created['id']}",
                         json={"notes": "updated", "bank_ref": "BR-1"})
    assert r.status_code == 200, r.get_json()
    body = admin_client.get(f"/api/lcs/{created['id']}").get_json()
    assert body["notes"] == "updated"
    assert body["bank_ref"] == "BR-1"


def test_lc_put_immutable_rejected(admin_client, db):
    co = _company(db, f"LC PI {_tag()}")
    created = _mk_lc(admin_client, co, f"LC-PI-{_tag()}")
    assert admin_client.put(
        f"/api/lcs/{created['id']}",
        json={"lc_number": "NOPE"}).status_code == 400
    assert admin_client.put(
        f"/api/lcs/{created['id']}",
        json={"company_id": 123}).status_code == 400
    assert admin_client.put("/api/lcs/999999",
                            json={"notes": "x"}).status_code == 404


def test_lc_delete_flows(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC Del {_tag()}")
    created = _mk_lc(admin_client, co, f"LC-DEL-{tag}")
    # 404 on missing
    assert admin_client.delete("/api/lcs/999999").status_code == 404
    # 409 while PIs attached
    sid = _sale(db, f"PI-DEL-{tag}", company_id=co)
    ar = admin_client.post(f"/api/lcs/{created['id']}/pis",
                           json={"sale_ids": [sid]})
    assert ar.status_code == 200, ar.get_json()
    dr = admin_client.delete(f"/api/lcs/{created['id']}")
    assert dr.status_code == 409
    # detach then delete succeeds
    assert admin_client.delete(
        f"/api/lcs/{created['id']}/pis/{sid}").status_code == 200
    assert admin_client.delete(
        f"/api/lcs/{created['id']}").status_code == 200
    assert admin_client.get(f"/api/lcs/{created['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# attach / detach
# --------------------------------------------------------------------------- #
def test_attach_detach_happy(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC AD {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-AD-{tag}")
    s1 = _sale(db, f"PI-AD1-{tag}", company_id=co)
    s2 = _sale(db, f"PI-AD2-{tag}", company_id=co)
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                          json={"sale_ids": [s1, s2]})
    assert r.status_code == 200, r.get_json()
    body = admin_client.get(f"/api/lcs/{lc['id']}").get_json()
    assert sorted(p["id"] for p in body["pis"]) == sorted([s1, s2])

    d = admin_client.delete(f"/api/lcs/{lc['id']}/pis/{s1}")
    assert d.status_code == 200, d.get_json()
    body = admin_client.get(f"/api/lcs/{lc['id']}").get_json()
    assert [p["id"] for p in body["pis"]] == [s2]


def test_attach_404_lc(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC A404 {_tag()}")
    sid = _sale(db, f"PI-A404-{tag}", company_id=co)
    r = admin_client.post("/api/lcs/999999/pis", json={"sale_ids": [sid]})
    assert r.status_code == 404


def test_attach_400_empty(admin_client, db):
    co = _company(db, f"LC AE {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-AE-{_tag()}")
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis", json={"sale_ids": []})
    assert r.status_code == 400
    r2 = admin_client.post(f"/api/lcs/{lc['id']}/pis", json={})
    assert r2.status_code == 400
    assert isinstance(r2.get_json().get("details"), list)


def test_attach_400_company_mismatch(admin_client, db):
    tag = _tag()
    co_a = _company(db, f"LC MA {tag}")
    co_b = _company(db, f"LC MB {tag}")
    lc = _mk_lc(admin_client, co_a, f"LC-MM-{tag}")
    other = _sale(db, f"PI-MM-{tag}", company_id=co_b)
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                          json={"sale_ids": [other]})
    assert r.status_code == 400


def test_attach_unknown_sale_404(admin_client, db):
    co = _company(db, f"LC AU {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-AU-{_tag()}")
    r = admin_client.post(f"/api/lcs/{lc['id']}/pis",
                          json={"sale_ids": [999999]})
    assert r.status_code == 404


def test_detach_404_not_linked(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC DN {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-DN-{tag}")
    sid = _sale(db, f"PI-DN-{tag}", company_id=co)
    assert admin_client.delete(
        f"/api/lcs/{lc['id']}/pis/{sid}").status_code == 404
    assert admin_client.delete(
        f"/api/lcs/999999/pis/{sid}").status_code == 404


# --------------------------------------------------------------------------- #
# move + shipment gate
# --------------------------------------------------------------------------- #
def test_move_happy_skips_gate_for_other_stages(admin_client, db):
    # Gate re-review: the gate is on CROSSING shipment_ongoing, so an overshoot
    # to payment_due is gated too — provision readiness and expect 200.
    tag = _tag()
    co = _company(db, f"LC MV {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-MV-{tag}")
    product = f"MVProd-{tag}"
    sid = _sale(db, f"PI-MV-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    assert add_recipe(db, f"R-MV-{tag}", 100, 0, co, product) is not False
    inv_id = create_invoice(db, sid, f"INV-MV-{tag}")['invoice_id']
    recipe_id = db.execute(
        "SELECT id FROM recipes WHERE company_id = %s AND lower(name)=lower(%s)",
        (co, f"R-MV-{tag}")).fetchone()[0]
    sale_item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sid,)).fetchone()[0]
    run_id = db.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, order_number, batch_number, production_date, qty_produced, created_by) VALUES (%s, %s, NULL, NULL, NULL, 10, NULL) RETURNING id",
        (recipe_id, sale_item_id)).fetchone()[0]
    db.execute("INSERT INTO production_run_links (run_id, sale_id, invoice_id) VALUES (%s, %s, %s)",
               (run_id, sid, inv_id))
    db.commit()
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "payment_due"


def test_move_409_gate_names_missing_preconditions(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC G {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-G-{tag}")
    sid = _sale(db, f"PI-G-{tag}", company_id=co, product=f"GProd-{tag}")
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 409, r.get_json()
    body = r.get_json()
    assert "missing" in body and len(body["missing"]) > 0
    # stage must not have moved
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "lc_received"


def test_move_passes_gate_with_recipe_and_invoice(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC GP {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-GP-{tag}")
    product = f"GPProd-{tag}"
    sid = _sale(db, f"PI-GP-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    assert add_recipe(db, f"R-GP-{tag}", 100, 0, co, product) is not False
    inv_id = create_invoice(db, sid, f"INV-GP-{tag}")['invoice_id']
    recipe_id = db.execute(
        "SELECT id FROM recipes WHERE company_id = %s AND lower(name)=lower(%s)",
        (co, f"R-GP-{tag}")).fetchone()[0]
    sale_item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id LIMIT 1",
        (sid,)).fetchone()[0]
    run_id = db.execute(
        "INSERT INTO production_runs (recipe_id, sale_item_id, order_number, batch_number, production_date, qty_produced, created_by) VALUES (%s, %s, NULL, NULL, NULL, 10, NULL) RETURNING id",
        (recipe_id, sale_item_id)).fetchone()[0]
    db.execute("INSERT INTO production_run_links (run_id, sale_id, invoice_id) VALUES (%s, %s, %s)",
               (run_id, sid, inv_id))
    db.commit()
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(
        f"/api/lcs/{lc['id']}").get_json()["stage"] == "shipment_ongoing"


def test_move_404(admin_client, db):
    r = admin_client.post("/api/lcs/999999/move",
                          json={"new_stage": "payment_due"})
    assert r.status_code == 404


def test_move_validation_400_shape(admin_client, db):
    co = _company(db, f"LC MVV {_tag()}")
    lc = _mk_lc(admin_client, co, f"LC-MVV-{_tag()}")
    r = admin_client.post(f"/api/lcs/{lc['id']}/move", json={})
    assert r.status_code == 400
    assert isinstance(r.get_json().get("details"), list)


# --------------------------------------------------------------------------- #
# batch create (ONE transaction)
# --------------------------------------------------------------------------- #
def _batch_payload(tag, co, n=2, product="BProd"):
    return {"sales": [
        {"sale": {"pi_number": f"PI-B{i}-{tag}", "company_id": co},
         "items": [{"product_name": f"{product}-{tag}",
                    "quantity": 5, "unit_price": 10}]}
        for i in range(n)
    ]}


def test_batch_happy(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC BH {_tag()}")
    r = admin_client.post("/api/sales/batch", json=_batch_payload(tag, co))
    assert r.status_code == 201, r.get_json()
    ids = r.get_json()["ids"]
    assert len(ids) == 2
    for sid in ids:
        assert admin_client.get(f"/api/sales/{sid}").status_code == 200


def test_batch_atomicity_second_fails_rolls_back_first(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC BA {_tag()}")
    payload = _batch_payload(tag, co)
    payload["sales"][1]["sale"]["company_id"] = 999999  # unknown company
    r = admin_client.post("/api/sales/batch", json=payload)
    assert r.status_code == 400, r.get_json()
    # first PI must NOT exist — the whole batch rolled back
    left = db.execute(
        "SELECT id FROM sales WHERE pi_number = %s",
        (f"PI-B0-{tag}",)).fetchone()
    assert left is None
    left2 = db.execute(
        "SELECT id FROM sales WHERE pi_number = %s",
        (f"PI-B1-{tag}",)).fetchone()
    assert left2 is None


def test_batch_validation_400_shape(admin_client, db):
    r = admin_client.post("/api/sales/batch", json={"sales": []})
    assert r.status_code == 400
    assert isinstance(r.get_json().get("details"), list)


def test_batch_dup_pi_warnings(admin_client, db):
    tag = _tag()
    co = _company(db, f"LC BW {_tag()}")
    _sale(db, f"PI-DUP-{tag}", company_id=co)
    payload = {"sales": [
        {"sale": {"pi_number": f"PI-DUP-{tag}", "company_id": co},
         "items": [{"product_name": "W", "quantity": 1, "unit_price": 1}]},
    ]}
    r = admin_client.post("/api/sales/batch", json=payload)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["warnings"], "dup PI numbers must warn (single-create semantics)"


# --------------------------------------------------------------------------- #
# invoice items
# --------------------------------------------------------------------------- #
def _invoiced_setup(db, tag, qty=10, price=5.0):
    co = _company(db, f"LC II {tag}")
    sid = _sale(db, f"PI-II-{tag}", company_id=co, qty=qty, price=price)
    item_id = _sale_item_id(db, sid)
    inv = create_invoice(db, sid, f"INV-II-{tag}", seed_lines=False)
    return inv["invoice_id"], item_id


def test_invoice_item_create_server_price(admin_client, db):
    tag = _tag()
    inv_id, item_id = _invoiced_setup(db, tag)
    r = admin_client.post(f"/api/invoices/{inv_id}/items",
                          json={"sale_item_id": item_id, "quantity": 4})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["unit_price"] == 5.0  # server-side, inherited from PI line
    assert body["quantity"] == 4

    listed = admin_client.get(f"/api/invoices/{inv_id}/items").get_json()
    assert len(listed) == 1
    assert listed[0]["unit_price"] == 5.0


def test_invoice_item_over_qty_409(admin_client, db):
    tag = _tag()
    inv_id, item_id = _invoiced_setup(db, tag, qty=10)
    assert admin_client.post(
        f"/api/invoices/{inv_id}/items",
        json={"sale_item_id": item_id, "quantity": 11}).status_code == 409
    # partial accumulation also enforced: 6 ok, then 5 exceeds remaining 4
    assert admin_client.post(
        f"/api/invoices/{inv_id}/items",
        json={"sale_item_id": item_id, "quantity": 6}).status_code == 201
    assert admin_client.post(
        f"/api/invoices/{inv_id}/items",
        json={"sale_item_id": item_id, "quantity": 5}).status_code == 409


def test_invoice_item_404_paths(admin_client, db):
    tag = _tag()
    _inv_id, item_id = _invoiced_setup(db, tag)
    assert admin_client.post(
        "/api/invoices/999999/items",
        json={"sale_item_id": item_id, "quantity": 1}).status_code == 404
    inv_id, _ = _invoiced_setup(db, f"X{tag}")
    assert admin_client.post(
        f"/api/invoices/{inv_id}/items",
        json={"sale_item_id": 999999, "quantity": 1}).status_code == 404
    assert admin_client.get(
        "/api/invoices/999999/items").status_code == 404


def test_invoice_item_validation_400_shape(admin_client, db):
    tag = _tag()
    inv_id, _item_id = _invoiced_setup(db, tag)
    r = admin_client.post(f"/api/invoices/{inv_id}/items", json={})
    assert r.status_code == 400
    assert isinstance(r.get_json().get("details"), list)


# --------------------------------------------------------------------------- #
# Oracle gate 2 remediation: B5(route)/M1/M3/B4-direct-jump/P2/P3
# --------------------------------------------------------------------------- #
def test_put_sale_lc_refused_when_linked(admin_client, db):
    """B5: PUT /api/sales/<id>/lc refuses 409 when the sale has lc_id."""
    tag = _tag()
    co = _company(db, f"LC B5R {tag}")
    lc = _mk_lc(admin_client, co, f"LC-B5R-{tag}")
    sid = _sale(db, f"PI-B5R-{tag}", company_id=co)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    r = admin_client.put(f"/api/sales/{sid}/lc",
                         json={"lc_number": "LC-X", "lc_date": "2026-01-01"})
    assert r.status_code == 409, r.get_json()
    assert "LC endpoints" in r.get_json().get("error", "")


def test_lc_put_date_mirrors_to_child_sale(admin_client, db):
    """M1: PUT LC lc_date is visible on the child sale row."""
    tag = _tag()
    co = _company(db, f"LC M1 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-M1-{tag}")
    sid = _sale(db, f"PI-M1-{tag}", company_id=co)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    r = admin_client.put(f"/api/lcs/{lc['id']}",
                         json={"lc_date": "2026-05-17"})
    assert r.status_code == 200, r.get_json()
    row = db.execute(
        "SELECT lc_date FROM sales WHERE id = %s", (sid,)).fetchone()
    assert row[0] == "2026-05-17"


def test_lc_delete_with_attached_pis_409(admin_client, db):
    """M3: delete with attached PIs -> 409 (locked count + FK backstop)."""
    tag = _tag()
    co = _company(db, f"LC M3 {tag}")
    lc = _mk_lc(admin_client, co, f"LC-M3-{tag}")
    sid = _sale(db, f"PI-M3-{tag}", company_id=co)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    r = admin_client.delete(f"/api/lcs/{lc['id']}")
    assert r.status_code == 409, r.get_json()


def test_move_direct_jump_gated_409(admin_client, db):
    """B4+B6 route: pi_issued LC jumping to shipment_ongoing is 409'd."""
    tag = _tag()
    co = _company(db, f"LC DJ {tag}")
    lc = _mk_lc(admin_client, co, f"LC-DJ-{tag}")
    sid = _sale(db, f"PI-DJ-{tag}", company_id=co, product=f"DJProd-{tag}")
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    db.execute("UPDATE letters_of_credit SET stage = 'pi_issued' WHERE id = %s",
               (lc["id"],))
    db.commit()
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 409, r.get_json()
    assert "missing" in r.get_json()


def test_legacy_gate_passes_move(admin_client, db):
    """P2 route: legacy recipes (NULL product) pass the shipment gate."""
    tag = _tag()
    co = _company(db, f"LC LEG {tag}")
    lc = _mk_lc(admin_client, co, f"LC-LEG-{tag}")
    product = f"LegProd-{tag}"
    sid = _sale(db, f"PI-LEG-{tag}", company_id=co, product=product)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    db.execute(
        "INSERT INTO recipes (name, company_id, product_name)"
        " VALUES (%s, %s, NULL)",
        (f"Legacy-R-{tag}", co),
    )
    db.commit()
    create_invoice(db, sid, f"INV-LEG-{tag}")
    r = admin_client.get(f"/api/production-source?sale_id={sid}")
    assert r.status_code == 200
    assert r.get_json()["products"][0]["recipes"], \
        "production-source must list legacy recipes"
    r = admin_client.post(f"/api/lcs/{lc['id']}/move",
                          json={"new_stage": "shipment_ongoing"})
    assert r.status_code == 200, r.get_json()


def test_batch_preserves_maturity_date(admin_client, db):
    """P3: batch create preserves per-sale maturity_date."""
    tag = _tag()
    co = _company(db, f"LC PM {tag}")
    payload = {"sales": [
        {"sale": {"pi_number": f"PI-PM-{tag}", "company_id": co,
                  "maturity_date": "2026-08-15"},
         "items": [{"product_name": "W", "quantity": 1, "unit_price": 1}]},
    ]}
    r = admin_client.post("/api/sales/batch", json=payload)
    assert r.status_code == 201, r.get_json()
    sid = r.get_json()["ids"][0]
    row = db.execute(
        "SELECT maturity_date FROM sales WHERE id = %s", (sid,)).fetchone()
    assert row[0] == "2026-08-15"


def test_batch_inf_qty_400(admin_client, db):
    """P3: inf quantity in batch items -> 400 validation shape."""
    tag = _tag()
    co = _company(db, f"LC INF {tag}")
    payload = {"sales": [
        {"sale": {"pi_number": f"PI-INF-{tag}", "company_id": co},
         "items": [{"product_name": "W", "quantity": float("inf"),
                    "unit_price": 1}]},
    ]}
    r = admin_client.post("/api/sales/batch", json=payload)
    assert r.status_code == 400, r.get_json()
    assert isinstance(r.get_json().get("details"), list)


def test_linked_sale_advance_refused_via_api(admin_client, db):
    """B5+M2 route: linked sale cannot advance into shipment_ongoing."""
    tag = _tag()
    co = _company(db, f"LC ADV {tag}")
    lc = _mk_lc(admin_client, co, f"LC-ADV-{tag}")
    sid = _sale(db, f"PI-ADV-{tag}", company_id=co)
    assert admin_client.post(
        f"/api/lcs/{lc['id']}/pis",
        json={"sale_ids": [sid]}).status_code == 200
    # pi_issued -> lc_received is fine
    r1 = admin_client.post(f"/api/sales/{sid}/move", json={})
    assert r1.status_code == 200, r1.get_json()
    assert r1.get_json()["new_stage"] == "lc_received"
    # lc_received -> shipment_ongoing must be refused for a linked sale
    r2 = admin_client.post(f"/api/sales/{sid}/move", json={})
    assert r2.status_code == 400, r2.get_json()


# --------------------------------------------------------------------------- #
# auth: mutating routes are admin-only
# --------------------------------------------------------------------------- #
def test_mutating_routes_require_admin(user_client, db):
    tag = _tag()
    co = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (f"LC Auth {tag}",)).fetchone()[0]
    db.commit()
    assert user_client.post(
        "/api/lcs",
        json={"company_id": co, "lc_number": f"LC-AU-{tag}"}).status_code == 403
    assert user_client.post("/api/sales/batch",
                            json=_batch_payload(tag, co)).status_code == 403
    # reads stay open to authenticated non-admins
    assert user_client.get("/api/lcs").status_code == 200
