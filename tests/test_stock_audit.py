"""P1-2 + P1-6: unit-conversion factors and stock-adjustment reasons.

`POST /api/unit-conversions` had no audit row at all while being reachable by
any session *or* API key, so a factor that governs every stock/quantity
conversion could change with no record of who changed it or from what.

P1-6: `update_stock` passed the operator's free-text reason as `ip_address`,
which meant the P0 client-IP default could never fire for manual adjustments —
the audit row most likely to be investigated had no IP at all.
"""
import pytest


def _row(db, action):
    return db.execute(
        "SELECT entity_id, user_id, old_value, new_value, ip_address "
        "FROM audit_logs WHERE action = %s ORDER BY id DESC LIMIT 1",
        (action,)).fetchone()


def _create_key(db, name):
    from chem_stock import create_api_key
    return create_api_key(db, name,
                          created_by=db.execute(
                              "SELECT id FROM users WHERE username = 'admin'"
                          ).fetchone()[0])


def _mock_results():
    return {
        "stats": {"total": 0, "matched": 0, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": 0, "not_in_upload": 0, "match_percentage": 0.0},
        "matches": [], "last_month_mismatches": [], "this_month_mismatches": [],
        "both_mismatches": [], "not_in_db": [], "not_in_upload": [],
    }


# ==================== P1-2: unit conversion factors ====================

def test_unit_conversion_is_audited(admin_client, db):
    r = admin_client.post("/api/unit-conversions",
                          json={"from_unit": "KG", "to_unit": "LB", "factor": 2.20462})
    assert r.status_code in (200, 201), r.get_json()
    row = _row(db, "UNIT_CONVERSION_UPSERT")
    assert row is not None, "changing a conversion factor left no audit row"
    entity_id, user_id, old_value, new_value, _ip = row
    assert user_id == "admin"
    assert "KG" in new_value and "LB" in new_value and "2.20462" in new_value
    assert old_value in (None, "", "none")


def test_unit_conversion_upsert_records_the_previous_factor(admin_client, db):
    """The whole point of an audit row here is the before/after of the factor."""
    admin_client.post("/api/unit-conversions",
                      json={"from_unit": "KG", "to_unit": "LB", "factor": 2.0})
    admin_client.post("/api/unit-conversions",
                      json={"from_unit": "KG", "to_unit": "LB", "factor": 3.0})
    _entity_id, _user_id, old_value, new_value, _ip = _row(db, "UNIT_CONVERSION_UPSERT")
    assert "2.0" in (old_value or ""), "the overwritten factor was not recorded"
    assert "3.0" in new_value


def test_unit_conversion_audit_records_the_api_key_actor(client, db):
    """An API key can reach this route, so it must be distinguishable.

    This is the test that retires the earlier claim that the
    ``api-key:<name>`` label was unreachable.
    """
    raw = _create_key(db, "FactorBot")
    r = client.post("/api/unit-conversions",
                    json={"from_unit": "G", "to_unit": "KG", "factor": 0.001},
                    headers={"X-API-Key": raw})
    assert r.status_code in (200, 201), r.get_json()
    assert _row(db, "UNIT_CONVERSION_UPSERT")[1] == "api-key:FactorBot"


def test_unit_conversion_rejects_a_non_positive_factor(admin_client, db):
    assert admin_client.post("/api/unit-conversions",
                             json={"from_unit": "KG", "to_unit": "LB",
                                   "factor": 0}).status_code == 400
    assert _row(db, "UNIT_CONVERSION_UPSERT") is None


# ==================== P1-6: stock adjustment reason + real IP ====================

def test_stock_adjustment_records_reason_and_the_client_ip(admin_client, db):
    admin_client.post("/api/chemicals", json={"name": "AudStock", "qty": 10})
    r = admin_client.post("/api/chemicals/update", json={
        "name": "AudStock", "delta": 5, "reason": "Supplier delivery"})
    assert r.status_code == 200, r.get_json()

    _entity_id, _user_id, old_value, new_value, ip = _row(db, "ADJUST_STOCK")
    assert old_value == "10.0", "quantities must stay exact"
    assert "15.0" in new_value
    assert "Supplier delivery" in new_value, "the operator's reason was dropped"
    assert ip == "127.0.0.1", "the real client IP never reached the audit row"


# ==================== the stock history feed must keep working ====================

def test_history_shows_delta_and_purpose_after_p1_6(admin_client, db):
    """P1-6 changed the audited value format; the history feed parses it.

    `get_stock_movements` derives delta from old/new and reads the operator's
    reason as the movement's `purpose`, so moving the reason into new_value
    would otherwise blank out both.
    """
    admin_client.post("/api/chemicals", json={"name": "HistFeed", "qty": 10})
    assert admin_client.post("/api/chemicals/update", json={
        "name": "HistFeed", "delta": -3, "reason": "Disposal / expiry"}).status_code == 200

    r = admin_client.get("/api/chemicals/history")
    assert r.status_code == 200
    adj = [m for m in r.get_json() if m["action"] == "ADJUST_STOCK"]
    assert len(adj) == 1
    assert adj[0]["delta"] == -3
    assert adj[0]["purpose"] == "Disposal / expiry"
    assert adj[0]["source"] == "manual"


def test_history_still_renders_rows_written_before_p1_6(db):
    """Historical rows keep their reason in ip_address — do not blank them out."""
    chem_id = db.execute(
        "INSERT INTO chemicals (name, current_qty, unit) VALUES ('LegacyFeed', 7, 'KG') "
        "RETURNING id").fetchone()[0]
    db.execute(
        "INSERT INTO audit_logs (action, entity_type, entity_id, user_id, "
        "old_value, new_value, ip_address) "
        "VALUES ('ADJUST_STOCK', 'chemical', %s, 'admin', '10.0', '7.0', %s)",
        (chem_id, "Disposal / expiry"))
    db.commit()

    from raas_tracker.stock import get_stock_movements
    rows = get_stock_movements(db, chemical_id=chem_id)
    adj = [m for m in rows if m["action"] == "ADJUST_STOCK"]
    assert len(adj) == 1
    assert adj[0]["delta"] == -3
    assert adj[0]["purpose"] == "Disposal / expiry"


def test_history_keeps_delta_when_the_reason_has_newlines(admin_client, db):
    """Regression: a multi-line reason silently blanked delta in the feed.

    The quantity pattern is anchored and `.` does not match a newline, so
    '7.0 (Line1\\nLine2)' used to parse as (None, None) — and a null delta
    contributes 0 to the net-change chart, making the adjustment vanish.
    """
    admin_client.post("/api/chemicals", json={"name": "MultiReason", "qty": 10})
    r = admin_client.post("/api/chemicals/update", json={
        "name": "MultiReason", "delta": -3, "reason": "Line1\nLine2"})
    assert r.status_code == 200

    adj = [m for m in admin_client.get("/api/chemicals/history").get_json()
           if m["action"] == "ADJUST_STOCK"]
    assert len(adj) == 1
    assert adj[0]["delta"] == -3, "the adjustment silently vanished from the chart"
    assert "Line1" in adj[0]["purpose"] and "Line2" in adj[0]["purpose"]


@pytest.mark.parametrize("value,expected_qty,expected_reason", [
    ("15.0", 15.0, None),
    ("-3.0", -3.0, None),
    ("0", 0.0, None),
    ("15", 15.0, None),
    ("15.0 (Supplier delivery)", 15.0, "Supplier delivery"),
    ("15.0 ()", 15.0, None),
    ("15.0 (   )", 15.0, None),
    ("15.0 (draft (count))", 15.0, "draft (count)"),
    ("15.0 (a\r\nb)", 15.0, "a\r\nb"),
    ("1e3", 1000.0, None),          # scientific notation falls back to float()
    ("not a number", None, None),
    (None, None, None),
])
def test_split_qty_reason(value, expected_qty, expected_reason):
    from raas_tracker.stock import _split_qty_reason
    qty, reason = _split_qty_reason(value)
    assert qty == expected_qty
    assert reason == expected_reason