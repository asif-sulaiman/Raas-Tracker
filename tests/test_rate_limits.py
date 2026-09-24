"""Tiered rate limits: key shapes + heavy-route enforcement (Phase C1)."""
import pytest


def test_limit_key_shapes():
    import flask_app
    from flask import g
    with flask_app.app.test_request_context("/"):
        assert flask_app._limit_key() == "127.0.0.1"
        g.current_identity = {"type": "human", "id": 7}
        assert flask_app._limit_key() == "user:7"
        g.current_identity = {"type": "api-key", "id": 9}
        assert flask_app._limit_key() == "key:9"
        g.current_identity = {"type": "human"}
        assert flask_app._limit_key() == "127.0.0.1"


def test_normal_use_unaffected(admin_client, monkeypatch):
    monkeypatch.delenv("RAAS_RATE_LIMITS", raising=False)
    for _ in range(5):
        assert admin_client.get("/api/chemicals").status_code == 200


def test_heavy_route_429_and_json(admin_client, monkeypatch):
    monkeypatch.delenv("RAAS_RATE_LIMITS", raising=False)
    codes = [admin_client.get("/api/sales/export").status_code for _ in range(16)]
    assert codes[:15] == [200] * 15
    assert codes[15] == 429
    body = admin_client.get("/api/sales/export").get_json()
    assert body == {"error": "rate limit exceeded, slow down"}
