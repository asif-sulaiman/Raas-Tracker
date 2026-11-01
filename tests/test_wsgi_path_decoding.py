"""Path parameters must arrive decoded.

Vercel's Python runtime hands the WSGI app a PATH_INFO that still contains
percent-escapes ("Production%20KNIT%20432"). PEP 3333 says the server decodes
it first; the Flask dev server, waitress and the test client all do, so the
defect never shows locally. Werkzeug then treats "%20" as three characters and
every name/chemical looked up from the path misses — which is what turned the
live Edit dialog into "Recipe not found".

The test client decodes the URL normally, so the Vercel condition is modelled
in two parts: ``DECODE_PATH_INFO=1`` (the deployment flag, via the
``encoded_path_host`` fixture) and ``environ_overrides={"PATH_INFO": ...}``
for the still-encoded path.
"""
import pytest

pytest.importorskip("psycopg")

from raas_tracker import recipes as recipesmod  # noqa: E402
from raas_tracker import sales as salesmod  # noqa: E402

NAME = "Production KNIT 432"
PRODUCT = "Androma LO2"
ENC = "/api/recipes/Production%20KNIT%20432"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _company(conn, name="Knit Concern LTD"):
    row = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id", (name,)
    ).fetchone()
    conn.commit()
    return row[0]


def _register_product(conn, company_id, product=PRODUCT):
    """A recipe needs an invoice; invoicing needs a PI line first."""
    return salesmod.add_sale(
        conn,
        {"pi_number": "PI-WSGI-1", "client_name": "Buyer",
         "company_id": company_id},
        [{"product_name": product, "quantity": 10, "unit_price": 5}],
    )


def _invoice(conn, sale_id, number="INV-WSGI-1"):
    lines = [{"sale_item_id": r[0], "quantity": float(r[1])}
             for r in conn.execute(
                 "SELECT id, quantity FROM sale_items WHERE sale_id = %s "
                 "ORDER BY id", (sale_id,)).fetchall()]
    return salesmod.create_invoice(conn, sale_id, number, lines=lines,
                                   invoice_date="2026-01-15")["invoice_id"]


def _recipe(conn, company_id, name=NAME):
    sale_id = _register_product(conn, company_id)
    inv = _invoice(conn, sale_id)
    return recipesmod.add_recipe(conn, name, company_id, inv)


def _chemical(conn, name="Soda Ash"):
    row = conn.execute(
        "INSERT INTO chemicals (name, current_qty, unit) VALUES (%s, 500, 'KG')"
        " RETURNING id", (name,)).fetchone()
    conn.commit()
    return row[0]


def _recipe_id(conn, company_id):
    return conn.execute(
        "SELECT id FROM recipes WHERE company_id = %s", (company_id,)).fetchone()[0]


@pytest.fixture()
def encoded_path_host(monkeypatch):
    """Model Vercel: its runtime leaves PATH_INFO encoded (DECODE_PATH_INFO=1)."""
    import flask_app

    original = flask_app.app.wsgi_app
    monkeypatch.setenv("DECODE_PATH_INFO", "1")
    flask_app._apply_proxy_fix()
    yield
    flask_app.app.wsgi_app = original


# --------------------------------------------------------------------------- #
# the shim itself
# --------------------------------------------------------------------------- #
def _run_shim(path_info):
    from raas_tracker.wsgi_compat import DecodePathInfo

    seen = {}

    def app(environ, start_response):
        seen.update(environ)
        return [b"ok"]

    body = DecodePathInfo(app)({"PATH_INFO": path_info}, lambda *a: None)
    assert body == [b"ok"]
    return seen["PATH_INFO"]


def test_decodes_a_percent_encoded_path():
    assert _run_shim("/api/recipes/Production%20KNIT%20432") == \
        "/api/recipes/Production KNIT 432"


def test_decodes_utf8():
    assert _run_shim("/api/recipes/%C3%89prouvette") == "/api/recipes/Éprouvette"


def test_leaves_a_plain_path_untouched():
    assert _run_shim("/api/recipes/Batch-A") == "/api/recipes/Batch-A"


def test_leaves_an_invalid_escape_untouched():
    # A lone '%' is not an escape; unquote returns the input unchanged.
    assert _run_shim("/api/recipes/100% cotton") == "/api/recipes/100% cotton"


# --------------------------------------------------------------------------- #
# the live request shapes
# --------------------------------------------------------------------------- #
def test_put_resolves_a_percent_encoded_recipe_name(admin_client, db, encoded_path_host):
    """The Edit dialog's save: PUT /api/recipes/<encoded name>."""
    co = _company(db)
    _recipe(db, co)

    r = admin_client.put(ENC, json={
        "company_id": co, "name": "Production KNIT 500",
    }, environ_overrides={"PATH_INFO": ENC})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {"success": True, "name": "Production KNIT 500"}

    stored = db.execute(
        "SELECT name FROM recipes WHERE company_id = %s", (co,)).fetchone()[0]
    assert stored == "Production KNIT 500"
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'RECIPE_UPDATE'"
        " AND entity_id = %s", (_recipe_id(db, co),)).fetchone()[0] == 1


def test_detail_resolves_a_percent_encoded_recipe_name(admin_client, db, encoded_path_host):
    """The pre-by-id View request; still the fallback for old payloads."""
    co = _company(db)
    _recipe(db, co)

    r = admin_client.get(ENC + f"?company_id={co}",
                         environ_overrides={"PATH_INFO": ENC})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["recipe"]["name"] == NAME


def test_item_update_resolves_encoded_name_and_chemical(admin_client, db, encoded_path_host):
    """Both path segments are names: the recipe and the chemical."""
    co = _company(db)
    recipe = _recipe(db, co)
    _chemical(db, "Soda Ash")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT, "Soda Ash", 50)

    path = ENC + "/items/Soda%20Ash"
    r = admin_client.put(path, json={"percentage": 40, "company_id": co},
                         environ_overrides={"PATH_INFO": path})
    assert r.status_code == 200, r.get_json()
    assert float(db.execute(
        "SELECT ri.percentage FROM recipe_items ri"
        " JOIN recipe_products rp ON rp.id = ri.recipe_product_id"
        " JOIN recipes r ON r.id = rp.recipe_id"
        " JOIN chemicals c ON c.id = ri.chemical_id"
        " WHERE r.company_id = %s AND c.name = 'Soda Ash'", (co,)).fetchone()[0]) == 40


def test_produce_resolves_a_percent_encoded_recipe_name(admin_client, db, encoded_path_host):
    co = _company(db)
    recipe = _recipe(db, co)
    _chemical(db, "Soda Ash")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT, "Soda Ash", 40)

    path = ENC + "/produce"
    r = admin_client.post(path, json={
        "company_id": co,
        "outputs": [{"product_name": PRODUCT, "quantity": 2}],
    }, environ_overrides={"PATH_INFO": path})
    assert r.status_code == 201, r.get_json()
    assert db.execute(
        "SELECT COUNT(*) FROM production_runs WHERE recipe_id = %s",
        (_recipe_id(db, co),)).fetchone()[0] == 1


def test_gate_sees_the_decoded_public_path(client, db, encoded_path_host):
    """Proof of the mechanism: an encoded spelling is the same public route."""
    assert client.get("/api/auth/status").status_code == 200
    r = client.get("/api/auth/%73tatus",
                   environ_overrides={"PATH_INFO": "/api/auth/%73tatus"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["setup_needed"] is False


def test_encoded_host_decodes_a_literal_escape_exactly_once(admin_client, db,
                                                            encoded_path_host):
    """On Vercel the shim is the only decode: "Batch%20A" survives."""
    co = _company(db)
    _recipe(db, co, name="Batch%20A")

    path = "/api/recipes/Batch%2520A"
    r = admin_client.put(path, json={"company_id": co,
                                     "name": "Batch%20A v2"},
                         environ_overrides={"PATH_INFO": path})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["name"] == "Batch%20A v2"


def test_compliant_host_keeps_a_literal_escape_in_the_name(admin_client, db):
    """Why the shim is gated: here the server already decoded the path once."""
    co = _company(db)
    _recipe(db, co, name="Batch%20A")

    r = admin_client.put("/api/recipes/Batch%2520A",
                         json={"company_id": co, "name": "Batch%20A v2"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["name"] == "Batch%20A v2"


def test_encoded_traversal_stays_blocked(client, db, encoded_path_host):
    """Decoding must not turn an escape into a file server path."""
    for path in ("/%2e%2e%2frequirements.txt",
                 "/assets/..%2F..%2Frequirements.txt"):
        r = client.get(path, environ_overrides={"PATH_INFO": path})
        assert r.status_code == 404
        assert "psycopg" not in r.get_data(as_text=True)
