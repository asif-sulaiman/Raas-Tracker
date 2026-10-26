"""The batch writer locks every PI line of the sale before it writes.

The 5% over-invoice ceiling is gone, but the row locks that serialise invoice
writes stay. ``create_invoices_batch`` takes ``sale_items ... FOR UPDATE`` for
the whole sale up front and holds it until its single commit, so a second
writer cannot interleave invoice/line/audit writes against a line that is
mid-flight.

This is a characterisation test: the lock already exists, so it passes on the
current code. It was mutation-checked by deleting the up-front lock and
observing the failure.
"""

import threading

import psycopg

from chem_stock import get_connection
from raas_tracker.sales import add_sale, add_sale_item, create_invoices_batch


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


def _get_sale_items(db, sale_id):
    """Get sale items for a sale."""
    return db.execute(
        "SELECT id, product_name, quantity FROM sale_items WHERE sale_id = %s ORDER BY id",
        (sale_id,)
    ).fetchall()


class TestWriterLockDiscipline:
    """The batch writer holds a sale-level PI-line lock before it writes.

    The lock no longer protects a quantity ceiling; it keeps two writers from
    interleaving their invoice, line, and audit writes. The batch takes it on
    every PI line of the sale up front (``sales.py:1933-1935``), not just the
    lines it bills.
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
