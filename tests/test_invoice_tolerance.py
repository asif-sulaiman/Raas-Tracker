"""Over-invoice tolerance tests for Phase 3-C: 5% global tolerance across 3 writers.

Rule: For each PI line, sum of invoice line quantities <= PI line quantity * 1.05
(5% ceiling). The 5% is a global constant, not per-LC, not per-product.

Three writers must share one helper:
1. create_invoice_item (single line)
2. create_invoice with explicit lines
3. create_invoices_batch (N invoices x M lines)
"""

import threading
import time
from decimal import Decimal

import psycopg
import pytest

from chem_stock import get_connection
from raas_tracker.sales import (
    add_sale,
    add_sale_item,
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


def _invoice(db, sale_id, invoice_number="INV-001", lines=None,
             invoice_date="2026-01-15"):
    """Create an invoice for a sale. Returns invoice_id.

    An invoice is always born with >= 1 explicit line now (the header-only
    seam is gone), so by default a single baseline unit of the first PI line
    is used and the tests add the quantities under test via
    create_invoice_item under the same 5% aggregate ceiling.
    """
    if lines is None:
        item = _get_sale_items(db, sale_id)[0]
        lines = [{"sale_item_id": item[0], "quantity": 1}]
    result = create_invoice(db, sale_id, invoice_number, lines=lines,
                            invoice_date=invoice_date)
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
        
        inv_id = _invoice(db, sid, "INV-1")
        
        # Baseline 1 + 106 = 107 > 105 ceiling
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
        
        inv_id = _invoice(db, sid, "INV-2")
        
        # Baseline 1 + 104 = 105 exactly at the ceiling
        line = create_invoice_item(db, inv_id, sale_item_id, float(ceiling) - 1)
        assert line["quantity"] == float(ceiling) - 1

    def test_create_invoice_item_under_tolerance_accepted(self, db):
        """create_invoice_item with quantity < ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        
        inv_id = _invoice(db, sid, "INV-3")
        
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
            ], invoice_date="2026-01-15")

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
        ], invoice_date="2026-01-15")
        assert result["status"] == "planned"

    def test_create_invoice_explicit_under_tolerance_accepted(self, db):
        """create_invoice with explicit lines under ceiling is accepted."""
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        items = _get_sale_items(db, sid)
        sale_item_id = items[0][0]
        
        result = create_invoice(db, sid, "INV-UNDER", lines=[
            {"sale_item_id": sale_item_id, "quantity": 50.0}
        ], invoice_date="2026-01-15")
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
                {"invoice_number": "INV-BATCH-1", "invoice_date": "2026-01-15",
                 "lines": [
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
                {"invoice_number": "INV-BATCH-OK", "invoice_date": "2026-01-15",
                 "lines": [
                    {"sale_item_id": item_a_id, "quantity": 50.0}
                ]},
                {"invoice_number": "INV-BATCH-BAD", "invoice_date": "2026-01-15",
                 "lines": [
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
            {"invoice_number": "INV-BATCH-1", "invoice_date": "2026-01-15",
             "lines": [
                {"sale_item_id": sale_item_id, "quantity": float(ceiling) / 2}
            ]},
            {"invoice_number": "INV-BATCH-2", "invoice_date": "2026-01-15",
             "lines": [
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
                {"invoice_number": "INV-BATCH-1", "invoice_date": "2026-01-15",
                 "lines": [
                    {"sale_item_id": sale_item_id, "quantity": 60.0}
                ]},
                {"invoice_number": "INV-BATCH-2", "invoice_date": "2026-01-15",
                 "lines": [
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
        
        inv_id = _invoice(db, sid, "INV-EXISTING")
        # Add first line: 60 units
        create_invoice_item(db, inv_id, sale_item_id, 60.0)
        # Try to add second line: 50 units (baseline 1 + 60 + 50 = 111 > 105)
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
        
        # First invoice: baseline 1 + 60 = 61
        inv1_id = _invoice(db, sid, "INV-1")
        create_invoice_item(db, inv1_id, sale_item_id, 60.0)
        
        # Second invoice: 50 more (61 + 1 + 50 = 112 > 105) should be rejected
        inv2_id = _invoice(db, sid, "INV-2")
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
        
        # First invoice: baseline 1 + 50 = 51
        inv1_id = _invoice(db, sid, "INV-1")
        create_invoice_item(db, inv1_id, sale_item_id, 50.0)
        
        # Second invoice: baseline 1 + 53 = 54; grand total 105 == ceiling
        inv2_id = _invoice(db, sid, "INV-2")
        line = create_invoice_item(db, inv2_id, sale_item_id, 53.0)
        assert line["quantity"] == 53.0


class TestWriterLockDiscipline:
    """Each writer must hold the PI line lock *before* it decides tolerance.

    The 5% ceiling is only sound if a second writer cannot read the same
    pre-existing total while the first is still mid-flight. Every writer
    therefore takes ``sale_items ... FOR UPDATE`` and holds it until commit:

    - ``create_invoice``        -> ``validate_invoice_lines`` locks each line
      (``sales.py:1714-1717``), then checks tolerance (``:1841``)
    - ``create_invoices_batch`` -> locks every PI line of the sale up front
      (``sales.py:1933-1935``), then checks tolerance (``:1985``)
    - ``create_invoice_item``   -> locks its single PI line (``sales.py:2518-2521``),
      then checks tolerance (``:2529``)

    These are characterisation tests: the locks already exist (the batch lock
    dates from 7a7ae73), so they pass on the current code. The batch lock was
    mutation-checked by deleting ``sales.py:1933-1935`` and observing both
    tests fail; see each test for what it does and does not pin.
    """

    @staticmethod
    def _hold_pi_line(pg_dsn, sale_item_id):
        """A second connection holding the PI line lock, like a writer mid-flight."""
        conn = get_connection(pg_dsn)
        conn.execute("SELECT id FROM sale_items WHERE id = %s FOR UPDATE",
                     (sale_item_id,))
        return conn

    @staticmethod
    def _run_locked_out(pg_dsn, call, timeout_ms=300):
        """Run ``call(conn)`` on a thread with a short ``lock_timeout``.

        Returns the exception the writer raised. A writer that takes the PI
        line lock blocks and trips ``lock_timeout``; one that does not returns
        normally, and the caller's assertion fails.

        ``lock_timeout`` is set transaction-locally (``set_config(..., true)``)
        because the pool has no ``reset=`` callback (``db.py:202-208``) and
        ``_PooledConnection.close()`` commits instead of closing
        (``db.py:55-72``). A session-scoped setting would therefore leak onto
        the next test that reuses this physical connection, and a plain
        ``close()`` would commit a half-written probe.
        """
        errors = []

        def _target():
            conn = get_connection(pg_dsn)
            try:
                conn.execute("SELECT set_config('lock_timeout', %s, true)",
                             (f"{timeout_ms}ms",))
                call(conn)
            except Exception as e:          # noqa: BLE001 - asserted by the caller
                errors.append(e)
            finally:
                conn.rollback()
                conn.close()

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive(), "writer never returned"
        return errors

    def test_batch_locks_every_pi_line_of_the_sale(self, db, pg_dsn):
        """A batch invoicing one product still waits on a lock held on another.

        Probe design matters here. Any writer that eventually INSERTs an
        ``invoice_items`` row blocks on the referenced ``sale_items`` row
        anyway, because the foreign key takes ``FOR KEY SHARE`` and that
        conflicts with ``FOR UPDATE``. Holding the lock on a *different* PI
        line of the same sale removes that confound, so a pass can only come
        from ``create_invoices_batch`` locking the whole sale up front
        (``sales.py:1933-1935``) rather than just the lines it bills.
        """
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        add_sale_item(db, sid, "Product B", 100, 100)
        first, second = _get_sale_items(db, sid)[:2]

        # The batch bills ONLY the second PI line.
        batch = [{"invoice_number": "INV-BATCH", "invoice_date": "2026-01-15",
                  "lines": [{"sale_item_id": second[0], "quantity": 5.0}]}]

        holder = self._hold_pi_line(pg_dsn, first[0])
        try:
            errors = self._run_locked_out(
                pg_dsn,
                lambda conn: create_invoices_batch(conn, sid, batch),
            )
        finally:
            holder.rollback()
            holder.close()

        assert len(errors) == 1, "batch billed a line the sale-level lock still holds"
        assert isinstance(errors[0], psycopg.errors.LockNotAvailable), errors[0]

    def test_add_line_and_batch_cannot_jointly_exceed_the_ceiling(self, db, pg_dsn):
        """The loser re-reads the winner's rows, so the two cannot jointly exceed 105.

        Both writers are parked on the same PI line lock, so whichever loses
        cannot evaluate tolerance until the winner commits. 60 + 60 against a
        105 ceiling therefore admits exactly one, in either grant order.

        This is an *outcome* test, not a deterministic mutation detector for
        the add-line lock. Delete that lock and whether this fails depends on
        which request is queued first on the row: if the unlocked writer wins
        the queue it commits, the other correctly refuses, and the assertion
        still holds. ``test_batch_locks_every_pi_line_of_the_sale`` is the
        deterministic detector, because it bills a *different* PI line and so
        removes the foreign-key confound.
        """
        co = _company(db)
        sid = _sale(db, co, qty=100, price=100)
        sale_item_id = _get_sale_items(db, sid)[0][0]
        target_invoice = _invoice(db, sid, "INV-A")
        _invoice(db, sid, "INV-B")          # baseline: 2 units already invoiced

        batch = [{"invoice_number": "INV-BATCH", "invoice_date": "2026-01-15",
                  "lines": [{"sale_item_id": sale_item_id, "quantity": 60.0}]}]

        errors = []
        parked = threading.Barrier(2, timeout=10)

        def _add_line():
            conn = get_connection(pg_dsn)
            try:
                parked.wait()
                create_invoice_item(conn, target_invoice, sale_item_id, 60.0)
            except Exception as e:          # noqa: BLE001 - asserted below
                errors.append(e)
            finally:
                conn.rollback()
                conn.close()

        def _batch():
            conn = get_connection(pg_dsn)
            try:
                parked.wait()
                create_invoices_batch(conn, sid, batch)
            except Exception as e:          # noqa: BLE001 - asserted below
                errors.append(e)
            finally:
                conn.rollback()
                conn.close()

        holder = self._hold_pi_line(pg_dsn, sale_item_id)
        threads = [threading.Thread(target=_add_line, daemon=True),
                   threading.Thread(target=_batch, daemon=True)]
        for thread in threads:
            thread.start()
        time.sleep(0.2)                     # let both park on the lock
        holder.rollback()
        holder.close()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "a writer never returned"

        invoiced = db.execute(
            "SELECT COALESCE(SUM(ii.quantity), 0) FROM invoice_items ii "
            "JOIN invoices i ON i.id = ii.invoice_id WHERE i.sale_id = %s",
            (sid,)).fetchone()[0]
        assert Decimal(str(invoiced)) <= Decimal("105"), (
            f"over-invoiced: {invoiced} > 105")
        assert len(errors) == 1, f"exactly one writer must lose, got {errors}"
