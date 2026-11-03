"""`GET /api/dashboard` — one request, one connection, cacheable.

The Home page used to issue seven API calls, each checking out its own
connection from the pool. The dashboard aggregate must return exactly the
shapes those calls returned, keep the admin-only audit preview out of
non-admin payloads, and stay cheap on repeat views through a private ETag.
"""
import flask_app


def _json(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _seed(admin_client):
    r = admin_client.post(
        "/api/chemicals", json={"name": "DashAcid", "qty": 12, "unit": "KG"})
    assert r.status_code == 200, r.get_json()
    r = admin_client.post("/api/sales", json={
        "sale": {"pi_number": "PI-DASH", "client_name": "Dash Co"},
        "items": [{"product_name": "A", "quantity": 2, "unit_price": 5}],
    })
    assert r.status_code in (200, 201), r.get_json()


def test_dashboard_requires_authentication(client):
    assert client.get("/api/dashboard").status_code == 401


def test_dashboard_matches_existing_endpoint_shapes(admin_client):
    _seed(admin_client)

    dash = _json(admin_client.get("/api/dashboard"))
    assert set(dash) == {
        "chemicals", "uploads", "recipes", "sales", "sales_summary",
        "lcs", "activity",
    }

    assert dash["chemicals"] == _json(admin_client.get("/api/chemicals"))
    assert dash["uploads"] == _json(admin_client.get("/api/uploads"))
    assert dash["recipes"] == _json(admin_client.get("/api/recipes"))
    assert dash["sales"] == _json(
        admin_client.get("/api/sales?page=1&page_size=20"))
    assert dash["sales_summary"] == _json(
        admin_client.get("/api/sales/summary"))
    assert dash["lcs"] == _json(admin_client.get("/api/lcs"))
    assert dash["activity"] == _json(
        admin_client.get("/api/audit-logs?limit=5"))

    assert dash["chemicals"][0]["name"] == "DashAcid"
    assert dash["sales"]["sales"][0]["pi_number"] == "PI-DASH"


def test_dashboard_hides_activity_from_non_admins(user_client):
    body = _json(user_client.get("/api/dashboard"))
    assert body["activity"] is None
    assert user_client.get("/api/audit-logs?limit=5").status_code == 403


def test_dashboard_uses_a_single_connection(client, monkeypatch):
    monkeypatch.setattr(flask_app, "_resolve_identity", lambda: {
        "type": "human", "id": 1, "username": "admin", "role": "admin",
        "must_change_password": False,
    })
    calls = []
    real_get_db = flask_app.get_db

    def counting_get_db():
        calls.append(1)
        return real_get_db()

    monkeypatch.setattr(flask_app, "get_db", counting_get_db)

    r = client.get("/api/dashboard")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(calls) == 1


def test_dashboard_etag_is_private_and_revalidates(admin_client):
    first = admin_client.get("/api/dashboard")
    assert first.status_code == 200
    etag = first.headers["ETag"]
    assert etag
    cache_control = first.headers["Cache-Control"]
    assert "private" in cache_control
    assert "max-age=" in cache_control

    second = admin_client.get(
        "/api/dashboard", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.get_data() == b""
    assert second.headers["ETag"] == etag
    assert second.headers["Cache-Control"] == cache_control

    # A state change must invalidate the validator instead of clinging to it.
    _seed(admin_client)
    changed = admin_client.get(
        "/api/dashboard", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["ETag"] != etag
