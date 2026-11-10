"""Slice 2 — outward ingredient sourcing + immutable per-run source evidence.

TDD red suite for the four Slice 2 backend behaviours: sourcing a recipe
ingredient from a client's outward purchase line, the reference/freeze
protection that then applies to that purchase line, the source-aware
requirement aggregation (exactly one in-house deduction per chemical, outward
rows never touching in-house stock), and the immutable per-run source
snapshot.

Every case drives the real Flask test client against the isolated pgserver
database from ``conftest``. Direct service calls are limited to
``purchases.get_outward_sources`` / ``is_line_referenced`` and
``recipes._aggregate_requirements`` / ``export_report_to_csv``, which have no
route of their own; everything else is black-box over HTTP so the status code
is part of the evidence.

Rulings this suite pins (from the plan, so they are tested rather than
assumed): *frozen means uneditable, never unusable*; an outward requirement is
always ``percentage x output quantity`` and is never capped to the receipt
quantity; and an in-house shortage rolls the WHOLE mixed-source run back,
source evidence included.
"""
from urllib.parse import quote

import pytest

from raas_tracker import purchases, recipes


# --------------------------------------------------------------------------- #
# helpers (patterns from test_chemical_purchases.py + test_production_atomicity)
# --------------------------------------------------------------------------- #
def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute("SELECT id FROM companies WHERE name = %s",
                      (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, products=("Prod",)):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": p, "quantity": 10, "unit_price": 5,
                   "unit": "KG"} for p in products]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _remainder_lines(db, sale_id):
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
        "invoice_number": number, "invoice_date": "2026-01-15",
        "lines": _remainder_lines(db, sale_id)})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["invoice_id"]


def _chemical(admin_client, db, name, qty, unit="KG"):
    r = admin_client.post("/api/chemicals",
                          json={"name": name, "qty": qty, "unit": unit})
    assert r.status_code == 200, r.get_json()
    return db.execute("SELECT id FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _purchase(admin_client, *, lines, number, delivery_type="outward",
              client_id=None, supplier="Outward Supplier"):
    body = {"supplier_name": supplier, "purchase_number": number,
            "purchase_date": "2026-01-15", "delivery_type": delivery_type,
            "lines": lines}
    if client_id is not None:
        body["client_id"] = client_id
    r = admin_client.post("/api/chemical-purchases", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _new_line(name, quantity=25, unit="KG"):
    return {"chemical_name": name, "quantity": quantity, "unit": unit,
            "is_new": True}


def _existing_line(chem_id, name, quantity=25, unit="KG"):
    return {"chemical_name": name, "chemical_id": chem_id, "quantity": quantity,
            "unit": unit, "is_new": False}


def _outward_line(admin_client, db, client_id, name, quantity=25, *, unit="KG",
                  number="PO-SRC-1", chem_id=None):
    """One outward receipt for this client; returns (purchase_id, line_id)."""
    line = (_existing_line(chem_id, name, quantity, unit) if chem_id
            else _new_line(name, quantity, unit))
    body = _purchase(admin_client, lines=[line], number=number,
                     client_id=client_id)
    return body["id"], body["items"][0]["id"]


def _source(line_id):
    return {"source_type": "outward", "outward_purchase_line_id": line_id}


def _recipe(admin_client, company_id, invoice_id, name, formulations):
    """formulations: {product: [(chemical, pct) | (chemical, pct, source)]}."""
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": company_id, "invoice_id": invoice_id})
    assert r.status_code == 201, r.get_json()
    recipe_id = r.get_json()["recipe"]["id"]
    for product, ingredients in formulations.items():
        for row in ingredients:
            body = {"chemical": row[0], "percentage": row[1]}
            if len(row) > 2:
                body.update(row[2])
            r = admin_client.post(
                f"/api/recipes/by-id/{recipe_id}/products/{quote(product)}/items",
                json=body)
            assert r.status_code == 200, (row, r.get_json())
    return recipe_id


def _produce(admin_client, recipe_id, outputs):
    return admin_client.post(
        f"/api/recipes/by-id/{recipe_id}/produce",
        json={"outputs": [{"product_name": p, "quantity": q}
                          for p, q in outputs.items()]})


def _stock(db, name):
    return db.execute("SELECT current_qty FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _item_id(db, recipe_id, chemical_name):
    return db.execute(
        """SELECT ri.id FROM recipe_items ri
             JOIN recipe_products rp ON rp.id = ri.recipe_product_id
             JOIN chemicals c ON c.id = ri.chemical_id
            WHERE rp.recipe_id = %s AND c.name = %s""",
        (recipe_id, chemical_name)).fetchone()[0]


def _item_source(db, recipe_id):
    return db.execute(
        """SELECT ri.source_type, ri.outward_purchase_line_id
             FROM recipe_items ri
             JOIN recipe_products rp ON rp.id = ri.recipe_product_id
            WHERE rp.recipe_id = %s""", (recipe_id,)).fetchone()


_RUN_ITEM_COLUMNS = (
    "chemical_name", "source_type", "outward_purchase_line_id", "required_qty",
    "deducted_qty", "unit", "purchase_number", "purchase_date", "supplier_name",
    "client_name", "source_qty", "source_unit")


def _run_items(db, run_id):
    """Per-run consumption, keyed by (chemical name, source type)."""
    rows = db.execute(
        "SELECT " + ", ".join(_RUN_ITEM_COLUMNS) +
        " FROM production_run_items WHERE run_id = %s", (run_id,)).fetchall()
    return [{col: value for col, value in zip(_RUN_ITEM_COLUMNS, row)}
            for row in rows]


def _run_items_by_source(db, run_id):
    keyed = {}
    for row in _run_items(db, run_id):
        keyed[(row["chemical_name"], row["source_type"])] = row
    return keyed


def _counts(db):
    return {t: db.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
            for t in ("production_runs", "production_run_outputs",
                      "production_run_items", "production_run_links")}


# --------------------------------------------------------------------------- #
# 1. a correctly-owned outward line is a valid ingredient source
# --------------------------------------------------------------------------- #
def test_correct_client_outward_source_accepted(admin_client, db):
    cid = _company(admin_client, db, "SrcOkCo")
    sid = _sale(admin_client, cid, "PI-SRC-OK", ("SrcProd",))
    inv = _invoice(admin_client, db, sid, "INV-SRC-OK")
    _, line_id = _outward_line(admin_client, db, cid, "Src Chem Ok", 25)

    rid = _recipe(admin_client, cid, inv, "SrcOkRecipe",
                  {"SrcProd": [("Src Chem Ok", 40, _source(line_id))]})

    assert _item_source(db, rid) == ("outward", line_id)
    item = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()[
        "products"][0]["items"][0]
    assert item["source_type"] == "outward"
    assert item["outward_purchase_line_id"] == line_id
    assert item["purchase_number"] == "PO-SRC-1"
    assert item["purchase_date"] == "2026-01-15"
    assert item["supplier_name"] == "Outward Supplier"
    assert item["client_name"] == "SrcOkCo"
    # An outward receipt never stocks the material: the source is the delivery,
    # not an in-house balance.
    assert _stock(db, "Src Chem Ok") == 0


# --------------------------------------------------------------------------- #
# 2. another client's outward line is not selectable
# --------------------------------------------------------------------------- #
def test_wrong_client_outward_source_rejected(admin_client, db):
    cid = _company(admin_client, db, "SrcMineCo")
    other = _company(admin_client, db, "SrcTheirsCo")
    sid = _sale(admin_client, cid, "PI-SRC-CLIENT", ("SrcProd",))
    inv = _invoice(admin_client, db, sid, "INV-SRC-CLIENT")
    _chemical(admin_client, db, "Src Chem Mine", 10)
    _, line_id = _outward_line(admin_client, db, other, "Src Chem Mine", 25)

    rid = _recipe(admin_client, cid, inv, "SrcClientRecipe", {})
    bad = admin_client.post(
        f"/api/recipes/by-id/{rid}/products/{quote('SrcProd')}/items",
        json={"chemical": "Src Chem Mine", "percentage": 40, **_source(line_id)})

    assert bad.status_code == 400, bad.get_json()
    assert "client" in bad.get_json()["error"].lower()
    assert db.execute("SELECT COUNT(*) FROM recipe_items ri "
                      "JOIN recipe_products rp ON rp.id = ri.recipe_product_id "
                      "WHERE rp.recipe_id = %s", (rid,)).fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 3. an in-house receipt line cannot be offered as an outward source
# --------------------------------------------------------------------------- #
def test_in_house_line_rejected_as_outward_source(admin_client, db):
    cid = _company(admin_client, db, "SrcTypeCo")
    sid = _sale(admin_client, cid, "PI-SRC-TYPE", ("SrcProd",))
    inv = _invoice(admin_client, db, sid, "INV-SRC-TYPE")
    body = _purchase(admin_client, lines=[_new_line("InHouse Named")],
                     number="PO-SRC-INHOUSE", delivery_type="in_house")
    in_house_line = body["items"][0]["id"]

    rid = _recipe(admin_client, cid, inv, "SrcTypeRecipe",
                  {"SrcProd": [("InHouse Named", 40)]})
    assert _item_source(db, rid) == ("in_house", None)

    r = admin_client.put(f"/api/recipes/by-id/{rid}/items/{_item_id(db, rid, 'InHouse Named')}",
                         json={"percentage": 40, **_source(in_house_line)})
    assert r.status_code == 400, r.get_json()
    assert "outward" in r.get_json()["error"].lower()
    # The refused switch left the row exactly as it was.
    assert _item_source(db, rid) == ("in_house", None)


# --------------------------------------------------------------------------- #
# 4. a line for a different material is not a source for this ingredient
# --------------------------------------------------------------------------- #
def test_wrong_material_on_source_line_rejected(admin_client, db):
    cid = _company(admin_client, db, "SrcMatCo")
    sid = _sale(admin_client, cid, "PI-SRC-MAT", ("SrcProd",))
    inv = _invoice(admin_client, db, sid, "INV-SRC-MAT")
    _chemical(admin_client, db, "Wanted Chem", 10)
    _, line_id = _outward_line(admin_client, db, cid, "Different Chem", 25)

    rid = _recipe(admin_client, cid, inv, "SrcMatRecipe", {})
    bad = admin_client.post(
        f"/api/recipes/by-id/{rid}/products/{quote('SrcProd')}/items",
        json={"chemical": "Wanted Chem", "percentage": 40, **_source(line_id)})

    assert bad.status_code == 400, bad.get_json()
    assert "material" in bad.get_json()["error"].lower()
    assert db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 5. a cancelled receipt is no longer a source
# --------------------------------------------------------------------------- #
def test_cancelled_outward_source_rejected(admin_client, db):
    cid = _company(admin_client, db, "SrcCancelCo")
    sid = _sale(admin_client, cid, "PI-SRC-CANCEL", ("SrcProd",))
    inv = _invoice(admin_client, db, sid, "INV-SRC-CANCEL")
    pid, line_id = _outward_line(admin_client, db, cid, "Voided Delivery", 25)
    rc = admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                           json={"expected_version": 1, "reason": "never shipped"})
    assert rc.status_code == 200, rc.get_json()

    rid = _recipe(admin_client, cid, inv, "SrcCancelRecipe", {})
    bad = admin_client.post(
        f"/api/recipes/by-id/{rid}/products/{quote('SrcProd')}/items",
        json={"chemical": "Voided Delivery", "percentage": 40,
              **_source(line_id)})
    assert bad.status_code == 400, bad.get_json()
    assert "cancelled" in bad.get_json()["error"].lower()
    assert db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0] == 0
    # A cancelled line is not even offered to the picker.
    assert line_id not in [s["line_id"] for s in
                           purchases.get_outward_sources(db, client_id=cid)]


# --------------------------------------------------------------------------- #
# 6. frozen means UNEDITABLE, not unusable
# --------------------------------------------------------------------------- #
def test_frozen_source_is_still_selectable_and_uncapped(admin_client, db):
    cid = _company(admin_client, db, "FrozenCo")
    sid = _sale(admin_client, cid, "PI-FROZEN-1")
    inv = _invoice(admin_client, db, sid, "INV-FROZEN-1")
    _, line_id = _outward_line(admin_client, db, cid, "Frozen Chem", 25)

    rid_a = _recipe(admin_client, cid, inv, "FrozenRecipeA",
                    {"Prod": [("Frozen Chem", 40, _source(line_id))]})
    assert purchases.is_line_referenced(db, line_id) is True

    # A second invoice recipe of the SAME client reuses the same line.
    sid_b = _sale(admin_client, cid, "PI-FROZEN-2")
    inv_b = _invoice(admin_client, db, sid_b, "INV-FROZEN-2")
    rid_b = _recipe(admin_client, cid, inv_b, "FrozenRecipeB", {})
    reuse = admin_client.post(
        f"/api/recipes/by-id/{rid_b}/products/{quote('Prod')}/items",
        json={"chemical": "Frozen Chem", "percentage": 40, **_source(line_id)})
    assert reuse.status_code == 200, reuse.get_json()

    row = next(s for s in purchases.get_outward_sources(db, client_id=cid)
               if s["line_id"] == line_id)
    assert row["frozen"] is True, "a referenced line is flagged, never hidden"

    # 40% of 100 KG is 40 KG against a 25 KG receipt: the requirement is the
    # formulation, never capped to the delivered quantity, and the delivery is
    # never depleted by being used.
    r = _produce(admin_client, rid_a, {"Prod": 100})
    assert r.status_code == 201, r.get_json()
    item = _run_items(db, r.get_json()["runs"][0]["run_id"])[0]
    assert item["required_qty"] == pytest.approx(40.0)
    assert item["source_qty"] == 25
    assert db.execute("SELECT quantity FROM chemical_purchase_items WHERE id = %s",
                      (line_id,)).fetchone()[0] == 25
    assert _stock(db, "Frozen Chem") == 0


# --------------------------------------------------------------------------- #
# 7. a referenced line's quantity is immutable - no admin override, and the
#    protection survives a later recipe edit because the RUN remembers it
# --------------------------------------------------------------------------- #
def test_referenced_line_quantity_is_immutable(admin_client, db):
    cid = _company(admin_client, db, "FreezeQtyCo")
    sid = _sale(admin_client, cid, "PI-FQ-1")
    inv = _invoice(admin_client, db, sid, "INV-FQ-1")
    pid, line_id = _outward_line(admin_client, db, cid, "Freeze Qty", 25)
    rid = _recipe(admin_client, cid, inv, "FreezeQtyRecipe",
                  {"Prod": [("Freeze Qty", 40, _source(line_id))]})
    r = _produce(admin_client, rid, {"Prod": 10})
    assert r.status_code == 201, r.get_json()

    def _patch(reason):
        # `force` is not a field the model knows: an unknown key is ignored,
        # and no admin flag exists that would waive the freeze.
        return admin_client.put(f"/api/chemical-purchases/{pid}", json={
            "expected_version": 1, "reason": reason, "force": True,
            "line_patches": [{"id": line_id, "quantity": 40}]})

    refused = _patch("quantity fix")
    assert refused.status_code == 409, refused.get_json()
    assert db.execute(
        "SELECT i.quantity, p.version FROM chemical_purchase_items i "
        "JOIN chemical_purchases p ON p.id = i.purchase_id WHERE i.id = %s",
        (line_id,)).fetchone() == (25, 1)

    # The recipe row is edited away; the historical run reference keeps the
    # freeze, so "reload the recipe" is never a way back into the receipt.
    assert admin_client.delete(
        f"/api/recipes/by-id/{rid}/items/{_item_id(db, rid, 'Freeze Qty')}"
        ).status_code == 200
    assert purchases.is_line_referenced(db, line_id) is True
    again = _patch("quantity fix after the recipe edit")
    assert again.status_code == 409, again.get_json()
    assert db.execute("SELECT quantity FROM chemical_purchase_items WHERE id = %s",
                      (line_id,)).fetchone()[0] == 25


# --------------------------------------------------------------------------- #
# 8. a referenced line's material and unit identity are immutable
# --------------------------------------------------------------------------- #
def test_referenced_line_identity_is_immutable(admin_client, db):
    cid = _company(admin_client, db, "FreezeIdCo")
    sid = _sale(admin_client, cid, "PI-FID-1")
    inv = _invoice(admin_client, db, sid, "INV-FID-1")
    chem_id = _chemical(admin_client, db, "Freeze Identity", 5)
    other_id = _chemical(admin_client, db, "Freeze Target", 5)
    pid, line_id = _outward_line(admin_client, db, cid, "Freeze Identity", 25,
                                 chem_id=chem_id)
    _recipe(admin_client, cid, inv, "FreezeIdRecipe",
            {"Prod": [("Freeze Identity", 40, _source(line_id))]})

    r = admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "wrong material on the line",
        "line_patches": [{"id": line_id, "quantity": 25,
                          "chemical_id": other_id, "unit": "KG"}]})

    assert r.status_code == 409, r.get_json()
    assert db.execute("SELECT chemical_id, unit FROM chemical_purchase_items "
                      "WHERE id = %s", (line_id,)).fetchone() == (chem_id, "KG")
    assert db.execute("SELECT version FROM chemical_purchases WHERE id = %s",
                      (pid,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# 9. once any line is referenced, the SHARED header freezes
# --------------------------------------------------------------------------- #
def test_referenced_purchase_header_is_frozen(admin_client, db):
    cid = _company(admin_client, db, "FreezeHdrCo")
    sid = _sale(admin_client, cid, "PI-FH-1")
    inv = _invoice(admin_client, db, sid, "INV-FH-1")
    body = _purchase(admin_client, client_id=cid, number="PO-FH-1",
                     lines=[_new_line("Freeze Held"), _new_line("Freeze Loose")])
    pid = body["id"]
    held = next(i["id"] for i in body["items"]
                if i["chemical_name"] == "Freeze Held")
    _recipe(admin_client, cid, inv, "FreezeHdrRecipe",
            {"Prod": [("Freeze Held", 40, _source(held))]})

    for field, value in (("supplier_name", "Second Supplier"),
                         ("purchase_number", "PO-FH-RENAMED"),
                         ("purchase_date", "2026-03-04")):
        r = admin_client.put(f"/api/chemical-purchases/{pid}", json={
            "expected_version": 1, "reason": f"header fix: {field}", field: value})
        assert r.status_code == 409, (field, r.get_json())

    row = db.execute("SELECT supplier_name, purchase_number, purchase_date, "
                     "delivery_type, client_id, version FROM chemical_purchases "
                     "WHERE id = %s", (pid,)).fetchone()
    assert row[:2] == ("Outward Supplier", "PO-FH-1")
    assert str(row[2]) == "2026-01-15"
    assert row[3] == "outward" and row[4] == cid
    assert row[5] == 1, "a refused correction must not burn a version"


# --------------------------------------------------------------------------- #
# 10. an unreferenced line still accepts an audited quantity correction
# --------------------------------------------------------------------------- #
def test_unreferenced_line_correction_still_allowed(admin_client, db):
    cid = _company(admin_client, db, "LooseLineCo")
    sid = _sale(admin_client, cid, "PI-LL-1")
    inv = _invoice(admin_client, db, sid, "INV-LL-1")
    body = _purchase(admin_client, client_id=cid, number="PO-LL-1",
                     lines=[_new_line("Loose Held"), _new_line("Loose Free")])
    pid = body["id"]
    held = next(i["id"] for i in body["items"]
                if i["chemical_name"] == "Loose Held")
    free = next(i["id"] for i in body["items"]
                if i["chemical_name"] == "Loose Free")
    _recipe(admin_client, cid, inv, "LooseLineRecipe",
            {"Prod": [("Loose Held", 40, _source(held))]})

    r = admin_client.put(f"/api/chemical-purchases/{pid}", json={
        "expected_version": 1, "reason": "delivered 40 not 25",
        "line_patches": [{"id": free, "quantity": 40}]})

    assert r.status_code == 200, r.get_json()
    quantities = {i["id"]: i["quantity"] for i in r.get_json()["items"]}
    assert quantities[free] == 40 and quantities[held] == 25
    assert r.get_json()["version"] == 2
    assert db.execute("SELECT COUNT(*) FROM audit_logs WHERE action = "
                      "'PURCHASE_UPDATE' AND entity_id = %s", (pid,)).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# 11. one referenced line blocks cancelling the whole receipt
# --------------------------------------------------------------------------- #
def test_whole_purchase_cancel_blocked_by_referenced_line(admin_client, db):
    cid = _company(admin_client, db, "CancelBlockCo")
    sid = _sale(admin_client, cid, "PI-CB-1")
    inv = _invoice(admin_client, db, sid, "INV-CB-1")
    body = _purchase(admin_client, client_id=cid, number="PO-CB-1",
                     lines=[_new_line("Block Held"), _new_line("Block Free")])
    pid = body["id"]
    held = next(i["id"] for i in body["items"]
                if i["chemical_name"] == "Block Held")
    _recipe(admin_client, cid, inv, "CancelBlockRecipe",
            {"Prod": [("Block Held", 40, _source(held))]})

    r = admin_client.post(f"/api/chemical-purchases/{pid}/cancel",
                          json={"expected_version": 1, "reason": "void it"})

    assert r.status_code == 409, r.get_json()
    row = db.execute("SELECT cancelled, cancellation_reason, version "
                     "FROM chemical_purchases WHERE id = %s", (pid,)).fetchone()
    assert row == (False, None, 1)
    assert admin_client.get(f"/api/chemical-purchases/{pid}").get_json()[
        "cancelled"] is False


# --------------------------------------------------------------------------- #
# 12. one chemical, two sources: the in-house half is deducted once
# --------------------------------------------------------------------------- #
def test_same_chemical_in_house_and_outward_across_products(admin_client, db):
    cid = _company(admin_client, db, "MixSrcCo")
    sid = _sale(admin_client, cid, "PI-MIX-1", ("MixProdA", "MixProdB"))
    inv = _invoice(admin_client, db, sid, "INV-MIX-1")
    chem_id = _chemical(admin_client, db, "Mix Chem", 100)
    _, line_id = _outward_line(admin_client, db, cid, "Mix Chem", 25,
                               chem_id=chem_id, number="PO-MIX-1")

    rid = _recipe(admin_client, cid, inv, "MixSrcRecipe", {
        "MixProdA": [("Mix Chem", 50)],
        "MixProdB": [("Mix Chem", 50, _source(line_id))]})

    r = _produce(admin_client, rid, {"MixProdA": 10, "MixProdB": 10})
    assert r.status_code == 201, r.get_json()
    run_id = r.get_json()["runs"][0]["run_id"]

    assert _stock(db, "Mix Chem") == 95, "only the in-house half moved"
    items = _run_items_by_source(db, run_id)
    assert set(items) == {("Mix Chem", "in_house"), ("Mix Chem", "outward")}
    in_house = items[("Mix Chem", "in_house")]
    outward = items[("Mix Chem", "outward")]
    assert in_house["outward_purchase_line_id"] is None
    assert in_house["required_qty"] == pytest.approx(5.0)
    assert in_house["deducted_qty"] == pytest.approx(5.0)
    assert outward["outward_purchase_line_id"] == line_id
    assert outward["required_qty"] == pytest.approx(5.0)
    assert outward["deducted_qty"] == 0

    # The immutable evidence is readable through the run detail route too.
    detail = admin_client.get(f"/api/recipes/by-id/{rid}/runs/{run_id}").get_json()
    row = next(i for i in detail["items"] if i["source_type"] == "outward")
    assert row["purchase_number"] == "PO-MIX-1"
    assert row["supplier_name"] == "Outward Supplier"
    assert row["client_name"] == "MixSrcCo"
    assert row["deducted_qty"] == 0
    assert row["source_qty"] == 25


# --------------------------------------------------------------------------- #
# 13. one chemical has exactly ONE source per product
# --------------------------------------------------------------------------- #
def test_duplicate_chemical_in_one_product_still_rejected(admin_client, db):
    cid = _company(admin_client, db, "DupSrcCo")
    sid = _sale(admin_client, cid, "PI-DUP-1")
    inv = _invoice(admin_client, db, sid, "INV-DUP-1")
    chem_id = _chemical(admin_client, db, "Dup Source Chem", 100)
    _, line_id = _outward_line(admin_client, db, cid, "Dup Source Chem", 25,
                               chem_id=chem_id, number="PO-DUP-1")

    rid = _recipe(admin_client, cid, inv, "DupSrcRecipe",
                  {"Prod": [("Dup Source Chem", 40)]})
    r = admin_client.post(
        f"/api/recipes/by-id/{rid}/products/{quote('Prod')}/items",
        json={"chemical": "Dup Source Chem", "percentage": 10,
              **_source(line_id)})

    # The unique (product, chemical) index is the authority: switching source
    # is a PUT on the existing row, never a second row.
    assert r.status_code == 409, r.get_json()
    assert db.execute("SELECT COUNT(*) FROM recipe_items").fetchone()[0] == 1
    assert _item_source(db, rid) == ("in_house", None)


# --------------------------------------------------------------------------- #
# 14. aggregation groups by source and deducts the in-house total once
# --------------------------------------------------------------------------- #
def test_aggregate_groups_by_source_and_deducts_in_house_total_once(admin_client, db):
    cid = _company(admin_client, db, "AggSrcCo")
    sid = _sale(admin_client, cid, "PI-AGG-1",
                ("AggProdA", "AggProdB", "AggProdC"))
    inv = _invoice(admin_client, db, sid, "INV-AGG-1")
    chem_id = _chemical(admin_client, db, "Agg Chem", 100)
    _, line_id = _outward_line(admin_client, db, cid, "Agg Chem", 80,
                               chem_id=chem_id, number="PO-AGG-1")
    rid = _recipe(admin_client, cid, inv, "AggSrcRecipe", {
        "AggProdA": [("Agg Chem", 50)],
        "AggProdB": [("Agg Chem", 50)],
        "AggProdC": [("Agg Chem", 50, _source(line_id))]})

    recipe = recipes._require_recipe(db, rid)
    rows = recipes._aggregate_requirements(db, recipe, [
        {"product_name": "AggProdA", "quantity": 10},
        {"product_name": "AggProdB", "quantity": 10},
        {"product_name": "AggProdC", "quantity": 10}], require_balanced=False)

    by_source = {(r["chemical_id"], r["source_type"]): r for r in rows}
    assert len(rows) == 2, "the two sources must not collapse into one row"
    assert set(by_source) == {(chem_id, "in_house"), (chem_id, "outward")}
    in_house = by_source[(chem_id, "in_house")]
    outward = by_source[(chem_id, "outward")]
    assert in_house["required_qty"] == pytest.approx(10.0)
    assert in_house["deducted_qty"] == pytest.approx(10.0)
    assert len(in_house["products"]) == 2
    assert outward["required_qty"] == pytest.approx(5.0)
    assert outward["deducted_qty"] == 0
    assert outward["outward_purchase_line_id"] == line_id

    r = _produce(admin_client, rid, {"AggProdA": 10, "AggProdB": 10,
                                     "AggProdC": 10})
    assert r.status_code == 201, r.get_json()
    assert _stock(db, "Agg Chem") == 90, "10 deducted, never 15"
    assert db.execute("SELECT quantity FROM chemical_purchase_items WHERE id = %s",
                      (line_id,)).fetchone()[0] == 80


# --------------------------------------------------------------------------- #
# 15. all-outward production works with zero in-house stock
# --------------------------------------------------------------------------- #
def test_all_outward_production_with_zero_stock(admin_client, db, tmp_path):
    cid = _company(admin_client, db, "AllOutCo")
    sid = _sale(admin_client, cid, "PI-ALL-OUT")
    inv = _invoice(admin_client, db, sid, "INV-ALL-OUT")
    _, line_id = _outward_line(admin_client, db, cid, "All Out Chem", 25)
    rid = _recipe(admin_client, cid, inv, "AllOutRecipe",
                  {"Prod": [("All Out Chem", 40, _source(line_id))]})

    r = _produce(admin_client, rid, {"Prod": 10})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()["runs"][0]
    assert _stock(db, "All Out Chem") == 0
    assert body["warnings"] == [], "an outward row is not a stock shortage"

    report = body["shortage_report"]
    assert len(report) == 1
    assert report[0]["source_type"] == "outward"
    assert report[0]["required_total"] == pytest.approx(4.0)
    assert report[0]["shortage"] is None
    assert report[0]["current_stock"] is None
    assert report[0]["status"] == "OUTWARD"

    # Every report reader must survive a row with no stock impact at all: a
    # numeric format on None is a 500 in the export route.
    path = tmp_path / "outward_report.csv"
    recipes.export_report_to_csv(report, "AllOutRecipe", 10,
                                 output_path=str(path))
    assert "OUTWARD" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 16. outward requirements convert units exactly like in-house ones
# --------------------------------------------------------------------------- #
def test_outward_requirement_converts_product_unit_to_registered_unit(admin_client, db):
    cid = _company(admin_client, db, "OutConvCo")
    sid = _sale(admin_client, cid, "PI-OCONV")
    inv = _invoice(admin_client, db, sid, "INV-OCONV")
    _, line_id = _outward_line(admin_client, db, cid, "Fine Outward", 25,
                               unit="G", number="PO-OCONV-1")
    rid = _recipe(admin_client, cid, inv, "OutConvRecipe",
                  {"Prod": [("Fine Outward", 40, _source(line_id))]})

    r = _produce(admin_client, rid, {"Prod": 10})
    assert r.status_code == 201, r.get_json()
    item = _run_items(db, r.get_json()["runs"][0]["run_id"])[0]
    # 40% of 10 KG = 4 KG -> 4000 g, in the chemical's registered unit.
    assert item["required_qty"] == pytest.approx(4000.0)
    assert item["unit"] == "G"
    assert item["source_unit"] == "G"
    assert item["source_qty"] == 25, "the snapshot is the delivery, not the need"
    assert item["deducted_qty"] == 0

    # A missing conversion blocks production exactly like an in-house row.
    _, barline = _outward_line(admin_client, db, cid, "Barrel Outward", 10,
                               unit="BLG", number="PO-OCONV-2")
    add = admin_client.post(
        f"/api/recipes/by-id/{rid}/products/{quote('Prod')}/items",
        json={"chemical": "Barrel Outward", "percentage": 10, **_source(barline)})
    assert add.status_code == 200, add.get_json()

    blocked = _produce(admin_client, rid, {"Prod": 10})
    assert blocked.status_code == 400, blocked.get_json()
    message = blocked.get_json()["error"]
    assert "KG" in message and "BLG" in message, message
    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# 17. an in-house shortage rolls back the whole mixed-source run
# --------------------------------------------------------------------------- #
def test_in_house_shortage_rolls_back_source_evidence(admin_client, db):
    cid = _company(admin_client, db, "MixRollCo")
    sid = _sale(admin_client, cid, "PI-MROLL", ("RollProdA", "RollProdB"))
    inv = _invoice(admin_client, db, sid, "INV-MROLL")
    _, line_id = _outward_line(admin_client, db, cid, "Roll Out", 25,
                               number="PO-MROLL-1")
    out_id = db.execute("SELECT id FROM chemicals WHERE name = 'Roll Out'").fetchone()[0]
    short_id = _chemical(admin_client, db, "Roll Short", 100)
    assert out_id < short_id, "the outward evidence must be written first"
    db.execute("UPDATE chemicals SET current_qty = 0.5 WHERE id = %s", (short_id,))
    db.commit()

    rid = _recipe(admin_client, cid, inv, "MixRollRecipe", {
        "RollProdA": [("Roll Out", 40, _source(line_id))],
        "RollProdB": [("Roll Short", 40)]})

    before = _counts(db)
    before_status = db.execute("SELECT status FROM invoices WHERE id = %s",
                               (inv,)).fetchone()[0]
    r = _produce(admin_client, rid, {"RollProdA": 10, "RollProdB": 10})
    assert r.status_code == 400, r.get_json()
    assert "insufficient stock" in r.get_json()["error"]

    # Nothing survived: header, outputs, links, the in-house deduction AND the
    # outward source evidence, in one transaction.
    assert _counts(db) == before
    assert db.execute("SELECT COUNT(*) FROM audit_logs "
                      "WHERE action = 'PRODUCTION_RUN_CREATE'").fetchone()[0] == 0
    assert db.execute("SELECT status FROM invoices WHERE id = %s",
                      (inv,)).fetchone()[0] == before_status
    assert _stock(db, "Roll Out") == 0
    assert _stock(db, "Roll Short") == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# 18. a caller that says nothing keeps the Slice 1 behaviour
# --------------------------------------------------------------------------- #
def test_legacy_recipe_item_defaults_to_in_house(admin_client, db):
    cid = _company(admin_client, db, "LegacySrcCo")
    sid = _sale(admin_client, cid, "PI-LEG-1")
    inv = _invoice(admin_client, db, sid, "INV-LEG-1")
    _chemical(admin_client, db, "Legacy Chem", 100)
    rid = _recipe(admin_client, cid, inv, "LegacySrcRecipe",
                  {"Prod": [("Legacy Chem", 40)]})

    assert _item_source(db, rid) == ("in_house", None)
    item = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()[
        "products"][0]["items"][0]
    assert item["source_type"] == "in_house"
    assert item["outward_purchase_line_id"] is None

    r = _produce(admin_client, rid, {"Prod": 10})
    assert r.status_code == 201, r.get_json()
    assert _stock(db, "Legacy Chem") == 96
    row = _run_items(db, r.get_json()["runs"][0]["run_id"])[0]
    assert row["source_type"] == "in_house"
    assert row["outward_purchase_line_id"] is None
    assert row["required_qty"] == pytest.approx(4.0)
    assert row["deducted_qty"] == pytest.approx(4.0)
