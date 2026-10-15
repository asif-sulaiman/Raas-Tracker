"""Over-invoice tolerance tests for Phase 3-C: 5% global tolerance across 3 writers.

Rule: For each PI line, sum of invoice line quantities <= PI line quantity * 1.05
(5% ceiling). The 5% is a global constant, not per-LC, not per-product.

Three writers must share one helper:
1. create_invoice_item (single line)
2. create_invoice with explicit lines
3. create_invoices_batch (N invoices x M lines)
"""

import pytest
from decimal import Decimal

from raas_tracker.sales import (
    add_sale,
    create_invoice,
    create_invoice_item,
    create_invoices_batch,
    check_invoice_tolerance,
    OVER_INVOICE_TOLERANCE,
)


def _company(db, name="Test Co"):
    """Create a test company."""
    row = db.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (name,)
    ).fetchone()
    return row[0]


def _sale(db, company_id, product="Product A", qty=100, price=100):
    """Create a test sale at pi_issued stage."""
    return add_sale(db, {"pi_number": f"PI-{product}", "client_name": "Test Client", "company_id": company_id},
                    [{"product_name": product, "quantity": qty, "unit_price": price}])


def _invoice(db, sale_id, invoice_number="INV-001", seed_lines=False):
    """Create an invoice for a sale. Returns invoice_id.
    
    By default, does NOT auto-seed lines (seed_lines=False) so tests can
    control exact line quantities for tolerance testing.
    """
    result = create_invoice(db, sale_id, invoice_number, seed_lines=seed_lines)
    return result["invoice_id"]


def _get_sale_items(db, sale_id):
    """Get sale items for a sale."""
    return db.execute(
        "SELECT id, product_name, quantity FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)
    ).fetchall()


class TestInvoiceTolerance:
    """Tests for 5% over-invoice tolerance enforcement."""

    def test_create_invoice_item_over_tolerance_rejected(self, db):
        """create_invoice_item with quantity > 1.05 * PI qty is rejected."""
        co = _company(db)
        # PI qty = 100, ceiling = 105
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        inv_id = _invoice(db, sid, "INV-1", seed_lines=False)
        
        # Try to add line exceeding ceiling (106 > 105)
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            create_invoice_item(db, inv_id, sale_item_id, float(ceiling) + 1)

    def test_create_invoice_item_at_ceiling_accepted(self, db):
        """create_invoice_item with quantity == ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        inv_id = _invoice(db, sid, "INV-2", seed_lines=False)
        
        # Add line exactly at ceiling (105 == 105)
        line = create_invoice_item(db, inv_id, sale_item_id, float(ceiling))
        assert line["quantity"] == float(ceiling)

    def test_create_invoice_item_under_tolerance_accepted(self, db):
        """create_invoice_item with quantity < ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        
        inv_id = _invoice(db, sid, "INV-3", seed_lines=False)
        
        # Add line under ceiling (50 < 105)
        line = create_invoice_item(db, inv_id, sale_item_id, 50.0)
        assert line["quantity"] == 50.0

    def test_create_invoice_explicit_over_tolerance_rejected(self, db):
        """create_invoice with explicit lines over ceiling is rejected."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # Try to create invoice with line exceeding ceiling
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            create_invoice(db, sid, "INV-OVER", lines=[
                {"sale_item_id": sale_item_id, "quantity": float(ceiling) + 1}
            ], seed_lines=False)

    def test_create_invoice_explicit_at_ceiling_accepted(self, db):
        """create_invoice with explicit lines at ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        result = create_invoice(db, sid, "INV-AT-CEILING", lines=[
            {"sale_item_id": sale_item_id, "quantity": float(ceiling)}
        ], seed_lines=False)
        assert result["status"] == "planned"

    def test_create_invoice_explicit_under_tolerance_accepted(self, db):
        """create_invoice with explicit lines under ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        
        result = create_invoice(db, sid, "INV-UNDER", lines=[
            {"sale_item_id": sale_item_id, "quantity": 50.0}
        ], seed_lines=False)
        assert result["status"] == "planned"

    def test_create_invoice_batch_over_tolerance_rejected(self, db):
        """create_invoices_batch with one line over ceiling is rejected."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # Batch with one invoice having line over ceiling
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            create_invoices_batch(db, sid, [
                {"invoice_number": "INV-BATCH-1", "lines": [
                    {"sale_item_id": sale_item_id, "quantity": float(ceiling) + 1}
                ]}
            ])

    def test_create_invoice_batch_partial_over_tolerance_rejected(self, db):
        """create_invoices_batch with mix of lines, one over ceiling, is rejected."""
        co = _company(db)
        # Two PI lines: A=100 (ceiling 105), B=50 (ceiling 52.5)
        sid = _sale(db, co, product="Product A", qty=100, price=100)
        db.execute(
            "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) VALUES (%s, %s, %s, %s, %s)",
            (sid, "Product B", 50, 200, "KG")
        )
        db.commit()
        
        items = _get_sale_items(db, sid)
        item_a_id = next(i[0] for i in items if i[1] == "Product A")
        item_b_id = next(i[0] for i in items if i[1] == "Product B")
        pi_qty_a = Decimal(str(next(i[2] for i in items if i[1] == "Product A")))
        ceiling_a = (pi_qty_a * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # Batch: first invoice OK, second invoice has line over ceiling for A
        with pytest.raises(ValueError, match=f"exceeds {ceiling_a:g}"):
            create_invoices_batch(db, sid, [
                {"invoice_number": "INV-BATCH-OK", "lines": [
                    {"sale_item_id": item_a_id, "quantity": 50.0}
                ]},
                {"invoice_number": "INV-BATCH-BAD", "lines": [
                    {"sale_item_id": item_a_id, "quantity": float(ceiling_a) + 1}
                ]}
            ])

    def test_create_invoice_batch_at_ceiling_accepted(self, db):
        """create_invoices_batch with lines at ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        result = create_invoices_batch(db, sid, [
            {"invoice_number": "INV-BATCH-1", "lines": [
                {"sale_item_id": sale_item_id, "quantity": float(ceiling) / 2}
            ]},
            {"invoice_number": "INV-BATCH-2", "lines": [
                {"sale_item_id": sale_item_id, "quantity": float(ceiling) / 2}
            ]}
        ])
        assert len(result["invoices"]) == 2
        for inv in result["invoices"]:
            assert inv["status"] == "planned"

    def test_create_invoice_batch_multiple_lines_aggregate_check(self, db):
        """create_invoices_batch aggregates quantities across invoices for same PI line."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # Two invoices, each 60 units = 120 total > 105 ceiling
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            create_invoices_batch(db, sid, [
                {"invoice_number": "INV-BATCH-1", "lines": [
                    {"sale_item_id": sale_item_id, "quantity": 60.0}
                ]},
                {"invoice_number": "INV-BATCH-2", "lines": [
                    {"sale_item_id": sale_item_id, "quantity": 60.0}
                ]}
            ])

    def test_create_invoice_item_aggregate_check(self, db):
        """create_invoice_item aggregates with existing invoice lines."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        inv_id = _invoice(db, sid, "INV-EXISTING", seed_lines=False)
        # Add first line: 60 units
        create_invoice_item(db, inv_id, sale_item_id, 60.0)
        # Try to add second line: 50 units (total 110 > 105)
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            create_invoice_item(db, inv_id, sale_item_id, 50.0)


class TestInvoiceToleranceHelper:
    """Tests for the check_invoice_tolerance helper function directly."""

    def test_check_invoice_tolerance_helper_over_ceiling(self, db):
        """check_invoice_tolerance raises ValueError when over ceiling."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        lines = [{"sale_item_id": sale_item_id, "quantity": float(ceiling) + 1}]
        
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            check_invoice_tolerance(db, sid, lines)

    def test_check_invoice_tolerance_helper_at_ceiling(self, db):
        """check_invoice_tolerance passes at ceiling."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        lines = [{"sale_item_id": sale_item_id, "quantity": float(ceiling)}]
        # Should not raise
        check_invoice_tolerance(db, sid, lines)

    def test_check_invoice_tolerance_helper_under_ceiling(self, db):
        """check_invoice_tolerance passes under ceiling."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        
        lines = [{"sale_item_id": sale_item_id, "quantity": 50.0}]
        # Should not raise
        check_invoice_tolerance(db, sid, lines)

    def test_check_invoice_tolerance_multiple_items(self, db):
        """check_invoice_tolerance checks each PI line independently."""
        co = _company(db)
        sid = _sale(db, co, product="Product A", qty=100, price=100)
        db.execute(
            "INSERT INTO sale_items (sale_id, product_name, quantity, unit_price, unit) VALUES (%s, %s, %s, %s, %s)",
            (sid, "Product B", 50, 200, "KG")
        )
        db.commit()
        
        items = _get_sale_items(db, sid)
        item_a_id = next(i[0] for i in items if i[1] == "Product A")
        item_b_id = next(i[0] for i in items if i[1] == "Product B")
        pi_qty_a = Decimal(str(next(i[2] for i in items if i[1] == "Product A")))
        ceiling_a = (pi_qty_a * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # Product A over ceiling, Product B under
        lines = [
            {"sale_item_id": item_a_id, "quantity": float(ceiling_a) + 1},
            {"sale_item_id": item_b_id, "quantity": 10.0}
        ]
        with pytest.raises(ValueError, match=f"exceeds {ceiling_a:g}"):
            check_invoice_tolerance(db, sid, lines)


class TestInvoiceBatchLineModel:
    """Tests for InvoiceBatchLineIn Pydantic model validation."""

    def test_invoice_batch_line_model_gt_zero(self):
        """InvoiceBatchLineIn rejects quantity <= 0."""
        from flask_app import InvoiceBatchLineIn
        from pydantic import ValidationError
        
        # quantity = 0 should fail
        with pytest.raises(ValidationError):
            InvoiceBatchLineIn.model_validate({"sale_item_id": 1, "quantity": 0})
        
        # quantity = -1 should fail
        with pytest.raises(ValidationError):
            InvoiceBatchLineIn.model_validate({"sale_item_id": 1, "quantity": -1})
        
        # quantity = 0.5 should pass
        line = InvoiceBatchLineIn.model_validate({"sale_item_id": 1, "quantity": 0.5})
        assert line.quantity == 0.5


class TestGlobalConstant:
    """Test that OVER_INVOICE_TOLERANCE is the correct global constant."""

    def test_over_invoice_tolerance_constant(self):
        """OVER_INVOICE_TOLERANCE should be Decimal('1.05')."""
        assert OVER_INVOICE_TOLERANCE == Decimal("1.05")


class TestMultipleInvoicesSameSaleItem:
    """Tests for aggregate tolerance across multiple invoices on same sale item."""

    def test_multiple_invoices_aggregate_over_ceiling(self, db):
        """Multiple invoices on same sale item: sum must not exceed ceiling."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # First invoice: 60 units
        inv1_id = _invoice(db, sid, "INV-1", seed_lines=False)
        create_invoice_item(db, inv1_id, sale_item_id, 60.0)
        
        # Second invoice: 50 units (total 110 > 105) should be rejected
        inv2_id = _invoice(db, sid, "INV-2", seed_lines=False)
        with pytest.raises(ValueError, match=f"exceeds {ceiling:g}"):
            create_invoice_item(db, inv2_id, sale_item_id, 50.0)

    def test_multiple_invoices_aggregate_at_ceiling(self, db):
        """Multiple invoices at exactly ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        pi_qty = Decimal(str(items[0][2]))
        ceiling = (pi_qty * Decimal("1.05")).quantize(Decimal("0.000001"))
        
        # First invoice: 50 units
        inv1_id = _invoice(db, sid, "INV-1", seed_lines=False)
        create_invoice_item(db, inv1_id, sale_item_id, 50.0)
        
        # Second invoice: 55 units (total 105 == ceiling) should be accepted
        inv2_id = _invoice(db, sid, "INV-2", seed_lines=False)
        line = create_invoice_item(db, inv2_id, sale_item_id, 55.0)
        assert line["quantity"] == 55.0