"""What ``total_pipeline_value`` is allowed to contain.

The Sales board header and Home's pipeline card both read these two totals, and
until now they summed every stage in ``SALE_STAGE_ORDER`` — including
``completed``. On the user's live database all three sales had been completed,
so the card labelled ``$50,174 / 3 sales`` as the pipeline while the four
in-progress columns beside it each read ``$0.00``, and Home printed the same
money twice: once under "sales in flow" and once under "3 completed · $50,174
booked".

A completed sale is settled business, not work in flight. The pipeline total is
what is still moving through PI → LC → shipment → payment due, so ``completed``
is excluded from it and keeps its own per-stage tile instead. The exclusion is
"not completed", not a whitelist of the four known stages: a stage added later
must count as in flight rather than silently vanish from the total.
"""
from raas_tracker.sales import add_sale, get_sales_summary


def _sale(db, pi, value, stage="pi_issued", quantity=10):
    """One single-line PI worth exactly ``value`` (10 × unit_price)."""
    sid = add_sale(db, {"pi_number": pi, "client_name": "Summary Co"},
                   [{"product_name": "A", "quantity": quantity,
                     "unit_price": value / quantity, "unit": "KG"}])
    if stage != "pi_issued":
        db.execute("UPDATE sales SET stage = %s WHERE id = %s", (stage, sid))
        db.commit()
    return sid


def _summary(admin_client):
    return admin_client.get("/api/sales/summary").get_json()


def test_completed_sales_do_not_count_in_the_pipeline_total(admin_client, db):
    _sale(db, "PI-OPEN", 1000)
    _sale(db, "PI-DONE", 500, stage="completed")

    s = _summary(admin_client)
    assert (s["total_pipeline_value"], s["total_sales"]) == (1000, 1)


def test_every_open_stage_counts_in_the_pipeline_total(admin_client, db):
    for i, stage in enumerate(["pi_issued", "lc_received",
                               "shipment_ongoing", "payment_due"], start=1):
        _sale(db, f"PI-{stage}", i * 100, stage=stage)

    s = _summary(admin_client)
    assert (s["total_pipeline_value"], s["total_sales"]) == (1000, 4)


def test_a_board_of_only_completed_sales_reads_an_empty_pipeline(admin_client, db):
    """The reported symptom: three completed sales, nothing in flight."""
    for pi, value in [("12322332", 648), ("12224322", 4600), ("26016779", 45432)]:
        _sale(db, pi, value, stage="completed")

    s = _summary(admin_client)
    assert (s["total_pipeline_value"], s["total_sales"]) == (0, 0)


def test_the_completed_tile_still_reports_its_own_value(admin_client, db):
    """Excluding completed from the total must not hide it from the board."""
    _sale(db, "PI-DONE", 500, stage="completed")

    s = _summary(admin_client)
    assert s["completed"] == {"count": 1, "value": 500}
    assert s["total_pipeline_value"] == 0


def test_an_unknown_stage_still_counts_as_in_flight(admin_client, db):
    """Why the rule is "not completed" and not a list of four known stages."""
    _sale(db, "PI-FUTURE", 700, stage="quality_review")

    s = _summary(admin_client)
    assert (s["total_pipeline_value"], s["total_sales"]) == (700, 1)


def test_the_dashboard_carries_the_same_pipeline_total(admin_client, db):
    _sale(db, "PI-OPEN", 1000)
    _sale(db, "PI-DONE", 500, stage="completed")

    dash = admin_client.get("/api/dashboard").get_json()["sales_summary"]
    assert dash["total_pipeline_value"] == _summary(admin_client)["total_pipeline_value"]


def test_the_service_and_the_route_agree(db, admin_client):
    _sale(db, "PI-OPEN", 1000)
    _sale(db, "PI-DONE", 500, stage="completed")
    assert get_sales_summary(db)["total_pipeline_value"] == \
        _summary(admin_client)["total_pipeline_value"]
