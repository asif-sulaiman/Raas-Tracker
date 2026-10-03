"""Summary KPI integrity: sale-level money counted once, money rounding kept.

get_commercial_report_summary builds its `base` CTE from
`sales s LEFT JOIN sale_items si`, i.e. ONE ROW PER LINE ITEM. The per-sale
scalars (received_amount / sale_total) are therefore repeated on every row of
that sale, so a naive SUM(received_amount) / SUM(sale_total - received_amount)
multiplies the money by the item count. These tests pin the KPIs to per-sale
truth (gross_sales stays a per-ITEM sum) and to the file's money convention
(COALESCE(ROUND(SUM(...)::numeric, 2)::float8, 0), due clamped at zero).
"""
from raas_tracker.sales import get_commercial_report_summary


# ---------- helpers ----------

def _sale(db, pi_number, items, maturity_date=None, pi_date="2026-01-15"):
    """Insert a sale + line items directly (full control of totals/dates).

    The report is invoice-driven, so each line is also invoiced in full. That
    keeps invoiced total == PI total here, so these tests keep pinning what
    they are about (per-sale money counted once, no float drift, due clamp)
    rather than the PI-vs-invoice source.
    """
    sid = db.execute(
        """INSERT INTO sales (stage, pi_number, pi_date, client_name, maturity_date)
           VALUES ('pi_issued', %s, %s, 'KPI Co', %s) RETURNING id""",
        (pi_number, pi_date, maturity_date)).fetchone()[0]
    line_ids = []
    for it in items:
        line_ids.append(db.execute(
            "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (sid, it["product_name"], it["quantity"], it["unit_price"],
             it.get("unit", "KG"))).fetchone()[0])
    inv_id = db.execute(
        "INSERT INTO invoices (sale_id, invoice_number, status, amount) "
        "VALUES (%s, %s, 'planned', 0) RETURNING id",
        (sid, f"INV-{pi_number}")).fetchone()[0]
    total = 0.0
    for line_id, it in zip(line_ids, items):
        line_total = round(it["quantity"] * it["unit_price"], 2)
        total = round(total + line_total, 2)
        db.execute(
            """INSERT INTO invoice_items (invoice_id, sale_item_id, product_name,
                  unit, quantity, unit_price, line_total)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (inv_id, line_id, it["product_name"], it.get("unit", "KG"),
             it["quantity"], it["unit_price"], line_total))
    db.execute("UPDATE invoices SET amount = %s WHERE id = %s", (total, inv_id))
    db.commit()
    return sid


def _pay(db, sid, amount, payment_date="2026-02-01"):
    db.execute(
        "INSERT INTO sale_payments (sale_id, payment_date, payment_amount) "
        "VALUES (%s, %s, %s)", (sid, payment_date, amount))
    db.commit()


def _kpis(db, **filters):
    """Single-period (group_by=none) KPI dict."""
    periods = get_commercial_report_summary(db, {"group_by": "none", **filters})
    assert len(periods) == 1, periods
    return periods[0]["kpis"]


# ---------- 1. per-item count must not inflate sale-level money ----------

def test_three_items_one_payment_money_counted_once(db):
    """1 sale / 3 items (1000 each = 3000) / 1 payment of 1000."""
    sid = _sale(db, "PI-KPI-1", [
        {"product_name": "A", "quantity": 1, "unit_price": 1000},
        {"product_name": "B", "quantity": 1, "unit_price": 1000},
        {"product_name": "C", "quantity": 1, "unit_price": 1000},
    ], maturity_date="2020-01-01")
    _pay(db, sid, 1000)

    kpis = _kpis(db)
    # gross_sales is a per-ITEM sum; received/due/overdue are per-SALE.
    assert kpis["gross_sales"] == 3000
    assert kpis["received"] == 1000     # not 3 x 1000
    assert kpis["due"] == 2000         # not 3 x 2000
    assert kpis["overdue"] == 2000     # maturity in the past, not 3 x 2000
    assert kpis["order_count"] == 1


def test_two_multi_item_sales_money_counted_once(db):
    """Sale A: 2 items (600 + 400 = 1000), paid 400. Sale B: 3 items (100 each = 300), unpaid."""
    a = _sale(db, "PI-KPI-2A", [
        {"product_name": "A1", "quantity": 1, "unit_price": 600},
        {"product_name": "A2", "quantity": 1, "unit_price": 400},
    ], maturity_date="2020-01-01", pi_date="2026-01-10")
    _pay(db, a, 400)
    b = _sale(db, "PI-KPI-2B", [
        {"product_name": "B1", "quantity": 1, "unit_price": 100},
        {"product_name": "B2", "quantity": 1, "unit_price": 100},
        {"product_name": "B3", "quantity": 1, "unit_price": 100},
    ], maturity_date="2099-12-31", pi_date="2026-01-20")

    kpis = _kpis(db)
    assert kpis["order_count"] == 2
    assert kpis["gross_sales"] == 1300          # 1000 + 300 (per item)
    assert kpis["received"] == 400              # once, not 2 x 400
    assert kpis["due"] == 900                   # (1000-400) + 300
    assert kpis["overdue"] == 600               # only sale A is past maturity

    # Per-sale drill-down: each period/sale reports its own money.
    periods = get_commercial_report_summary(db, {"group_by": "month", "date_anchor": "pi_date"})
    jan_a = next(p for p in periods if p["period_start"] == "2026-01-01")
    assert jan_a["kpis"]["received"] == 400
    assert jan_a["kpis"]["due"] == 900
    assert b  # both sales share the January period


def test_multi_item_sale_detail_rows_keep_sale_level_money(db):
    """Detail rows repeat the sale's money identically (per-item rows, sale money)."""
    sid = _sale(db, "PI-KPI-2B-DET", [
        {"product_name": "A", "quantity": 1, "unit_price": 700},
        {"product_name": "B", "quantity": 1, "unit_price": 300},
    ], maturity_date="2020-01-01")
    _pay(db, sid, 250)

    periods = get_commercial_report_summary(db, {"group_by": "month", "date_anchor": "pi_date"})
    items = periods[0]["items"]
    assert len(items) == 2
    for row in items:
        assert row["received_amount"] == 250
        assert row["due_amount"] == 750
        assert row["payment_status"] == "Overdue"


# ---------- 2. money rounding + due clamp ----------

def test_overpaid_sale_reports_zero_due(db):
    """Payments above the sale total must report due 0 (never negative)."""
    sid = _sale(db, "PI-KPI-3", [
        {"product_name": "A", "quantity": 1, "unit_price": 10},
        {"product_name": "B", "quantity": 1, "unit_price": 20},
    ], maturity_date="2020-01-01")
    _pay(db, sid, 500)  # total 30 -> overpaid by 470

    kpis = _kpis(db)
    assert kpis["gross_sales"] == 30
    assert kpis["received"] == 500
    assert kpis["due"] == 0
    assert kpis["overdue"] == 0


def test_fractional_amounts_summed_without_float_drift(db):
    """3 x 0.1 must report 0.3, not 0.30000000000000004."""
    sid = _sale(db, "PI-KPI-4", [
        {"product_name": "A", "quantity": 1, "unit_price": 0.1},
        {"product_name": "B", "quantity": 1, "unit_price": 0.1},
        {"product_name": "C", "quantity": 1, "unit_price": 0.1},
    ])
    _pay(db, sid, 0.1)

    kpis = _kpis(db)
    assert kpis["gross_sales"] == 0.3
    assert kpis["received"] == 0.1
    assert kpis["due"] == 0.2


# ---------- 3. filters still apply over the collapsed per-sale rows ----------

def test_summary_filters_apply_over_per_sale_rows(db):
    """product_name / payment_status / date filters run inside the per-sale CTE."""
    a = _sale(db, "PI-KPI-F1", [
        {"product_name": "NITRIC", "quantity": 1, "unit_price": 1000},
        {"product_name": "SULFUR", "quantity": 1, "unit_price": 1000},
    ], maturity_date="2099-12-31", pi_date="2026-01-10")
    _pay(db, a, 500)
    _sale(db, "PI-KPI-F2", [{"product_name": "SODA", "quantity": 1, "unit_price": 100}],
          maturity_date="2020-01-01", pi_date="2026-02-10")

    # product_name: EXISTS picks the sale, the whole sale's gross is reported.
    kpis = _kpis(db, product_name="NITRIC")
    assert kpis["order_count"] == 1
    assert kpis["gross_sales"] == 2000
    assert kpis["received"] == 500
    assert kpis["due"] == 1500

    # payment_status: only the overdue (unpaid, past-maturity) sale.
    kpis = _kpis(db, payment_status="Overdue")
    assert kpis["order_count"] == 1
    assert kpis["gross_sales"] == 100
    assert kpis["received"] == 0
    assert kpis["due"] == 100
    assert kpis["overdue"] == 100

    # date range on the pi_date anchor.
    kpis = _kpis(db, date_anchor="pi_date", date_from="2026-02-01",
                 date_to="2026-02-28")
    assert kpis["order_count"] == 1
    assert kpis["gross_sales"] == 100

    # stage filter.
    kpis = _kpis(db, stage="pi_issued")
    assert kpis["order_count"] == 2
    assert kpis["gross_sales"] == 2100

