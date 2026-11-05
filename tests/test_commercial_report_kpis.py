"""Summary KPI integrity: row money summed once, rounding kept.

``get_commercial_report_summary`` builds on the report's row grain: one row per
invoice line plus one provisional remainder row per PI line. Payments are
allocated to rows (invoice payments to that invoice's lines, legacy sale-level
payments across the sale's rows), so a row's ``received_amount`` is its own
money and the KPIs are plain sums of the displayed rows.

These tests pin the file's money convention
(COALESCE(ROUND(SUM(...)::numeric, 2)::float8, 0), due clamped at zero) and
that received/due/overdue never multiply by the row count.
"""
from raas_tracker.sales import (
    get_commercial_report,
    get_commercial_report_summary,
    set_sale_item_wont_ship,
)


# ---------- helpers ----------

def _sale(db, pi_number, items, maturity_date=None, pi_date="2026-01-15",
          invoice_qty=None):
    """Insert a sale + line items directly (full control of totals/dates).

    ``invoice_qty`` selects what the report sees as final:
      * None (default) — every line is invoiced in full, so invoiced total
        equals PI total and these tests keep pinning per-sale money counting;
      * ``{}`` — no invoice at all, so every line is provisional PI data;
      * ``{index: qty}`` — only those lines are invoiced, at those quantities.
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
    if invoice_qty is None:
        invoice_qty = {idx: it["quantity"] for idx, it in enumerate(items)}
    if invoice_qty:
        inv_id = db.execute(
            "INSERT INTO invoices (sale_id, invoice_number, status, amount) "
            "VALUES (%s, %s, 'planned', 0) RETURNING id",
            (sid, f"INV-{pi_number}")).fetchone()[0]
        total = 0.0
        for idx, qty in invoice_qty.items():
            it = items[idx]
            line_total = round(qty * it["unit_price"], 2)
            total = round(total + line_total, 2)
            db.execute(
                """INSERT INTO invoice_items (invoice_id, sale_item_id, product_name,
                      unit, quantity, unit_price, line_total)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (inv_id, line_ids[idx], it["product_name"], it.get("unit", "KG"),
                 qty, it["unit_price"], line_total))
        db.execute("UPDATE invoices SET amount = %s WHERE id = %s", (total, inv_id))
    db.commit()
    return sid


def _pay(db, sid, amount, payment_date="2026-02-01"):
    db.execute(
        "INSERT INTO sale_payments (sale_id, payment_date, payment_amount) "
        "VALUES (%s, %s, %s)", (sid, payment_date, amount))
    db.commit()


def _line_id(db, sid, product):
    return db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s AND product_name = %s",
        (sid, product)).fetchone()[0]


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


def test_multi_item_sale_detail_rows_allocate_the_payment(db):
    """Detail rows carry the sale's payment allocated in line order."""
    sid = _sale(db, "PI-KPI-2B-DET", [
        {"product_name": "A", "quantity": 1, "unit_price": 700},
        {"product_name": "B", "quantity": 1, "unit_price": 300},
    ], maturity_date="2020-01-01")
    _pay(db, sid, 250)

    periods = get_commercial_report_summary(db, {"group_by": "month", "date_anchor": "pi_date"})
    items = periods[0]["items"]
    assert len(items) == 2
    by_product = {r["product_name"]: r for r in items}
    assert by_product["A"]["received_amount"] == 250
    assert by_product["A"]["due_amount"] == 450
    assert by_product["B"]["received_amount"] == 0
    assert by_product["B"]["due_amount"] == 300
    assert all(r["payment_status"] == "Overdue" for r in items)
    assert sum(r["received_amount"] for r in items) == 250
    assert sum(r["due_amount"] for r in items) == 750


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


# ---------- 4. provisional PI lines feed the same KPIs ----------

def test_provisional_pi_lines_count_in_kpis(db):
    """A fresh PI with no invoices is revenue the moment it has lines."""
    sid = _sale(db, "PI-KPI-PROV", [
        {"product_name": "A", "quantity": 2, "unit_price": 100},
        {"product_name": "B", "quantity": 1, "unit_price": 50},
    ], maturity_date="2020-01-01", invoice_qty={})
    _pay(db, sid, 100)

    kpis = _kpis(db)
    assert kpis["gross_sales"] == 250
    assert kpis["received"] == 100
    assert kpis["due"] == 150
    # No invoice means no maturity date, so nothing here can be overdue.
    assert kpis["overdue"] == 0
    assert kpis["order_count"] == 1


def test_mixed_sale_kpis_and_detail_use_displayed_line_totals(db):
    """A invoiced 8@10 + 2@10 remainder, B provisional 5@20 -> 200."""
    _sale(db, "PI-KPI-MIX", [
        {"product_name": "A", "quantity": 10, "unit_price": 10},
        {"product_name": "B", "quantity": 5, "unit_price": 20},
    ], pi_date="2026-01-10", invoice_qty={0: 8})

    kpis = _kpis(db)
    assert kpis["gross_sales"] == 200
    assert kpis["due"] == 200

    flat, _total = get_commercial_report(db, {
        "date_anchor": "pi_date", "date_from": "2026-01-01",
        "date_to": "2026-01-31",
    })
    periods = get_commercial_report_summary(
        db, {"group_by": "month", "date_anchor": "pi_date"})
    jan = next(p for p in periods if p["period_start"] == "2026-01-01")

    assert len(jan["items"]) == len(flat) == 3

    def shape(rows):
        return {
            (r["product_name"], r["is_provisional"]): (
                r["quantity"], r["total_price"], r["due_amount"])
            for r in rows
        }
    assert shape(jan["items"]) == shape(flat)
    assert shape(flat) == {
        ("A", False): (8, 80, 80),
        ("A", True): (2, 20, 20),
        ("B", True): (5, 100, 100),
    }


# ---------- 5. KPI rows are sums of the displayed rows ----------

def test_report_kpi_received_equals_sum_of_row_payments(db):
    """Received is the sum of the rows' allocated payments, once."""
    sid = _sale(db, "PI-KPI-SUM-RECV", [
        {"product_name": "A", "quantity": 10, "unit_price": 10},
        {"product_name": "B", "quantity": 10, "unit_price": 10},
    ], invoice_qty={0: 6})
    _pay(db, sid, 120)

    rows, _total = get_commercial_report(db, {})
    kpis = _kpis(db)
    assert sum(r["received_amount"] for r in rows) == 120
    assert kpis["received"] == 120


def test_report_kpi_due_equals_row_totals_minus_received(db):
    """Due is each row's total minus its received, clamped and summed."""
    sid = _sale(db, "PI-KPI-SUM-DUE", [
        {"product_name": "A", "quantity": 10, "unit_price": 10},
        {"product_name": "B", "quantity": 10, "unit_price": 10},
    ], invoice_qty={0: 6})
    _pay(db, sid, 120)

    rows, _total = get_commercial_report(db, {})
    kpis = _kpis(db)
    expected = sum(max(r["total_price"] - r["received_amount"], 0) for r in rows)
    assert kpis["due"] == expected == 80
    assert kpis["gross_sales"] == sum(r["total_price"] for r in rows) == 200


# ---------- 6. a won't-ship line leaves the provisional figures ----------

def _mark_product(db, product, flag=1):
    """Mark the PI line behind a provisional row, acting on the row's own id.

    Reading the target out of the report is deliberate: it is the only id the
    frontend is handed, so if ``sale_item_id`` ever came back NULL the mark would
    be unreachable from the screen that offers it.
    """
    rows, _total = get_commercial_report(db, {})
    row = next(r for r in rows
               if r["product_name"] == product and r["is_provisional"])
    assert row["sale_item_id"] is not None, "provisional rows carry no line id"
    return set_sale_item_wont_ship(db, row["sale_id"], row["sale_item_id"], flag)


def test_wont_ship_drops_the_remainder_from_rows_and_kpis(db):
    """The reported case: 450 ordered, 400 billed at 1.44, 50 KG left unshipped.

    The remainder holds Gross Sales at 648 and Due at 72 for a balance that will
    never ship. Marking the line takes both back down to what was billed, and
    leaves Received where it was.
    """
    sid = _sale(db, "PI-WS-1", [
        {"product_name": "A", "quantity": 450, "unit_price": 1.44},
    ], invoice_qty={0: 400})
    _pay(db, sid, 576)

    rows, total = get_commercial_report(db, {})
    assert total == len(rows) == 2
    kpis = _kpis(db)
    assert (kpis["gross_sales"], kpis["received"], kpis["due"]) == (648, 576, 72)

    assert _mark_product(db, "A") is True

    rows, total = get_commercial_report(db, {})
    assert total == len(rows) == 1, "the provisional remainder is still shown"
    assert rows[0]["is_provisional"] is False
    kpis = _kpis(db)
    assert (kpis["gross_sales"], kpis["received"], kpis["due"]) == (576, 576, 0)


def test_wont_ship_keeps_the_flat_report_and_the_kpis_agreeing(db):
    """Rows, drill-down items and KPI sums are one read of one CTE.

    They are three shapes of the same query, so an exclusion that reached only
    one of them would let the KPI cards, the table and Export CSV disagree —
    which is the invariant the rest of this file exists to hold.
    """
    _sale(db, "PI-WS-2", [
        {"product_name": "A", "quantity": 10, "unit_price": 10},
        {"product_name": "B", "quantity": 5, "unit_price": 10},
    ], invoice_qty={0: 10})

    assert _mark_product(db, "B") is True

    flat, total = get_commercial_report(db, {})
    jan = next(p for p in get_commercial_report_summary(
        db, {"group_by": "month", "date_anchor": "pi_date"})
        if p["period_start"] == "2026-01-01")
    assert total == len(flat) == len(jan["items"]) == 1
    assert [r["product_name"] for r in flat] == ["A"]
    assert [r["product_name"] for r in jan["items"]] == ["A"]
    assert _kpis(db)["gross_sales"] == sum(r["total_price"] for r in flat) == 100


def test_wont_ship_does_not_lose_a_payment_anchored_to_the_remainder(db):
    """Suppressing the row that held a legacy payment moves the payment down.

    A sale-level payment is allocated across the sale's rows in order, and the
    last row absorbs the rest. Cutting the remainder inside the CTE — not in an
    outer WHERE — recomputes that allocation, so Received survives on the row
    that remains instead of disappearing with the row that did not.
    """
    sid = _sale(db, "PI-WS-3", [
        {"product_name": "A", "quantity": 10, "unit_price": 10},
        {"product_name": "B", "quantity": 5, "unit_price": 10},
    ], invoice_qty={0: 10})
    _pay(db, sid, 130)

    before = {(r["product_name"], r["is_provisional"]): r["received_amount"]
              for r in get_commercial_report(db, {})[0]}
    assert before == {("A", False): 100, ("B", True): 30}

    assert _mark_product(db, "B") is True

    rows, _total = get_commercial_report(db, {})
    assert [(r["product_name"], r["received_amount"], r["due_amount"])
            for r in rows] == [("A", 130, 0)]
    kpis = _kpis(db)
    assert (kpis["gross_sales"], kpis["received"], kpis["due"]) == (100, 130, 0)


def test_un_marking_a_line_brings_the_remainder_back(db):
    """The mark is a durable view on the line, not a delete — reversible."""
    sid = _sale(db, "PI-WS-4", [
        {"product_name": "B", "quantity": 5, "unit_price": 10},
    ], invoice_qty={})
    item_id = _line_id(db, sid, "B")

    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    assert get_commercial_report(db, {})[1] == 0

    assert set_sale_item_wont_ship(db, sid, item_id, 0) is True
    rows, total = get_commercial_report(db, {})
    assert total == len(rows) == 1
    assert rows[0]["is_provisional"] is True
    assert _kpis(db)["gross_sales"] == 50


def test_marking_a_fully_invoiced_line_changes_no_figure(db):
    """No remainder exists, so the mark is accepted and simply invisible.

    Deliberately not an error: the line is the thing being marked, and refusing
    would depend on how much has been invoiced at the moment of the click.
    """
    sid = _sale(db, "PI-WS-5", [
        {"product_name": "A", "quantity": 10, "unit_price": 10},
    ])
    item_id = _line_id(db, sid, "A")
    before = _kpis(db)

    assert set_sale_item_wont_ship(db, sid, item_id, 1) is True
    assert _kpis(db) == before
    assert len(get_commercial_report(db, {})[0]) == 1


def test_a_sale_whose_only_rows_are_suppressed_leaves_the_report(db):
    """Known corner, pinned rather than guarded.

    When every row of a sale is a suppressed remainder, the sale drops out of
    the report and its legacy payment drops out of Received with it. That is the
    same outcome as deleting an uninvoiced line today: ``update_sale_full``
    allows it, and the payment then has no row to allocate to. Not a new hole.
    """
    x = _sale(db, "PI-WS-X", [
        {"product_name": "X", "quantity": 5, "unit_price": 10},
    ], invoice_qty={})
    _pay(db, x, 20)
    _sale(db, "PI-WS-Y", [
        {"product_name": "Y", "quantity": 1, "unit_price": 100},
    ])

    kpis = _kpis(db)
    assert (kpis["gross_sales"], kpis["received"]) == (150, 20)

    assert _mark_product(db, "X") is True

    kpis = _kpis(db)
    rows, _total = get_commercial_report(db, {})
    assert [r["product_name"] for r in rows] == ["Y"]
    assert (kpis["gross_sales"], kpis["received"]) == (100, 0)

