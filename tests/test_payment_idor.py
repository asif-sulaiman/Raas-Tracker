"""P5: IDOR on nested sale resources.

`PUT /api/sales/<sale_id>/payments/<payment_id>` and
`DELETE /api/sales/<sale_id>/payments/<payment_id>` take `sale_id` from the
URL but the writers only filter on `payment_id`, so a caller who knows one
payment's id can edit or delete it through *any* sale's URL — including a
sale they do not own. The binding must be enforced: a payment may only be
touched through the sale it belongs to.

Follow-up (same class): `PUT /api/sales/<sale_id>/items/<item_id>` had the
same hole — `update_sale_item` filtered on `item_id` only and the route
discarded its return, so a wrong-sale or unknown id answered a silent
200. The DELETE sibling was already scoped; PUT now matches it.
"""
import pytest


def _create_sale(client, pi_number):
    r = client.post("/api/sales", json={
        "sale": {
            "pi_number": pi_number,
            "pi_date": "2026-09-01",
            "client_name": "IDOR Client",
            "company_id": None,
        },
        "items": [
            {"product_name": "ProdA", "quantity": 100, "unit_price": 3.25, "unit": "KG"},
        ],
    })
    assert r.status_code == 201, f"create sale failed: {r.get_json()}"
    return r.get_json()["id"]


def _record_payment(client, sale_id, amount="1000"):
    r = client.put(f"/api/sales/{sale_id}/payment", json={
        "payment_date": "2026-09-15",
        "payment_amount": amount,
        "notes": "test",
    })
    assert r.status_code == 200, f"record payment failed: {r.get_json()}"
    return r.get_json()["payment_id"]


def _edit_payment(client, sale_id, payment_id, amount="2000"):
    return client.put(f"/api/sales/{sale_id}/payments/{payment_id}", json={
        "payment_amount": amount,
        "notes": "tampered",
    })


def _delete_payment(client, sale_id, payment_id):
    return client.delete(f"/api/sales/{sale_id}/payments/{payment_id}")


def test_edit_payment_through_wrong_sale_is_404(admin_client, db):
    """A payment must not be editable through another sale's URL."""
    sale_a = _create_sale(admin_client, "PI-IDOR-A")
    sale_b = _create_sale(admin_client, "PI-IDOR-B")
    pid_a = _record_payment(admin_client, sale_a)

    r = _edit_payment(admin_client, sale_b, pid_a)
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.get_json()}"

    # The payment itself is untouched.
    get = admin_client.get(f"/api/sales/{sale_a}").get_json()
    paid = [p for p in get["payments"] if p["id"] == pid_a][0]
    assert paid["payment_amount"] == 1000.0


def test_delete_payment_through_wrong_sale_is_404(admin_client, db):
    """A payment must not be deletable through another sale's URL."""
    sale_a = _create_sale(admin_client, "PI-IDOR-C")
    sale_b = _create_sale(admin_client, "PI-IDOR-D")
    pid_a = _record_payment(admin_client, sale_a)

    r = _delete_payment(admin_client, sale_b, pid_a)
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.get_json()}"

    # The payment still exists.
    assert admin_client.get(f"/api/sales/{sale_a}").get_json()["payments"]


def test_edit_payment_through_own_sale_is_200(admin_client, db):
    """Editing a payment through its own sale still works."""
    sale_a = _create_sale(admin_client, "PI-IDOR-E")
    pid_a = _record_payment(admin_client, sale_a)

    r = _edit_payment(admin_client, sale_a, pid_a)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.get_json()}"
    assert r.get_json()["message"] == "Payment updated"


def test_delete_payment_through_own_sale_is_200(admin_client, db):
    """Deleting a payment through its own sale still works."""
    sale_a = _create_sale(admin_client, "PI-IDOR-F")
    pid_a = _record_payment(admin_client, sale_a)

    r = _delete_payment(admin_client, sale_a, pid_a)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.get_json()}"
    assert r.get_json()["message"] == "Payment deleted"


# ---------- sale items (same IDOR class, PUT route) ----------

def _update_item(client, sale_id, item_id, name="Tampered"):
    return client.put(f"/api/sales/{sale_id}/items/{item_id}", json={
        "product_name": name,
    })


def _first_item_id(client, sale_id):
    items = client.get(f"/api/sales/{sale_id}").get_json()["items"]
    return items[0]["id"]


def test_update_item_through_wrong_sale_is_404(admin_client, db):
    """A sale item must not be editable through another sale's URL."""
    sale_a = _create_sale(admin_client, "PI-IDOR-G")
    sale_b = _create_sale(admin_client, "PI-IDOR-H")
    item_a = _first_item_id(admin_client, sale_a)

    r = _update_item(admin_client, sale_b, item_a)
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.get_json()}"

    # The item itself is untouched.
    after = admin_client.get(f"/api/sales/{sale_a}").get_json()["items"]
    assert [i for i in after if i["id"] == item_a][0]["product_name"] == "ProdA"


def test_update_item_through_own_sale_is_200(admin_client, db):
    """Editing an item through its own sale still works."""
    sale_a = _create_sale(admin_client, "PI-IDOR-I")
    item_a = _first_item_id(admin_client, sale_a)

    r = _update_item(admin_client, sale_a, item_a)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.get_json()}"
    assert r.get_json()["message"] == "Item updated"
