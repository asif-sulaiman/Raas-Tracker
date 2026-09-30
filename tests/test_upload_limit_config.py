"""M7.1: MAX_CONTENT_LENGTH_MB env override resolves to a byte cap."""
import os

import pytest

import flask_app


DEFAULT = 50 * 1024 * 1024


def test_default_when_unset():
    assert flask_app._max_content_length_bytes(None) == DEFAULT


def test_default_when_empty():
    assert flask_app._max_content_length_bytes("  ") == DEFAULT


def test_integer_mb():
    assert flask_app._max_content_length_bytes("50") == DEFAULT


def test_vercel_fractional_mb():
    assert flask_app._max_content_length_bytes("4.5") == int(4.5 * 1024 * 1024)


def test_small_value():
    assert flask_app._max_content_length_bytes("1") == 1024 * 1024


def test_invalid_falls_back_to_default(capsys):
    assert flask_app._max_content_length_bytes("not-a-number") == DEFAULT
    captured = capsys.readouterr()
    assert "MAX_CONTENT_LENGTH_MB" in captured.err


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_non_positive_falls_back_to_default(raw):
    assert flask_app._max_content_length_bytes(raw) == DEFAULT


def test_app_config_wired_to_helper():
    assert flask_app.app.config["MAX_CONTENT_LENGTH"] == (
        flask_app._max_content_length_bytes(os.environ.get("MAX_CONTENT_LENGTH_MB"))
    )


def test_413_message_reflects_configured_cap(client):
    app = flask_app.app
    original = app.config["MAX_CONTENT_LENGTH"]
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    try:
        resp = client.post(
            "/api/auth/login",
            data=b"x" * (1024 * 1024 + 1024),
            content_type="application/json",
        )
        assert resp.status_code == 413
        assert "max 1MB" in resp.get_json()["error"]
    finally:
        app.config["MAX_CONTENT_LENGTH"] = original
