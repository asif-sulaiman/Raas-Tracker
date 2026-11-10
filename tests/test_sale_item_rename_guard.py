"""Slice 2 — post-production product-identity rename guard.

`production_run_outputs` snapshots a product NAME, and there is no
`sale_item_id` anywhere on the production tables, so the only provenance path
from a PI line to its production history is
`production_run_links.sale_id -> production_runs -> production_run_outputs`,
with `invoice_items.sale_item_id -> invoices -> production_run_links.invoice_id`
as the line-level fallback where it is unambiguous.

Renaming a PI line after it has been produced therefore silently detaches the
product's history from the document the history belongs to. This suite pins
that both identity-bearing routes (`update_sale_item` and `update_sale_full`)
refuse an identity-changing rename with a 409, allow the changes that preserve
the normalised identity, and refuse when the historical identity cannot be
attributed to one line - so the ambiguity is never an escape hatch.

The guard prevents NEW mismatches only: no test here expects a historical
snapshot to be rewritten.
"""
from urllib.parse import quote

# --------------------------------------------------------------------------- #
# helpers (patterns from tests/test_production_atomicity.py)
# --------------------------------------------------------------------------- #
def _company(admin_client, db, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute("SELECT id FROM companies WHERE name = %s",
                      (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi, products=("GuardProd",), qty=10):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [{"product_name": p, "quantity": qty, "unit_price": 5,
                   "unit": "KG"} for p in products]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _add_item(admin_client, db, sale_id, name, qty=5):
    r = admin_client.post(f"/api/sales/{sale_id}/items",
                          json={"product_name": name, "quantity": qty,
                                "unit_price": 5, "unit": "KG"})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _item_ids(db, sale_id):
    return [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchall()]


def _name(db, item_id):
    return db.execute("SELECT product_name FROM sale_items WHERE id = %s",
                      (item_id,)).fetchone()[0]


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


def _recipe(admin_client, company_id, invoice_id, name, formulations):
    """formulations: {product: [(chemical, percentage), ...]}."""
    r = admin_client.post("/api/recipes", json={
        "name": name, "company_id": company_id, "invoice_id": invoice_id})
    assert r.status_code == 201, r.get_json()
    recipe_id = r.get_json()["recipe"]["id"]
    for product, ingredients in formulations.items():
        for chem, pct in ingredients:
            r = admin_client.post(
                f"/api/recipes/by-id/{recipe_id}/products/{quote(product)}/items",
                json={"chemical": chem, "percentage": pct})
            assert r.status_code == 200, r.get_json()
    return recipe_id


def _produce(admin_client, recipe_id, outputs):
    return admin_client.post(
        f"/api/recipes/by-id/{recipe_id}/produce",
        json={"outputs": [{"product_name": p, "quantity": q}
                          for p, q in outputs.items()]})


def _chemical(admin_client, name, qty=100):
    r = admin_client.post("/api/chemicals",
                          json={"name": name, "qty": qty, "unit": "KG"})
    assert r.status_code == 200, r.get_json()


def _produced(admin_client, db, name, pi, products=("GuardProd",)):
    """A PI that has actually been produced; returns (cid, sid, inv, rid)."""
    cid = _company(admin_client, db, name)
    sid = _sale(admin_client, cid, pi, products)
    _chemical(admin_client, f"GuardChem-{pi}")
    inv = _invoice(admin_client, db, sid, f"INV-{pi}")
    rid = _recipe(admin_client, cid, inv, f"GuardRecipe-{pi}",
                  {p: [(f"GuardChem-{pi}", 40)] for p in products})
    r = _produce(admin_client, rid, {p: 5 for p in products})
    assert r.status_code == 201, r.get_json()
    return cid, sid, inv, rid


# --------------------------------------------------------------------------- #
# 1. the single-line PUT refuses a produced product's new name
# --------------------------------------------------------------------------- #
def test_post_production_rename_blocked_via_update_sale_item(admin_client, db):
    cid, sid, inv, rid = _produced(admin_client, db, "RenA", "PI-RENA-1")
    item_id = _item_ids(db, sid)[0]

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                         json={"product_name": "RenamedAfterProduction"})

    assert r.status_code == 409, r.get_json()
    assert "produc" in r.get_json()["error"].lower()
    assert _name(db, item_id) == "GuardProd"
    # The guard blocks the rename; it never rewrites the historical snapshot.
    assert [o[0] for o in db.execute(
        "SELECT product_name FROM production_run_outputs").fetchall()] == ["GuardProd"]
    # A non-identity field on the same line is still editable.
    ok = admin_client.put(f"/api/sales/{sid}/items/{item_id}", json={"unit": "KG"})
    assert ok.status_code == 200, ok.get_json()


# --------------------------------------------------------------------------- #
# 2. the whole-sale PUT refuses it too - and rolls the header back with it
# --------------------------------------------------------------------------- #
def test_post_production_rename_blocked_via_update_sale_full(admin_client, db):
    cid, sid, inv, rid = _produced(admin_client, db, "RenB", "PI-RENB-1")
    item_id = _item_ids(db, sid)[0]

    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"pi_number": "PI-RENB-1", "client_name": "x",
                   "company_id": cid},
        "items": [{"id": item_id, "product_name": "RenamedAfterProduction",
                   "quantity": 10, "unit_price": 5, "unit": "KG"}],
        "removedIds": []})

    assert r.status_code == 409, r.get_json()
    assert "produc" in r.get_json()["error"].lower()
    assert _name(db, item_id) == "GuardProd"
    assert db.execute("SELECT comments FROM sales WHERE id = %s",
                      (sid,)).fetchone()[0] is None

    # The same route keeps working when the identity is preserved.
    ok = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"pi_number": "PI-RENB-1", "client_name": "x",
                   "company_id": cid},
        "items": [{"id": item_id, "product_name": "GuardProd",
                   "quantity": 10, "unit_price": 5, "unit": "KG"}],
        "removedIds": []})
    assert ok.status_code == 200, ok.get_json()


# --------------------------------------------------------------------------- #
# 3. a change that preserves the normalised identity is descriptive, not
#    an identity change
# --------------------------------------------------------------------------- #
def test_capitalisation_only_change_is_allowed(admin_client, db):
    cid, sid, inv, rid = _produced(admin_client, db, "RenC", "PI-RENC-1")
    item_id = _item_ids(db, sid)[0]

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                         json={"product_name": "  GUARDPROD  "})

    assert r.status_code == 200, r.get_json()
    # SaleItemPatchIn strips whitespace (Phase 2-A: an unstripped "  " would slip
    # past min_length=1 as a blank name), so the stored value is the normalized
    # one. What this case proves is that a name-preserving edit is ALLOWED -- not
    # that padding survives into storage.
    assert _name(db, item_id) == "GUARDPROD"
    # ...and the historical snapshot was left alone, so the two still match on
    # the trimmed, case-insensitive identity the readers use.
    assert db.execute("SELECT COUNT(*) FROM production_run_outputs "
                      "WHERE lower(trim(product_name)) = 'guardprod'"
                      ).fetchone()[0] == 1


# --------------------------------------------------------------------------- #
# 4. without production history the rename is an ordinary descriptive edit
# --------------------------------------------------------------------------- #
def test_rename_without_production_history_is_allowed(admin_client, db):
    cid = _company(admin_client, db, "RenD")
    sid = _sale(admin_client, cid, "PI-REND-1")
    _chemical(admin_client, "GuardChem-PI-REND-1")
    inv = _invoice(admin_client, db, sid, "INV-REND-1")
    # Invoiced but never produced: the invoiced-line rule is about money, and
    # a product name carries none.
    _recipe(admin_client, cid, inv, "RenDRecipe",
            {"GuardProd": [("GuardChem-PI-REND-1", 40)]})
    item_id = _item_ids(db, sid)[0]

    r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                         json={"product_name": "FreshName"})

    assert r.status_code == 200, r.get_json()
    assert _name(db, item_id) == "FreshName"
    assert db.execute("SELECT COUNT(*) FROM production_runs").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# 5. ambiguous historical identity is never a way around the guard
# --------------------------------------------------------------------------- #
def test_ambiguous_provenance_blocks_identity_change(admin_client, db):
    cid, sid, inv, rid = _produced(admin_client, db, "RenE", "PI-RENE-1",
                                   ("AmbiProdA", "AmbiProdB"))
    ids = _item_ids(db, sid)
    # Drift: one historical output name no longer matches any PI line (the
    # snapshot predates the document). Attribution is now ambiguous, so no
    # line on this PI may change identity - and no line may claim the
    # ambiguity proves it is safe.
    db.execute("UPDATE production_run_outputs SET product_name = 'AmbiProdC' "
               "WHERE product_name = 'AmbiProdB'")
    db.commit()

    for item_id in ids:
        r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                             json={"product_name": "Slipped Through"})
        assert r.status_code == 409, (item_id, r.get_json())
        assert _name(db, item_id) != "Slipped Through"

    # The preserved-identity path stays open, so the guard is a lock on the
    # identity, not on the row.
    ok = admin_client.put(f"/api/sales/{sid}/items/{ids[0]}",
                          json={"product_name": "ambiproda"})
    assert ok.status_code == 200, ok.get_json()


# --------------------------------------------------------------------------- #
# 6. two PI lines with the same product name cannot be played against the
#    guard (one of them "must" be free, and neither is)
# --------------------------------------------------------------------------- #
def test_duplicate_product_lines_cannot_bypass_the_guard(admin_client, db):
    cid = _company(admin_client, db, "RenF")
    sid = _sale(admin_client, cid, "PI-RENF-1", ("Dup Prod",), qty=5)
    _add_item(admin_client, db, sid, "Dup Prod", qty=5)
    _chemical(admin_client, "GuardChem-PI-RENF-1")
    inv = _invoice(admin_client, db, sid, "INV-RENF-1")
    rid = _recipe(admin_client, cid, inv, "RenFRecipe",
                  {"Dup Prod": [("GuardChem-PI-RENF-1", 40)]})
    assert _produce(admin_client, rid, {"Dup Prod": 10}).status_code == 201
    ids = _item_ids(db, sid)
    assert len(ids) == 2

    for item_id in ids:
        r = admin_client.put(f"/api/sales/{sid}/items/{item_id}",
                             json={"product_name": "Split Off Dup"})
        assert r.status_code == 409, (item_id, r.get_json())
        assert _name(db, item_id) == "Dup Prod"

    assert db.execute("SELECT COUNT(*) FROM production_run_outputs "
                      "WHERE lower(product_name) = 'dup prod'").fetchone()[0] == 1
