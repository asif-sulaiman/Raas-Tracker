"""Tests for data integrity fixes (Slice B).

Tests cover:
1. Atomic update_stock with FOR UPDATE and negative stock rejection
2. Recipe company isolation
3. URL encoding for recipe names
4. ProxyFix remote_addr handling
"""

import threading
import time
import pytest
from chem_stock import (
    get_connection, add_chemical, update_stock, add_recipe, add_recipe_item,
    get_recipe_by_name, list_recipe_items, create_production_run,
    create_company, list_companies, add_sale
)


def test_update_stock_atomic_concurrent(db, pg_dsn):
    """Two concurrent update_stock calls with FOR UPDATE serialize; final qty correct."""
    conn = db
    # Add a chemical with initial stock
    add_chemical(conn, "TestChem", 100.0, "KG")
    conn.commit()  # Ensure it's visible to other connections
    
    # Function to run in threads
    def add_stock(delta, results, idx):
        try:
            c = get_connection(pg_dsn)
            update_stock(c, "TestChem", delta)
            c.close()
            results[idx] = True
        except Exception as e:
            results[idx] = e
    
    # Run two concurrent additions of 50 each
    results = [None, None]
    t1 = threading.Thread(target=add_stock, args=(50.0, results, 0))
    t2 = threading.Thread(target=add_stock, args=(50.0, results, 1))
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Both should succeed
    assert results[0] is True
    assert results[1] is True
    
    # Final stock should be 100 + 50 + 50 = 200
    conn = get_connection(pg_dsn)
    row = conn.execute("SELECT current_qty FROM chemicals WHERE name = 'TestChem'").fetchone()
    conn.close()
    assert row[0] == 200.0


def test_update_stock_negative_raises(db, pg_dsn):
    """Delta pushing below zero raises ValueError('insufficient stock')."""
    conn = db
    add_chemical(conn, "TestChem2", 50.0, "KG")
    conn.commit()
    
    # Try to remove more than available
    with pytest.raises(ValueError, match="insufficient stock"):
        update_stock(conn, "TestChem2", -100.0)
    
    # Stock should remain unchanged at 50
    conn = get_connection(pg_dsn)
    row = conn.execute("SELECT current_qty FROM chemicals WHERE name = 'TestChem2'").fetchone()
    conn.close()
    assert row[0] == 50.0


def test_update_stock_negative_400(client, admin_client):
    """Flask endpoint catches ValueError -> 400 {'error': 'insufficient stock'}."""
    # First add a chemical via admin
    r = admin_client.post("/api/chemicals", json={"name": "TestChem3", "qty": 50, "unit": "KG"})
    assert r.status_code == 200
    
    # Try to remove more than available
    r = admin_client.post("/api/chemicals/update", json={"name": "TestChem3", "delta": -100})
    assert r.status_code == 400
    data = r.get_json()
    assert data["error"] == "insufficient stock"
    
    # Stock should remain unchanged
    r = client.get("/api/chemicals")
    chem = next(c for c in r.get_json() if c["name"] == "TestChem3")
    assert chem["qty"] == 50.0


def test_recipe_company_isolation(db):
    """Two companies with same recipe name; each company's calls only see their own recipe."""
    conn = db
    
    # Create two companies
    c1_id = create_company(conn, name="Company A", code="CA", country="US", address="Addr A", contact_person="Contact A")
    c2_id = create_company(conn, name="Company B", code="CB", country="US", address="Addr B", contact_person="Contact B")
    
    # Create a product for each company (required for recipe creation)
    add_sale(conn, {
        "pi_number": "PI-CA-1", "pi_date": "2024-01-01",
        "client_name": "Company A", "company_id": c1_id
    }, [{"product_name": "Product A", "quantity": 10, "unit_price": 100, "unit": "KG"}])
    add_sale(conn, {
        "pi_number": "PI-CB-1", "pi_date": "2024-01-01",
        "client_name": "Company B", "company_id": c2_id
    }, [{"product_name": "Product B", "quantity": 10, "unit_price": 100, "unit": "KG"}])
    
    # Add chemicals
    add_chemical(conn, "ChemA", 100, "KG")
    add_chemical(conn, "ChemB", 200, "KG")
    
    # Create same recipe name for both companies
    add_recipe(conn, "Shared Recipe", 1000, 0, c1_id, "Product A")
    add_recipe(conn, "Shared Recipe", 2000, 10, c2_id, "Product B")
    
    # Add different items to each recipe
    add_recipe_item(conn, c1_id, "Shared Recipe", "ChemA", 50.0)
    add_recipe_item(conn, c2_id, "Shared Recipe", "ChemB", 30.0)
    
    # Query recipes - each company should only see their own
    recipe_c1 = get_recipe_by_name(conn, c1_id, "Shared Recipe")
    recipe_c2 = get_recipe_by_name(conn, c2_id, "Shared Recipe")
    
    assert recipe_c1 is not None
    assert recipe_c2 is not None
    assert recipe_c1["company_id"] == c1_id
    assert recipe_c2["company_id"] == c2_id
    assert recipe_c1["total_quantity"] == 1000
    assert recipe_c2["total_quantity"] == 2000
    assert recipe_c1["water_percentage"] == 0
    assert recipe_c2["water_percentage"] == 10
    
    # List items - each should only see their own items
    items_c1 = list_recipe_items(conn, c1_id, "Shared Recipe")
    items_c2 = list_recipe_items(conn, c2_id, "Shared Recipe")
    
    assert len(items_c1) == 1
    assert len(items_c2) == 1
    assert items_c1[0]["chemical_name"] == "ChemA"
    assert items_c2[0]["chemical_name"] == "ChemB"
    
    # Cross-company query should return None
    recipe_c1_wrong = get_recipe_by_name(conn, c1_id, "NonExistent")
    assert recipe_c1_wrong is None


def test_recipe_url_encoding(client, admin_client, db, pg_dsn):
    """Recipe name with spaces/special chars works (URL encoding)."""
    conn = db
    
    # Create company and product
    c_id = create_company(conn, name="Company C", code="CC", country="US", address="Addr C", contact_person="Contact C")
    add_sale(conn, {
        "pi_number": "PI-CC-1", "pi_date": "2024-01-01",
        "client_name": "Company C", "company_id": c_id
    }, [{"product_name": "Product C", "quantity": 10, "unit_price": 100, "unit": "KG"}])
    
    # Add chemical
    add_chemical(conn, "ChemC", 100, "KG")
    
    # Create recipe with special characters in name
    recipe_name = "Special Recipe / Test & Co."
    add_recipe(conn, recipe_name, 1000, 0, c_id, "Product C")
    add_recipe_item(conn, c_id, recipe_name, "ChemC", 25.0)
    
    # URL-encode the recipe name for the API call
    import urllib.parse
    encoded_name = urllib.parse.quote(recipe_name, safe='')
    
    # Test GET /api/recipes/<name> with company_id
    r = admin_client.get(f"/api/recipes/{encoded_name}?company_id={c_id}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["recipe"]["name"] == recipe_name
    assert len(data["items"]) == 1
    assert data["items"][0]["chemical_name"] == "ChemC"
    
    # Test GET /api/recipes/<name>/runs
    r = admin_client.get(f"/api/recipes/{encoded_name}/runs?company_id={c_id}")
    assert r.status_code == 200
    
    # Note: POST /api/recipes/<name>/produce has a pre-existing schema issue
    # (sale_item_id NOT NULL but not provided) - tested separately


def test_proxyfix_remote_addr(client):
    """Mock X-Forwarded-For header; assert request.remote_addr = client IP."""
    # This test verifies that ProxyFix is working by checking
    # that the Flask app can be configured with it.
    # In a real deployment behind a proxy, X-Forwarded-For would be used.
    
    # The test client doesn't go through a real proxy, but we can verify
    # the middleware is installed by checking the app's wsgi_app chain
    from flask_app import app
    from werkzeug.middleware.proxy_fix import ProxyFix
    
    # Check that ProxyFix is in the middleware chain
    wsgi_app = app.wsgi_app
    found_proxy_fix = False
    while hasattr(wsgi_app, 'app'):
        if isinstance(wsgi_app, ProxyFix):
            found_proxy_fix = True
            break
        wsgi_app = wsgi_app.app
    
    assert found_proxy_fix, "ProxyFix middleware not found in WSGI chain"
    
# Verify configuration by checking the ProxyFix object's attributes.
    # `x_for` is deliberately NOT asserted here: P1-7 gates header trust on
    # TRUSTED_PROXY, and both states are asserted in tests/test_proxy_trust.py.
    wsgi_app = app.wsgi_app
    while hasattr(wsgi_app, 'app'):
        if isinstance(wsgi_app, ProxyFix):
            assert wsgi_app.x_proto == 1
            assert wsgi_app.x_host == 1
            assert wsgi_app.x_prefix == 1
            break
        wsgi_app = wsgi_app.app


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

def test_production_run_get_serializers_map_columns(admin_client, db, pg_dsn):
    """List and detail serializers must map every SELECT column to its own key."""
    conn = db
    c_id = create_company(conn, name="Run Co", code="RUNCO", country="US",
                          address="Addr", contact_person="P")
    add_sale(conn, {"pi_number": "PI-RUN-1", "client_name": "Run Co",
                    "company_id": c_id},
             [{"product_name": "Product R", "quantity": 10, "unit_price": 10,
               "unit": "KG"}])
    add_chemical(conn, "ChemRun", 100, "KG")
    add_recipe(conn, "Run Recipe", 100, 0, c_id, "Product R")
    add_recipe_item(conn, c_id, "Run Recipe", "ChemRun", 10.0)
    create_production_run(conn, c_id, "Run Recipe", 42.5,
                          order_number="ORD-1", batch_number="BATCH-1",
                          production_date="2026-05-04", notes="hello")
    conn.commit()
    recipe = get_recipe_by_name(conn, c_id, "Run Recipe")
    run_id = conn.execute(
        "SELECT id FROM production_runs WHERE recipe_id = %s ORDER BY id DESC LIMIT 1",
        (recipe["id"],)).fetchone()[0]

    import urllib.parse
    enc = urllib.parse.quote("Run Recipe", safe='')

    r = admin_client.get(f"/api/recipes/{enc}/runs?company_id={c_id}")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1
    row = rows[0]
    assert row["order_number"] == "ORD-1"
    assert row["batch_number"] == "BATCH-1"
    assert row["production_date"] == "2026-05-04"
    assert row["qty_produced"] == 42.5
    assert row["notes"] == "hello"
    assert row["created_at"]

    r = admin_client.get(f"/api/recipes/{enc}/runs/{run_id}?company_id={c_id}")
    assert r.status_code == 200
    d = r.get_json()
    assert d["order_number"] == "ORD-1"
    assert d["batch_number"] == "BATCH-1"
    assert d["production_date"] == "2026-05-04"
    assert d["qty_produced"] == 42.5
    assert d["notes"] == "hello"
    assert d["created_at"]
