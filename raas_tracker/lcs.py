"""Letters-of-credit domain services (Phase 2 lane A).

One company holds one or many PIs under a single LC. The LC owns the
pipeline journey; PIs always progress together. Legacy
``sales.lc_number``/``lc_date`` TEXT mirrors are written from the LC in the
same transaction (compat/display only); ``sales.lc_id`` is the link.

Finding-5 invariants (service-enforced):
  * trim ``lc_number`` on every insert/lookup;
  * scope LC selects by ``(company_id, lc_number)``;
  * when ``sales.company_id`` changes, clear ``sales.lc_id`` (see
    ``raas_tracker.sales.relink_sale_company``, called from
    ``update_sale_full``).

Status-carrying refusals (Phase 4-A) carry their HTTP status on the exception
TYPE, never in the wording: a duplicate LC number is a
:class:`~raas_tracker._exceptions.LcNumberAlreadyExists` (409), an absent LC or
sale a :class:`~raas_tracker._exceptions.LcNotFound` /
:class:`~raas_tracker._exceptions.LcSaleNotFound` (404), and each readiness
gate its own conflict class. Plain validation ``ValueError``s stay plain and
still map to 400.
"""

import psycopg
from datetime import datetime as _dt, timezone
from typing import Optional, Dict, Any

from ._exceptions import (
    LcNotFound,
    LcNotReadyPastShipment,
    LcNumberAlreadyExists,
    LcSaleNotFound,
)


def _now_str() -> str:
    return _dt.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": row[0],
        "lc_number": row[1],
        "company_id": row[2],
        "lc_date": row[3],
        "expiry_date": row[4],
        "bank_ref": row[5],
        "stage": row[6],
        "notes": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


_LC_COLS = (
    "id, lc_number, company_id, lc_date, expiry_date, bank_ref, "
    "stage, notes, created_at, updated_at"
)


def create_lc(conn: psycopg.Connection, company_id: int, lc_number: str,
              lc_date=None, expiry_date=None, bank_ref=None,
              notes=None) -> dict:
    """Create an LC for a company. Trims ``lc_number``; rejects blank.

    Scoped UNIQUE per ``(company_id, lc_number)``: a trimmed duplicate for
    the same company raises ``LcNumberAlreadyExists`` (409) — race-safe via
    the DB unique index, which raises the same class so the two paths cannot
    disagree; the same number for a different company is fine. Audits
    ``LC_CREATE``.
    """
    from .audit import log_audit_action

    lc_number = (lc_number or "").strip() if isinstance(lc_number, str) else ""
    if not lc_number:
        raise ValueError("lc_number is required")
    company = conn.execute(
        "SELECT id FROM companies WHERE id = %s", (company_id,)).fetchone()
    if not company:
        raise ValueError("unknown company")
    # Scoped lookup on the trimmed value (Finding-5): legacy rows may carry
    # padding, so compare on trim() rather than the raw column.
    dup = conn.execute(
        "SELECT id FROM letters_of_credit "
        "WHERE company_id = %s AND trim(lc_number) = %s",
        (company_id, lc_number)).fetchone()
    if dup:
        raise LcNumberAlreadyExists(
            f"LC number '{lc_number}' already exists for this company")

    def _clean(value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    try:
        row = conn.execute(
            f"INSERT INTO letters_of_credit (lc_number, company_id, lc_date,"
            f" expiry_date, bank_ref, notes) VALUES (%s, %s, %s, %s, %s, %s)"
            f" RETURNING {_LC_COLS}",
            (lc_number, company_id, _clean(lc_date), _clean(expiry_date),
             _clean(bank_ref), _clean(notes)),
        ).fetchone()
    except psycopg.errors.IntegrityError:
        try:
            conn.rollback()
        except Exception:
            pass
        raise LcNumberAlreadyExists(
            f"LC number '{lc_number}' already exists for this company")
    lc = _row_to_dict(row)
    log_audit_action(conn, "LC_CREATE", "lc", lc["id"],
                     new_value=lc_number, atomic=False)
    conn.commit()
    return lc


def get_lc(conn: psycopg.Connection, lc_id: int) -> Optional[dict]:
    """Return an LC with its linked PIs, or None when missing.

    ``pis`` lists ``{id, pi_number, stage}`` for linked sales, oldest first.
    """
    row = conn.execute(
        f"SELECT {_LC_COLS} FROM letters_of_credit WHERE id = %s",
        (lc_id,)).fetchone()
    if not row:
        return None
    lc = _row_to_dict(row)
    lc["pis"] = [
        {"id": r[0], "pi_number": r[1], "stage": r[2]}
        for r in conn.execute(
            "SELECT id, pi_number, stage FROM sales WHERE lc_id = %s "
            "ORDER BY id",
            (lc_id,)).fetchall()
    ]
    return lc


def list_lcs(conn: psycopg.Connection, company_id=None) -> list:
    """List LCs (optionally one company's), each with a ``pi_count``."""
    if company_id is None:
        rows = conn.execute(
            f"SELECT {_LC_COLS} FROM letters_of_credit ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_LC_COLS} FROM letters_of_credit "
            "WHERE company_id = %s ORDER BY id",
            (company_id,)).fetchall()
    out = []
    for row in rows:
        lc = _row_to_dict(row)
        lc["pi_count"] = conn.execute(
            "SELECT COUNT(*) FROM sales WHERE lc_id = %s", (lc["id"],)
        ).fetchone()[0]
        out.append(lc)
    return out


def attach_pis(conn: psycopg.Connection, lc_id: int, sale_ids: list) -> dict:
    """Link sales (PIs) to an LC in ONE transaction.

    Every sale must share the LC's ``company_id`` or the whole call raises
    ``LcSaleNotFound`` (404) and links nothing. A sale that belongs to another
    company is reported **exactly** as a sale that does not exist — same
    class, same message — because the previous "sale N belongs to a different
    company" / 400 pairing against a 404 for a missing sale was a
    cross-tenant enumeration oracle: walking ``sale_id`` values told a caller
    which ones existed and belonged to somebody else. Mirrors the trimmed
    ``lc_number`` / ``lc_date`` onto each sale row (compat). Audits
    ``LC_ATTACH`` per sale.

    Linking IS entering LC: every attached sale still at ``pi_issued`` is
    advanced to ``lc_received`` (with ``sales_stage_history`` + ``SALE_MOVE``
    rows) in this same transaction. Sales already past ``pi_issued`` are left
    untouched — never downgraded, and a late attach to an advanced LC still
    lands the newcomer on ``lc_received``, never past the invoice gate.
    """
    from .audit import log_audit_action

    sale_ids = sorted(set(sale_ids or []))
    try:
        lc_row = conn.execute(
            "SELECT id, lc_number, company_id, lc_date "
            "FROM letters_of_credit WHERE id = %s FOR UPDATE",
            (lc_id,)).fetchone()
        if not lc_row:
            conn.rollback()
            raise LcNotFound("LC not found")
        _, raw_number, lc_company, lc_date = lc_row
        lc_number = (raw_number or "").strip()
        prior_stages = {}
        for sale_id in sale_ids:
            # Tenant scoping lives in the WHERE clause, not in a Python
            # comparison, so the cross-tenant path is literally the same query
            # as the missing path: same primary-key lock, same empty result,
            # same refusal. Absent and cross-tenant are one condition
            # deliberately — same class and same text — so the response cannot
            # be used to tell "no such sale" from "sale exists, wrong tenant".
            sale = conn.execute(
                "SELECT id, stage FROM sales"
                " WHERE id = %s AND company_id = %s FOR UPDATE",
                (sale_id, lc_company)).fetchone()
            if not sale:
                raise LcSaleNotFound(f"sale {sale_id} not found")
            prior_stages[sale_id] = sale[1]
        for sale_id in sale_ids:
            conn.execute(
                "UPDATE sales SET lc_id = %s, lc_number = %s, lc_date = %s,"
                " updated_at = %s WHERE id = %s",
                (lc_id, lc_number, lc_date, _now_str(), sale_id))
            log_audit_action(conn, "LC_ATTACH", "lc", lc_id,
                             new_value=f"sale={sale_id}", atomic=False)
            if prior_stages.get(sale_id) == "pi_issued":
                # Entering LC: inline, no move_sale_to_stage (it commits
                # mid-transaction, splitting this atomic unit and releasing
                # the row locks early). Timestamp via the column DEFAULT,
                # exactly as move_sale_to_stage does.
                conn.execute(
                    "UPDATE sales SET stage = %s, updated_at = %s WHERE id = %s",
                    ("lc_received", _now_str(), sale_id))
                conn.execute(
                    "INSERT INTO sales_stage_history (sale_id, from_stage,"
                    " to_stage, notes) VALUES (%s, %s, %s, %s)",
                    (sale_id, "pi_issued", "lc_received",
                     "auto-advanced on LC attach"))
                log_audit_action(conn, "SALE_MOVE", "sale", sale_id,
                                 old_value="pi_issued", new_value="lc_received",
                                 atomic=False)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return get_lc(conn, lc_id)


def detach_pi(conn: psycopg.Connection, lc_id: int, sale_id: int) -> bool:
    """Unlink one sale from an LC. Clears ``lc_id`` + legacy mirror columns.

    One transaction: locks the LC row + the sale row (``FOR UPDATE``),
    re-checks ``lc_id`` under lock, then clears the mirrors. Returns False
    when the sale does not exist or is not linked to this LC.
    """
    from .audit import log_audit_action

    try:
        lc_row = conn.execute(
            "SELECT id FROM letters_of_credit WHERE id = %s FOR UPDATE",
            (lc_id,)).fetchone()
        if not lc_row:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        row = conn.execute(
            "SELECT lc_id FROM sales WHERE id = %s FOR UPDATE",
            (sale_id,)).fetchone()
        if not row or row[0] != lc_id:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        conn.execute(
            "UPDATE sales SET lc_id = NULL, lc_number = NULL, lc_date = NULL,"
            " updated_at = %s WHERE id = %s",
            (_now_str(), sale_id))
        log_audit_action(conn, "LC_DETACH", "lc", lc_id,
                         old_value=f"sale={sale_id}",
                         new_value="detached", atomic=False)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return True


def move_lc_stage(conn: psycopg.Connection, lc_id: int, new_stage: str,
                  notes=None) -> bool:
    """Move an LC and ALL its child PIs to ``new_stage`` in a single commit.

    Locks the LC row AND each child sale row (``SELECT ... FOR UPDATE``,
    children in id order) before writing anything, so concurrent moves
    serialize instead of interleaving. Validates ``new_stage`` against
    ``SALE_STAGE_ORDER`` (``ValueError`` otherwise). Crossing into
    ``shipment_ongoing`` requires no invoice: invoices are created in Shipment
    Ongoing. When the move carries the LC into ``payment_due`` the gate
    requires at least one non-void invoice past booking (booked/shipped/paid)
    across the LC's child PIs; booking is the per-invoice proof that
    production finished.

    That refusal is typed at the point the message is built —
    ``LcNotReadyPastShipment``, 409, text unchanged. The route used to
    recover the status by scanning the text for ``"recipes:"`` /
    ``"invoices:"`` / ``"production:"``, three of which are vocabulary shared
    with the readiness payload the message is itself assembled from, so any
    rewording (or any new colon in those words) silently changed the HTTP
    status. Propagates ``sales.stage`` + ``sales_stage_history`` + ``SALE_MOVE``
    audit per child (``via_lc=True``: linked PIs only travel via this path).
    Returns False when the LC does not exist.
    """
    from .audit import log_audit_action
    from .sales import SALE_STAGE_ORDER, booked_invoice_readiness

    if new_stage not in SALE_STAGE_ORDER:
        raise ValueError(f"invalid stage: {new_stage}")
    try:
        lc_row = conn.execute(
            "SELECT id, stage FROM letters_of_credit"
            " WHERE id = %s FOR UPDATE",
            (lc_id,)).fetchone()
        if not lc_row:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        _, lc_stage = lc_row
        children = conn.execute(
            "SELECT id, stage FROM sales WHERE lc_id = %s ORDER BY id FOR UPDATE",
            (lc_id,)).fetchall()
        # Gate inside the txn: which barrier(s) apply depends on how far this
        # stage move carries the LC. Crossing into ``payment_due`` means at
        # least one invoice past booking across the LC's child PIs. Shipment
        # entry is ungated.
        try:
            tgt_idx = SALE_STAGE_ORDER.index(new_stage)
        except ValueError:
            tgt_idx = -1
        try:
            cur_idx = SALE_STAGE_ORDER.index(lc_stage)
        except ValueError:
            cur_idx = -1
        pay_idx = SALE_STAGE_ORDER.index("payment_due")
        sale_ids = [c[0] for c in children]

        if tgt_idx >= pay_idx > cur_idx:
            ready = booked_invoice_readiness(conn, sale_ids)
            if not ready["booked_ok"]:
                msg = ("LC is not ready to go past shipment: "
                       "invoices: at least one booked invoice required")
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise LcNotReadyPastShipment(msg)
        conn.execute(
            "UPDATE letters_of_credit SET stage = %s, updated_at = %s"
            " WHERE id = %s",
            (new_stage, _now_str(), lc_id))
        log_audit_action(conn, "LC_MOVE", "lc", lc_id,
                         old_value=lc_row[1], new_value=new_stage,
                         atomic=False)
        # Child propagation bypasses the direct-move guard by design
        # (LC-linked sales move only via the LC).
        for child_id, child_stage in children:
            if new_stage == "shipment_ongoing":
                conn.execute(
                    "UPDATE sales SET stage = %s,"
                    " shipment_status = COALESCE(shipment_status,"
                    " 'production_running'), updated_at = %s WHERE id = %s",
                    (new_stage, _now_str(), child_id))
            else:
                conn.execute(
                    "UPDATE sales SET stage = %s, updated_at = %s WHERE id = %s",
                    (new_stage, _now_str(), child_id))
            conn.execute(
                "INSERT INTO sales_stage_history (sale_id, from_stage,"
                " to_stage, notes) VALUES (%s, %s, %s, %s)",
                (child_id, child_stage, new_stage, notes))
            log_audit_action(conn, "SALE_MOVE", "sale", child_id,
                             old_value=child_stage, new_value=new_stage,
                             atomic=False)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return True
