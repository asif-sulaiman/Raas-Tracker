"""Slice 1 integration — inventory reader visibility audit + ingress guards.

Outward-only materials (``in_house_registered = FALSE``) are bought for a client
and never stocked on site. The audit (Task 4 brief §3) rules:

* EXCLUDE them from the in-house stock list, the dashboard feed, the inventory
  view and reorder alerts — they are not on-site stock.
* INCLUDE them in the catalog (entry selectors), the upload identity lookup
  (so a count can promote them) and the purchase service (existing-material
  validation).
* NEVER demote a once-stocked material back to hidden, and NEVER change the unit
  of a material that has purchase history during a file import.

Two of these are TDD witnesses for hardening fixes carried over from Task 2:
* concern (b): ``stock.import_from_json`` overwrote ``unit`` unconditionally.
* concern (f): the atomic ``stock.update_stock`` path notified reorder status
  without checking ``in_house_registered``.
"""
import json

from raas_tracker.stock import (
    import_from_json, sync_reorder_notifications, update_stock,
)
from raas_tracker.uploads import adjust_stock_from_upload, compare_stock_upload


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _visible(admin_client, db, name, qty=5, unit="KG"):
    r = admin_client.post("/api/chemicals",
                          json={"name": name, "qty": qty, "unit": unit})
    assert r.status_code == 200, r.get_json()
    return db.execute("SELECT id FROM chemicals WHERE name = %s",
                      (name,)).fetchone()[0]


def _hidden(admin_client, db, name, qty=10):
    """An outward-only identity: created by an outward purchase, never stocked."""
    cid = db.execute("INSERT INTO companies (name) VALUES (%s) RETURNING id",
                     (f"Client for {name}",)).fetchone()[0]
    db.commit()
    r = admin_client.post("/api/chemical-purchases", json={
        "supplier_name": "Visibility Supplies", "purchase_number": f"PO-{name}",
        "purchase_date": "2026-01-15", "delivery_type": "outward",
        "client_id": cid,
        "lines": [{"chemical_name": name, "quantity": qty, "unit": "KG",
                   "is_new": True}]})
    assert r.status_code == 201, r.get_json()
    row = db.execute("SELECT id, in_house_registered FROM chemicals "
                     "WHERE lower(name) = lower(%s)", (name,)).fetchone()
    assert row[1] is False, "outward purchase must leave the identity hidden"
    return row[0]


def _chem_notifications(db):
    return db.execute("SELECT COUNT(*) FROM notifications "
                      "WHERE entity_type = 'chemical'").fetchone()[0]


# --------------------------------------------------------------------------- #
# readers that MUST EXCLUDE outward-only
# --------------------------------------------------------------------------- #
def test_chemicals_list_excludes_outward_only(admin_client, db):
    _visible(admin_client, db, "List Visible")
    _hidden(admin_client, db, "List Hidden")
    names = {c["name"] for c in admin_client.get("/api/chemicals").get_json()}
    assert "List Visible" in names
    assert "List Hidden" not in names


def test_dashboard_excludes_outward_only(admin_client, db):
    _visible(admin_client, db, "Dash Visible")
    _hidden(admin_client, db, "Dash Hidden")
    r = admin_client.get("/api/dashboard")
    assert r.status_code == 200, r.get_json()
    names = {c["name"] for c in r.get_json()["chemicals"]}
    assert "Dash Visible" in names
    assert "Dash Hidden" not in names


def test_inventory_excludes_outward_only(admin_client, db):
    _visible(admin_client, db, "Inv Visible")
    _hidden(admin_client, db, "Inv Hidden")
    body = admin_client.get("/api/chemicals/inventory").get_json()
    names = {m["name"] for m in body["materials"]}
    assert "Inv Visible" in names
    assert "Inv Hidden" not in names


def test_sync_reorder_notifications_skips_hidden(db):
    db.execute(
        "INSERT INTO chemicals (name, current_qty, unit, reorder_level, "
        "in_house_registered) VALUES ('Hidden Reorder', 1, 'KG', 10, FALSE)")
    db.commit()
    # qty 1 <= reorder 10 would raise a stock_low alert for a registered material.
    sync_reorder_notifications(db, ["Hidden Reorder"])
    assert _chem_notifications(db) == 0


# --------------------------------------------------------------------------- #
# readers that MUST INCLUDE outward-only
# --------------------------------------------------------------------------- #
def test_catalog_includes_outward_only(admin_client, db):
    _hidden(admin_client, db, "Catalog Hidden")
    catalog = admin_client.get("/api/chemicals/catalog").get_json()
    row = next(c for c in catalog if c["name"] == "Catalog Hidden")
    assert row["in_house_registered"] is False


def test_upload_comparison_includes_hidden_identity(admin_client, db):
    """The reconciliation identity lookup must find a hidden material (so a
    later count can promote it), not report it as not_in_db."""
    _hidden(admin_client, db, "Compare Hidden")
    results = compare_stock_upload(db, [
        {"name": "Compare Hidden", "balance_last_month": 0,
         "balance_this_month": 0}])
    assert results["stats"]["not_in_db"] == 0
    found = (results["matches"] + results["last_month_mismatches"]
             + results["this_month_mismatches"] + results["both_mismatches"])
    assert any(r["name"] == "Compare Hidden" for r in found)


# --------------------------------------------------------------------------- #
# visibility invariants that MUST NOT change
# --------------------------------------------------------------------------- #
def test_upload_zero_count_never_demotes_registered(admin_client, db):
    vid = _visible(admin_client, db, "Once Stocked Zero", 5)
    uid = db.execute("INSERT INTO uploads (filename, status) "
                     "VALUES ('zero.csv', 'uploaded') RETURNING id").fetchone()[0]
    db.execute("INSERT INTO upload_rows "
               "(upload_id, chemical_name, balance_this_month, upload_unit) "
               "VALUES (%s, 'Once Stocked Zero', 0, 'KG')", (uid,))
    db.commit()
    assert adjust_stock_from_upload(db, uid, reviewed_by="admin") is True
    row = db.execute("SELECT current_qty, in_house_registered FROM chemicals "
                     "WHERE id = %s", (vid,)).fetchone()
    assert row[0] == 0
    assert row[1] is True               # a zero count keeps it visible


# --------------------------------------------------------------------------- #
# concern (f): atomic update_stock must skip reorder alerts for hidden materials
# --------------------------------------------------------------------------- #
def test_atomic_update_stock_skips_notify_for_hidden(db):
    db.execute(
        "INSERT INTO chemicals (name, current_qty, unit, reorder_level, "
        "in_house_registered) VALUES ('Hidden Atomic', 5, 'KG', 10, FALSE)")
    db.commit()
    # 5 - 1 = 4 <= reorder 10: a registered material would notify stock_low.
    assert update_stock(db, "Hidden Atomic", -1, reason="audit") is True
    assert _chem_notifications(db) == 0


def test_atomic_update_stock_notifies_for_registered(db):
    db.execute(
        "INSERT INTO chemicals (name, current_qty, unit, reorder_level, "
        "in_house_registered) VALUES ('Reg Atomic', 5, 'KG', 10, TRUE)")
    db.commit()
    assert update_stock(db, "Reg Atomic", -1) is True
    assert _chem_notifications(db) == 1


# --------------------------------------------------------------------------- #
# concern (b): import_from_json must not change the unit of a material that has
# purchase history (its registered unit governs every posted receipt).
# --------------------------------------------------------------------------- #
def test_import_from_json_preserves_unit_with_purchase_history(admin_client, db,
                                                              tmp_path):
    cid = _visible(admin_client, db, "History Unit", 10, unit="KG")
    r = admin_client.post("/api/chemical-purchases", json={
        "supplier_name": "History Supplies", "purchase_number": "PO-HIST-UNIT",
        "purchase_date": "2026-01-15", "delivery_type": "in_house",
        "lines": [{"chemical_name": "History Unit", "chemical_id": cid,
                   "quantity": 5, "unit": "KG", "is_new": False}]})
    assert r.status_code == 201, r.get_json()
    assert db.execute("SELECT COUNT(*) FROM chemical_purchase_items "
                      "WHERE chemical_id = %s", (cid,)).fetchone()[0] == 1

    payload = tmp_path / "stock.json"
    payload.write_text(json.dumps([
        {"product_name": "History Unit", "balance_this_month": 42,
         "balance_last_month": 10, "this_month_unit": "L"}]), encoding="utf-8")
    import_from_json(str(payload))

    row = db.execute("SELECT current_qty, unit FROM chemicals WHERE id = %s",
                     (cid,)).fetchone()
    assert row[0] == 42                 # quantity still reconciled
    assert row[1] == "KG"               # unit preserved: purchase history exists


def test_import_from_json_updates_unit_without_purchase_history(db, tmp_path):
    db.execute("INSERT INTO chemicals (name, current_qty, unit) "
               "VALUES ('No History', 1, 'KG')")
    db.commit()
    payload = tmp_path / "stock.json"
    payload.write_text(json.dumps([
        {"product_name": "No History", "balance_this_month": 7,
         "balance_last_month": 1, "this_month_unit": "L"}]), encoding="utf-8")
    import_from_json(str(payload))
    row = db.execute("SELECT current_qty, unit FROM chemicals "
                     "WHERE name = 'No History'").fetchone()
    assert row == (7, "L")              # no history: unit may still change