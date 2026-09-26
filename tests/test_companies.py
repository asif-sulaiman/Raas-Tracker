"""P0: companies master table + sales.company_id wiring."""
import flask_app
from raas_tracker.companies import backfill_company_links


def test_company_crud_admin(admin_client, db):
    r = admin_client.post("/api/companies",
                          json={"name": "Acme Ltd", "country": "BD"})
    assert r.status_code == 201
    cid = r.get_json()["id"]
    assert r.get_json()["country"] == "BD"

    r = admin_client.get("/api/companies")
    assert r.status_code == 200
    assert any(c["name"] == "Acme Ltd" for c in r.get_json())

    r = admin_client.put(f"/api/companies/{cid}",
                         json={"name": "Acme Limited", "swift": "ACMEBDDH"})
    assert r.status_code == 200
    assert r.get_json()["name"] == "Acme Limited"

    assert admin_client.delete(f"/api/companies/{cid}").status_code == 200
    assert admin_client.get("/api/companies").get_json() == []


def test_company_name_required_and_unique(admin_client):
    assert admin_client.post(
        "/api/companies", json={"name": "   "}).status_code == 400
    assert admin_client.post(
        "/api/companies", json={"name": "DupCo"}).status_code == 201
    r = admin_client.post("/api/companies", json={"name": "dupco"})
    assert r.status_code == 409
    assert "already exists" in r.get_json()["error"]


def test_company_gates(user_client):
    assert user_client.get("/api/companies").status_code == 200
    assert user_client.post(
        "/api/companies", json={"name": "X"}).status_code == 403
    c = flask_app.app.test_client()
    assert c.get("/api/companies").status_code == 401


def test_company_delete_blocked_when_linked(admin_client, db):
    admin_client.post("/api/companies", json={"name": "LinkedCo"})
    db.execute("INSERT INTO sales (pi_number, client_name) "
               "VALUES ('PI-1', 'LinkedCo')")
    db.execute("UPDATE sales SET company_id = "
               "(SELECT id FROM companies WHERE name = 'LinkedCo') "
               "WHERE pi_number = 'PI-1'")
    db.commit()
    cid = db.execute(
        "SELECT id FROM companies WHERE name = 'LinkedCo'").fetchone()[0]
    r = admin_client.delete(f"/api/companies/{cid}")
    assert r.status_code == 409
    assert "linked" in r.get_json()["error"].lower()


def test_backfill_links_legacy_sales(db):
    db.execute("INSERT INTO sales (pi_number, client_name) VALUES "
               "('PI-BF1', 'Backfill Co'), ('PI-BF2', 'backfill co')")
    db.commit()
    linked = backfill_company_links(db)
    assert linked == 2
    names = [r[0] for r in db.execute("SELECT name FROM companies").fetchall()]
    assert len(names) == 1  # case variants merge to one company
    nulls = db.execute(
        "SELECT COUNT(*) FROM sales WHERE company_id IS NULL").fetchone()[0]
    assert nulls == 0
    assert backfill_company_links(db) == 0  # idempotent rerun links nothing new
