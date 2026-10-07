"""P1-3: every audit value column is bounded and free of control characters.

`audit_logs.entity_type` / `old_value` / `new_value` are bare `TEXT`
(`raas_tracker/db.py`) and no Pydantic model in the app sets `max_length`, so
nothing stopped an attacker-influenced or simply enormous string from landing
in the audit trail. The bound is enforced once, centrally, in
`log_audit_action`, so current and future call sites are covered.

Two call sites also had to be corrected at the source, because the value they
wrote was wrong in kind rather than merely too long:
`LEGACY_TOKEN_USED` used the request path as an entity *type*, and the
`SALE_UPDATE` snapshot included the stored server-side PI file path.
"""
import pytest


def _row(db, action):
    return db.execute(
        "SELECT entity_type, old_value, new_value FROM audit_logs "
        "WHERE action = %s ORDER BY id DESC LIMIT 1", (action,)).fetchone()


# ==================== central bounds ====================

def test_oversize_new_value_is_truncated(db):
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_NEW", "probe", 1, new_value="x" * 5000)
    assert len(_row(db, "CAP_NEW")[2]) == 2000


def test_oversize_old_value_is_truncated(db):
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_OLD", "probe", 1, old_value="y" * 5000)
    assert len(_row(db, "CAP_OLD")[1]) == 2000


def test_oversize_entity_type_is_capped(db):
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_ET", "e" * 500, 1)
    assert len(_row(db, "CAP_ET")[0]) == 64


def test_control_characters_are_stripped(db):
    """NULs and terminal escapes must not reach a log reader."""
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_CTRL", "probe", 1,
                     new_value="a\x00b\x1bc\x7fd")
    assert _row(db, "CAP_CTRL")[2] == "abcd"


def test_newlines_and_tabs_are_preserved(db):
    """Legitimate multi-line values (json.dumps output) stay readable."""
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_WS", "probe", 1, new_value="l1\nl2\tend")
    assert _row(db, "CAP_WS")[2] == "l1\nl2\tend"


def test_absent_values_stay_null(db):
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_NONE", "probe", 1)
    entity_type, old_value, new_value = _row(db, "CAP_NONE")
    assert (entity_type, old_value, new_value) == ("probe", None, None)


def test_ip_address_column_is_bounded(db):
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_IP", "probe", 1, ip_address="9" * 4000)
    assert len(db.execute(
        "SELECT ip_address FROM audit_logs WHERE action = 'CAP_IP' "
        "ORDER BY id DESC LIMIT 1").fetchone()[0]) <= 255


# ==================== call sites fixed at the source ====================

def test_legacy_token_used_records_a_type_not_a_path(client, db):
    """entity_type must stay a type name: it is indexed and filtered on."""
    r = client.get("/api/auth/me", headers={"X-API-Token": "whatever"})
    assert r.status_code == 401
    entity_type, _, new_value = _row(db, "LEGACY_TOKEN_USED")
    assert entity_type == "request"
    assert "/api/auth/me" in new_value, "the path belongs in the detail, not the type"


def test_legacy_token_used_path_is_bounded(client, db):
    client.get("/api/auth/me?pad=" + "a" * 4000,
               headers={"X-API-Token": "whatever"})
    _, _, new_value = _row(db, "LEGACY_TOKEN_USED")
    assert len(new_value) <= 2000


def test_carriage_return_is_stripped_by_the_central_bound(db):
    """CR must be handled by _bounded itself, not by a call-site sanitiser.

    Asserting this through save_upload would keep passing while the central
    hole was open, because the filename helper strips CR on its own.
    """
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_CR", "probe", 1, new_value="a\rb")
    assert _row(db, "CAP_CR")[2] == "ab"


def test_oversize_value_records_that_it_was_truncated(db):
    """A cut-off value must be distinguishable from a complete one."""
    from raas_tracker.audit import log_audit_action
    log_audit_action(db, "CAP_MARK", "probe", 1, new_value="x" * 5000)
    stored = _row(db, "CAP_MARK")[2]
    assert len(stored) <= 2000
    assert "truncated" in stored, "silent truncation loses evidence with no marker"


def test_sale_update_audit_excludes_the_server_file_path(admin_client, db):
    """pi_file_path is a server-side path; it must not enter the audit trail."""
    from raas_tracker.companies import create_company
    from raas_tracker.sales import add_sale
    cid = create_company(db, name="PathCo")
    add_sale(db, {"pi_number": "PI-PATH", "client_name": "PathCo", "company_id": cid},
             [{"product_name": "P", "quantity": 1, "unit_price": 5, "unit": "KG"}])
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = 'PI-PATH'").fetchone()[0]

    secret = "C:/srv/private/tenants/acme/2026/pi-secret.pdf"
    r = admin_client.put(f"/api/sales/{sale_id}",
                         json={"pi_file_path": secret, "comments": "hello"})
    assert r.status_code == 200, r.get_json()
    _, old_value, new_value = _row(db, "SALE_UPDATE")
    for blob in (old_value or "", new_value or ""):
        assert secret not in blob
        assert "pi_file_path" not in blob
    assert "hello" in new_value, "genuine business fields are still audited"


def test_full_update_audit_excludes_the_server_file_path(admin_client, db):
    """The full-body SALE_UPDATE path (sales.py) is a *separate* code path.

    Excluding pi_file_path from the patch path only (flask_app.py) left the
    items-in-body route still writing the server-side path into the trail.
    """
    from raas_tracker.companies import create_company
    from raas_tracker.sales import add_sale
    cid = create_company(db, name="FullPathCo")
    add_sale(db, {"pi_number": "PI-FULLPATH", "client_name": "FullPathCo",
                  "company_id": cid},
             [{"product_name": "P", "quantity": 1, "unit_price": 5, "unit": "KG"}])
    sale_id = db.execute("SELECT id FROM sales WHERE pi_number = 'PI-FULLPATH'").fetchone()[0]
    item_id = db.execute(
        "SELECT id FROM sale_items WHERE sale_id = %s", (sale_id,)).fetchone()[0]

    secret = "C:/srv/private/tenants/acme/2026/pi-secret.pdf"
    r = admin_client.put(f"/api/sales/{sale_id}", json={
        "header": {"pi_number": "PI-FULLPATH", "client_name": "FullPathCo",
                   "pi_file_path": secret},
        "items": [{"id": item_id, "product_name": "P", "quantity": 2,
                   "unit_price": 5, "unit": "KG"}],
        "removedIds": [],
    })
    assert r.status_code == 200, r.get_json()
    for blob in (_row(db, "SALE_UPDATE")[1] or "", _row(db, "SALE_UPDATE")[2] or ""):
        assert secret not in blob
        assert "pi_file_path" not in blob


def test_upload_create_audit_strips_a_hostile_filename(db):
    """The audit row must not carry the raw client-supplied name.

    The route now derives one canonical name via `_safe_upload_filename`
    (P1-11) and that is what save_upload sanitises again and audits, so the
    audit row never shows control chars or traversal.
    """
    from raas_tracker.uploads import save_upload
    results = {
        "stats": {"total": 0, "matched": 0, "last_month_mismatches": 0,
                  "this_month_mismatches": 0, "both_mismatches": 0,
                  "not_in_db": 0, "not_in_upload": 0, "match_percentage": 0.0},
        "matches": [], "last_month_mismatches": [], "this_month_mismatches": [],
        "both_mismatches": [], "not_in_db": [], "not_in_upload": [],
    }
    save_upload(db, "../../evil\x00name\r.xlsx", results)
    new_value = db.execute(
        "SELECT new_value FROM audit_logs WHERE action = 'UPLOAD_CREATE' "
        "ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "\x00" not in new_value and "\r" not in new_value
    assert ".." not in new_value