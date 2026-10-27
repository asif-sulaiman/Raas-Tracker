"""Phase 1: LC payment terms live on the LC and are inherited by its invoices.

At-sight LCs carry no tenor; deferred LCs (90/120/180 days) must carry a
positive tenor. Legacy rows created before this column existed read as
at-sight, so no backfill is needed.
"""
import uuid

import pytest


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _company(db, name):
    row = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    db.commit()
    return row[0]


def _mk_lc(admin_client, company_id, lc_number, **kw):
    return admin_client.post("/api/lcs", json={
        "company_id": company_id, "lc_number": lc_number, **kw})


def _get_lc(admin_client, lc_id):
    r = admin_client.get(f"/api/lcs/{lc_id}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _fields(body):
    return {d["field"] for d in (body.get("details") or [])}


def test_create_lc_defaults_to_at_sight(admin_client, db):
    co = _company(db, f"Terms Co {_tag()}")
    r = _mk_lc(admin_client, co, f"LC-T-{_tag()}")
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["payment_terms"] == "at_sight"
    assert body["tenor_days"] is None
    assert _get_lc(admin_client, body["id"])["payment_terms"] == "at_sight"


def test_create_lc_deferred_stores_and_lists_the_tenor(admin_client, db):
    co = _company(db, f"Terms D Co {_tag()}")
    r = _mk_lc(admin_client, co, f"LC-D-{_tag()}",
               payment_terms="deferred", tenor_days=90)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["payment_terms"] == "deferred"
    assert body["tenor_days"] == 90

    got = _get_lc(admin_client, body["id"])
    assert got["payment_terms"] == "deferred"
    assert got["tenor_days"] == 90

    listed = admin_client.get(f"/api/lcs?company_id={co}").get_json()
    row = next(lc for lc in listed if lc["id"] == body["id"])
    assert row["payment_terms"] == "deferred"
    assert row["tenor_days"] == 90


@pytest.mark.parametrize("tenor", [0, -5, 721])
def test_deferred_tenor_out_of_range_is_a_400_field_error(admin_client, db, tenor):
    co = _company(db, f"Terms R Co {_tag()}")
    r = _mk_lc(admin_client, co, f"LC-R-{_tag()}",
               payment_terms="deferred", tenor_days=tenor)
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    assert "tenor_days" in _fields(body)
    assert "error" in body


def test_deferred_without_a_tenor_is_a_400_field_error(admin_client, db):
    co = _company(db, f"Terms N Co {_tag()}")
    r = _mk_lc(admin_client, co, f"LC-N-{_tag()}", payment_terms="deferred")
    assert r.status_code == 400, r.get_json()
    assert "tenor_days" in _fields(r.get_json())


def test_unknown_payment_terms_is_a_400_field_error(admin_client, db):
    co = _company(db, f"Terms U Co {_tag()}")
    r = _mk_lc(admin_client, co, f"LC-U-{_tag()}",
               payment_terms="net30", tenor_days=30)
    assert r.status_code == 400, r.get_json()
    assert "payment_terms" in _fields(r.get_json())


def test_at_sight_ignores_a_supplied_tenor(admin_client, db):
    co = _company(db, f"Terms I Co {_tag()}")
    r = _mk_lc(admin_client, co, f"LC-I-{_tag()}",
               payment_terms="at_sight", tenor_days=90)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["payment_terms"] == "at_sight"
    assert body["tenor_days"] is None


def test_update_lc_terms_then_clear_them_with_at_sight(admin_client, db):
    co = _company(db, f"Terms P Co {_tag()}")
    created = _mk_lc(admin_client, co, f"LC-P-{_tag()}").get_json()

    r = admin_client.put(f"/api/lcs/{created['id']}", json={
        "payment_terms": "deferred", "tenor_days": 120})
    assert r.status_code == 200, r.get_json()
    got = _get_lc(admin_client, created["id"])
    assert got["payment_terms"] == "deferred"
    assert got["tenor_days"] == 120

    r = admin_client.put(f"/api/lcs/{created['id']}",
                   json={"payment_terms": "at_sight"})
    assert r.status_code == 200, r.get_json()
    got = _get_lc(admin_client, created["id"])
    assert got["payment_terms"] == "at_sight"
    assert got["tenor_days"] is None, "at sight must clear the inherited tenor"


def test_update_to_deferred_without_a_tenor_is_a_400(admin_client, db):
    co = _company(db, f"Terms Q Co {_tag()}")
    created = _mk_lc(admin_client, co, f"LC-Q-{_tag()}").get_json()

    r = admin_client.put(f"/api/lcs/{created['id']}",
                   json={"payment_terms": "deferred"})
    assert r.status_code == 400, r.get_json()
    assert "tenor_days" in _fields(r.get_json())
    assert _get_lc(admin_client, created["id"])["payment_terms"] == "at_sight"


def test_legacy_null_terms_read_as_at_sight(admin_client, db):
    """Rows that predate the columns carry NULL; they must read as at-sight."""
    co = _company(db, f"Terms L Co {_tag()}")
    lc_id = db.execute(
        "INSERT INTO letters_of_credit (lc_number, company_id) "
        "VALUES (%s, %s) RETURNING id",
        (f"LC-L-{_tag()}", co)).fetchone()[0]
    db.commit()

    got = _get_lc(admin_client, lc_id)
    assert got["payment_terms"] == "at_sight"
    assert got["tenor_days"] is None
