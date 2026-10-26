"""Gate integrity tests for Phase 3: Stage barriers + Invoice line freeze.

Phase 3-A: Stage barriers in move_sale_to_stage
Phase 3-B: Freeze invoice lines once invoice is booked/paid/shipped/void

These tests verify:
Phase 3-A:
1. lc_received -> shipment_ongoing requires no invoice (invoices are created
   in Shipment Ongoing)
2. shipment_ongoing -> payment_due requires every invoiced product has
   a company recipe AND an invoice-linked production run
3. Direct move on LC-linked sale to shipment_ongoing is refused (via_lc=False)

Phase 3-B:
4. create_invoice_item on booked invoice is rejected
5. create_invoice_item on paid invoice is rejected
6. create_invoice_item on shipped invoice is rejected
7. create_invoice_item on void invoice is rejected
8. create_invoice_item on planned invoice is allowed
9. create_invoice with explicit lines succeeds (new invoice is planned)
10. create_invoices_batch succeeds (new invoices are planned)
11. create_invoice without lines is refused (the auto-seed mode is gone)
"""

import pytest

from raas_tracker.sales import (
    add_sale,
    create_invoice,
    create_invoice_item,
    create_invoices_batch,
    move_sale_to_stage,
    advance_sale,
    validate_invoice_lines,
    book_invoice,
)
from raas_tracker.recipes import create_production_run, add_recipe_item
from raas_tracker import lcs as lcsmod


def _company(db, name="Test Co"):
    """Create a test company."""
    row = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (name,)
    ).fetchone()
    return row[0]


def _sale(db, company_id, product="Product A", qty=10, price=100):
    """Create a test sale at pi_issued stage."""
    return add_sale(db, {"pi_number": f"PI-{product}", "client_name": "Test Client", "company_id": company_id},
                    [{"product_name": product, "quantity": qty, "unit_price": price}])


def _lc(db, company_id, lc_number="LC-001"):
    """Create a test letter of credit."""
    return lcsmod.create_lc(db, company_id, lc_number)["id"]


def _attach_pi(db, lc_id, sale_ids):
    """Attach sale(s) to LC."""
    lcsmod.attach_pis(db, lc_id, sale_ids)


def _recipe(db, company_id, product_name):
    """Create a recipe for a product with a dummy ingredient."""
    db.execute(
        "INSERT INTO recipes (name, company_id, product_name) VALUES (%s, %s, %s)",
        (f"Recipe-{product_name}", company_id, product_name)
    )
    # Ensure Water chemical exists with sufficient stock
    db.execute(
        "INSERT INTO chemicals (name, unit, current_qty, reorder_level) VALUES ('Water', 'KG', 10000, 0) "
        "ON CONFLICT (name) DO UPDATE SET current_qty = GREATEST(chemicals.current_qty, 10000)"
    )
    # Add a dummy ingredient so production can run (percentage = 100%)
    add_recipe_item(db, company_id, f"Recipe-{product_name}", "Water", 100.0)
    db.commit()


def _invoice(db, sale_id, invoice_number="INV-001"):
    """Create an invoice for a sale with one baseline line. Returns invoice_id."""
    items = _get_sale_items(db, sale_id)
    result = create_invoice(
        db, sale_id, invoice_number,
        lines=[{"sale_item_id": items[0][0], "quantity": 1}],
        invoice_date="2026-01-15")
    return result["invoice_id"]


def _invoice_explicit(db, sale_id, invoice_number, lines):
    """Create an invoice with explicit lines. lines = [{"sale_item_id": int, "quantity": float}, ...]."""
    result = create_invoice(db, sale_id, invoice_number, lines=lines,
                            invoice_date="2026-01-15")
    return result["invoice_id"]


def _production_run(db, company_id, recipe_name, production_qty, invoice_ids):
    """Create a production run linked to invoices."""
    result = create_production_run(
        db, company_id, recipe_name, production_qty,
        invoice_ids=invoice_ids, atomic=True
    )
    return result


def _void_invoice(db, invoice_id):
    """Void an invoice."""
    db.execute("UPDATE invoices SET status = 'void' WHERE id = %s", (invoice_id,))
    db.commit()


def _get_sale_items(db, sale_id):
    """Get sale items for a sale."""
    return db.execute(
        "SELECT id, product_name FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)
    ).fetchall()


def test_move_to_shipment_ongoing_without_invoice_allowed(db):
    """Sale at lc_received with no invoice can start the shipment."""
    co = _company(db)
    sid = _sale(db, co)
    # Move to lc_received first
    move_sale_to_stage(db, sid, "lc_received")
    # No invoice is needed to enter shipment_ongoing.
    result = move_sale_to_stage(db, sid, "shipment_ongoing")
    assert result is True, "Should allow move without an invoice"
    # Stage must have advanced
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "shipment_ongoing"


def test_move_to_shipment_ongoing_with_invoice_allowed(db):
    """Sale at lc_received with invoice can move to shipment_ongoing."""
    co = _company(db)
    sid = _sale(db, co)
    move_sale_to_stage(db, sid, "lc_received")
    # Create an invoice
    _invoice(db, sid)
    # Move to shipment_ongoing should succeed
    result = move_sale_to_stage(db, sid, "shipment_ongoing")
    assert result is True, "Should allow move with invoice"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "shipment_ongoing"


def test_move_to_payment_due_without_production_rejected(db):
    """Sale at shipment_ongoing without production run cannot move to payment_due."""
    co = _company(db)
    sid = _sale(db, co)
    move_sale_to_stage(db, sid, "lc_received")
    _invoice(db, sid)
    move_sale_to_stage(db, sid, "shipment_ongoing")
    # No recipe or production run - should reject
    result = move_sale_to_stage(db, sid, "payment_due")
    assert result is False, "Should reject move without production"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "shipment_ongoing"


def test_move_to_payment_due_with_production_allowed(db):
    """Sale at shipment_ongoing with recipe and production run can move to payment_due."""
    co = _company(db)
    sid = _sale(db, co, product="Product X")
    move_sale_to_stage(db, sid, "lc_received")
    _invoice(db, sid)
    move_sale_to_stage(db, sid, "shipment_ongoing")
    # Add recipe with ingredient
    _recipe(db, co, "Product X")
    # Record production run linked to the invoice
    inv_row = db.execute("SELECT id FROM invoices WHERE sale_id = %s", (sid,)).fetchone()
    invoice_id = inv_row[0]
    _production_run(db, co, f"Recipe-Product X", 100, [invoice_id])
    # Now move to payment_due should succeed
    result = move_sale_to_stage(db, sid, "payment_due")
    assert result is True, "Should allow move with production"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "payment_due"


def test_direct_move_bypasses_lc_endpoint_rejected(db):
    """Direct POST /api/sales/<id>/move to shipment_ongoing on LC-linked sale is rejected."""
    co = _company(db)
    lc_id = _lc(db, co)
    sid = _sale(db, co)
    _attach_pi(db, lc_id, [sid])
    # Sale is now at lc_received via LC attach
    # Try direct move to shipment_ongoing (via_lc=False by default)
    result = move_sale_to_stage(db, sid, "shipment_ongoing", via_lc=False)
    assert result is False, "Direct move on LC-linked sale should be rejected"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "lc_received"


def test_direct_move_via_lc_true_allowed(db):
    """Move with via_lc=True on LC-linked sale is allowed (simulates LC endpoint)."""
    co = _company(db)
    lc_id = _lc(db, co)
    sid = _sale(db, co)
    _attach_pi(db, lc_id, [sid])
    # No invoice is needed for the shipment hop.
    result = move_sale_to_stage(db, sid, "shipment_ongoing", via_lc=True)
    assert result is True, "Move via LC should be allowed"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "shipment_ongoing"


def test_multiple_products_all_need_production(db):
    """All invoiced products must have recipe + production for payment_due."""
    co = _company(db)
    sid = _sale(db, co, product="Product A", qty=10, price=100)
    # Add second product to sale
    db.execute(
        "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) VALUES (%s, %s, %s, %s, %s)",
        (sid, "Product B", 5, 200, "KG")
    )
    db.commit()
    move_sale_to_stage(db, sid, "lc_received")
    # Get sale item IDs
    items = _get_sale_items(db, sid)
    item_a_id = next(i[0] for i in items if i[1] == "Product A")
    item_b_id = next(i[0] for i in items if i[1] == "Product B")
    # Create invoice with both products
    _invoice_explicit(db, sid, "INV-MULTI", [
        {"sale_item_id": item_a_id, "quantity": 10},
        {"sale_item_id": item_b_id, "quantity": 5},
    ])
    move_sale_to_stage(db, sid, "shipment_ongoing")
    # Add recipe only for Product A
    _recipe(db, co, "Product A")
    inv_row = db.execute("SELECT id FROM invoices WHERE sale_id = %s", (sid,)).fetchone()
    invoice_id = inv_row[0]
    _production_run(db, co, "Recipe-Product A", 100, [invoice_id])
    # Should reject - Product B missing recipe and production
    result = move_sale_to_stage(db, sid, "payment_due")
    assert result is False, "Should reject when any product lacks production"
    # Now add recipe and production for Product B
    _recipe(db, co, "Product B")
    _production_run(db, co, "Recipe-Product B", 50, [invoice_id])
    # Should now succeed
    result = move_sale_to_stage(db, sid, "payment_due")
    assert result is True, "Should allow when all products have production"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "payment_due"


def test_advance_sale_respects_barriers(db):
    """advance_sale should respect the remaining payment barrier."""
    co = _company(db)
    sid = _sale(db, co)
    move_sale_to_stage(db, sid, "lc_received")
    # No invoice is needed to start the shipment now.
    result = advance_sale(db, sid)
    assert result == "shipment_ongoing"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "shipment_ongoing"
    # Payment Due still requires production for the invoiced product. (The
    # zero-invoice Payment Due gate lands in Phase 2; this test pins the
    # existing production barrier, not the temporary vacuous pass.)
    _invoice(db, sid)
    result = advance_sale(db, sid)
    assert result is None, "advance_sale should return None when blocked at payment_due"
    stage = db.execute("SELECT stage FROM sales WHERE id = %s", (sid,)).fetchone()[0]
    assert stage == "shipment_ongoing"


# ============================================================
# Phase 3-B: Freeze invoice lines on book (3 writers)
# ============================================================

def _set_invoice_status(db, invoice_id, status):
    """Set invoice status directly (for testing freeze guards)."""
    db.execute("UPDATE invoices SET status = %s WHERE id = %s", (status, invoice_id))
    db.commit()


def _book_invoice(db, invoice_id):
    """Book an invoice by setting status directly (bypasses production check for testing)."""
    _set_invoice_status(db, invoice_id, "booked")


def _pay_invoice(db, invoice_id):
    """Mark an invoice as paid."""
    _set_invoice_status(db, invoice_id, "paid")


def _ship_invoice(db, invoice_id):
    """Mark an invoice as shipped."""
    _set_invoice_status(db, invoice_id, "shipped")


def test_create_invoice_item_on_booked_invoice_rejected(db):
    """create_invoice_item on a booked invoice raises ValueError."""
    co = _company(db)
    sid = _sale(db, co)
    inv_id = _invoice(db, sid, "INV-BOOKED")
    # Book the invoice
    _book_invoice(db, inv_id)
    # Get a sale_item_id to add a line
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    # Try to add an invoice line - should fail
    with pytest.raises(ValueError, match="cannot modify a booked invoice"):
        create_invoice_item(db, inv_id, sale_item_id, 5.0)


def test_create_invoice_item_on_paid_invoice_rejected(db):
    """create_invoice_item on a paid invoice raises ValueError."""
    co = _company(db)
    sid = _sale(db, co)
    inv_id = _invoice(db, sid, "INV-PAID")
    _pay_invoice(db, inv_id)
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    with pytest.raises(ValueError, match="cannot modify a paid invoice"):
        create_invoice_item(db, inv_id, sale_item_id, 5.0)


def test_create_invoice_item_on_shipped_invoice_rejected(db):
    """create_invoice_item on a shipped invoice raises ValueError."""
    co = _company(db)
    sid = _sale(db, co)
    inv_id = _invoice(db, sid, "INV-SHIPPED")
    _ship_invoice(db, inv_id)
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    with pytest.raises(ValueError, match="cannot modify a shipped invoice"):
        create_invoice_item(db, inv_id, sale_item_id, 5.0)


def test_create_invoice_item_on_void_invoice_rejected(db):
    """create_invoice_item on a void invoice raises ValueError."""
    co = _company(db)
    sid = _sale(db, co)
    inv_id = _invoice(db, sid, "INV-VOID")
    _void_invoice(db, inv_id)
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    with pytest.raises(ValueError, match="cannot modify a void invoice"):
        create_invoice_item(db, inv_id, sale_item_id, 5.0)


def test_create_invoice_item_on_planned_invoice_allowed(db):
    """create_invoice_item on a planned invoice succeeds."""
    co = _company(db)
    sid = _sale(db, co)
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    # Born with its baseline line (1 unit); quantities are uncapped.
    from raas_tracker.sales import create_invoice
    result = create_invoice(
        db, sid, "INV-PLANNED",
        lines=[{"sale_item_id": sale_item_id, "quantity": 1}],
        invoice_date="2026-01-15")
    inv_id = result["invoice_id"]
    line = create_invoice_item(db, inv_id, sale_item_id, 5.0)
    assert line["quantity"] == 5.0


def test_create_invoice_explicit_lines_allowed(db):
    """create_invoice with explicit lines succeeds (new invoice is planned)."""
    co = _company(db)
    sid = _sale(db, co)
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    # Should succeed - new invoice starts as 'planned'
    result = create_invoice(db, sid, "INV-EXPLICIT",
                            lines=[{"sale_item_id": sale_item_id, "quantity": 5}],
                            invoice_date="2026-01-15")
    assert result["status"] == "planned"
    assert result["amount"] > 0


def test_create_invoices_batch_allowed(db):
    """create_invoices_batch succeeds (new invoices are planned)."""
    co = _company(db)
    sid = _sale(db, co)
    items = _get_sale_items(db, sid)
    sale_item_id = items[0][0]
    # Should succeed - new invoices start as 'planned'
    result = create_invoices_batch(db, sid, [
        {"invoice_number": "INV-BATCH-1", "invoice_date": "2026-01-15",
         "lines": [{"sale_item_id": sale_item_id, "quantity": 3}]},
        {"invoice_number": "INV-BATCH-2", "invoice_date": "2026-01-16",
         "lines": [{"sale_item_id": sale_item_id, "quantity": 4}]},
    ])
    assert len(result["invoices"]) == 2
    for inv in result["invoices"]:
        assert inv["status"] == "planned"


def test_create_invoice_without_lines_refused(db):
    """The auto-seed mode is gone; create_invoice requires explicit lines."""
    from raas_tracker._exceptions import ServiceError

    co = _company(db)
    sid = _sale(db, co)
    with pytest.raises(ServiceError, match="lines are required"):
        create_invoice(db, sid, "INV-SEED", lines=None,
                       invoice_date="2026-01-15")
    db.rollback()
    assert db.execute(
        "SELECT COUNT(*) FROM invoices WHERE sale_id = %s", (sid,)).fetchone()[0] == 0


def test_assert_invoice_mutable_helper(db):
    """Test the _assert_invoice_mutable helper directly."""
    from raas_tracker.sales import _assert_invoice_mutable
    
    co = _company(db)
    sid = _sale(db, co)
    inv_id = _invoice(db, sid, "INV-HELPER")
    
    # Planned - should pass
    _assert_invoice_mutable(db, inv_id)
    
    # Booked
    _set_invoice_status(db, inv_id, "booked")
    with pytest.raises(ValueError, match="cannot modify a booked invoice"):
        _assert_invoice_mutable(db, inv_id)
    
    # Paid
    _set_invoice_status(db, inv_id, "paid")
    with pytest.raises(ValueError, match="cannot modify a paid invoice"):
        _assert_invoice_mutable(db, inv_id)
    
    # Shipped
    _set_invoice_status(db, inv_id, "shipped")
    with pytest.raises(ValueError, match="cannot modify a shipped invoice"):
        _assert_invoice_mutable(db, inv_id)
    
    # Void
    _set_invoice_status(db, inv_id, "void")
    with pytest.raises(ValueError, match="cannot modify a void invoice"):
        _assert_invoice_mutable(db, inv_id)