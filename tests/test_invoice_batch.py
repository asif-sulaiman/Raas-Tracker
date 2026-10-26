"""Multi-invoice batch creation (POST /api/sales/<id>/invoices/batch).

Mirrors the /api/sales/batch atomicity contract: N invoices in one
transaction, explicit per-line allocation, header == sum(lines) always.
Quantities are free relative to the PI, so over-allocation is accepted; a
batch that fails any guard still writes nothing.
"""

import pytest


def _company(admin_client, db, name="BatchCo"):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale_two_items(admin_client, company_id, pi):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x", "company_id": company_id},
        "items": [
            {"product_name": "ProdA", "quantity": 10, "unit_price": 5, "unit": "KG"},
            {"product_name": "ProdB", "quantity": 20, "unit_price": 10, "unit": "KG"},
        ]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _item_ids(db, sale_id):
    return [r[0] for r in db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchall()]


def _invoice_rows(db, sale_id):
    return db.execute(
        "SELECT id, invoice_number, amount FROM invoices WHERE sale_id = %s ORDER BY id",
        (sale_id,)).fetchall()


def _lines_sum(db, invoice_id):
    return float(db.execute(
        "SELECT COALESCE(SUM(line_total),0) FROM invoice_items WHERE invoice_id = %s",
        (invoice_id,)).fetchone()[0])


def test_batch_two_invoices_split_by_line_allocation(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo1")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-1")
    a, b = _item_ids(db, sid)
    payload = {"invoices": [
        {"invoice_number": "INV-B1-A", "invoice_date": "2026-01-15",
         "lines": [{"sale_item_id": a, "quantity": 4}]},
        {"invoice_number": "INV-B1-B", "invoice_date": "2026-01-16",
         "lines": [{"sale_item_id": b, "quantity": 10},
                   {"sale_item_id": a, "quantity": 6}]},
    ]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert len(body["invoices"]) == 2

    invs = _invoice_rows(db, sid)
    assert len(invs) == 2
    # header == sum(lines) for each invoice
    for inv_id, _num, hdr in invs:
        assert float(hdr) == _lines_sum(db, inv_id)
    # per-line allocation honoured: 3 lines total
    assert db.execute(
        "SELECT COUNT(*) FROM invoice_items WHERE invoice_id IN "
        "(SELECT id FROM invoices WHERE sale_id=%s)",
        (sid,)).fetchone()[0] == 3
    # amounts: A = 4*5=20, B = 10*10 + 6*5 = 130
    assert sorted(float(r[2]) for r in invs) == [20.0, 130.0]


def test_batch_accepts_allocations_above_pi(admin_client, db):
    """8 + 5 against a PI of 10 is fine: there is no quantity ceiling."""
    cid = _company(admin_client, db, name="BatchCo2")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-2")
    a, b = _item_ids(db, sid)
    payload = {"invoices": [
        {"invoice_number": "INV-B2-A", "invoice_date": "2026-01-15",
         "lines": [{"sale_item_id": a, "quantity": 8}]},
        {"invoice_number": "INV-B2-B", "invoice_date": "2026-01-16",
         "lines": [{"sale_item_id": b, "quantity": 10},
                   {"sale_item_id": a, "quantity": 5}]},  # 8+5 > 10
    ]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 201, r.get_json()

    invs = _invoice_rows(db, sid)
    assert len(invs) == 2
    # header == sum(lines) for each invoice
    for inv_id, _num, hdr in invs:
        assert float(hdr) == _lines_sum(db, inv_id)
    # A = 8*5=40; B = 10*10 + 5*5=125
    assert sorted(float(row[2]) for row in invs) == [40.0, 125.0]


def test_batch_failure_writes_no_invoice(admin_client, db):
    """A refused batch writes nothing, even when its earlier entries are valid.

    Duplicate numbers inside one batch are a guard failure, so this pins the
    all-or-nothing contract without relying on the removed ceiling.
    """
    cid = _company(admin_client, db, name="BatchCo2b")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-2B")
    a, b = _item_ids(db, sid)
    payload = {"invoices": [
        {"invoice_number": "INV-B2-OK", "invoice_date": "2026-01-15",
         "lines": [{"sale_item_id": a, "quantity": 1}]},
        {"invoice_number": "INV-B2-OK", "invoice_date": "2026-01-16",
         "lines": [{"sale_item_id": b, "quantity": 1}]},
    ]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 400, r.get_json()
    assert _invoice_rows(db, sid) == []


def test_batch_single_invoice_path_still_works(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo3")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-3")
    a = _item_ids(db, sid)[0]
    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-SINGLE",
                                "invoice_date": "2026-01-15",
                                "lines": [{"sale_item_id": a, "quantity": 4}]})
    assert r.status_code == 201


def test_batch_duplicate_invoice_number_rejected(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo4")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-4")
    a, b = _item_ids(db, sid)
    admin_client.post(f"/api/sales/{sid}/invoices",
                      json={"invoice_number": "INV-EXISTING",
                            "invoice_date": "2026-01-15",
                            "lines": [{"sale_item_id": a, "quantity": 1}]})
    payload = {"invoices": [
        {"invoice_number": "INV-EXISTING", "invoice_date": "2026-01-16",
         "lines": [{"sale_item_id": a, "quantity": 1}]},
    ]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 400


def test_batch_empty_invoice_lines_rejected(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo5")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-5")
    payload = {"invoices": [{"invoice_number": "INV-EMPTY",
                             "invoice_date": "2026-01-15", "lines": []}]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 400


def test_invoice_batch_line_model_gt_zero():
    """InvoiceBatchLineIn rejects quantity <= 0."""
    from flask_app import InvoiceBatchLineIn
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvoiceBatchLineIn.model_validate({"sale_item_id": 1, "quantity": 0})
    with pytest.raises(ValidationError):
        InvoiceBatchLineIn.model_validate({"sale_item_id": 1, "quantity": -1})

    line = InvoiceBatchLineIn.model_validate({"sale_item_id": 1, "quantity": 0.5})
    assert line.quantity == 0.5
