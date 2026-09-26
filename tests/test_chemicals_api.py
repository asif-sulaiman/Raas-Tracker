"""API: POST /api/chemicals — admin-only add chemical with full fields."""
import flask_app


def test_add_chemical_admin_ok(admin_client, db):
    r = admin_client.post("/api/chemicals", json={
        "name": "Test Acid", "qty": 25, "unit": "L", "reorder_level": 5})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    row = db.execute(
        "SELECT current_qty, unit, reorder_level FROM chemicals WHERE name = %s",
        ("Test Acid",)).fetchone()
    assert (row[0], row[1], row[2]) == (25, "L", 5)


def test_add_chemical_defaults(admin_client, db):
    r = admin_client.post("/api/chemicals", json={"name": "Bare Chem"})
    assert r.status_code == 200
    row = db.execute(
        "SELECT current_qty, unit, reorder_level FROM chemicals WHERE name = %s",
        ("Bare Chem",)).fetchone()
    assert (row[0], row[1], row[2]) == (0, "KG", 0)


def test_add_chemical_user_forbidden(user_client):
    r = user_client.post("/api/chemicals", json={"name": "Nope", "qty": 1})
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"


def test_add_chemical_anon_unauthorized():
    c = flask_app.app.test_client()
    r = c.post("/api/chemicals", json={"name": "Nope", "qty": 1})
    assert r.status_code == 401


def test_add_chemical_duplicate_409(admin_client):
    assert admin_client.post(
        "/api/chemicals", json={"name": "DupAcid"}).status_code == 200
    r = admin_client.post("/api/chemicals", json={"name": "dupacid", "qty": 1})
    assert r.status_code == 409
    assert "already exists" in r.get_json()["error"]


def test_add_chemical_validation_400(admin_client):
    assert admin_client.post(
        "/api/chemicals", json={"name": "   "}).status_code == 400
    assert admin_client.post(
        "/api/chemicals", json={"name": "Neg", "qty": -1}).status_code == 400
    r = admin_client.post(
        "/api/chemicals", json={"name": "NegR", "reorder_level": -2})
    assert r.status_code == 400
    assert r.get_json()["details"]
