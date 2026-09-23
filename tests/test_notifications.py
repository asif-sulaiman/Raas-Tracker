"""Notifications: dedupe, role scoping, read state, event hooks, API endpoints."""
from datetime import date, timedelta

from chem_stock import (
    compare_stock_upload, save_upload, update_stock, set_reorder_level,
    add_sale, move_sale_to_stage,
)
from raas_tracker.notifications import (
    notify, list_notifications_for, unread_count, mark_read, mark_read_all_for,
    notify_reorder_status,
)


def _seed(db):
    db.execute(
        "INSERT INTO chemicals (name, current_qty, balance_last_month, unit) VALUES "
        "('Alpha', 100, 90, 'KG'), ('Beta', 50, 50, 'KG'), ('Gamma', 0, 10, 'KG')"
    )
    db.commit()


def _types(db):
    return [r[0] for r in db.execute("SELECT type FROM notifications").fetchall()]


# ---------- unit: notify / dedupe / scope / read ----------

def test_notify_inserts_and_lists(db):
    nid = notify(db, type="t", title="Hello", body="World", severity="warning")
    assert nid is not None
    items = list_notifications_for(db, user_id=1, user_role="admin")
    assert items[0]["title"] == "Hello"
    assert items[0]["severity"] == "warning"
    assert items[0]["is_read"] is False
    assert unread_count(db, user_id=1, user_role="admin") == 1


def test_dedupe_prevents_duplicates_and_clear_allows_reinsert(db):
    first = notify(db, type="t", title="Once", dedupe_key="k1")
    second = notify(db, type="t", title="Once again", dedupe_key="k1")
    assert first is not None and second is None
    assert db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
    db.execute("DELETE FROM notifications WHERE dedupe_key = 'k1'")
    db.commit()
    third = notify(db, type="t", title="Once more", dedupe_key="k1")
    assert third is not None


def test_admin_scope_invisible_to_regular_users(db):
    notify(db, type="sec", title="Admin only", role_scope="admin")
    notify(db, type="gen", title="Everyone")
    user_view = list_notifications_for(db, user_id=2, user_role="user")
    admin_view = list_notifications_for(db, user_id=1, user_role="admin")
    assert [i["title"] for i in user_view] == ["Everyone"]
    assert {i["title"] for i in admin_view} == {"Admin only", "Everyone"}
    assert unread_count(db, user_id=2, user_role="user") == 1
    assert unread_count(db, user_id=1, user_role="admin") == 2


def test_mark_read_specific_and_all(db):
    a = notify(db, type="t", title="A")
    b = notify(db, type="t", title="B")
    notify(db, type="t", title="C")
    assert mark_read(db, user_id=1, notification_ids=[a]) == 1
    assert mark_read(db, user_id=1, notification_ids=[a]) == 0  # already read
    assert unread_count(db, user_id=1, user_role="admin") == 2
    assert mark_read_all_for(db, user_id=1, user_role="admin") == 2  # B and C; A already read
    assert unread_count(db, user_id=1, user_role="admin") == 0
    flags = {i["title"]: i["is_read"] for i in list_notifications_for(db, 1, "admin")}
    assert flags == {"A": True, "B": True, "C": True}
    assert b is not None


# ---------- unit: reorder + sale hooks ----------

def test_reorder_zero_critical_and_recovery_clears(db):
    _seed(db)
    notify_reorder_status(db, 1, "Alpha", 0, 0)
    row = db.execute(
        "SELECT severity FROM notifications WHERE type = 'stock_out'"
    ).fetchone()
    assert row and row[0] == "critical"
    notify_reorder_status(db, 1, "Alpha", 0, 0)  # still breached: deduped
    assert _types(db).count("stock_out") == 1
    notify_reorder_status(db, 1, "Alpha", 50, 0)  # recovered
    assert "stock_out" not in _types(db)


def test_reorder_low_needs_positive_level(db):
    _seed(db)
    notify_reorder_status(db, 1, "Alpha", 5, 0)   # level off: no low-stock alert
    assert "stock_low" not in _types(db)
    notify_reorder_status(db, 1, "Alpha", 5, 10)  # level on and breached
    assert "stock_low" in _types(db)
    notify_reorder_status(db, 1, "Alpha", 5, 10)  # deduped
    assert _types(db).count("stock_low") == 1
    notify_reorder_status(db, 1, "Alpha", 50, 10)  # recovered
    assert "stock_low" not in _types(db)


def test_sale_stage_due_completed_and_revert_clears(db):
    sid = add_sale(db, {"client_name": "ACME", "pi_number": "PI-1"},
                   [{"product_name": "X", "quantity": 1, "unit_price": 10}])
    move_sale_to_stage(db, sid, "lc_received")
    assert "sale_payment_due" not in _types(db)
    move_sale_to_stage(db, sid, "shipment_ongoing")
    move_sale_to_stage(db, sid, "payment_due")
    assert "sale_payment_due" in _types(db)
    move_sale_to_stage(db, sid, "payment_due")  # dedupe on same stage re-entry
    assert _types(db).count("sale_payment_due") == 1
    move_sale_to_stage(db, sid, "completed")
    assert "sale_completed" in _types(db)
    assert "sale_payment_due" not in _types(db)  # cleared on completion

    sid2 = add_sale(db, {"client_name": "BetaCo", "pi_number": "PI-2"},
                    [{"product_name": "Y", "quantity": 2, "unit_price": 5}])
    for stage in ("lc_received", "shipment_ongoing", "payment_due"):
        move_sale_to_stage(db, sid2, stage)
    assert "sale_payment_due" in _types(db)
    move_sale_to_stage(db, sid2, "shipment_ongoing")  # revert before payment
    assert _types(db).count("sale_payment_due") == 0


# ---------- hooks: upload / stock endpoints ----------

def test_save_upload_mismatch_notifies_and_writes_audit(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Alpha", "balance_last_month": 90,
                                     "balance_this_month": 111}])
    save_upload(db, "AUG.pdf", out)
    assert "upload_mismatch" in _types(db)
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'UPLOAD_CREATE'"
    ).fetchone()[0] == 1


def test_save_upload_clean_creates_no_mismatch_alert(db):
    _seed(db)
    out = compare_stock_upload(db, [{"name": "Alpha", "balance_last_month": 90,
                                     "balance_this_month": 100},
                                    {"name": "Beta", "balance_last_month": 50,
                                     "balance_this_month": 50},
                                    {"name": "Gamma", "balance_last_month": 10,
                                     "balance_this_month": 0}])
    save_upload(db, "CLEAN.pdf", out)
    assert "upload_mismatch" not in _types(db)


def test_upload_outcome_expiry_findings(db):
    from raas_tracker.uploads import _notify_upload_outcome
    past = "2020-01-01"
    soon = (date.today() + timedelta(days=10)).isoformat()
    results = {
        "stats": {"total": 3, "matched": 3, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": 0, "not_in_upload": 0, "match_percentage": 100.0},
        "matches": [{"name": "A", "expiry_date": past},
                    {"name": "B", "expiry_date": soon},
                    {"name": "C", "expiry_date": ""}],
    }
    _notify_upload_outcome(db, 99, "f.pdf", results)
    types = _types(db)
    assert "stock_expired" in types and "stock_expiring" in types
    assert "upload_mismatch" not in types
    row = db.execute(
        "SELECT severity FROM notifications WHERE type = 'stock_expired'"
    ).fetchone()
    assert row[0] == "critical"


def test_update_stock_breach_and_adjust_audit(db):
    _seed(db)
    update_stock(db, "Alpha", -100)
    assert "stock_out" in _types(db)
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'ADJUST_STOCK'"
    ).fetchone()[0] == 1
    update_stock(db, "Alpha", 150)
    assert "stock_out" not in _types(db)


def test_set_reorder_level_fires_low_stock(db):
    _seed(db)
    set_reorder_level(db, "Alpha", 120)  # 100 <= 120 -> breached
    assert "stock_low" in _types(db)
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'SET_REORDER_LEVEL'"
    ).fetchone()[0] == 1
    set_reorder_level(db, "Alpha", 50)   # 100 > 50 -> recovered
    assert "stock_low" not in _types(db)


# ---------- API ----------

def test_api_requires_auth(client):
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/read", json={}).status_code == 401


def test_api_list_unread_and_mark_read(admin_client, db):
    notify(db, type="t", title="Shared")
    r = admin_client.get("/api/notifications")
    assert r.status_code == 200
    data = r.get_json()
    assert data["unread"] >= 1
    ids = [i["id"] for i in data["items"] if not i["is_read"]]
    r2 = admin_client.post("/api/notifications/read", json={"ids": ids})
    assert r2.status_code == 200
    assert r2.get_json()["unread"] == 0
    r3 = admin_client.post("/api/notifications/read", json={})
    assert r3.status_code == 200 and r3.get_json()["unread"] == 0


def test_api_rejects_bad_ids(admin_client):
    assert admin_client.post("/api/notifications/read",
                             json={"ids": "nope"}).status_code == 400
    assert admin_client.post("/api/notifications/read",
                             json={"ids": [True]}).status_code == 400


def test_api_role_scoping_between_admin_and_user(admin_client, user_client, db):
    notify(db, type="gen", title="For everyone")
    notify(db, type="sec", title="For admins", role_scope="admin")
    admin_data = admin_client.get("/api/notifications").get_json()
    user_data = user_client.get("/api/notifications").get_json()
    admin_titles = {i["title"] for i in admin_data["items"]}
    user_titles = {i["title"] for i in user_data["items"]}
    assert admin_titles == {"For everyone", "For admins"}
    assert "For admins" not in user_titles
    assert "For everyone" in user_titles
    assert admin_data["unread"] == 2 and user_data["unread"] == 1


def test_failed_login_threshold_alerts_admins_only(admin_client, user_client, client, db):
    # Four fails through the API, then pin their timestamps to the server
    # clock so the threshold check inside the fifth request is deterministic
    # regardless of host clock drift between the app and database machines.
    for _ in range(4):
        resp = client.post("/api/auth/login",
                           json={"username": "admin", "password": "wrong-pass"})
        assert resp.status_code in (401, 429)
    db.execute("UPDATE login_attempts SET attempted_at = '2999-01-01 00:00:00'")
    db.commit()
    resp = client.post("/api/auth/login",
                       json={"username": "admin", "password": "wrong-pass"})
    # Fifth failure is not blocked at entry (only four prior fails) and
    # must record + trigger the threshold notification.
    assert resp.status_code == 401, (resp.status_code, resp.get_json())
    # Five fail rows through the API (fixture logins add two success rows).
    fails = db.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE success = 0").fetchone()[0]
    assert fails == 5, fails
    admin_items = admin_client.get("/api/notifications").get_json()["items"]
    user_items = user_client.get("/api/notifications").get_json()["items"]
    admin_types = [i["type"] for i in admin_items]
    user_types = [i["type"] for i in user_items]
    assert "login_failures" in admin_types
    assert "login_failures" not in user_types


def test_login_threshold_notification_wiring(admin_client, db):
    """Hermetic: the same threshold call the login route makes must alert.

    Five API-recorded fails with immune timestamps, notifications wiped,
    then the route's _notify_login_failures directly: it must insert exactly
    one admin alert. Fully deterministic under any clock behavior, unlike
    the end-to-end trigger above whose fresh row needs a stable clock.
    """
    from flask_app import _notify_login_failures
    for _ in range(5):
        r = admin_client.post("/api/auth/login",
                              json={"username": "admin", "password": "wrong-pass"})
        assert r.status_code == 401
    db.execute("UPDATE login_attempts SET attempted_at = '2999-01-01 00:00:00'")
    db.execute("DELETE FROM notifications")
    db.commit()
    _notify_login_failures(db, "admin", "127.0.0.1")
    types = [i["type"] for i in admin_client.get("/api/notifications").get_json()["items"]]
    assert types == ["login_failures"]
