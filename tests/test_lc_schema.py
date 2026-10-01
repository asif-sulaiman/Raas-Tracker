"""Phase 1 (schema foundation) TDD tests.

Covers the LC master + sales link + invoice-lines schema:
  a. `letters_of_credit` table + columns/defaults/FK.
  b. UNIQUE (company_id, lc_number) enforced.
  c. `sales.lc_id` column + FK + index.
  d. `invoice_items` table + columns/FKs/indexes.
  e. Legacy backfill groups shared (company_id, lc_number) mirrors into one
     LC row; singletons get their own row; re-running is idempotent.

Uses the conftest `db`/`pg_dsn` fixtures. Run with:
    python -m pytest tests/test_lc_schema.py -q
"""
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from raas_tracker import db as dbmod  # noqa: E402


def _tag() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture()
def lc_db(db):
    """`db` truncation covers the Phase-1 tables; yield it directly."""
    yield db


def _columns(conn, table: str):
    rows = conn.execute(
        """SELECT column_name, is_nullable, column_default, data_type
           FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s""",
        (table,),
    ).fetchall()
    return {
        r[0]: {"nullable": r[1], "default": r[2], "type": r[3]} for r in rows
    }


def _fk_refs(conn, table: str):
    """{(column, ref_table, ref_column)} FKs declared on `table`."""
    rows = conn.execute(
        """SELECT kcu.column_name, ccu.table_name, ccu.column_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON kcu.constraint_name = tc.constraint_name
            AND kcu.table_schema = tc.table_schema
           JOIN information_schema.constraint_column_usage ccu
             ON ccu.constraint_name = tc.constraint_name
           WHERE tc.table_schema = 'public'
             AND tc.table_name = %s
             AND tc.constraint_type = 'FOREIGN KEY'""",
        (table,),
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def _indexdefs(conn, table: str):
    rows = conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = %s",
        (table,),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------- #
# (a) letters_of_credit table
# --------------------------------------------------------------------------- #
def test_letters_of_credit_columns(lc_db):
    cols = _columns(lc_db, "letters_of_credit")
    assert set(cols) == {
        "id", "lc_number", "company_id", "lc_date", "expiry_date",
        "bank_ref", "stage", "notes", "created_at", "updated_at",
    }, f"unexpected letters_of_credit columns: {sorted(cols)}"
    assert cols["lc_number"]["nullable"] == "NO"
    assert cols["company_id"]["nullable"] == "NO"
    assert cols["stage"]["nullable"] == "NO"
    assert "lc_received" in (cols["stage"]["default"] or ""), \
        f"stage default should be 'lc_received': {cols['stage']['default']!r}"
    assert cols["lc_date"]["nullable"] == "YES"
    assert cols["expiry_date"]["nullable"] == "YES"
    assert ("company_id", "companies", "id") in _fk_refs(lc_db, "letters_of_credit")


def test_letters_of_credit_company_restrict(lc_db):
    """Deleting a company with LCs must be rejected (RESTRICT)."""
    conn = lc_db
    tag = _tag()
    co = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (f"LC Restrict Co {tag}",),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO letters_of_credit (lc_number, company_id) VALUES (%s, %s)",
        (f"LC-R-{tag}", co),
    )
    conn.commit()
    with pytest.raises(psycopg.errors.IntegrityError):
        conn.execute("DELETE FROM companies WHERE id = %s", (co,))
    conn.rollback()


# --------------------------------------------------------------------------- #
# (b) UNIQUE (company_id, lc_number)
# --------------------------------------------------------------------------- #
def test_letters_of_credit_unique_company_lc_number(lc_db):
    conn = lc_db
    tag = _tag()
    co = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (f"LC Uq Co {tag}",),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO letters_of_credit (lc_number, company_id) VALUES (%s, %s)",
        (f"LC-U-{tag}", co),
    )
    conn.commit()
    with pytest.raises(psycopg.errors.IntegrityError):
        conn.execute(
            "INSERT INTO letters_of_credit (lc_number, company_id) "
            "VALUES (%s, %s)",
            (f"LC-U-{tag}", co),
        )
    conn.rollback()
    # Same lc_number for a *different* company is fine.
    co2 = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (f"LC Uq Co2 {tag}",),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO letters_of_credit (lc_number, company_id) VALUES (%s, %s)",
        (f"LC-U-{tag}", co2),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# (c) sales.lc_id
# --------------------------------------------------------------------------- #
def test_sales_lc_id_column_fk_index(lc_db):
    cols = _columns(lc_db, "sales")
    assert "lc_id" in cols, "sales.lc_id column is missing"
    assert ("lc_id", "letters_of_credit", "id") in _fk_refs(lc_db, "sales")
    idx = _indexdefs(lc_db, "sales")
    assert "idx_sales_lc_id" in idx, f"idx_sales_lc_id missing: {sorted(idx)}"
    assert "lc_id" in idx["idx_sales_lc_id"]


def test_sales_lc_id_restrict(lc_db):
    """Deleting an LC with linked sales must be rejected (RESTRICT)."""
    conn = lc_db
    tag = _tag()
    co = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (f"LC Link Co {tag}",),
    ).fetchone()[0]
    lc_id = conn.execute(
        "INSERT INTO letters_of_credit (lc_number, company_id) "
        "VALUES (%s, %s) RETURNING id",
        (f"LC-L-{tag}", co),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sales (client_name, company_id, lc_id) VALUES (%s, %s, %s)",
        ("x", co, lc_id),
    )
    conn.commit()
    with pytest.raises(psycopg.errors.IntegrityError):
        conn.execute("DELETE FROM letters_of_credit WHERE id = %s", (lc_id,))
    conn.rollback()


# --------------------------------------------------------------------------- #
# (d) invoice_items table
# --------------------------------------------------------------------------- #
def test_invoice_items_columns(lc_db):
    cols = _columns(lc_db, "invoice_items")
    assert set(cols) == {
        "id", "invoice_id", "sale_item_id", "product_name",
        "unit", "quantity", "unit_price", "line_total",
    }, f"unexpected invoice_items columns: {sorted(cols)}"
    assert cols["invoice_id"]["nullable"] == "NO"
    assert cols["sale_item_id"]["nullable"] == "YES"
    assert cols["product_name"]["nullable"] == "NO"
    assert cols["unit"]["nullable"] == "NO"
    assert "KG" in (cols["unit"]["default"] or ""), \
        f"unit default should be 'KG': {cols['unit']['default']!r}"
    fks = _fk_refs(lc_db, "invoice_items")
    assert ("invoice_id", "invoices", "id") in fks
    assert ("sale_item_id", "sale_items", "id") in fks


def test_invoice_items_indexes(lc_db):
    idx = _indexdefs(lc_db, "invoice_items")
    assert any("invoice_id" in d for d in idx.values()), \
        f"no index on invoice_items(invoice_id): {sorted(idx)}"
    assert any("sale_item_id" in d for d in idx.values()), \
        f"no index on invoice_items(sale_item_id): {sorted(idx)}"


def test_invoice_items_cascade_and_set_null(lc_db):
    """invoice DELETE cascades to items; sale_item DELETE nulls the link."""
    conn = lc_db
    tag = _tag()
    sale_id = conn.execute(
        "INSERT INTO sales (client_name) VALUES (%s) RETURNING id",
        (f"Inv Co {tag}",),
    ).fetchone()[0]
    item_id = conn.execute(
        "INSERT INTO sale_items (sale_id, product_name) VALUES (%s, %s) "
        "RETURNING id",
        (sale_id, f"Prod {tag}"),
    ).fetchone()[0]
    inv_id = conn.execute(
        "INSERT INTO invoices (sale_id, invoice_number) VALUES (%s, %s) "
        "RETURNING id",
        (sale_id, f"INV-{tag}"),
    ).fetchone()[0]
    line_id = conn.execute(
        """INSERT INTO invoice_items
               (invoice_id, sale_item_id, product_name, quantity, unit_price)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (inv_id, item_id, f"Prod {tag}", 10, 5),
    ).fetchone()[0]
    conn.commit()

    conn.execute("DELETE FROM sale_items WHERE id = %s", (item_id,))
    conn.commit()
    row = conn.execute(
        "SELECT sale_item_id FROM invoice_items WHERE id = %s", (line_id,)
    ).fetchone()
    assert row is not None and row[0] is None, \
        "sale_item DELETE should SET NULL the invoice_items link"

    conn.execute("DELETE FROM invoices WHERE id = %s", (inv_id,))
    conn.commit()
    assert conn.execute(
        "SELECT 1 FROM invoice_items WHERE id = %s", (line_id,)
    ).fetchone() is None, "invoice DELETE should CASCADE to invoice_items"


# --------------------------------------------------------------------------- #
# (e) legacy backfill: group shared (company_id, lc_number) mirrors
# --------------------------------------------------------------------------- #
def test_legacy_backfill_groups_shared_lc(lc_db):
    from raas_tracker.db import backfill_lc_links

    conn = lc_db
    tag = _tag()
    co = conn.execute(
        "INSERT INTO companies (name) VALUES (%s) RETURNING id",
        (f"LC BF Co {tag}",),
    ).fetchone()[0]
    shared = f"LC-SHARED-{tag}"
    single = f"LC-SINGLE-{tag}"
    s1 = conn.execute(
        "INSERT INTO sales (client_name, company_id, lc_number, lc_date) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("x", co, shared, "2026-01-01"),
    ).fetchone()[0]
    s2 = conn.execute(
        "INSERT INTO sales (client_name, company_id, lc_number, lc_date) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("x", co, shared, "2026-02-01"),
    ).fetchone()[0]
    s3 = conn.execute(
        "INSERT INTO sales (client_name, company_id, lc_number, lc_date) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("x", co, single, "2026-03-01"),
    ).fetchone()[0]
    s4 = conn.execute(
        "INSERT INTO sales (client_name, company_id) VALUES (%s, %s) "
        "RETURNING id",
        ("x", co),
    ).fetchone()[0]
    s5 = conn.execute(
        "INSERT INTO sales (client_name, company_id, lc_number, lc_date) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("x", co, f"  {shared}  ", "2026-04-01"),
    ).fetchone()[0]
    s6 = conn.execute(
        "INSERT INTO sales (client_name, company_id, lc_number) "
        "VALUES (%s, %s, %s) RETURNING id",
        ("x", co, "   "),
    ).fetchone()[0]
    s7 = conn.execute(
        "INSERT INTO sales (client_name, lc_number) VALUES (%s, %s) "
        "RETURNING id",
        ("x", shared),
    ).fetchone()[0]
    conn.commit()

    backfill_lc_links(conn)
    conn.commit()

    def _lc_id(sale_id):
        return conn.execute(
            "SELECT lc_id FROM sales WHERE id = %s", (sale_id,)
        ).fetchone()[0]

    lc1, lc2, lc3, lc4 = _lc_id(s1), _lc_id(s2), _lc_id(s3), _lc_id(s4)
    lc5, lc6, lc7 = _lc_id(s5), _lc_id(s6), _lc_id(s7)
    assert lc1 is not None and lc1 == lc2, \
        "two sales sharing (company_id, lc_number) must share ONE lc row"
    assert lc3 is not None and lc3 != lc1, \
        "a singleton lc_number must get its OWN lc row"
    assert lc4 is None, "sales without lc_number must stay unlinked"
    assert lc5 == lc1, \
        "whitespace-padded lc_number must join the same LC row"
    assert lc6 is None, "blank-only lc_number must stay unlinked"
    assert lc7 is None, "lc_number with NULL company must stay unlinked"
    # Legacy mirrors untouched; LC row carries the mirror data.
    assert conn.execute(
        "SELECT lc_number FROM sales WHERE id = %s", (s1,)
    ).fetchone()[0] == shared
    assert conn.execute(
        "SELECT lc_number, company_id FROM letters_of_credit WHERE id = %s",
        (lc1,),
    ).fetchone() == (shared, co)
    assert conn.execute(
        "SELECT COUNT(*) FROM letters_of_credit WHERE company_id = %s",
        (co,),
    ).fetchone()[0] == 2

    # Re-running is idempotent: no dupes, same ids.
    backfill_lc_links(conn)
    conn.commit()
    assert (_lc_id(s1), _lc_id(s2), _lc_id(s3)) == (lc1, lc2, lc3)
    assert (_lc_id(s5), _lc_id(s6), _lc_id(s7)) == (lc1, None, None)
    assert conn.execute(
        "SELECT COUNT(*) FROM letters_of_credit WHERE company_id = %s",
        (co,),
    ).fetchone()[0] == 2


def test_schema_version_bumped_for_phase1():
    assert dbmod._SCHEMA_VERSION >= 2, \
        "Phase 1 DDL must bump _SCHEMA_VERSION 1 -> 2"
