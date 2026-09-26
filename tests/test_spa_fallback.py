"""SPA fallback: extensionless routes serve index.html; missing static assets 404.

Regression test for the production blank page: a stale bundle reference
(e.g. /assets/index-<oldhash>.js) must fail loudly with 404 instead of
returning index.html (HTML served as JS leaves the SPA permanently blank
with a MIME-type error).

Hermetic: builds a fake REACT_BUILD_DIR in tmp_path, so it runs with or
without a built frontend and touches no database.
"""
import flask_app


def _fake_build_dir(tmp_path):
    (tmp_path / "index.html").write_text("<div id=\"root\"></div>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log('ok')", encoding="utf-8")
    return str(tmp_path)


def test_missing_asset_returns_404_not_index(tmp_path, monkeypatch):
    monkeypatch.setattr(flask_app, "REACT_BUILD_DIR", _fake_build_dir(tmp_path))
    client = flask_app.app.test_client()
    r = client.get("/assets/index-oldhash999.js")
    assert r.status_code == 404


def test_missing_top_level_asset_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(flask_app, "REACT_BUILD_DIR", _fake_build_dir(tmp_path))
    client = flask_app.app.test_client()
    r = client.get("/favicon-missing.svg")
    assert r.status_code == 404


def test_missing_builtin_static_returns_404(tmp_path, monkeypatch):
    # Flask's built-in /static/* route 404s into the app 404 handler,
    # which must not convert a missing asset back to index.html.
    monkeypatch.setattr(flask_app, "REACT_BUILD_DIR", _fake_build_dir(tmp_path))
    client = flask_app.app.test_client()
    r = client.get("/static/missing.js")
    assert r.status_code == 404


def test_spa_route_still_serves_index(tmp_path, monkeypatch):
    monkeypatch.setattr(flask_app, "REACT_BUILD_DIR", _fake_build_dir(tmp_path))
    client = flask_app.app.test_client()
    for route in ("/", "/login", "/reports"):
        r = client.get(route)
        assert r.status_code == 200, route
        assert b"root" in r.data, route


def test_existing_asset_is_served(tmp_path, monkeypatch):
    monkeypatch.setattr(flask_app, "REACT_BUILD_DIR", _fake_build_dir(tmp_path))
    client = flask_app.app.test_client()
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert b"console.log" in r.data
