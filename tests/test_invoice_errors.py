"""Invoice creation failure modes: structured errors, never a bare 500.

Regression context: the LC shipment-barrier modal once POSTed to
``/api/sales/null/invoices`` (null sale id) and the operator saw
``500 {"error": "internal server error"}``. The path matches no POST route,
so Werkzeug raises 405 — which, without a dedicated handler, fell into the
catch-all ``Exception`` handler and was reported as a 500. Likewise a
non-finite PI line total (NaN/Inf in a REAL column) poisoned the ``SUM`` and
raised ``InvalidOperation`` inside the money math, surfacing as the same
generic 500 instead of a 400 naming the bad data.
"""


def _company(admin_client, db, name="ErrCo"):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return db.execute(
        "SELECT id FROM companies WHERE name = %s", (name,)).fetchone()[0]


def _sale(admin_client, company_id, pi="PI-ERR-1"):
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": pi, "client_name": "x",
                 "company_id": company_id},
        "items": [{"product_name": "ProdErr", "quantity": 10,
                   "unit_price": 5, "unit": "KG"}]})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def test_wrong_method_on_api_path_returns_405_json(admin_client):
    """A POST where only GET matches must be a 405 with JSON, not a 500."""
    r = admin_client.post("/api/sales/null/invoices",
                          json={"invoice_number": "INV-405"})
    assert r.status_code == 405
    assert r.get_json() == {"error": "method not allowed"}


def test_nonfinite_pi_total_returns_400_not_500(admin_client, db):
    """NaN quantity is bad data (400), and nothing may be persisted."""
    cid = _company(admin_client, db)
    sid = _sale(admin_client, cid)
    db.execute("UPDATE sale_items SET quantity = %s WHERE sale_id = %s",
               (float("nan"), sid))
    db.commit()

    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-NAN"})
    assert r.status_code == 400
    body = r.get_json()
    assert "error" in body and "non-finite" in body["error"]

    count = db.execute("SELECT COUNT(*) FROM invoices WHERE sale_id = %s",
                       (sid,)).fetchone()[0]
    assert count == 0


def test_infinite_unit_price_returns_400_not_500(admin_client, db):
    """Inf unit price is bad data (400), and nothing may be persisted."""
    cid = _company(admin_client, db, name="ErrCoInf")
    sid = _sale(admin_client, cid, pi="PI-ERR-INF")
    db.execute("UPDATE sale_items SET unit_price = %s WHERE sale_id = %s",
               (float("inf"), sid))
    db.commit()

    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-INF"})
    assert r.status_code == 400
    assert "non-finite" in r.get_json()["error"]

    count = db.execute("SELECT COUNT(*) FROM invoices WHERE sale_id = %s",
                       (sid,)).fetchone()[0]
    assert count == 0


def test_nonfinite_invoiced_lines_return_400_not_500(admin_client, db):
    """A legacy NaN line_total must not 500 a follow-up invoice create."""
    cid = _company(admin_client, db, name="ErrCoLegacy")
    sid = _sale(admin_client, cid, pi="PI-ERR-LEGACY")
    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-LEGACY-1"})
    assert r.status_code == 201, r.get_json()
    inv_id = r.get_json()["invoice_id"]
    db.execute("UPDATE invoice_items SET line_total = 'NaN' "
               "WHERE invoice_id = %s", (inv_id,))
    db.commit()

    r = admin_client.post(f"/api/sales/{sid}/invoices",
                          json={"invoice_number": "INV-LEGACY-2"})
    assert r.status_code == 400
    assert "non-finite" in r.get_json()["error"]
