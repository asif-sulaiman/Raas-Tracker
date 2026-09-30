"""P4: live commercial report — gates, row contract, sale-level aggregation, CSV export."""
import csv
import io
from datetime import date

import flask_app


# Exact contract keys the API must emit per sale-item row.
CONTRACT_KEYS = {
    "sale_id", "customer_name", "pi_number", "pi_date", "lc_number", "lc_date",
    "product_name", "unit", "quantity", "unit_price", "total_price",
    "invoice_date", "latest_ship_date", "actual_ship_date",
    "received_amount", "due_amount", "receive_date", "maturity_date",
    "payment_status", "payment_comment",
}

STATUSES = {"Paid", "Overdue", "Partial", "Due", "Pending"}

CSV_HEADER = (
    "Customer Name,PI No,PI Date,LC No,LC Date,Product Name,Unit,Quantity,"
    "Unit Price,Total Price ($),Invoice Date,Latest Ship Date,Actual Ship Date,"
    "Maturity Date,Receive Date,Received Amount ($),Due Amount ($),"
    "Payment Status,Payment Comment"
)


def _sale(db, pi_number, client_name="Test Client", items=None, created_at="2026-01-01 00:00:00",
          maturity_date=None, comments=None, shipment_date=None, pi_date="2026-01-15",
          lc_number=None, lc_date=None):
    """Insert a sale + items directly (full control over dates/totals)."""
    sid = db.execute(
        """INSERT INTO sales (stage, pi_number, pi_date, client_name, lc_number, lc_date,
                              shipment_date, maturity_date, comments, created_at)
           VALUES ('pi_issued', %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (pi_number, pi_date, client_name, lc_number, lc_date,
         shipment_date, maturity_date, comments, created_at)).fetchone()[0]
    for it in (items or [{"product_name": "Item", "quantity": 1,
                          "unit_price": 10, "unit": "KG"}]):
        db.execute(
            "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, it["product_name"], it["quantity"], it["unit_price"],
             it.get("unit", "KG")))
    db.commit()
    return sid


def _pay(db, sid, amount, payment_date="2026-02-01"):
    db.execute(
        "INSERT INTO sale_payments (sale_id, payment_date, payment_amount) "
        "VALUES (%s, %s, %s)", (sid, payment_date, amount))
    db.commit()


def _ship(db, sid, ship_date, invoice_date=None):
    db.execute(
        "INSERT INTO shipments (sale_id, ship_date, invoice_date) VALUES (%s, %s, %s)",
        (sid, ship_date, invoice_date))
    db.commit()


def _rows(client):
    r = client.get("/api/reports/live")
    assert r.status_code == 200
    return r.get_json()


def _of(rows, sid):
    return [r for r in rows if r["sale_id"] == sid]


# ------------------------- gates -------------------------
def test_live_report_401_unauthenticated(db):
    _sale(db, "PI-G1")
    c = flask_app.app.test_client()
    r = c.get("/api/reports/live")
    assert r.status_code == 401
    assert r.get_json()["error"] == "authentication required"


def test_live_report_403_non_admin(user_client, db):
    _sale(db, "PI-G2")
    r = user_client.get("/api/reports/live")
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"


def test_live_report_200_admin_json_list(admin_client, db):
    sid = _sale(db, "PI-G3")
    r = admin_client.get("/api/reports/live")
    assert r.status_code == 200
    body = r.get_json()
    assert isinstance(body, list)
    assert [x["pi_number"] for x in body] == ["PI-G3"]
    assert body[0]["sale_id"] == sid


# ------------------------- row contract -------------------------
def test_row_shape_contract_keys(admin_client, db):
    sid = _sale(db, "PI-SHAPE", client_name="Shape Co",
                items=[{"product_name": "Acid", "quantity": 2, "unit_price": 25,
                        "unit": "DRUM"}],
                maturity_date="2099-12-31", comments="  needs LC  ",
                shipment_date="2026-03-15")
    _ship(db, sid, "2026-03-10", "2026-03-05")
    _pay(db, sid, 10, "2026-03-20")
    rows = _of(_rows(admin_client), sid)
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == CONTRACT_KEYS
    assert row["payment_status"] in STATUSES
    assert row["sale_id"] == sid
    assert row["customer_name"] == "Shape Co"
    assert row["product_name"] == "Acid"
    assert row["unit"] == "DRUM"
    assert row["total_price"] == 50
    assert row["invoice_date"] == "2026-03-05"
    assert row["latest_ship_date"] == "2026-03-10"
    assert row["actual_ship_date"] == "2026-03-15"
    assert row["receive_date"] == "2026-03-20"
    assert row["payment_comment"] == "needs LC"


def test_rows_ordered_by_created_at_desc(admin_client, db):
    _sale(db, "PI-OLD", created_at="2026-01-01 00:00:00")
    _sale(db, "PI-NEW", created_at="2026-06-01 00:00:00")
    assert [r["pi_number"] for r in _rows(admin_client)] == ["PI-NEW", "PI-OLD"]


# ------------------------- aggregation regression -------------------------
def test_two_items_one_payment_not_double_counted(admin_client, db):
    """Sale with 2 items + 2 shipments + 1 payment: payment counted once."""
    sid = _sale(db, "PI-MULTI", client_name="Multi Co",
                items=[{"product_name": "A", "quantity": 10, "unit_price": 10, "unit": "KG"},
                       {"product_name": "B", "quantity": 5, "unit_price": 20, "unit": "L"}],
                maturity_date="2099-12-31")
    _ship(db, sid, "2026-04-01", "2026-03-30")
    _ship(db, sid, "2026-04-10", "2026-04-05")
    _pay(db, sid, 50, "2026-04-15")

    rows = _of(_rows(admin_client), sid)
    assert len(rows) == 2  # one row per sale_item, shipments must not multiply
    assert {r["product_name"] for r in rows} == {"A", "B"}
    # Sale total = 10*10 + 5*20 = 200; paid 50 once (not per item/shipment).
    for r in rows:
        assert r["received_amount"] == 50
        assert r["due_amount"] == 150
        assert r["latest_ship_date"] == "2026-04-10"  # MAX across shipments
        assert r["receive_date"] == "2026-04-15"
    assert rows[0]["due_amount"] == rows[1]["due_amount"]
    assert rows[0]["total_price"] == 100
    assert rows[1]["total_price"] == 100


def test_due_clamped_at_zero_and_paid_status(admin_client, db):
    sid = _sale(db, "PI-OVERPAY", maturity_date="2020-01-01")
    _pay(db, sid, 50)  # total 10 -> overpaid
    row = _of(_rows(admin_client), sid)[0]
    assert row["due_amount"] == 0
    assert row["payment_status"] == "Paid"


# ------------------------- payment_status derivation -------------------------
def test_status_paid_takes_precedence_over_past_maturity(admin_client, db):
    sid = _sale(db, "PI-PAID", maturity_date="2020-01-01")
    _pay(db, sid, 10)  # full payment, maturity long past
    row = _of(_rows(admin_client), sid)[0]
    assert row["due_amount"] == 0
    assert row["payment_status"] == "Paid"


def test_status_overdue_past_maturity_with_due(admin_client, db):
    sid = _sale(db, "PI-OVERDUE", maturity_date="2020-01-01")  # no payment
    row = _of(_rows(admin_client), sid)[0]
    assert row["due_amount"] == 10
    assert row["payment_status"] == "Overdue"


def test_status_partial(admin_client, db):
    sid = _sale(db, "PI-PARTIAL", maturity_date="2099-12-31")
    _pay(db, sid, 4)  # 4 of 10
    row = _of(_rows(admin_client), sid)[0]
    assert row["received_amount"] == 4
    assert row["due_amount"] == 6
    assert row["payment_status"] == "Partial"


def test_status_due_future_maturity_no_payment(admin_client, db):
    sid = _sale(db, "PI-DUE", maturity_date="2099-12-31")
    row = _of(_rows(admin_client), sid)[0]
    assert row["received_amount"] == 0
    assert row["payment_status"] == "Due"


def test_status_pending_no_maturity_no_payment(admin_client, db):
    sid = _sale(db, "PI-PENDING")
    row = _of(_rows(admin_client), sid)[0]
    assert row["payment_status"] == "Pending"


def test_payment_comment_blank_when_no_comments(admin_client, db):
    sid = _sale(db, "PI-NOCOMMENT", comments="   ")
    assert _of(_rows(admin_client), sid)[0]["payment_comment"] == ""


# ------------------------- filtered endpoint -------------------------
def _filtered_rows(client, params=None):
    """Call /api/reports/live/filtered with query params."""
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"/api/reports/live/filtered?{query}" if query else "/api/reports/live/filtered"
    r = client.get(url)
    assert r.status_code == 200
    return r.get_json()


def _summary(client, params=None):
    """Call /api/reports/live/summary with query params."""
    query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"/api/reports/live/summary?{query}" if query else "/api/reports/live/summary"
    r = client.get(url)
    assert r.status_code == 200
    return r.get_json()


def test_filter_by_date_anchor_and_range(admin_client, db):
    """date_from/to on pi_date anchor."""
    _sale(db, "PI-D1", created_at="2026-01-01 00:00:00", pi_date="2026-01-10")
    _sale(db, "PI-D2", created_at="2026-02-01 00:00:00", pi_date="2026-02-15")
    _sale(db, "PI-D3", created_at="2026-03-01 00:00:00", pi_date="2026-03-20")
    # Filter by pi_date from 2026-02-01 to 2026-03-01
    rows = _filtered_rows(admin_client, {
        "date_anchor": "pi_date",
        "date_from": "2026-02-01",
        "date_to": "2026-03-01",
    })
    pi_numbers = {r["pi_number"] for r in rows}
    assert pi_numbers == {"PI-D2"}  # Only PI-D2 falls in range


def test_filter_by_customer_name_ilike(admin_client, db):
    """Partial case-insensitive match on customer_name."""
    _sale(db, "PI-C1", client_name="Alpha Corp")
    _sale(db, "PI-C2", client_name="Beta Industries")
    _sale(db, "PI-C3", client_name="Gamma LLC")
    rows = _filtered_rows(admin_client, {"customer_name": "alpha"})
    assert {r["pi_number"] for r in rows} == {"PI-C1"}
    rows = _filtered_rows(admin_client, {"customer_name": "ind"})
    assert {r["pi_number"] for r in rows} == {"PI-C2"}
    rows = _filtered_rows(admin_client, {"customer_name": "xyz"})
    assert rows == []


def test_filter_by_product_name_exists(admin_client, db):
    """EXISTS subquery on sale_items.product_name (ILIKE)."""
    sid1 = _sale(db, "PI-P1", items=[
        {"product_name": "Sulfuric Acid", "quantity": 1, "unit_price": 10, "unit": "KG"},
        {"product_name": "Hydrochloric Acid", "quantity": 1, "unit_price": 20, "unit": "KG"},
    ])
    sid2 = _sale(db, "PI-P2", items=[
        {"product_name": "Sodium Hydroxide", "quantity": 1, "unit_price": 15, "unit": "KG"},
    ])
    rows = _filtered_rows(admin_client, {"product_name": "sulfur"})
    pi_numbers = {r["pi_number"] for r in rows}
    assert pi_numbers == {"PI-P1"}
    # Should return both rows for PI-P1 (both items match the sale)
    assert len(rows) == 2
    # "hydro" matches both "Hydrochloric Acid" (PI-P1) and "Sodium Hydroxide" (PI-P2)
    rows = _filtered_rows(admin_client, {"product_name": "hydro"})
    pi_numbers = {r["pi_number"] for r in rows}
    assert pi_numbers == {"PI-P1", "PI-P2"}


def test_filter_by_company_id(admin_client, db):
    """Exact FK match on company_id."""
    _sale(db, "PI-CO1", client_name="Company A")
    _sale(db, "PI-CO2", client_name="Company B")
    # Need companies to exist for company_id FK
    conn = db
    c1 = conn.execute("INSERT INTO companies (name) VALUES ('Company A') RETURNING id").fetchone()[0]
    c2 = conn.execute("INSERT INTO companies (name) VALUES ('Company B') RETURNING id").fetchone()[0]
    conn.execute("UPDATE sales SET company_id = %s WHERE pi_number = 'PI-CO1'", (c1,))
    conn.execute("UPDATE sales SET company_id = %s WHERE pi_number = 'PI-CO2'", (c2,))
    conn.commit()
    
    rows = _filtered_rows(admin_client, {"company_id": c1})
    assert {r["pi_number"] for r in rows} == {"PI-CO1"}
    rows = _filtered_rows(admin_client, {"company_id": c2})
    assert {r["pi_number"] for r in rows} == {"PI-CO2"}
    rows = _filtered_rows(admin_client, {"company_id": 9999})
    assert rows == []


def test_filter_by_stage_enum(admin_client, db):
    """Valid stages only: pi_issued, lc_received, shipment_ongoing, payment_due, completed."""
    _sale(db, "PI-S1", items=[{"product_name": "Item", "quantity": 1, "unit_price": 10}])
    _sale(db, "PI-S2", items=[{"product_name": "Item", "quantity": 1, "unit_price": 10}])
    db.execute("UPDATE sales SET stage = 'lc_received' WHERE pi_number = 'PI-S2'")
    db.commit()
    
    rows = _filtered_rows(admin_client, {"stage": "pi_issued"})
    assert {r["pi_number"] for r in rows} == {"PI-S1"}
    rows = _filtered_rows(admin_client, {"stage": "lc_received"})
    assert {r["pi_number"] for r in rows} == {"PI-S2"}
    rows = _filtered_rows(admin_client, {"stage": "invalid"})
    # Invalid stage should return empty (no match)
    assert rows == []


def test_filter_by_payment_status(admin_client, db):
    """Paid/Overdue/Partial/Due/Pending."""
    # Paid
    sid_paid = _sale(db, "PI-PAID2", maturity_date="2099-12-31")
    _pay(db, sid_paid, 10)
    # Overdue
    sid_overdue = _sale(db, "PI-OVERDUE2", maturity_date="2020-01-01")
    # Partial
    sid_partial = _sale(db, "PI-PARTIAL2", maturity_date="2099-12-31")
    _pay(db, sid_partial, 4)
    # Due
    sid_due = _sale(db, "PI-DUE2", maturity_date="2099-12-31")
    # Pending
    sid_pending = _sale(db, "PI-PENDING2")
    
    rows = _filtered_rows(admin_client, {"payment_status": "Paid"})
    assert {r["pi_number"] for r in rows} == {"PI-PAID2"}
    rows = _filtered_rows(admin_client, {"payment_status": "Overdue"})
    assert {r["pi_number"] for r in rows} == {"PI-OVERDUE2"}
    rows = _filtered_rows(admin_client, {"payment_status": "Partial"})
    assert {r["pi_number"] for r in rows} == {"PI-PARTIAL2"}
    rows = _filtered_rows(admin_client, {"payment_status": "Due"})
    assert {r["pi_number"] for r in rows} == {"PI-DUE2"}
    rows = _filtered_rows(admin_client, {"payment_status": "Pending"})
    assert {r["pi_number"] for r in rows} == {"PI-PENDING2"}


def test_pagination_page_size(admin_client, db):
    """page=2, page_size=1 returns second item."""
    _sale(db, "PI-PG1", created_at="2026-01-01 00:00:00")
    _sale(db, "PI-PG2", created_at="2026-01-02 00:00:00")
    _sale(db, "PI-PG3", created_at="2026-01-03 00:00:00")
    # Default page=1, page_size=50 -> all 3
    rows = _filtered_rows(admin_client, {"page_size": "50"})
    assert len(rows) == 3
    # page=1, page_size=1 -> first (newest)
    rows = _filtered_rows(admin_client, {"page": "1", "page_size": "1"})
    assert len(rows) == 1
    assert rows[0]["pi_number"] == "PI-PG3"
    # page=2, page_size=1 -> second
    rows = _filtered_rows(admin_client, {"page": "2", "page_size": "1"})
    assert len(rows) == 1
    assert rows[0]["pi_number"] == "PI-PG2"
    # page=3, page_size=1 -> third
    rows = _filtered_rows(admin_client, {"page": "3", "page_size": "1"})
    assert len(rows) == 1
    assert rows[0]["pi_number"] == "PI-PG1"


def test_summary_group_by_month(admin_client, db):
    """group_by=month returns period-aggregated summary."""
    # Sale in Jan 2026
    sid1 = _sale(db, "PI-SUM1", created_at="2026-01-10 00:00:00", pi_date="2026-01-10",
                 items=[{"product_name": "A", "quantity": 10, "unit_price": 100}], maturity_date="2026-02-15")
    _pay(db, sid1, 500, "2026-01-20")
    # Sale in Feb 2026
    sid2 = _sale(db, "PI-SUM2", created_at="2026-02-15 00:00:00", pi_date="2026-02-15",
                 items=[{"product_name": "B", "quantity": 5, "unit_price": 200}], maturity_date="2026-03-15")
    _pay(db, sid2, 200, "2026-02-20")
    # Sale in Mar 2026
    sid3 = _sale(db, "PI-SUM3", created_at="2026-03-10 00:00:00", pi_date="2026-03-10",
                 items=[{"product_name": "C", "quantity": 2, "unit_price": 500}], maturity_date="2026-04-15")
    
    summary = _summary(admin_client, {"group_by": "month", "date_anchor": "pi_date"})
    periods = summary["periods"]
    assert len(periods) == 3
    # Check period structure
    for p in periods:
        assert "period_start" in p
        assert "period_end" in p
        assert "kpis" in p
        assert "items" in p
        kpis = p["kpis"]
        assert set(kpis.keys()) == {"gross_sales", "received", "due", "overdue", "order_count"}
    # Jan period: gross=1000, received=500, due=500, overdue=500 (maturity 2026-02-15 < today 2026-09-28), orders=1
    jan = next(p for p in periods if p["period_start"] == "2026-01-01")
    assert jan["period_end"] == "2026-01-31"
    assert jan["kpis"]["gross_sales"] == 1000
    assert jan["kpis"]["received"] == 500
    assert jan["kpis"]["due"] == 500
    assert jan["kpis"]["overdue"] == 500
    assert jan["kpis"]["order_count"] == 1
    # Feb period: gross=1000, received=200, due=800, overdue=800 (maturity 2026-03-15 < today), orders=1
    feb = next(p for p in periods if p["period_start"] == "2026-02-01")
    assert feb["kpis"]["gross_sales"] == 1000
    assert feb["kpis"]["received"] == 200
    assert feb["kpis"]["due"] == 800
    assert feb["kpis"]["overdue"] == 800
    # Mar period: gross=1000, received=0, due=1000, overdue=1000 (maturity 2026-04-15 < today), orders=1
    mar = next(p for p in periods if p["period_start"] == "2026-03-01")
    assert mar["kpis"]["gross_sales"] == 1000
    assert mar["kpis"]["received"] == 0
    assert mar["kpis"]["due"] == 1000
    assert mar["kpis"]["overdue"] == 1000


# ------------------------- export -------------------------
def _export_rows(client, filters=None, method="POST"):
    """Call /api/reports/live/export with filters."""
    if method == "GET":
        query = "&".join(f"{k}={v}" for k, v in (filters or {}).items())
        url = f"/api/reports/live/export?{query}" if query else "/api/reports/live/export"
        return client.get(url)
    else:
        return client.post("/api/reports/live/export", json=filters or {})


def test_export_401_and_403(user_client, db):
    _sale(db, "PI-E0")
    c = flask_app.app.test_client()
    assert c.post("/api/reports/live/export", json={}).status_code == 401
    r = user_client.post("/api/reports/live/export", json={})
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"


def test_export_admin_csv(admin_client, db):
    _sale(db, "PI-E1", client_name="Alpha Co", created_at="2026-01-01 00:00:00",
          items=[{"product_name": "A", "quantity": 2, "unit_price": 5, "unit": "KG"}])
    _sale(db, "PI-E2", client_name="Beta Co", created_at="2026-01-02 00:00:00",
          items=[{"product_name": "B", "quantity": 1, "unit_price": 20, "unit": "L"},
                 {"product_name": "C", "quantity": 3, "unit_price": 7, "unit": "KG"}])
    expected_rows = _rows(admin_client)
    assert len(expected_rows) == 3

    r = admin_client.post("/api/reports/live/export", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["filename"] == f"commercial_report_{date.today().isoformat()}.csv"
    content = body["content"]
    assert isinstance(content, str)
    assert content.startswith("Customer Name,PI No,")

    lines = content.strip().splitlines()
    assert lines[0] == CSV_HEADER
    # One data line per report row (2 items on sale 2 -> 3 rows total).
    assert len(lines) == len(expected_rows) + 1
    parsed = list(csv.reader(io.StringIO(content)))
    assert len(parsed) == len(expected_rows) + 1
    assert {p[0] for p in parsed[1:]} == {"Alpha Co", "Beta Co"}


def test_export_neutralizes_formula_injection(admin_client, db):
    """User-supplied cells starting with =/+/-/@ must not reach Excel as live formulas."""
    _sale(db, "PI-FX1", client_name='=HYPERLINK("http://evil","x")',
          created_at="2026-01-01 00:00:00",
          items=[{"product_name": "=1+1", "quantity": 1, "unit_price": 10, "unit": "KG"}])
    r = admin_client.post("/api/reports/live/export", json={})
    assert r.status_code == 200
    parsed = list(csv.reader(io.StringIO(r.get_json()["content"])))
    assert len(parsed) == 2
    assert parsed[1][0] == '\'=HYPERLINK("http://evil","x")'
    assert parsed[1][5] == "'=1+1"


def test_export_filtered_by_date_range(admin_client, db):
    """Export with date_from/date_to filters."""
    _sale(db, "PI-ED1", created_at="2026-01-01 00:00:00", pi_date="2026-01-10")
    _sale(db, "PI-ED2", created_at="2026-02-01 00:00:00", pi_date="2026-02-15")
    _sale(db, "PI-ED3", created_at="2026-03-01 00:00:00", pi_date="2026-03-20")
    # Filter by pi_date from 2026-02-01 to 2026-03-01
    r = _export_rows(admin_client, {
        "date_anchor": "pi_date",
        "date_from": "2026-02-01",
        "date_to": "2026-03-01",
    })
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    # Header + 1 data row (only PI-ED2)
    assert len(parsed) == 2
    assert parsed[1][1] == "PI-ED2"  # PI No column


def test_export_filtered_by_customer(admin_client, db):
    """Export with customer_name filter."""
    _sale(db, "PI-EC1", client_name="Alpha Corp")
    _sale(db, "PI-EC2", client_name="Beta Industries")
    _sale(db, "PI-EC3", client_name="Gamma LLC")
    r = _export_rows(admin_client, {"customer_name": "alpha"})
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    assert len(parsed) == 2  # header + 1 row
    assert parsed[1][0] == "Alpha Corp"


def test_export_filtered_by_stage(admin_client, db):
    """Export with stage filter."""
    _sale(db, "PI-ES1", items=[{"product_name": "Item", "quantity": 1, "unit_price": 10}])
    _sale(db, "PI-ES2", items=[{"product_name": "Item", "quantity": 1, "unit_price": 10}])
    db.execute("UPDATE sales SET stage = 'lc_received' WHERE pi_number = 'PI-ES2'")
    db.commit()
    
    r = _export_rows(admin_client, {"stage": "pi_issued"})
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    assert len(parsed) == 2  # header + 1 row
    assert parsed[1][1] == "PI-ES1"


def test_export_filtered_by_payment_status(admin_client, db):
    """Export with payment_status filter."""
    # Paid
    sid_paid = _sale(db, "PI-EP1", maturity_date="2099-12-31")
    _pay(db, sid_paid, 10)
    # Overdue
    sid_overdue = _sale(db, "PI-EP2", maturity_date="2020-01-01")
    # Partial
    sid_partial = _sale(db, "PI-EP3", maturity_date="2099-12-31")
    _pay(db, sid_partial, 4)
    
    r = _export_rows(admin_client, {"payment_status": "Paid"})
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    assert len(parsed) == 2  # header + 1 row
    assert parsed[1][1] == "PI-EP1"


def test_export_filtered_by_product_name(admin_client, db):
    """Export with product_name filter."""
    sid1 = _sale(db, "PI-EPR1", items=[
        {"product_name": "Sulfuric Acid", "quantity": 1, "unit_price": 10, "unit": "KG"},
        {"product_name": "Hydrochloric Acid", "quantity": 1, "unit_price": 20, "unit": "KG"},
    ])
    sid2 = _sale(db, "PI-EPR2", items=[
        {"product_name": "Sodium Hydroxide", "quantity": 1, "unit_price": 15, "unit": "KG"},
    ])
    r = _export_rows(admin_client, {"product_name": "sulfur"})
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    # Should return both rows for PI-EPR1 (both items match the sale)
    assert len(parsed) == 3  # header + 2 rows
    pi_numbers = {p[1] for p in parsed[1:]}
    assert pi_numbers == {"PI-EPR1"}


def test_export_all_filters_combined(admin_client, db):
    """Export with multiple filters at once."""
    _sale(db, "PI-EC1", client_name="Alpha Corp", pi_date="2026-01-15",
          items=[{"product_name": "Sulfuric Acid", "quantity": 1, "unit_price": 10}])
    _sale(db, "PI-EC2", client_name="Alpha Corp", pi_date="2026-02-15",
          items=[{"product_name": "Hydrochloric Acid", "quantity": 1, "unit_price": 20}])
    _sale(db, "PI-EC3", client_name="Beta Corp", pi_date="2026-01-15",
          items=[{"product_name": "Sulfuric Acid", "quantity": 1, "unit_price": 10}])
    
    # Filter: Alpha Corp + sulfur + Jan 2026
    r = _export_rows(admin_client, {
        "customer_name": "alpha",
        "product_name": "sulfur",
        "date_anchor": "pi_date",
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
    })
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    assert len(parsed) == 2  # header + 1 row (only PI-EC1 matches all)


def test_export_empty_result(admin_client, db):
    """Export with filters that match nothing -> 400 error."""
    _sale(db, "PI-EE1", client_name="Alpha Corp")
    r = _export_rows(admin_client, {"customer_name": "nonexistent"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "No data to export"


def test_export_get_method_with_query_params(admin_client, db):
    """Export via GET with query parameters works."""
    _sale(db, "PI-EG1", client_name="Alpha Corp", pi_date="2026-01-15")
    _sale(db, "PI-EG2", client_name="Beta Corp", pi_date="2026-02-15")
    r = _export_rows(admin_client, {
        "customer_name": "alpha",
    }, method="GET")
    assert r.status_code == 200
    body = r.get_json()
    content = body["content"]
    parsed = list(csv.reader(io.StringIO(content)))
    assert len(parsed) == 2  # header + 1 row
    assert parsed[1][0] == "Alpha Corp"


# ------------------------- quick search (q) + server total -------------------------
def _q_seed(db):
    a = _sale(db, "PI-QQ1", client_name="Acme Ltd",
              items=[{"product_name": "Widget Alpha", "quantity": 1, "unit_price": 10, "unit": "KG"}])
    b = _sale(db, "PI-77", client_name="QQ Traders",
              items=[{"product_name": "Bolt M6", "quantity": 2, "unit_price": 5, "unit": "EA"}])
    c = _sale(db, "PI-88", client_name="Globex",
              items=[{"product_name": "QQ Liquid", "quantity": 3, "unit_price": 7, "unit": "L"}])
    d = _sale(db, "PI-99", client_name="Other Co",
              items=[{"product_name": "Plain Salt", "quantity": 1, "unit_price": 3, "unit": "KG"}])
    return a, b, c, d


def test_quick_search_matches_pi_or_customer_or_product(admin_client, db):
    a, b, c, d = _q_seed(db)
    rows = _filtered_rows(admin_client, {"q": "QQ"})
    assert {r["sale_id"] for r in rows} == {a, b, c}
    rows = _filtered_rows(admin_client, {"q": "widget"})
    assert {r["sale_id"] for r in rows} == {a}
    rows = _filtered_rows(admin_client, {"q": "bolt"})
    assert {r["sale_id"] for r in rows} == {b}
    rows = _filtered_rows(admin_client, {"q": "PI-99"})
    assert {r["sale_id"] for r in rows} == {d}


def test_quick_search_combines_with_other_filters(admin_client, db):
    a, b, c, _d = _q_seed(db)
    rows = _filtered_rows(admin_client, {"q": "QQ", "stage": "pi_issued"})
    assert {r["sale_id"] for r in rows} == {a, b, c}
    rows = _filtered_rows(admin_client, {"q": "QQ", "stage": "completed"})
    assert rows == []


def test_filtered_total_header(admin_client, db):
    _q_seed(db)
    r = admin_client.get("/api/reports/live/filtered?page_size=2&q=QQ")
    assert r.status_code == 200
    assert len(r.get_json()) == 2
    assert r.headers.get("X-Total-Count") == "3"
    r = admin_client.get("/api/reports/live/filtered?page_size=2&page=2&q=QQ")
    assert len(r.get_json()) == 1
    assert r.headers.get("X-Total-Count") == "3"
    r = admin_client.get("/api/reports/live/filtered?q=nomatch")
    assert r.get_json() == []
    assert r.headers.get("X-Total-Count") == "0"


def test_summary_quick_search(admin_client, db):
    _q_seed(db)
    body = _summary(admin_client, {"group_by": "month", "q": "QQ"})
    assert sum(p["kpis"]["order_count"] for p in body["periods"]) == 3
    body = _summary(admin_client, {"group_by": "month"})
    assert sum(p["kpis"]["order_count"] for p in body["periods"]) == 4


def test_export_q_filter_and_row_count(admin_client, db):
    _q_seed(db)
    r = _export_rows(admin_client, {"q": "QQ"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["row_count"] == 3
    assert body["truncated"] is False
    parsed = list(csv.reader(io.StringIO(body["content"])))
    assert len(parsed) == 4  # header + 3 matching rows
    r = _export_rows(admin_client, {"q": "widget"}, method="GET")
    assert r.status_code == 200
    parsed = list(csv.reader(io.StringIO(r.get_json()["content"])))
    assert len(parsed) == 2  # header + 1 matching row
