"""Sale payments, auto-complete, and atomic full-update tests."""
import pytest

from chem_stock import (
    _complete_if_paid,
    add_sale,
    get_sale_by_id,
    get_sale_invoice_total,
    get_sale_total_paid,
    move_sale_to_stage,
    record_sale_payment,
    update_sale_full,
)


def _sale(db, total=100.0):
    return add_sale(db, {"pi_number": "PI-T1", "client_name": "T Co"},
                    [{"product_name": "W", "quantity": 10, "unit_price": total / 10}])


def test_partial_payment_totals(db):
    sid = _sale(db)
    out = record_sale_payment(db, sid, "2026-09-01", 40.0)
    assert out["total_paid"] == 40.0
    assert out["balance"] == 60.0
    assert get_sale_total_paid(db, sid) == 40.0
    assert get_sale_invoice_total(db, sid) == 100.0


def test_full_payment_auto_completes(db):
    sid = _sale(db)
    move_sale_to_stage(db, sid, "payment_due", "test")
    out = record_sale_payment(db, sid, "2026-09-02", 100.0)
    assert out["stage"] == "completed"
    assert get_sale_by_id(db, sid)["stage"] == "completed"


def test_partial_payment_does_not_complete(db):
    sid = _sale(db)
    move_sale_to_stage(db, sid, "payment_due", "test")
    out = record_sale_payment(db, sid, "2026-09-02", 99.99)
    assert out["stage"] == "payment_due"


def test_complete_if_paid_only_from_payment_due(db):
    sid = _sale(db)  # stage pi_issued
    assert _complete_if_paid(db, sid, "pi_issued", 100.0, 100.0) is False
    assert _complete_if_paid(db, sid, "payment_due", 50.0, 100.0) is False
    assert _complete_if_paid(db, sid, "payment_due", 100.0, 100.0) is True


def test_non_positive_payment_rejected(db):
    sid = _sale(db)
    with pytest.raises(ValueError):
        record_sale_payment(db, sid, "2026-09-02", 0)
    with pytest.raises(ValueError):
        record_sale_payment(db, sid, "2026-09-02", -5)


def _snap(db, sid):
    return get_sale_by_id(db, sid)


def test_full_update_happy_path(db):
    sid = add_sale(db, {"pi_number": "PI-T2", "client_name": "Old"},
                   [{"product_name": "A", "quantity": 1, "unit_price": 10},
                    {"product_name": "B", "quantity": 2, "unit_price": 20},
                    {"product_name": "C", "quantity": 3, "unit_price": 30}])
    ids = {i["product_name"]: i["id"] for i in _snap(db, sid)["items"]}
    sale = update_sale_full(
        db, sid,
        {"pi_number": "PI-T2X", "pi_date": None, "client_name": "New"},
        [{"id": ids["A"], "product_name": "A+", "quantity": 5, "unit_price": 6},
         {"id": ids["B"], "product_name": "B", "quantity": 2, "unit_price": 20},
         {"product_name": "D", "quantity": 4, "unit_price": 40}],
        [ids["C"]])
    assert sale["pi_number"] == "PI-T2X"
    assert sorted(i["product_name"] for i in sale["items"]) == ["A+", "B", "D"]


def test_full_update_ghost_sale_none(db):
    assert update_sale_full(db, 999999, {"pi_number": "X"},
                            [{"product_name": "Q", "quantity": 1, "unit_price": 1}], []) is None


def _atomic(db, mutate):
    sid = add_sale(db, {"pi_number": "PI-T3", "client_name": "Atomic"},
                   [{"product_name": "A", "quantity": 1, "unit_price": 10},
                    {"product_name": "B", "quantity": 2, "unit_price": 20}])
    before = _snap(db, sid)
    with pytest.raises(ValueError):
        mutate(db, sid, before)
    assert _snap(db, sid) == before


def test_atomic_negative_price(db):
    def mutate(db, sid, before):
        a = before["items"][0]
        update_sale_full(db, sid, {"pi_number": "HACK"},
                         [{"id": a["id"], "product_name": "A", "quantity": 1, "unit_price": 1},
                          {"product_name": "X", "quantity": 1, "unit_price": -1}], [])
    _atomic(db, mutate)


def test_atomic_cross_sale_id(db):
    sid2 = add_sale(db, {"pi_number": "PI-T4"},
                    [{"product_name": "Z", "quantity": 1, "unit_price": 1}])
    other = _snap(db, sid2)["items"][0]["id"]

    def mutate(db, sid, before):
        update_sale_full(db, sid, {"pi_number": "HACK"},
                         [{"id": other, "product_name": "Z", "quantity": 1, "unit_price": 1}], [])
    _atomic(db, mutate)


def test_atomic_update_plus_remove_conflict(db):
    def mutate(db, sid, before):
        a = before["items"][0]
        update_sale_full(db, sid, {"pi_number": "HACK"},
                         [{"id": a["id"], "product_name": "A", "quantity": 1, "unit_price": 1}],
                         [a["id"]])
    _atomic(db, mutate)


def test_atomic_unknown_removal(db):
    def mutate(db, sid, before):
        a = before["items"][0]
        update_sale_full(db, sid, {"pi_number": "HACK"},
                         [{"id": a["id"], "product_name": "A", "quantity": 1, "unit_price": 1}],
                         [424242])
    _atomic(db, mutate)


def test_atomic_empty_items(db):
    sid = add_sale(db, {"pi_number": "PI-T5"},
                   [{"product_name": "A", "quantity": 1, "unit_price": 1}])
    before = _snap(db, sid)
    with pytest.raises(ValueError):
        update_sale_full(db, sid, {"pi_number": "HACK"}, [], [])
    assert _snap(db, sid) == before


def test_api_full_update_roundtrip(admin_client):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-T6", "client_name": "API"},
        "items": [{"product_name": "A", "quantity": 1, "unit_price": 10},
                  {"product_name": "B", "quantity": 2, "unit_price": 20}]})
    assert r.status_code in (200, 201)
    sid = r.get_json().get("id") or r.get_json()["sale"]["id"]
    items = admin_client.get(f"/api/sales/{sid}").get_json()["items"]
    ids = {i["product_name"]: i["id"] for i in items}
    r = admin_client.put(f"/api/sales/{sid}", json={
        "header": {"pi_number": "PI-T6X", "pi_date": None, "client_name": "API2"},
        "items": [{"id": ids["A"], "product_name": "A", "quantity": 9, "unit_price": 9},
                  {"product_name": "C", "quantity": 1, "unit_price": 1}],
        "removedIds": [ids["B"]]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["pi_number"] == "PI-T6X"
    assert sorted(i["product_name"] for i in body["items"]) == ["A", "C"]
    # Legacy header-only patch still works.
    assert admin_client.put(f"/api/sales/{sid}", json={"client_name": "API3"}).status_code == 200
    assert admin_client.get(f"/api/sales/{sid}").get_json()["client_name"] == "API3"
