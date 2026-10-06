"""P1-4: every allowlisted company field change is audited.

`update_company` gated its `COMPANY_UPDATE` row on a *name* change
(`raas_tracker/companies.py`), so a `PUT` that moved only `swift`, `lc_bank`,
`address`, `contact_person`, `code` or `country` committed with no audit row at
all — a bank-detail edit left no trace of who made it, when, or from where.

Recording decision (user-approved): **field names only, no values.** Logging the
values would push contact and bank data into `audit_logs` for the first time,
reversing the zero-sensitive-PII property the P1 analysis established. A
company *name* keeps its before/after because it is an identifier, not PII.

Second decision: a field resubmitted with the value it already has is a no-op
and must not add a row, so re-saving a form does not spam the trail.

Which tests are actual evidence: `test_swift_only_change_is_audited`,
`test_every_allowlisted_field_is_detectable`,
`test_bank_and_contact_values_never_reach_the_audit_trail`,
`test_unchanged_submission_writes_no_row`,
`test_clearing_a_field_is_recorded_as_a_change` and
`test_full_form_resubmit_marks_only_the_edited_field` all fail with the fix
reverted. `test_name_change_keeps_before_and_after` and
`test_company_update_requires_admin` pass either way — the first pins
pre-existing behaviour that had to be preserved, the second is a gate guard
rail. Do not count them as coverage of the defect.
"""
import pytest


def _cid(admin_client, name):
    r = admin_client.post("/api/companies", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _last_update(db):
    return db.execute(
        "SELECT user_id, old_value, new_value, ip_address FROM audit_logs "
        "WHERE action = 'COMPANY_UPDATE' ORDER BY id DESC LIMIT 1").fetchone()


def _update_count(db):
    return db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'COMPANY_UPDATE'"
    ).fetchone()[0]


def test_swift_only_change_is_audited(admin_client, db):
    cid = _cid(admin_client, "AuditBank")
    r = admin_client.put(f"/api/companies/{cid}", json={"swift": "DEUTDEFFXXX"})
    assert r.status_code == 200, r.get_json()

    row = _last_update(db)
    assert row is not None, "changing only the SWIFT wrote no audit row"
    user_id, old_value, new_value, ip = row
    assert user_id == "admin"
    assert new_value == "changed=swift"
    assert old_value is None
    assert ip == "127.0.0.1"


def test_every_allowlisted_field_is_detectable(admin_client, db):
    cid = _cid(admin_client, "AuditAllFields")
    r = admin_client.put(f"/api/companies/{cid}", json={
        "code": "DE-1", "country": "Germany", "address": "1 Private Lane",
        "contact_person": "Jane Q", "swift": "SECRETSWIFT999",
        "lc_bank": "Bank of Secret Ltd"})
    assert r.status_code == 200, r.get_json()

    _user_id, _old_value, new_value, _ip = _last_update(db)
    for field in ("address", "code", "contact_person", "country", "lc_bank", "swift"):
        assert field in new_value, f"{field} change was not recorded"


def test_bank_and_contact_values_never_reach_the_audit_trail(admin_client, db):
    """The zero-PII property must survive the coverage fix."""
    cid = _cid(admin_client, "AuditPii")
    secrets = {
        "swift": "SECRETSWIFT999",
        "lc_bank": "Bank of Secret Ltd",
        "contact_person": "Jane Q. Secret",
        "address": "1 Private Lane, Vault",
    }
    assert admin_client.put(f"/api/companies/{cid}", json=secrets).status_code == 200

    _user_id, old_value, new_value, _ip = _last_update(db)
    blob = f"{old_value or ''}{new_value or ''}"
    for secret in secrets.values():
        assert secret not in blob, "a sensitive value leaked into audit_logs"


def test_unchanged_submission_writes_no_row(admin_client, db):
    """Re-saving a form resubmits every field; unchanged ones are not evidence."""
    cid = _cid(admin_client, "AuditNoop")
    assert admin_client.put(f"/api/companies/{cid}",
                            json={"swift": "AAAABBBB"}).status_code == 200
    assert _update_count(db) == 1

    assert admin_client.put(f"/api/companies/{cid}",
                            json={"swift": "AAAABBBB"}).status_code == 200
    assert _update_count(db) == 1, "a no-op resubmission wrote a spurious audit row"

    # A genuine second change must be recorded.
    assert admin_client.put(f"/api/companies/{cid}",
                            json={"swift": "CCCCDDDD"}).status_code == 200
    assert _update_count(db) == 2


def test_clearing_a_field_is_recorded_as_a_change(admin_client, db):
    cid = _cid(admin_client, "AuditClear")
    assert admin_client.put(f"/api/companies/{cid}",
                            json={"swift": "TOBEMOVED"}).status_code == 200
    assert admin_client.put(f"/api/companies/{cid}",
                            json={"swift": ""}).status_code == 200
    _user_id, _old_value, new_value, _ip = _last_update(db)
    assert new_value and "swift" in new_value, "clearing a stored value is still a change"


def test_name_change_keeps_before_and_after(admin_client, db):
    """Company name is an identifier, not PII: keep the existing behaviour."""
    cid = _cid(admin_client, "OldName")
    assert admin_client.put(f"/api/companies/{cid}",
                            json={"name": "NewName"}).status_code == 200
    _user_id, old_value, new_value, _ip = _last_update(db)
    assert old_value == "OldName"
    assert new_value == "NewName"


def test_name_and_field_change_records_both(admin_client, db):
    cid = _cid(admin_client, "ComboOld")
    assert admin_client.put(f"/api/companies/{cid}",
                            json={"name": "ComboNew", "swift": "ZZZZ9999"}).status_code == 200
    _user_id, old_value, new_value, _ip = _last_update(db)
    assert old_value == "ComboOld"
    assert "ComboNew" in new_value
    assert "changed=swift" in new_value
    assert "ZZZZ9999" not in new_value


def test_full_form_resubmit_marks_only_the_edited_field(admin_client, db):
    """Companies.jsx submits all seven fields on every save.

    This is the exact combination the P1-4 decisions exist to serve: a full
    resubmit where only one field actually changed. A regression that compared
    against a default instead of the stored row would mark all seven changed,
    and every other test in this file would still pass.
    """
    cid = _cid(admin_client, "FormCo")
    payload = {"name": "FormCo", "code": "C-1", "country": "BD",
               "address": "Old St", "contact_person": "",
               "swift": "AAAABBBB", "lc_bank": "Old Bank"}
    assert admin_client.put(f"/api/companies/{cid}",
                            json=payload).status_code == 200
    assert _update_count(db) == 1, "the initial full save changes five fields"

    payload["swift"] = "CCCCDDDD"
    assert admin_client.put(f"/api/companies/{cid}",
                            json=payload).status_code == 200
    _user_id, old_value, new_value, _ip = _last_update(db)
    assert new_value == "changed=swift", f"expected only swift, got {new_value!r}"
    assert old_value is None


def test_company_update_requires_admin(admin_client, user_client, db):
    """Guard rail: the audit work must not weaken the existing gate."""
    cid = _cid(admin_client, "GuardCo")
    assert user_client.put(f"/api/companies/{cid}",
                           json={"swift": "NOPE1234"}).status_code == 403
    assert _update_count(db) == 0