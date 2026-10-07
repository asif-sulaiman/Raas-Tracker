"""P1-1: minting and revoking a script credential is audited.

`create_api_key` and `revoke_api_key` wrote no `audit_logs` row at all, so a
live `ck_live_…` key could appear — or be revoked — with no trace of who did
it. That is the highest-value gap in the audit trail: unlike the rest of the
mutating surface, these two actions create and destroy the credential that
bypasses human authentication entirely.

The raw key and its hash must never reach the audit table.
"""
import pytest


def _rows(db, action):
    return db.execute(
        "SELECT entity_type, entity_id, user_id, old_value, new_value "
        "FROM audit_logs WHERE action = %s ORDER BY id", (action,)).fetchall()


def test_api_key_create_is_audited(admin_client, db):
    r = admin_client.post("/api/keys", json={"name": "NightlySync"})
    assert r.status_code == 201, r.get_json()
    raw = r.get_json()["key"]

    rows = _rows(db, "API_KEY_CREATE")
    assert len(rows) == 1, "creating a live credential left no audit row"
    entity_type, entity_id, user_id, old_value, new_value = rows[0]
    assert entity_type == "api_key"
    assert user_id == "admin", "the admin who minted it must be recorded"
    assert entity_id is not None
    assert "NightlySync" in new_value
    assert raw not in (new_value or ""), "the raw key must never be audited"


def test_api_key_create_audits_its_ip_allowlist(admin_client, db):
    """The allowlist is the access control on the key, so it belongs in the trail."""
    assert admin_client.post("/api/keys", json={
        "name": "Locked", "allowed_ips": "10.0.0.0/8"}).status_code == 201
    assert "10.0.0.0/8" in _rows(db, "API_KEY_CREATE")[-1][4]


def test_api_key_hash_is_never_audited(admin_client, db):
    from raas_tracker.auth import _api_key_hash
    raw = admin_client.post("/api/keys", json={"name": "HashProbe"}).get_json()["key"]
    digest = _api_key_hash(raw)
    for _entity_type, _entity_id, _user_id, old_value, new_value in _rows(db, "API_KEY_CREATE"):
        assert digest not in (old_value or "")
        assert digest not in (new_value or "")


def test_api_key_revoke_is_audited(admin_client, db):
    admin_client.post("/api/keys", json={"name": "DoomedKey"})
    key_id = db.execute("SELECT id FROM api_keys WHERE name = 'DoomedKey'").fetchone()[0]
    assert admin_client.delete(f"/api/keys/{key_id}").status_code == 200

    rows = _rows(db, "API_KEY_REVOKE")
    assert len(rows) == 1, "revoking a credential left no audit row"
    entity_type, entity_id, user_id, old_value, _new_value = rows[0]
    assert (entity_type, entity_id, user_id) == ("api_key", key_id, "admin")
    assert old_value == "DoomedKey", "the revoked key must be identifiable"


def test_revoking_an_unknown_key_writes_no_audit_row(admin_client, db):
    assert admin_client.delete("/api/keys/999999").status_code == 404
    assert _rows(db, "API_KEY_REVOKE") == []


def test_key_creation_requires_admin(user_client, db):
    """Guard rail: the audit work must not weaken the existing gate."""
    assert user_client.post("/api/keys", json={"name": "Sneaky"}).status_code == 403
    assert _rows(db, "API_KEY_CREATE") == []



def test_api_key_create_audits_its_expiry(admin_client, db):
    """The expiry is part of the credential's policy, so it belongs in the trail.

    `API_KEY_CREATE` recorded the name and allowlist but not `expires_at`, so a
    key created with a lifetime looked identical to one that never expires.
    """
    assert admin_client.post("/api/keys", json={
        "name": "Expiring", "expires_at": "2027-01-31 12:00:00"}).status_code == 201
    assert "expires=2027-01-31 12:00:00" in _rows(db, "API_KEY_CREATE")[-1][4]


def test_api_key_create_audits_no_expiry_as_never(admin_client, db):
    assert admin_client.post("/api/keys", json={"name": "Forever"}).status_code == 201
    assert "expires=never" in _rows(db, "API_KEY_CREATE")[-1][4]


def test_api_key_rejects_a_malformed_expiry(admin_client, db):
    """`validate_api_key` compares expires_at lexicographically, so "banana"
    sorts after every real timestamp - the key would silently never expire."""
    r = admin_client.post("/api/keys", json={
        "name": "BadExp", "expires_at": "banana"})
    assert r.status_code == 400, r.get_json()
    assert _rows(db, "API_KEY_CREATE") == []


def test_api_key_rejects_an_impossible_expiry_date(admin_client, db):
    r = admin_client.post("/api/keys", json={
        "name": "BadDate", "expires_at": "2026-13-45 99:99:99"})
    assert r.status_code == 400, r.get_json()


def test_api_key_rejects_an_unparseable_allowlist_entry(admin_client, db):
    """`_ip_allowed` skipped entries it could not parse, so a typo in a
    non-first position quietly narrowed access instead of erroring."""
    r = admin_client.post("/api/keys", json={
        "name": "BadIps", "allowed_ips": "10.0.0.0/8, not-an-ip"})
    assert r.status_code == 400, r.get_json()
    assert _rows(db, "API_KEY_CREATE") == []


def test_api_key_accepts_a_mixed_valid_allowlist(admin_client, db):
    """Both a CIDR and a bare IP are legitimate, and must keep working."""
    r = admin_client.post("/api/keys", json={
        "name": "GoodIps", "allowed_ips": "10.0.0.0/8, 192.168.1.5"})
    assert r.status_code == 201, r.get_json()
    assert "10.0.0.0/8" in _rows(db, "API_KEY_CREATE")[-1][4]


def test_api_key_rejects_an_absurdly_long_allowlist(admin_client, db):
    r = admin_client.post("/api/keys", json={
        "name": "HugeIps",
        "allowed_ips": ",".join(["10.0.0.%d" % (i % 255) for i in range(300)])})
    assert r.status_code == 400, r.get_json()
