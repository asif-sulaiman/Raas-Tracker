"""Multi-invoice batch creation (POST /api/sales/<id>/invoices/batch).

Mirrors the /api/sales/batch atomicity contract: N invoices in one
transaction, explicit per-line allocation, header == sum(lines) always,
over-allocation rejected with 400 and nothing written.
"""


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
        {"invoice_number": "INV-B1-A",
         "lines": [{"sale_item_id": a, "quantity": 4}]},
        {"invoice_number": "INV-B1-B",
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


def test_batch_over_allocation_rejected_and_atomic(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo2")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-2")
    a, b = _item_ids(db, sid)
    payload = {"invoices": [
        {"invoice_number": "INV-B2-A",
         "lines": [{"sale_item_id": a, "quantity": 8}]},
        {"invoice_number": "INV-B2-B",
         "lines": [{"sale_item_id": a, "quantity": 5}]},  # 8+5 > 10
    ]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 400, r.get_json()
    assert _invoice_rows(db, sid) == []


def test_batch_single_invoice_path_still_works(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo3")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-3")
    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-SINGLE"})
    assert r.status_code == 201


def test_batch_duplicate_invoice_number_rejected(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo4")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-4")
    a, b = _item_ids(db, sid)
    admin_client.post(f"/api/sales/{sid}/invoices",
                      json={"invoice_number": "INV-EXISTING"})
    payload = {"invoices": [
        {"invoice_number": "INV-EXISTING",
         "lines": [{"sale_item_id": a, "quantity": 1}]},
    ]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 400


def test_batch_empty_invoice_lines_rejected(admin_client, db):
    cid = _company(admin_client, db, name="BatchCo5")
    sid = _sale_two_items(admin_client, cid, "PI-BATCH-5")
    payload = {"invoices": [{"invoice_number": "INV-EMPTY", "lines": []}]}
    r = admin_client.post(f"/api/sales/{sid}/invoices/batch", json=payload)
    assert r.status_code == 400
