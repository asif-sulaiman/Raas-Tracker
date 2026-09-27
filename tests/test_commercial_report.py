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


# ------------------------- export -------------------------
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
