"""P5: Consistency lock — pipeline mutations admin-only; every write dated + actor."""
import json
import pytest


def _create_sale_and_items(client, pi_number="PI-TEST", company_id=None):
    """Create a sale with items, return (sale_id, item_id)."""
    payload = {
        "sale": {
            "pi_number": pi_number,
            "pi_date": "2026-09-01",
            "client_name": "Test Client",
            "company_id": company_id,
        },
        "items": [
            {"product_name": "ProdA", "quantity": 100, "unit_price": 3.25, "unit": "KG"},
            {"product_name": "ProdB", "quantity": 50, "unit_price": 5.00, "unit": "DRUM"},
        ],
    }
    r = client.post("/api/sales", json=payload)
    assert r.status_code == 201, f"Create sale failed: {r.get_json()}"
    sale_id = r.get_json()["id"]
    # Get item IDs
    detail = client.get(f"/api/sales/{sale_id}").get_json()
    item_ids = [item["id"] for item in detail["items"]]
    return sale_id, item_ids


def test_create_sale_non_admin_allowed(user_client, db):
    """POST /api/sales returns 201 for regular user (PI creation allowed)."""
    payload = {
        "sale": {"pi_number": "PI-NONADMIN", "pi_date": "2026-09-01", "client_name": "NonAdmin Co"},
        "items": [{"product_name": "ProdX", "quantity": 10, "unit_price": 1.0, "unit": "KG"}],
    }
    r = user_client.post("/api/sales", json=payload)
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.get_json()}"
    assert "id" in r.get_json()


# --- 8 endpoints: non-admin gets 403 ---

def test_put_sale_non_admin_403(user_client, admin_client, db):
    """PUT /api/sales/<id> - non-admin gets 403."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = user_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-UPDATED", "pi_date": "2026-09-01", "client_name": "Test Client"},
        "items": [{"product_name": "ProdA", "quantity": 100, "unit_price": 3.25, "unit": "KG"}],
        "removedIds": [],
    })
    assert r.status_code == 403


def test_post_sale_move_non_admin_403(user_client, admin_client, db):
    """POST /api/sales/<id>/move - non-admin gets 403."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = user_client.post(f"/api/sales/{sale_id}/move", json={"notes": "test"})
    assert r.status_code == 403


def test_put_sale_lc_non_admin_403(user_client, admin_client, db):
    """PUT /api/sales/<id>/lc - non-admin gets 403."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = user_client.put(f"/api/sales/{sale_id}/lc", json={
        "lc_number": "LC-123", "lc_date": "2026-09-05", "shipment_date": "2026-09-10"
    })
    assert r.status_code == 403


def test_put_sale_payment_non_admin_403(user_client, admin_client, db):
    """PUT /api/sales/<id>/payment - non-admin gets 403."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = user_client.put(f"/api/sales/{sale_id}/payment", json={
        "payment_date": "2026-09-15", "payment_amount": 1000, "notes": "test"
    })
    assert r.status_code == 403


def test_put_sale_payment_record_non_admin_403(user_client, admin_client, db):
    """PUT /api/sales/<id>/payments/<pid> - non-admin gets 403."""
    sale_id, _ = _create_sale_and_items(admin_client)
    # First create a payment as admin
    r = admin_client.put(f"/api/sales/{sale_id}/payment", json={
        "payment_date": "2026-09-15", "payment_amount": 1000, "notes": "test"
    })
    assert r.status_code == 200
    payment_id = r.get_json()["payment_id"]
    # Try to edit as non-admin
    r = user_client.put(f"/api/sales/{sale_id}/payments/{payment_id}", json={
        "payment_amount": 2000
    })
    assert r.status_code == 403


def test_post_sale_items_non_admin_403(user_client, admin_client, db):
    """POST /api/sales/<id>/items - non-admin gets 403."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = user_client.post(f"/api/sales/{sale_id}/items", json={
        "product_name": "ProdC", "quantity": 25, "unit_price": 10.0, "unit": "KG"
    })
    assert r.status_code == 403


def test_put_sale_item_non_admin_403(user_client, admin_client, db):
    """PUT /api/sales/<id>/items/<iid> - non-admin gets 403."""
    sale_id, item_ids = _create_sale_and_items(admin_client)
    r = user_client.put(f"/api/sales/{sale_id}/items/{item_ids[0]}", json={
        "quantity": 200
    })
    assert r.status_code == 403


def test_delete_sale_non_admin_403(user_client, admin_client, db):
    """DELETE /api/sales/<id> - non-admin gets 403 (already has @admin_required)."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = user_client.delete(f"/api/sales/{sale_id}")
    assert r.status_code == 403


# --- 8 endpoints: admin gets 200/201 ---

def test_put_sale_admin_200(admin_client, db):
    """PUT /api/sales/<id> - admin succeeds."""
    sale_id, item_ids = _create_sale_and_items(admin_client)
    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-UPDATED", "pi_date": "2026-09-01", "client_name": "Updated Client", "comments": "updated"},
        "items": [
            {"id": item_ids[0], "product_name": "ProdA", "quantity": 150, "unit_price": 3.25, "unit": "KG"},
            {"id": item_ids[1], "product_name": "ProdB", "quantity": 50, "unit_price": 5.00, "unit": "DRUM"},
        ],
        "removedIds": [],
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    data = r.get_json()
    assert data["pi_number"] == "PI-UPDATED"
    assert data["comments"] == "updated"
    assert data["items"][0]["quantity"] == 150


def test_post_sale_move_admin_200(admin_client, db):
    """POST /api/sales/<id>/move - admin succeeds."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = admin_client.post(f"/api/sales/{sale_id}/move", json={"notes": "advance to LC"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    assert "new_stage" in r.get_json()


def test_put_sale_lc_admin_200(admin_client, db):
    """PUT /api/sales/<id>/lc - admin succeeds."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = admin_client.put(f"/api/sales/{sale_id}/lc", json={
        "lc_number": "LC-123", "lc_date": "2026-09-05", "shipment_date": "2026-09-10"
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    detail = admin_client.get(f"/api/sales/{sale_id}").get_json()
    assert detail["lc_number"] == "LC-123"
    assert detail["stage"] == "lc_received"


def test_put_sale_payment_admin_200(admin_client, db):
    """PUT /api/sales/<id>/payment - admin succeeds."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = admin_client.put(f"/api/sales/{sale_id}/payment", json={
        "payment_date": "2026-09-15", "payment_amount": 1000, "notes": "partial"
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    assert "payment_id" in r.get_json()
    assert r.get_json()["total_paid"] == 1000


def test_put_sale_payment_record_admin_200(admin_client, db):
    """PUT /api/sales/<id>/payments/<pid> - admin succeeds."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = admin_client.put(f"/api/sales/{sale_id}/payment", json={
        "payment_date": "2026-09-15", "payment_amount": 1000, "notes": "test"
    })
    assert r.status_code == 200
    payment_id = r.get_json()["payment_id"]
    r = admin_client.put(f"/api/sales/{sale_id}/payments/{payment_id}", json={
        "payment_amount": 2000, "notes": "updated"
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    detail = admin_client.get(f"/api/sales/{sale_id}").get_json()
    assert detail["total_paid"] == 2000


def test_post_sale_items_admin_201(admin_client, db):
    """POST /api/sales/<id>/items - admin succeeds."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = admin_client.post(f"/api/sales/{sale_id}/items", json={
        "product_name": "ProdC", "quantity": 25, "unit_price": 10.0, "unit": "KG"
    })
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.get_json()}"
    assert "id" in r.get_json()
    detail = admin_client.get(f"/api/sales/{sale_id}").get_json()
    assert len(detail["items"]) == 3


def test_put_sale_item_admin_200(admin_client, db):
    """PUT /api/sales/<id>/items/<iid> - admin succeeds."""
    sale_id, item_ids = _create_sale_and_items(admin_client)
    r = admin_client.put(f"/api/sales/{sale_id}/items/{item_ids[0]}", json={
        "quantity": 200, "unit_price": 4.0
    })
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    detail = admin_client.get(f"/api/sales/{sale_id}").get_json()
    assert detail["items"][0]["quantity"] == 200
    assert detail["items"][0]["unit_price"] == 4.0


def test_delete_sale_admin_200(admin_client, db):
    """DELETE /api/sales/<id> - admin succeeds (already has @admin_required)."""
    sale_id, _ = _create_sale_and_items(admin_client)
    r = admin_client.delete(f"/api/sales/{sale_id}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.get_json()}"
    r = admin_client.get(f"/api/sales/{sale_id}")
    assert r.status_code == 404


# --- Audit logging tests ---

def test_update_sale_full_audit_logged(admin_client, db):
    """Admin update_sale_full creates audit row with action='SALE_UPDATE'."""
    sale_id, item_ids = _create_sale_and_items(admin_client, pi_number="PI-AUDIT-1")
    # Get original sale for comparison
    original = admin_client.get(f"/api/sales/{sale_id}").get_json()

    # Perform full update
    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-AUDIT-1-UPDATED", "pi_date": "2026-09-01", "client_name": "Updated Client", "comments": "audit test"},
        "items": [
            {"id": item_ids[0], "product_name": "ProdA", "quantity": 150, "unit_price": 3.25, "unit": "KG"},
            {"id": item_ids[1], "product_name": "ProdB", "quantity": 75, "unit_price": 5.00, "unit": "DRUM"},
        ],
        "removedIds": [],
    })
    assert r.status_code == 200

    # Check audit log
    logs = admin_client.get("/api/audit-logs", query_string={"limit": 10}).get_json()
    # Find the SALE_UPDATE for this sale
    audit = next((log for log in logs if log["action"] == "SALE_UPDATE" and log["entity_id"] == sale_id), None)
    assert audit is not None, f"SALE_UPDATE audit not found for sale {sale_id}"
    assert audit["entity_type"] == "sale"
    old_val = json.loads(audit["old_value"])
    new_val = json.loads(audit["new_value"])
    assert old_val["pi_number"] == "PI-AUDIT-1"
    assert new_val["header"]["pi_number"] == "PI-AUDIT-1-UPDATED"
    assert new_val["header"]["comments"] == "audit test"
    assert len(new_val["items"]) == 2
    assert new_val["items"][0]["quantity"] == 150


def test_full_update_without_comments_key_preserves_comments(admin_client, db):
    """Edit Sale sends only pi fields — it must never NULL-wipe comments."""
    sale_id, item_ids = _create_sale_and_items(admin_client, pi_number="PI-COMM-1")
    r = admin_client.put(f"/api/sales/{sale_id}", json={"comments": "ship via Dubai"})
    assert r.status_code == 200
    assert admin_client.get(f"/api/sales/{sale_id}").get_json()["comments"] == "ship via Dubai"

    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-COMM-1", "pi_date": "2026-09-01",
                   "client_name": "Test Client"},
        "items": [
            {"id": item_ids[0], "product_name": "ProdA", "quantity": 100,
             "unit_price": 3.25, "unit": "KG"},
            {"id": item_ids[1], "product_name": "ProdB", "quantity": 50,
             "unit_price": 5.00, "unit": "DRUM"},
        ],
        "removedIds": [],
    })
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(f"/api/sales/{sale_id}").get_json()["comments"] == "ship via Dubai"


def test_clearing_comments_really_clears(admin_client, db):
    """An explicit null/empty comments patch must write, not silently no-op."""
    sale_id, _ = _create_sale_and_items(admin_client, pi_number="PI-COMM-2")
    r = admin_client.put(f"/api/sales/{sale_id}", json={"comments": "temporary"})
    assert r.status_code == 200

    r = admin_client.put(f"/api/sales/{sale_id}", json={"comments": None})
    assert r.status_code == 200
    detail = admin_client.get(f"/api/sales/{sale_id}").get_json()
    assert detail["comments"] in (None, "")


def test_full_update_without_pi_date_preserves_pi_date(admin_client, db):
    """Absent header fields keep their stored values (the rule comments now follow)."""
    sale_id, item_ids = _create_sale_and_items(admin_client, pi_number="PI-COMM-3")
    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-COMM-3", "client_name": "Test Client"},
        "items": [
            {"id": item_ids[0], "product_name": "ProdA", "quantity": 100,
             "unit_price": 3.25, "unit": "KG"},
            {"id": item_ids[1], "product_name": "ProdB", "quantity": 50,
             "unit_price": 5.00, "unit": "DRUM"},
        ],
        "removedIds": [],
    })
    assert r.status_code == 200, r.get_json()
    assert admin_client.get(f"/api/sales/{sale_id}").get_json()["pi_date"] == "2026-09-01"


def test_add_sale_item_audit_logged(admin_client, db):
    """Admin add_sale_item creates audit row with action='SALE_ITEM_ADD'."""
    sale_id, _ = _create_sale_and_items(admin_client, pi_number="PI-AUDIT-2")
    r = admin_client.post(f"/api/sales/{sale_id}/items", json={
        "product_name": "ProdC", "quantity": 25, "unit_price": 10.0, "unit": "KG"
    })
    assert r.status_code == 201
    new_item_id = r.get_json()["id"]

    # Check audit log
    logs = admin_client.get("/api/audit-logs", query_string={"limit": 10}).get_json()
    audit = next((log for log in logs if log["action"] == "SALE_ITEM_ADD" and log["entity_id"] == new_item_id), None)
    assert audit is not None, f"SALE_ITEM_ADD audit not found for item {new_item_id}"
    assert audit["entity_type"] == "sale_item"
    new_val = json.loads(audit["new_value"])
    assert new_val["sale_id"] == sale_id
    assert new_val["product_name"] == "ProdC"
    assert new_val["quantity"] == 25
    assert new_val["unit_price"] == 10.0
    assert new_val["unit"] == "KG"
    assert audit["old_value"] is None


def test_update_sale_item_audit_logged(admin_client, db):
    """Admin update_sale_item creates audit row with action='SALE_ITEM_UPDATE'."""
    sale_id, item_ids = _create_sale_and_items(admin_client, pi_number="PI-AUDIT-3")
    item_id = item_ids[0]

    # Get original item
    original = admin_client.get(f"/api/sales/{sale_id}").get_json()
    old_item = next(i for i in original["items"] if i["id"] == item_id)

    # Update the item
    r = admin_client.put(f"/api/sales/{sale_id}/items/{item_id}", json={
        "quantity": 300, "unit_price": 6.5
    })
    assert r.status_code == 200

    # Check audit log
    logs = admin_client.get("/api/audit-logs", query_string={"limit": 10}).get_json()
    audit = next((log for log in logs if log["action"] == "SALE_ITEM_UPDATE" and log["entity_id"] == item_id), None)
    assert audit is not None, f"SALE_ITEM_UPDATE audit not found for item {item_id}"
    assert audit["entity_type"] == "sale_item"
    old_val = json.loads(audit["old_value"])
    new_val = json.loads(audit["new_value"])
    assert old_val["product_name"] == "ProdA"
    assert old_val["quantity"] == 100
    assert old_val["unit_price"] == 3.25
    assert new_val["quantity"] == 300
    assert new_val["unit_price"] == 6.5



def test_update_sale_full_persists_pi_file_path(admin_client, db):
    """P1-15: the full-body path silently dropped pi_file_path.

    The flat patch route (PUT with a header-only body) wrote it, so the two
    routes disagreed: a field the API accepts and advertises was discarded with
    no trace anywhere - and the audit snapshot excluded it, so nothing recorded
    the loss either.
    """
    sale_id, item_ids = _create_sale_and_items(admin_client, pi_number="PI-PATH-1")
    stored_path = "C:/srv/private/tenants/acme/2026/pi.pdf"
    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-PATH-1", "client_name": "Updated Client",
                   "pi_file_path": stored_path},
        "items": [
            {"id": item_ids[0], "product_name": "ProdA", "quantity": 150,
             "unit_price": 3.25, "unit": "KG"},
            {"id": item_ids[1], "product_name": "ProdB", "quantity": 75,
             "unit_price": 5.00, "unit": "DRUM"},
        ],
        "removedIds": [],
    })
    assert r.status_code == 200, r.get_json()
    got = db.execute("SELECT pi_file_path FROM sales WHERE id = %s",
                     (sale_id,)).fetchone()[0]
    assert got == stored_path, "the full-body update discarded pi_file_path"


def test_update_sale_full_keeps_pi_file_path_when_absent(admin_client, db):
    """Absent optional fields keep their stored values - never NULL-wipe.

    This is the existing rule at sales.py ("Absent optional fields keep their
    stored values"), applied to the newly-persisted field.
    """
    sale_id, item_ids = _create_sale_and_items(admin_client, pi_number="PI-PATH-2")
    kept = "C:/kept/pi.pdf"
    db.execute("UPDATE sales SET pi_file_path = %s WHERE id = %s", (kept, sale_id))
    db.commit()
    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-PATH-2"},
        "items": [
            {"id": item_ids[0], "product_name": "ProdA", "quantity": 150,
             "unit_price": 3.25, "unit": "KG"},
            {"id": item_ids[1], "product_name": "ProdB", "quantity": 75,
             "unit_price": 5.00, "unit": "DRUM"},
        ],
        "removedIds": [],
    })
    assert r.status_code == 200, r.get_json()
    got = db.execute("SELECT pi_file_path FROM sales WHERE id = %s",
                     (sale_id,)).fetchone()[0]
    assert got == kept, "an omitted field NULL-wiped the stored value"
