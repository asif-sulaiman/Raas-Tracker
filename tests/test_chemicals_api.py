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


def _seed_chem(admin_client, name="AdjAcid"):
    r = admin_client.post(
        "/api/chemicals", json={"name": name, "qty": 10, "unit": "KG"})
    assert r.status_code == 200


def test_update_chemical_admin_ok(admin_client, db):
    _seed_chem(admin_client)
    r = admin_client.post(
        "/api/chemicals/update", json={"name": "AdjAcid", "delta": 5})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    row = db.execute("SELECT current_qty FROM chemicals WHERE name = %s",
                     ("AdjAcid",)).fetchone()
    assert row[0] == 15


def test_update_chemical_user_forbidden(user_client):
    r = user_client.post(
        "/api/chemicals/update", json={"name": "AdjAcid", "delta": 5})
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"


def test_update_chemical_anon_unauthorized():
    c = flask_app.app.test_client()
    r = c.post("/api/chemicals/update", json={"name": "AdjAcid", "delta": 5})
    assert r.status_code == 401


def test_reorder_admin_ok(admin_client, db):
    _seed_chem(admin_client)
    r = admin_client.put(
        "/api/chemicals/reorder", json={"name": "AdjAcid", "reorder_level": 3})
    assert r.status_code == 200
    row = db.execute("SELECT reorder_level FROM chemicals WHERE name = %s",
                     ("AdjAcid",)).fetchone()
    assert row[0] == 3


def test_reorder_user_forbidden(user_client):
    r = user_client.put(
        "/api/chemicals/reorder", json={"name": "AdjAcid", "reorder_level": 3})
    assert r.status_code == 403
    assert r.get_json()["error"] == "admin required"


def test_reorder_anon_unauthorized():
    c = flask_app.app.test_client()
    r = c.put(
        "/api/chemicals/reorder", json={"name": "AdjAcid", "reorder_level": 3})
    assert r.status_code == 401


def test_update_stock_persists_reason_in_audit(admin_client, db):
    admin_client.post("/api/chemicals", json={"name": "ReasonAcid", "qty": 10})
    r = admin_client.post("/api/chemicals/update", json={
        "name": "ReasonAcid", "delta": 5, "reason": "Supplier delivery"})
    assert r.status_code == 200
    row = db.execute(
        "SELECT old_value, new_value, ip_address FROM audit_logs "
        "WHERE action = 'ADJUST_STOCK' ORDER BY id DESC LIMIT 1").fetchone()
    assert (row[0], row[1], row[2]) == ("10.0", "15.0", "Supplier delivery")


def test_add_chemical_logs_birth_audit(admin_client, db):
    admin_client.post(
        "/api/chemicals", json={"name": "BirthAcid", "qty": 7, "unit": "G"})
    row = db.execute(
        "SELECT entity_type, new_value FROM audit_logs "
        "WHERE action = 'ADD_CHEMICAL' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert (row[0], row[1]) == ("chemical", "7.0")


def test_chemicals_history(admin_client):
    admin_client.post("/api/chemicals", json={"name": "HistAcid", "qty": 10})
    admin_client.post("/api/chemicals/update", json={
        "name": "HistAcid", "delta": -3, "reason": "Disposal / expiry"})
    r = admin_client.get("/api/chemicals/history")
    assert r.status_code == 200
    rows = r.get_json()
    kinds = {(m["action"], m["chemical"]) for m in rows}
    assert ("ADD_CHEMICAL", "HistAcid") in kinds
    adj = [m for m in rows
           if m["action"] == "ADJUST_STOCK" and m["chemical"] == "HistAcid"][0]
    assert adj["delta"] == -3
    assert adj["purpose"] == "Disposal / expiry"
    assert adj["actor"] == "admin"
    chem_id = adj["chemical_id"]
    r2 = admin_client.get(f"/api/chemicals/history?chemical_id={chem_id}")
    assert r2.status_code == 200
    assert r2.get_json()
    assert all(m["chemical_id"] == chem_id for m in r2.get_json())


def test_chemicals_history_forbidden(user_client):
    r = user_client.get("/api/chemicals/history")
    assert r.status_code == 403


def test_chemicals_history_anon():
    c = flask_app.app.test_client()
    assert c.get("/api/chemicals/history").status_code == 401
