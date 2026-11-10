"""Chemical Purchases — Slice 1 backend service.

An in-house/outward receipt register with atomic multi-line posting, optimistic
-lock corrections and cancellation. Every mutation is ONE caller-visible
transaction: the service owns commit/rollback (the route only releases the
connection), stock moves through ``stock.update_stock(atomic=False)`` so a
mid-loop failure cannot leave earlier lines durably applied, and reorder
notifications are flushed only AFTER the commit (a buy alert for a receipt that
rolled back would be a lie).

Lock ordering follows the Task 1 audit: chemical row locks are acquired in
ascending ``chemical_id`` order. New-material identity is case-insensitive and
race-safe via ``register_chemical_in_transaction`` (INSERT ... ON CONFLICT
(lower(name)) DO NOTHING + re-SELECT FOR UPDATE). ``update_stock`` locks by NAME
internally, so the caller controls the order by resolving every identity first
and then walking the sorted ids.

Statuses are carried by exception TYPE (see ``_exceptions``): a plain
``ValueError`` is a 400 (validation, unit mismatch, duplicate, insufficient
stock), ``LifecycleConflict`` is a 409 (version mismatch / already cancelled),
``ResourceMissing`` is a 404. ``status_for`` reads the type, never the message.
"""
from typing import Any, Optional

import psycopg

from .db import logger
from .audit import log_audit_action
from ._numeric import finite_values
from ._exceptions import LifecycleConflict, ResourceMissing
from .stock import (
    update_stock, sync_reorder_notifications, register_chemical_in_transaction,
)

# One header projection, shared by the single read and the paginated list, so
# the two can never disagree about column order. client_name rides a LEFT JOIN
# (an in-house purchase has no client).
_HEADER_SELECT = (
    "SELECT p.id, p.supplier_name, p.purchase_number, p.purchase_date, "
    "p.delivery_type, p.client_id, c.name, p.cancelled, p.cancelled_at, "
    "p.cancelled_by, p.cancellation_reason, p.version, p.created_by, "
    "p.created_at, p.updated_at "
    "FROM chemical_purchases p LEFT JOIN companies c ON c.id = p.client_id"
)
_ITEM_SELECT = (
    "SELECT i.id, i.purchase_id, i.chemical_id, ch.name, i.quantity, i.unit "
    "FROM chemical_purchase_items i JOIN chemicals ch ON ch.id = i.chemical_id"
)


def _iso(value: Any) -> Any:
    """date/datetime -> ISO string for JSON; None and plain values pass through.

    psycopg returns DATE as ``datetime.date`` and TIMESTAMPTZ as ``datetime``;
    Flask's JSON provider would serialise them, but the service contracts return
    strings so a direct caller (and the latest-receipt map) sees a stable shape.
    """
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else value


def _item_dict(row: Any) -> dict:
    # quantity is NUMERIC -> psycopg Decimal; the JSON provider does not handle
    # Decimal, so it is floated here (NUMERIC(18,6) precision survives the cast).
    return {"id": row[0], "purchase_id": row[1], "chemical_id": row[2],
            "chemical_name": row[3], "quantity": float(row[4]), "unit": row[5]}


def _header_dict(row: Any, items: list) -> dict:
    return {"id": row[0], "supplier_name": row[1], "purchase_number": row[2],
            "purchase_date": _iso(row[3]), "delivery_type": row[4],
            "client_id": row[5], "client_name": row[6], "cancelled": row[7],
            "cancelled_at": _iso(row[8]), "cancelled_by": row[9],
            "cancellation_reason": row[10], "version": row[11],
            "created_by": row[12], "created_at": _iso(row[13]),
            "updated_at": _iso(row[14]), "items": items}


def _positive_quantity(value: Any) -> float:
    """Coerce + guard a NUMERIC input: finite and > 0, else ValueError (400)."""
    try:
        q = float(value)
    except (TypeError, ValueError):
        raise ValueError("quantity must be a finite number greater than 0")
    finite_values(q)
    if q <= 0:
        raise ValueError("quantity must be greater than 0")
    return q


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #
def create_purchase(conn: psycopg.Connection, *, supplier_name: str,
                    purchase_number: str, purchase_date, delivery_type: str,
                    client_id: Optional[int], lines: list, actor: str) -> Optional[dict]:
    """Post a multi-line receipt atomically; return the created purchase dict.

    Phases: validate every line -> resolve identities read-only -> reject
    duplicates -> register truly-new materials -> lock all chemicals in
    ascending id order -> verify units -> apply in-house stock -> insert header
    + items -> audit -> commit -> flush reorder notifications (in-house only).
    Any failure before the commit rolls the whole receipt back.
    """
    supplier_name = (supplier_name or "").strip()
    purchase_number = (purchase_number or "").strip()
    if not supplier_name:
        raise ValueError("supplier_name is required")
    if not purchase_number:
        raise ValueError("purchase_number is required")
    if delivery_type not in ("in_house", "outward"):
        raise ValueError("delivery_type must be 'in_house' or 'outward'")
    if delivery_type == "in_house" and client_id is not None:
        raise ValueError("an in_house purchase must not name a client")
    if delivery_type == "outward" and client_id is None:
        raise ValueError("an outward purchase requires a client")
    if not lines:
        raise ValueError("a purchase requires at least one line")

    # Phase A: validate every line before any read or write.
    for line in lines:
        if not (line.get("chemical_name") or "").strip():
            raise ValueError("chemical_name is required")
        if not (line.get("unit") or "").strip():
            raise ValueError("unit is required")
        _positive_quantity(line.get("quantity"))
        reorder = line.get("reorder_level")
        if reorder is not None:
            finite_values(reorder)
            if float(reorder) < 0:
                raise ValueError("reorder_level must be 0 or greater")

    in_house = delivery_type == "in_house"
    reorder_names: list = []
    purchase_id = None
    try:
        if not in_house:
            if conn.execute("SELECT id FROM companies WHERE id = %s",
                            (client_id,)).fetchone() is None:
                raise ValueError("client not found")

        # Phase B: resolve identities read-only (no locks yet). A "new" name
        # that already exists converges on the existing row; only a genuinely
        # absent name is registered below.
        entries = []
        for line in lines:
            is_new = bool(line.get("is_new", False))
            name = (line.get("chemical_name") or "").strip()
            unit = (line.get("unit") or "").strip().upper()
            raw_q = line.get("quantity")
            q = float(raw_q)
            reorder = line.get("reorder_level")
            if is_new:
                row = conn.execute(
                    "SELECT id, name, unit, in_house_registered FROM chemicals "
                    "WHERE lower(name) = lower(%s)", (name,)).fetchone()
                if row:
                    entries.append({"chem_id": row[0], "name": row[1],
                                    "chem_unit": row[2], "registered": row[3],
                                    "unit": unit, "quantity": q,
                                    "raw_quantity": raw_q,
                                    "reorder_level": reorder, "truly_new": False})
                else:
                    entries.append({"chem_id": None, "name": name,
                                    "chem_unit": unit, "registered": None,
                                    "unit": unit, "quantity": q,
                                    "raw_quantity": raw_q,
                                    "reorder_level": reorder, "truly_new": True})
            else:
                chem_id = line.get("chemical_id")
                if chem_id is None:
                    raise ValueError("chemical_id is required for an existing material")
                row = conn.execute(
                    "SELECT id, name, unit, in_house_registered FROM chemicals "
                    "WHERE id = %s", (chem_id,)).fetchone()
                if row is None:
                    raise ValueError(f"chemical {chem_id} not found")
                entries.append({"chem_id": row[0], "name": row[1],
                                "chem_unit": row[2], "registered": row[3],
                                "unit": unit, "quantity": q, "raw_quantity": raw_q,
                                "reorder_level": reorder, "truly_new": False})

        # Duplicate material in one receipt is rejected before any write. Keys
        # live in two spaces (resolved id vs new lower(name)); a new name that
        # matched an existing row already converged to an id above.
        keys = [("id", e["chem_id"]) if e["chem_id"] is not None
                else ("name", e["name"].lower()) for e in entries]
        if len(set(keys)) != len(keys):
            raise ValueError("the same material appears twice in this purchase")

        # Phase C: register truly-new materials in a deterministic name order.
        for e in sorted((e for e in entries if e["truly_new"]),
                        key=lambda x: x["name"].lower()):
            chem = register_chemical_in_transaction(
                conn, e["name"], e["unit"], e["reorder_level"], in_house)
            e["chem_id"] = chem["id"]
            e["name"] = chem["name"]
            e["chem_unit"] = chem["unit"]
            e["registered"] = chem["in_house_registered"]

        # Convergence under a concurrent same-name race can merge two lines onto
        # one id after the pre-check; re-check so the refusal is a clean 400
        # rather than a unique-index IntegrityError at the item INSERT.
        ids = [e["chem_id"] for e in entries]
        if len(set(ids)) != len(ids):
            raise ValueError("the same material appears twice in this purchase")

        # Lock every chemical in ascending id order (Task 1 ruling), then verify
        # the requested unit against the registered unit.
        entries.sort(key=lambda e: e["chem_id"])
        for e in entries:
            row = conn.execute(
                "SELECT name, unit, in_house_registered FROM chemicals "
                "WHERE id = %s FOR UPDATE", (e["chem_id"],)).fetchone()
            if row is None:
                raise ValueError(f"chemical {e['chem_id']} not found")
            e["name"], e["chem_unit"], e["registered"] = row[0], row[1], row[2]
            if e["unit"] != row[1].upper():
                raise ValueError(
                    f"unit mismatch for '{row[0]}': registered {row[1]}, "
                    f"requested {e['unit']}")

        # In-house receipts stock the material; outward receipts never touch
        # stock (the goods go straight to the client).
        if in_house:
            for e in entries:
                if not update_stock(conn, e["name"], e["quantity"], e["chem_unit"],
                                    reason=f"Purchase {purchase_number}",
                                    atomic=False):
                    raise ValueError(f"chemical '{e['name']}' not found")
                # Once stocked on site, a previously hidden identity becomes
                # visible and stays visible (cancellation never demotes it).
                if not e["registered"]:
                    conn.execute(
                        "UPDATE chemicals SET in_house_registered = TRUE "
                        "WHERE id = %s", (e["chem_id"],))
                reorder_names.append(e["name"])

        purchase_id = conn.execute(
            "INSERT INTO chemical_purchases (supplier_name, purchase_number, "
            "purchase_date, delivery_type, client_id, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (supplier_name, purchase_number, purchase_date, delivery_type,
             client_id, actor)).fetchone()[0]
        for e in entries:
            conn.execute(
                "INSERT INTO chemical_purchase_items (purchase_id, chemical_id, "
                "quantity, unit) VALUES (%s, %s, %s, %s)",
                (purchase_id, e["chem_id"], e["raw_quantity"], e["chem_unit"]))

        log_audit_action(conn, "PURCHASE_CREATE", "chemical_purchase", purchase_id,
                         user_id=actor,
                         new_value=(f"{purchase_number} ({delivery_type}) "
                                    f"supplier={supplier_name} lines={len(entries)}"),
                         atomic=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # After commit only: a notification failure must not undo a posted receipt.
    if in_house and reorder_names:
        try:
            sync_reorder_notifications(conn, reorder_names)
        except Exception:
            logger.exception("reorder sync failed after purchase %s", purchase_id)
    return get_purchase(conn, purchase_id)


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def get_purchase(conn: psycopg.Connection, purchase_id: int) -> Optional[dict]:
    """One purchase with its items, or None when the id does not exist."""
    row = conn.execute(_HEADER_SELECT + " WHERE p.id = %s",
                       (purchase_id,)).fetchone()
    if row is None:
        return None
    items = conn.execute(_ITEM_SELECT + " WHERE i.purchase_id = %s "
                       "ORDER BY i.chemical_id", (purchase_id,)).fetchall()
    return _header_dict(row, [_item_dict(i) for i in items])


def get_purchases(conn: psycopg.Connection, *, delivery_type: Optional[str] = None,
                  client_id: Optional[int] = None,
                  chemical_id: Optional[int] = None,
                  include_cancelled: bool = False, page: int = 1,
                  per_page: int = 20) -> dict:
    """Paginated, filtered receipt list, newest first (date DESC, id DESC).

    Items are batched into one query for the page (no N+1). Filters assemble
    hardcoded clauses with %s params only — no value is ever interpolated.
    """
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = max(1, min(int(per_page), 100))
    except (TypeError, ValueError):
        per_page = 20

    where = []
    params: list = []
    if not include_cancelled:
        where.append("p.cancelled = FALSE")
    if delivery_type is not None:
        where.append("p.delivery_type = %s")
        params.append(delivery_type)
    if client_id is not None:
        where.append("p.client_id = %s")
        params.append(client_id)
    if chemical_id is not None:
        where.append("EXISTS (SELECT 1 FROM chemical_purchase_items i "
                     "WHERE i.purchase_id = p.id AND i.chemical_id = %s)")
        params.append(chemical_id)
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute("SELECT COUNT(*) FROM chemical_purchases p" + where_clause,
                         params).fetchone()[0]
    headers = conn.execute(
        _HEADER_SELECT + where_clause +
        " ORDER BY p.purchase_date DESC, p.id DESC LIMIT %s OFFSET %s",
        params + [per_page, (page - 1) * per_page]).fetchall()
    ids = [h[0] for h in headers]
    items_by_purchase: dict = {}
    if ids:
        for ir in conn.execute(
                _ITEM_SELECT + " WHERE i.purchase_id = ANY(%s) "
                "ORDER BY i.purchase_id, i.chemical_id", (ids,)).fetchall():
            items_by_purchase.setdefault(ir[1], []).append(_item_dict(ir))
    return {"purchases": [_header_dict(h, items_by_purchase.get(h[0], []))
                          for h in headers],
            "total": total, "page": page, "per_page": per_page}


def get_latest_receipt(conn: psycopg.Connection, chemical_id: int) -> Optional[dict]:
    """Latest non-cancelled in-house receipt for a chemical, or None.

    Backdated receipts never override a newer one: the order is the stated
    purchase_date DESC, then id DESC as the tie-breaker.
    """
    row = conn.execute(
        "SELECT p.purchase_date, i.quantity, p.supplier_name "
        "FROM chemical_purchase_items i "
        "JOIN chemical_purchases p ON p.id = i.purchase_id "
        "WHERE i.chemical_id = %s AND p.delivery_type = 'in_house' "
        "AND p.cancelled = FALSE "
        "ORDER BY p.purchase_date DESC, p.id DESC LIMIT 1",
        (chemical_id,)).fetchone()
    if row is None:
        return None
    return {"date": _iso(row[0]), "quantity": float(row[1]),
            "supplier_name": row[2]}


def get_catalog(conn: psycopg.Connection) -> list:
    """Every chemical identity (including outward-only) for entry selectors."""
    return [{"id": r[0], "name": r[1], "unit": r[2], "in_house_registered": r[3]}
            for r in conn.execute(
                "SELECT id, name, unit, in_house_registered FROM chemicals "
                "ORDER BY name").fetchall()]


def get_inventory(conn: psycopg.Connection) -> dict:
    """Slice 1 inventory: in-house materials with their latest receipt fields.

    ``projected_quantity`` / ``required_quantity`` come back as ``None`` here
    and ``coverage`` / ``unstocked_requirements`` as empty: they are the PI
    forecast, and only ``flask_app``'s inventory route fills them — it reads
    ``pi_demos.project_purchasing`` from the same transaction first, so the
    shelf figure and the demand computed against it are one instant. Any other
    caller of this service gets the inventory alone, which is what it asked for.
    Two queries total: the in-house materials and a DISTINCT ON latest-receipt
    map, so the view never fans out into one receipt query per material.
    """
    chems = conn.execute(
        "SELECT id, name, current_qty, unit, COALESCE(reorder_level, 0) "
        "FROM chemicals WHERE in_house_registered = TRUE ORDER BY name").fetchall()
    receipts = conn.execute(
        "SELECT DISTINCT ON (i.chemical_id) i.chemical_id, p.purchase_date, "
        "i.quantity, p.supplier_name "
        "FROM chemical_purchase_items i "
        "JOIN chemical_purchases p ON p.id = i.purchase_id "
        "WHERE p.delivery_type = 'in_house' AND p.cancelled = FALSE "
        "ORDER BY i.chemical_id, p.purchase_date DESC, p.id DESC").fetchall()
    receipt_map = {r[0]: {"date": _iso(r[1]), "quantity": float(r[2]),
                          "supplier_name": r[3]} for r in receipts}
    materials = []
    for c in chems:
        chem_id, name, qty, unit, reorder = c[0], c[1], c[2], c[3], c[4] or 0
        r = receipt_map.get(chem_id)
        if qty <= 0:
            status = "out_of_stock"
        elif reorder > 0 and qty <= reorder:
            status = "low_stock"
        else:
            status = "ok"
        materials.append({
            "id": chem_id, "name": name, "qty": qty, "unit": unit,
            "reorder_level": reorder, "status": status,
            "last_purchase_date": r["date"] if r else None,
            "last_purchase_quantity": r["quantity"] if r else None,
            "supplier_name": r["supplier_name"] if r else None,
            "required_quantity": None, "projected_quantity": None,
        })
    return {"materials": materials, "coverage": None,
            "unstocked_requirements": []}


# --------------------------------------------------------------------------- #
# reference (freeze) protection + the outward-source picker
# --------------------------------------------------------------------------- #
# A purchase line becomes EVIDENCE the moment a formulation names it as the
# source of an ingredient, and it stays evidence even after the recipe row is
# edited away: the production run keeps its own immutable snapshot pointing at
# the same line. Both tables are therefore authoritative, forever.
#
# The ruling this encodes is *frozen means uneditable, never unusable* — the
# picker still offers a referenced line (flagged), and production may run from it
# as many times as it likes. What freezes is the receipt itself, because a
# corrected quantity or a re-attached material under an existing run would
# silently rewrite what that run's evidence claims was delivered.
_REFERENCE_SQL = (
    "SELECT 1 FROM recipe_items WHERE outward_purchase_line_id = %s "
    "UNION ALL "
    "SELECT 1 FROM production_run_items WHERE outward_purchase_line_id = %s "
    "LIMIT 1")


def is_line_referenced(conn: psycopg.Connection, line_id: int) -> bool:
    """True when a recipe row OR a historical run row points at this line."""
    return conn.execute(_REFERENCE_SQL, (line_id, line_id)).fetchone() is not None


def _referenced_line_ids(conn: psycopg.Connection, purchase_id: int) -> set:
    """The referenced line ids of one purchase, in a single set-probe query.

    Line ids come back rather than a bare boolean because the freeze is
    per-line: a receipt with one held and one loose line still accepts a
    correction of the loose one.
    """
    return {row[0] for row in conn.execute(
        "SELECT i.id FROM chemical_purchase_items i "
        "WHERE i.purchase_id = %s AND ("
        "EXISTS (SELECT 1 FROM recipe_items ri "
        "WHERE ri.outward_purchase_line_id = i.id) "
        "OR EXISTS (SELECT 1 FROM production_run_items pri "
        "WHERE pri.outward_purchase_line_id = i.id))",
        (purchase_id,)).fetchall()}


def _header_changed(new: Any, old: Any) -> bool:
    """Whether a supplied header value actually differs from the stored one.

    An absent (None) field is not a change — the route echoes only what the
    caller sent. A field sent with its current value is not a change either, so
    a client that resubmits the whole header to correct an UNreferenced line is
    not refused by the freeze.
    """
    if new is None:
        return False
    if isinstance(new, str) and isinstance(old, str):
        return new.strip() != old.strip()
    return str(new) != str(old)


def get_outward_sources(conn: psycopg.Connection, *, client_id: int,
                        chemical_id: Optional[int] = None) -> list:
    """Outward lines this client may source a recipe ingredient from.

    Cancelled receipts are gone (the delivery does not exist any more); a
    referenced/frozen line is KEPT and flagged, because freezing blocks edits
    and never usage. The picker is the only place an operator learns which
    receipts are already committed as evidence, so the flag is the contract.
    """
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        raise ValueError("client_id must be an integer")
    if client_id <= 0:
        raise ValueError("client_id must be a positive integer")

    sql = (
        "SELECT i.id, i.purchase_id, p.purchase_number, p.purchase_date, "
        "p.supplier_name, p.client_id, c.name, i.chemical_id, ch.name, "
        "i.quantity, i.unit, "
        "EXISTS (SELECT 1 FROM recipe_items ri "
        "WHERE ri.outward_purchase_line_id = i.id) "
        "OR EXISTS (SELECT 1 FROM production_run_items pri "
        "WHERE pri.outward_purchase_line_id = i.id) "
        "FROM chemical_purchase_items i "
        "JOIN chemical_purchases p ON p.id = i.purchase_id "
        "JOIN chemicals ch ON ch.id = i.chemical_id "
        "LEFT JOIN companies c ON c.id = p.client_id "
        "WHERE p.delivery_type = 'outward' AND p.cancelled = FALSE "
        "AND p.client_id = %s")
    params: list = [client_id]
    if chemical_id is not None:
        try:
            chemical_id = int(chemical_id)
        except (TypeError, ValueError):
            raise ValueError("chemical_id must be an integer")
        sql += " AND i.chemical_id = %s"
        params.append(chemical_id)
    sql += " ORDER BY p.purchase_date DESC, p.id DESC, i.chemical_id"
    return [{"line_id": r[0], "purchase_id": r[1], "purchase_number": r[2],
             "purchase_date": _iso(r[3]), "supplier_name": r[4],
             "client_id": r[5], "client_name": r[6], "chemical_id": r[7],
             "chemical_name": r[8], "quantity": float(r[9]), "unit": r[10],
             "frozen": bool(r[11])}
            for r in conn.execute(sql, params).fetchall()]


# --------------------------------------------------------------------------- #
# update (correction)
# --------------------------------------------------------------------------- #
def update_purchase(conn: psycopg.Connection, purchase_id: int, *,
                    expected_version: int, reason: str,
                    supplier_name: Optional[str] = None,
                    purchase_number: Optional[str] = None,
                    purchase_date=None, line_patches: Optional[list] = None,
                    actor: str) -> Optional[dict]:
    """Correct a posted receipt under an optimistic version lock.

    Identity fields (chemical_id, unit, delivery_type, client_id) are immutable
    after posting; only header prose and line quantities change. An in-house
    correction applies the DIFFERENCE (new - current posted) per line, in
    ascending chemical_id order, and a reversal that would drive stock negative
    is refused (update_stock raises ValueError -> 400) with nothing changed.

    Reference protection (Slice 2) is checked after the header lock and BEFORE
    any chemical lock or write, so a refused correction burns no version:
    a line a recipe or a production run points at is immutable (quantity,
    material, unit) with no admin override — an audit reason is not one — and
    while ANY line of the receipt is referenced the shared header (supplier,
    number, date) freezes too, because the run's evidence snapshots it.
    Unreferenced lines of the same receipt keep their audited corrections.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a correction reason is required")
    line_patches = line_patches or []
    if (supplier_name is None and purchase_number is None
            and purchase_date is None and not line_patches):
        raise ValueError("nothing to update")

    delivery_type = None
    reorder_names: list = []
    try:
        row = conn.execute(
            "SELECT delivery_type, purchase_number, supplier_name, "
            "purchase_date, cancelled, version "
            "FROM chemical_purchases WHERE id = %s FOR UPDATE",
            (purchase_id,)).fetchone()
        if row is None:
            raise ResourceMissing("purchase not found")
        delivery_type, old_number, old_supplier, old_date, cancelled, version = row
        if cancelled:
            raise LifecycleConflict("purchase is already cancelled")
        if version != expected_version:
            raise LifecycleConflict("purchase version conflict — reload and retry")

        items = conn.execute(
            "SELECT id, chemical_id, quantity, unit FROM chemical_purchase_items "
            "WHERE purchase_id = %s", (purchase_id,)).fetchall()
        item_map = {i[0]: i for i in items}
        referenced = _referenced_line_ids(conn, purchase_id)

        # Validate patches against this purchase's own lines before any write.
        seen = set()
        deltas: dict = {}
        for patch in line_patches:
            item_id = patch.get("id")
            if item_id not in item_map:
                raise ValueError(f"line item {item_id} is not on this purchase")
            if item_id in seen:
                raise ValueError("the same line item is patched twice")
            seen.add(item_id)
            new_q = _positive_quantity(patch.get("quantity"))
            chem_id = item_map[item_id][1]
            deltas[chem_id] = new_q - float(item_map[item_id][2])

        if referenced:
            frozen_lines = sorted(pid for pid in seen if pid in referenced)
            if frozen_lines:
                raise LifecycleConflict(
                    f"line(s) {frozen_lines} of purchase {old_number} are "
                    "referenced by a recipe or a production run — a referenced "
                    "line's material, unit and quantity are immutable (no "
                    "override; correct an unreferenced line instead)")
            held = (("supplier_name", supplier_name, old_supplier),
                    ("purchase_number", purchase_number, old_number),
                    ("purchase_date", purchase_date, old_date))
            held_header = [name for name, new, old in held
                           if _header_changed(new, old)]
            if held_header:
                raise LifecycleConflict(
                    f"purchase {old_number} is referenced by a recipe or a "
                    f"production run — its {', '.join(held_header)} cannot "
                    "change while the receipt is source evidence")

        eff_number = (purchase_number or old_number or "").strip()
        if delivery_type == "in_house" and deltas:
            locked = {}
            for chem_id in sorted(deltas):
                r = conn.execute(
                    "SELECT name, unit FROM chemicals WHERE id = %s FOR UPDATE",
                    (chem_id,)).fetchone()
                if r is None:
                    raise ValueError(f"chemical {chem_id} not found")
                locked[chem_id] = r
            for chem_id in sorted(deltas):
                delta = deltas[chem_id]
                if delta == 0:
                    continue
                name, unit = locked[chem_id]
                if not update_stock(conn, name, delta, unit,
                                    reason=f"Purchase {eff_number} correction",
                                    atomic=False):
                    raise ValueError(f"chemical '{name}' not found")
                reorder_names.append(name)

        for patch in line_patches:
            conn.execute(
                "UPDATE chemical_purchase_items SET quantity = %s WHERE id = %s",
                (patch.get("quantity"), patch.get("id")))

        set_clause = "version = version + 1, updated_at = now()"
        params: list = []
        if supplier_name is not None:
            set_clause += ", supplier_name = %s"
            params.append(supplier_name.strip())
        if purchase_number is not None:
            set_clause += ", purchase_number = %s"
            params.append(purchase_number.strip())
        if purchase_date is not None:
            set_clause += ", purchase_date = %s"
            params.append(purchase_date)
        params.append(purchase_id)
        conn.execute("UPDATE chemical_purchases SET " + set_clause +
                     " WHERE id = %s", params)

        log_audit_action(conn, "PURCHASE_UPDATE", "chemical_purchase", purchase_id,
                         user_id=actor, old_value=f"version={version}",
                         new_value=f"version={version + 1} reason={reason}",
                         atomic=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if delivery_type == "in_house" and reorder_names:
        try:
            sync_reorder_notifications(conn, reorder_names)
        except Exception:
            logger.exception("reorder sync failed after purchase %s update",
                             purchase_id)
    return get_purchase(conn, purchase_id)


# --------------------------------------------------------------------------- #
# cancel
# --------------------------------------------------------------------------- #
def cancel_purchase(conn: psycopg.Connection, purchase_id: int, *,
                    expected_version: int, reason: str, actor: str) -> Optional[dict]:
    """Void a receipt, reversing each line's CURRENT posted quantity.

    A correction may have changed a line since posting, so cancellation reverses
    the stored item quantity (not the original). A reversal that would drive
    stock negative is refused (400) with nothing changed. Repeated cancellation
    is a 409. Registration is never demoted: a once-stocked material stays
    visible at stock 0.

    Reference protection: while ANY line of the receipt is a recipe's or a run's
    source, the WHOLE cancellation is refused with a 409. Voiding one referenced
    line would void the header the evidence quotes (number, date, supplier), so
    the block is deliberately not line-scoped here.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a cancellation reason is required")

    delivery_type = None
    reorder_names: list = []
    try:
        row = conn.execute(
            "SELECT delivery_type, purchase_number, cancelled, version "
            "FROM chemical_purchases WHERE id = %s FOR UPDATE",
            (purchase_id,)).fetchone()
        if row is None:
            raise ResourceMissing("purchase not found")
        delivery_type, purchase_number, cancelled, version = row
        if cancelled:
            raise LifecycleConflict("purchase is already cancelled")
        if version != expected_version:
            raise LifecycleConflict("purchase version conflict — reload and retry")

        if _referenced_line_ids(conn, purchase_id):
            raise LifecycleConflict(
                f"purchase {purchase_number} has a line referenced by a recipe "
                "or a production run — it cannot be cancelled while it is "
                "source evidence")

        if delivery_type == "in_house":
            items = conn.execute(
                "SELECT chemical_id, quantity FROM chemical_purchase_items "
                "WHERE purchase_id = %s ORDER BY chemical_id",
                (purchase_id,)).fetchall()
            locked = {}
            for chem_id in sorted({i[0] for i in items}):
                r = conn.execute(
                    "SELECT name, unit FROM chemicals WHERE id = %s FOR UPDATE",
                    (chem_id,)).fetchone()
                if r is None:
                    raise ValueError(f"chemical {chem_id} not found")
                locked[chem_id] = r
            for chem_id, qty in items:
                name, unit = locked[chem_id]
                if not update_stock(conn, name, -float(qty), unit,
                                    reason=f"Cancel purchase {purchase_number}",
                                    atomic=False):
                    raise ValueError(f"chemical '{name}' not found")
                reorder_names.append(name)

        conn.execute(
            "UPDATE chemical_purchases SET cancelled = TRUE, cancelled_at = now(), "
            "cancelled_by = %s, cancellation_reason = %s, version = version + 1, "
            "updated_at = now() WHERE id = %s", (actor, reason, purchase_id))
        log_audit_action(conn, "PURCHASE_CANCEL", "chemical_purchase", purchase_id,
                         user_id=actor, new_value=f"cancelled: {reason}",
                         atomic=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if delivery_type == "in_house" and reorder_names:
        try:
            sync_reorder_notifications(conn, reorder_names)
        except Exception:
            logger.exception("reorder sync failed after purchase %s cancel",
                             purchase_id)
    return get_purchase(conn, purchase_id)