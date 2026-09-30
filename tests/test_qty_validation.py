"""NaN/Infinity quantities must be rejected at every numeric entry point.

`nan <= 0` is False and `inf > 0` is True, so plain "> 0" guards wave
NaN/Infinity straight through into SQL (real accepts 'NaN'/'Infinity') and
report math, where they silently poison totals instead of erroring.
"""
import pytest

from raas_tracker.recipes import create_production_run


def _produce(admin_client, payload):
    return admin_client.post("/api/recipes/Any/produce", json=payload)


def test_produce_rejects_nan_recipe_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": 5, "company_id": 1,
        "recipes": [{"recipe_name": "Any", "qty": float("nan")}],
    })
    assert r.status_code == 400
    # Rejected by schema validation, not by a downstream ValueError handler.
    assert "details" in r.get_json()


def test_produce_rejects_infinite_recipe_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": 5, "company_id": 1,
        "recipes": [{"recipe_name": "Any", "qty": float("inf")}],
    })
    assert r.status_code == 400
    assert "details" in r.get_json()


def test_produce_rejects_nan_production_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": float("nan"), "company_id": 1,
    })
    assert r.status_code == 400
    assert "details" in r.get_json()


def test_produce_rejects_infinite_production_qty(admin_client):
    r = _produce(admin_client, {
        "production_qty": float("inf"), "company_id": 1,
    })
    assert r.status_code == 400
    assert "details" in r.get_json()


def test_generate_rejects_nan_qty(admin_client):
    r = admin_client.post("/api/reports/generate", json={
        "recipes": [{"company_id": 1, "recipe_name": "x"}],
        "qty": float("nan"),
    })
    assert r.status_code == 400
    assert "finite" in r.get_json()["error"]


def test_generate_rejects_infinite_qty(admin_client):
    r = admin_client.post("/api/reports/generate", json={
        "recipes": [{"company_id": 1, "recipe_name": "x"}],
        "qty": float("inf"),
    })
    assert r.status_code == 400
    assert "finite" in r.get_json()["error"]


def test_export_rejects_nan_qty(admin_client):
    r = admin_client.post("/api/reports/export", json={
        "recipes": [{"company_id": 1, "recipe_name": "x"}],
        "qty": float("nan"),
    })
    assert r.status_code == 400
    assert "finite" in r.get_json()["error"]


def test_create_production_run_service_guard_rejects_nan(db):
    with pytest.raises(ValueError, match="must be positive"):
        create_production_run(db, 1, "nope", float("nan"), atomic=True)


def test_create_production_run_service_guard_rejects_inf(db):
    with pytest.raises(ValueError, match="must be positive"):
        create_production_run(db, 1, "nope", float("inf"), atomic=True)
