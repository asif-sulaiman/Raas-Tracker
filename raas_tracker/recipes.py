"""Invoice-scoped recipes and production reports.

A recipe is the production plan for **one invoice**. Its products are the
invoice's products, and each one gets its own formulation slot
(``recipe_products`` + ``recipe_items``). Production records one output per
product per run, and readiness asks whether every invoiced product has a
formulation *and* an output on a run linked to that invoice.

Water/solvent is whatever is not formulated: a slot's percentages may sum to
anything from 0 to 100, and only the listed chemicals are deducted. An
optional per-slot `water_percentage` overrides that remainder for display; it
is never added to a deduction or used as a production quantity, but
`ingredients + water` must total 100% (within 0.01) before a production run.
There is no batch basis on the recipe any more; the quantity is entered at
production time and prefilled from the invoice.
"""

import math
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional

import psycopg

from .audit import log_audit_action
from .db import get_connection, logger
from .stock import (
    convert_quantity,
    get_all_chemicals,
    get_unit_conversion,
    sync_reorder_notifications,
    update_stock,
)
from ._numeric import finite_values
from ._exceptions import (
    InvoiceAlreadyAdvanced,
    InvoiceNotFound,
    LifecycleConflict,
    RecipeHasRuns,
    RecipeInvoiceConflict,
    RecipeItemConflict,
    RecipeNameConflict,
    ResourceMissing,
)
from .uploads import csv_safe


# Percentages are REAL (float4), so a legitimate two-decimal split can sit a
# few 1e-7 away from 100. One hundredth of a percentage point absorbs that
# without letting a real formulation error through.
_BALANCE_TOLERANCE = 0.01


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #

_RECIPE_SELECT = """
    SELECT r.id, r.name, r.created_date, r.company_id, c.name,
           r.invoice_id, i.invoice_number, i.status, i.sale_id,
           s.pi_number, s.client_name, s.lc_id, lc.lc_number,
           (SELECT COUNT(*) FROM recipe_products rp WHERE rp.recipe_id = r.id),
           (SELECT COUNT(*) FROM recipe_products rp
             WHERE rp.recipe_id = r.id
               AND EXISTS (SELECT 1 FROM recipe_items ri
                            WHERE ri.recipe_product_id = rp.id)),
           (SELECT COUNT(*) FROM production_runs pr WHERE pr.recipe_id = r.id)
      FROM recipes r
      LEFT JOIN companies c ON c.id = r.company_id
      JOIN invoices i ON i.id = r.invoice_id
      JOIN sales s ON s.id = i.sale_id
      LEFT JOIN letters_of_credit lc ON lc.id = s.lc_id
"""


def _recipe_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0], "name": row[1], "created": row[2],
        "company_id": row[3], "company_name": row[4],
        "invoice_id": row[5], "invoice_number": row[6],
        "invoice_status": row[7], "sale_id": row[8],
        "pi_number": row[9], "client_name": row[10],
        "lc_id": row[11], "lc_number": row[12],
        "product_count": row[13], "formulated_count": row[14],
        "run_count": row[15],
    }


def list_recipes(conn: psycopg.Connection, company_id: Optional[int] = None,
                 invoice_id: Optional[int] = None) -> List[Dict[str, Any]]:
    query = _RECIPE_SELECT
    clauses = []
    params: list = []
    if company_id is not None:
        clauses.append("r.company_id = %s")
        params.append(company_id)
    if invoice_id is not None:
        clauses.append("r.invoice_id = %s")
        params.append(invoice_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.name"
    return [_recipe_dict(row) for row in conn.execute(query, params).fetchall()]


def get_recipe_by_id(conn: psycopg.Connection,
                     recipe_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        _RECIPE_SELECT + " WHERE r.id = %s", (recipe_id,)).fetchone()
    return _recipe_dict(row) if row else None


def get_recipe_by_invoice(conn: psycopg.Connection,
                          invoice_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        _RECIPE_SELECT + " WHERE r.invoice_id = %s", (invoice_id,)).fetchone()
    return _recipe_dict(row) if row else None


def get_recipe_by_name(conn: psycopg.Connection, company_id: int,
                       name: str) -> Optional[Dict[str, Any]]:
    """One-release name alias: the (company, name) pair still resolves."""
    row = conn.execute(
        _RECIPE_SELECT + " WHERE r.company_id = %s AND lower(r.name) = lower(%s)",
        (company_id, (name or "").strip())).fetchone()
    return _recipe_dict(row) if row else None


def find_recipe(conn: psycopg.Connection, company_id: int,
                name: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT id, name FROM recipes WHERE company_id = %s AND lower(name) = lower(%s)",
        (company_id, (name or "").strip())).fetchone()
    return {"id": row[0], "name": row[1]} if row else None


def _require_recipe(conn: psycopg.Connection,
                    recipe_id: int) -> Dict[str, Any]:
    recipe = get_recipe_by_id(conn, recipe_id)
    if not recipe:
        raise ResourceMissing(f"Recipe {recipe_id} not found")
    return recipe


def _invoice_product_lines(conn: psycopg.Connection,
                           invoice_id: int) -> List[Dict[str, Any]]:
    """Group an invoice's lines by product, summing in the first line's unit.

    Duplicate lines are normal (the same product invoiced twice). When their
    units differ, each line is converted to the first line's unit; a missing
    conversion is reported on the group instead of being mixed silently.
    """
    rows = conn.execute(
        "SELECT id, product_name, COALESCE(NULLIF(trim(unit), ''), 'KG'), quantity "
        "FROM invoice_items WHERE invoice_id = %s ORDER BY id",
        (invoice_id,)).fetchall()
    groups: Dict[str, Dict[str, Any]] = {}
    for _item_id, name, unit, qty in rows:
        name = (name or "").strip() if isinstance(name, str) else ""
        if not name:
            continue
        key = name.lower()
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": key, "product_name": name, "unit": unit,
                "lines": [], "invoiced_qty": 0.0, "unconverted": [],
            }
        group["lines"].append({"unit": unit, "quantity": float(qty or 0)})
    for group in groups.values():
        total = 0.0
        for line in group["lines"]:
            if line["unit"].upper() == group["unit"].upper():
                total += line["quantity"]
                continue
            converted = convert_quantity(
                conn, line["quantity"], line["unit"], group["unit"])
            if converted is None:
                group["unconverted"].append(
                    {"from_unit": line["unit"], "to_unit": group["unit"]})
            else:
                total += converted
        group["invoiced_qty"] = total
    return list(groups.values())


# --------------------------------------------------------------------------- #
# outward sourcing (Slice 2)
# --------------------------------------------------------------------------- #

# One read of a purchase line plus everything a source must agree with: the
# header's delivery type / client / cancellation, the line's material and unit
# snapshot, and the chemical's *registered* unit. `companies` is a LEFT JOIN so
# an in-house line (which carries no client) still returns a row and is refused
# for the right reason - "not an outward delivery", not "not found".
_SOURCE_SELECT = """
    SELECT i.id, i.purchase_id, i.chemical_id, i.quantity, i.unit,
           p.delivery_type, p.client_id, p.cancelled, p.purchase_number,
           p.purchase_date, p.supplier_name, co.name, ch.name, ch.unit
      FROM chemical_purchase_items i
      JOIN chemical_purchases p ON p.id = i.purchase_id
      JOIN chemicals ch ON ch.id = i.chemical_id
      LEFT JOIN companies co ON co.id = p.client_id
"""


def _iso(value: Any) -> Any:
    """date/datetime -> ISO text, so a DATE column reads back as ``YYYY-MM-DD``.

    psycopg hands DATE back as ``datetime.date``; the service contracts return
    text so a route response and a direct caller see the same shape.
    """
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else value


def _invoice_client_id(conn: psycopg.Connection, invoice_id: int) -> int:
    """The client an invoice belongs to, through its sale."""
    row = conn.execute(
        """SELECT s.company_id FROM invoices i
             JOIN sales s ON s.id = i.sale_id
            WHERE i.id = %s""", (invoice_id,)).fetchone()
    if not row:
        raise InvoiceNotFound(f"Invoice {invoice_id} not found")
    return row[0]


def check_source_pairing(source_type: Optional[str],
                         outward_purchase_line_id: Optional[int],
                         *, required: bool = True) -> str:
    """The (source_type, line) pairing rule, shared by every write path.

    The DB CHECK (`chk_recipe_item_source`) is the backstop, not the only
    check: a 400 naming the field beats an IntegrityError surfacing as a 500.
    """
    if source_type is None:
        if required:
            raise ValueError("source_type is required - 'in_house' or 'outward'")
        return "in_house"
    if source_type not in ("in_house", "outward"):
        raise ValueError(
            f"unknown source_type '{source_type}' - use 'in_house' or 'outward'")
    if source_type == "outward" and outward_purchase_line_id is None:
        raise ValueError(
            "an outward ingredient needs outward_purchase_line_id - the "
            "purchase line it is sourced from")
    if source_type == "in_house" and outward_purchase_line_id is not None:
        raise ValueError(
            "outward_purchase_line_id is only valid with source_type 'outward'")
    return source_type


def validate_source(conn: psycopg.Connection, *, chemical_id: int,
                    outward_purchase_line_id: Optional[int],
                    invoice_id: Optional[int] = None,
                    invoice_client_id: Optional[int] = None) -> Dict[str, Any]:
    """Prove one purchase line is a legal source for this ingredient.

    Every refusal names what failed, because the picker, the saved formulation
    and the production run all reach this same rule and an operator has to be
    able to tell "wrong client" from "cancelled delivery" from "different
    material". Returns the immutable snapshot fields the run records.

    A line that production has already referenced is **valid to use**: frozen
    means uneditable, never unusable. And nothing here caps the requirement at
    the delivered quantity - the need is `percentage x output`, the receipt is
    only evidence of where the material came from.
    """
    if outward_purchase_line_id is None:
        raise ValueError("an outward ingredient needs a purchase line id")
    if invoice_client_id is None:
        if invoice_id is None:
            raise ValueError("invoice_id or invoice_client_id is required")
        invoice_client_id = _invoice_client_id(conn, invoice_id)

    src = conn.execute(_SOURCE_SELECT + " WHERE i.id = %s",
                       (outward_purchase_line_id,)).fetchone()
    if not src:
        raise ValueError(
            f"outward purchase line {outward_purchase_line_id} not found")
    (line_id, purchase_id, line_chemical_id, quantity, line_unit,
     delivery_type, purchase_client_id, cancelled, purchase_number,
     purchase_date, supplier_name, client_name, chemical_name,
     registered_unit) = src

    if cancelled:
        raise ValueError(
            f"purchase {purchase_number} is cancelled - a cancelled delivery "
            f"cannot source '{chemical_name}'")
    if delivery_type != "outward":
        raise ValueError(
            f"purchase {purchase_number} is an in-house delivery - only an "
            f"outward purchase line can source a recipe ingredient "
            f"(line {line_id})")
    if line_chemical_id != chemical_id:
        raise ValueError(
            f"purchase line {line_id} delivered '{chemical_name}', not the "
            "material this ingredient formulates - the source line must carry "
            "the same material")
    if (line_unit or "").upper() != (registered_unit or "").upper():
        raise ValueError(
            f"purchase line {line_id} is in {line_unit} but '{chemical_name}' "
            f"is registered in {registered_unit} - an outward source must be "
            "in the material's registered unit")
    if purchase_client_id != invoice_client_id:
        raise ValueError(
            f"purchase {purchase_number} belongs to client "
            f"'{client_name}', not this invoice's client - an outward source "
            "must be a delivery to the same client")

    return {
        "outward_purchase_line_id": line_id,
        "purchase_id": purchase_id,
        "purchase_number": purchase_number,
        "purchase_date": _iso(purchase_date),
        "supplier_name": supplier_name,
        "client_id": purchase_client_id,
        "client_name": client_name,
        "source_qty": float(quantity or 0),
        "source_unit": line_unit,
    }


def _lock_outward_sources(conn: psycopg.Connection,
                          requirements: List[Dict[str, Any]], *,
                          invoice_id: int) -> None:
    """Lock, then re-read, every outward source before a run writes anything.

    Saving the formulation validated the line; that answer is minutes old by
    the time anyone presses produce. A correction or cancellation committed in
    between would otherwise leave a run whose evidence points at a receipt that
    no longer exists - or at another client's receipt.

    Lock order is the one the whole service agrees on: invoice (already held)
    -> purchase headers, ascending id -> chemicals, ascending id. Headers first
    because a purchase correction takes the header then the materials, so the
    two paths queue in the same direction instead of deadlocking.
    """
    line_ids = sorted({row["outward_purchase_line_id"] for row in requirements
                       if row["source_type"] == "outward"})
    if not line_ids:
        return
    invoice_client_id = _invoice_client_id(conn, invoice_id)
    purchase_ids = sorted(
        row[0] for row in conn.execute(
            "SELECT purchase_id FROM chemical_purchase_items "
            "WHERE id = ANY(%s::int[]) AND purchase_id IS NOT NULL",
            (line_ids,)).fetchall())
    for purchase_id in purchase_ids:
        conn.execute(
            "SELECT 1 FROM chemical_purchases WHERE id = %s FOR UPDATE",
            (purchase_id,))
    # requirements is already ascending by chemical_id (see
    # _aggregate_requirements), so this walk is the same order the deduction
    # loop would take anyway; update_stock re-locks in-house rows as a no-op.
    for row in requirements:
        conn.execute("SELECT 1 FROM chemicals WHERE id = %s FOR UPDATE",
                     (row["chemical_id"],))
    for row in requirements:
        if row["source_type"] != "outward":
            continue
        row.update(validate_source(
            conn, chemical_id=row["chemical_id"],
            outward_purchase_line_id=row["outward_purchase_line_id"],
            invoice_client_id=invoice_client_id))


def _slot_items(conn: psycopg.Connection,
                recipe_id: int) -> Dict[int, List[Dict[str, Any]]]:
    """One formulation slot's items, with their sourcing.

    The outward display fields ride along on the LEFT JOIN so a formulation
    read says *where* each ingredient comes from without a second query per
    row. A line that was cancelled or corrected since the recipe was saved
    still shows what it is: refusing to display it would hide the reason the
    next run refuses.
    """
    rows = conn.execute(
        """SELECT rp.id, ri.id, ri.percentage, c.id, c.name,
                  c.current_qty, c.unit, ri.source_type,
                  ri.outward_purchase_line_id, p.purchase_number,
                  p.purchase_date, p.supplier_name, co.name, i2.quantity,
                  i2.unit
             FROM recipe_products rp
             LEFT JOIN recipe_items ri ON ri.recipe_product_id = rp.id
             LEFT JOIN chemicals c ON c.id = ri.chemical_id
             LEFT JOIN chemical_purchase_items i2
                      ON i2.id = ri.outward_purchase_line_id
             LEFT JOIN chemical_purchases p ON p.id = i2.purchase_id
             LEFT JOIN companies co ON co.id = p.client_id
            WHERE rp.recipe_id = %s
            ORDER BY rp.id, c.name""",
        (recipe_id,)).fetchall()
    items: Dict[int, List[Dict[str, Any]]] = {}
    for (slot_id, item_id, pct, chem_id, chem_name, current, unit,
         source_type, line_id, purchase_number, purchase_date, supplier_name,
         client_name, source_qty, source_unit) in rows:
        bucket = items.setdefault(slot_id, [])
        if item_id is None:
            continue
        bucket.append({
            "id": item_id, "chemical_id": chem_id,
            "chemical_name": chem_name, "current_stock": current,
            "unit": unit, "percentage": float(pct or 0),
            "required_per_unit": (float(pct or 0) / 100.0),
            "source_type": source_type or "in_house",
            "outward_purchase_line_id": line_id,
            "purchase_number": purchase_number,
            "purchase_date": _iso(purchase_date),
            "supplier_name": supplier_name,
            "client_name": client_name,
            "source_qty": None if source_qty is None else float(source_qty),
            "source_unit": source_unit,
        })
    return items


def _produced_by_product(conn: psycopg.Connection,
                         recipe_id: int) -> Dict[str, float]:
    rows = conn.execute(
        """SELECT lower(o.product_name), SUM(o.qty_produced)
             FROM production_run_outputs o
             JOIN production_runs pr ON pr.id = o.run_id
            WHERE pr.recipe_id = %s
            GROUP BY lower(o.product_name)""",
        (recipe_id,)).fetchall()
    return {row[0]: float(row[1] or 0) for row in rows}


def list_recipe_products(conn: psycopg.Connection,
                         recipe_id: int) -> List[Dict[str, Any]]:
    """The invoice's products left-joined to their formulation slots.

    Derived on read: a product added to the invoice after the recipe exists
    appears immediately as a ghost (id null, formulated false) without a GET
    writing anything. A slot whose invoice line was later removed stays
    visible so its formulation is not silently orphaned.
    """
    recipe = _require_recipe(conn, recipe_id)
    invoice_products = _invoice_product_lines(conn, recipe["invoice_id"])
    slots = {}
    for row in conn.execute(
            "SELECT id, product_name, unit, water_percentage "
            "FROM recipe_products WHERE recipe_id = %s ORDER BY id",
            (recipe_id,)).fetchall():
        slots[(row[1] or "").strip().lower()] = {
            "id": row[0], "product_name": row[1], "unit": row[2],
            "water_percentage": row[3]}
    items = _slot_items(conn, recipe_id)
    produced = _produced_by_product(conn, recipe_id)

    products: List[Dict[str, Any]] = []
    seen = set()
    for group in invoice_products:
        slot = slots.get(group["key"])
        seen.add(group["key"])
        products.append(_product_row(group, slot, items, produced, on_invoice=True))
    for key, slot in slots.items():
        if key in seen:
            continue
        products.append(_product_row(
            {"key": key, "product_name": slot["product_name"],
             "unit": slot["unit"], "lines": [], "invoiced_qty": 0.0,
             "unconverted": []}, slot, items, produced, on_invoice=False))
    _attach_material_quantities(conn, products)
    products.sort(key=lambda p: (p["product_name"] or "").lower())
    return products


def _product_row(group, slot, items, produced, on_invoice=True) -> Dict[str, Any]:
    slot_items = items.get(slot["id"], []) if slot else []
    invoiced = float(group.get("invoiced_qty") or 0)
    built = float(produced.get(group["key"], 0))
    ingredient_pct = sum(i["percentage"] for i in slot_items)
    stored_water = slot.get("water_percentage") if slot else None
    if stored_water is not None:
        water_effective = float(stored_water)
    elif slot_items:
        water_effective = round(100.0 - ingredient_pct, 6)
    else:
        water_effective = None
    grand_total = (
        None if water_effective is None
        else round(ingredient_pct + water_effective, 6))
    return {
        "id": slot["id"] if slot else None,
        "on_invoice": on_invoice,
        "product_name": group["product_name"],
        "unit": (slot["unit"] if slot else group["unit"]) or "KG",
        "invoiced_qty": invoiced,
        "line_count": len(group.get("lines") or []),
        "lines": group.get("lines") or [],
        "unconverted": group.get("unconverted") or [],
        "produced_qty": built,
        "remaining_qty": max(invoiced - built, 0.0),
        "formulated": bool(slot_items),
        "percentage": ingredient_pct,
        "water_percentage": stored_water,
        "water_effective": water_effective,
        "grand_total": grand_total,
        "items": slot_items,
    }


def _attach_material_quantities(conn: psycopg.Connection,
                                products: List[Dict[str, Any]]) -> None:
    """Add what each row still needs and what the invoice leaves in stock.

    ``required_qty`` is a row's share of a chemical for the quantity still to
    produce, converted into the chemical's stock unit. ``stock_remaining``
    subtracts the whole invoice's need from current stock *once* per chemical,
    so every row that shares a chemical shows the same projection.

    ``water_qty`` is the effective water share in product units; it is display
    only and never deducted. A missing unit conversion (or an invoice line
    without one) leaves the quantities ``None`` rather than guessing, because
    an understated remainder would overstate the stock projection.
    """
    factors: Dict[tuple, Optional[float]] = {}
    needs: Dict[int, float] = {}
    unsafe: Dict[int, bool] = {}

    def factor(from_unit: str, to_unit: str) -> Optional[float]:
        key = ((from_unit or "").upper().strip(),
               (to_unit or "").upper().strip())
        if key not in factors:
            factors[key] = get_unit_conversion(conn, key[0], key[1])
        return factors[key]

    for product in products:
        remaining = max(float(product.get("remaining_qty") or 0.0), 0.0)
        water = product.get("water_effective")
        product["water_qty"] = (
            None if water is None
            else round(float(water) / 100.0 * remaining, 6))
        blind = bool(product.get("unconverted"))
        for item in product["items"]:
            qty = None
            if not blind:
                rate = factor(product["unit"], item["unit"])
                if rate is None:
                    unsafe[item["chemical_id"]] = True
                else:
                    qty = (item["percentage"] / 100.0) * remaining * rate
                    # Gross use is shown for every row, but only an in-house
                    # need draws on the stock projection. An outward row is
                    # covered by the client's own delivery: subtracting it from
                    # our balance - again, after a run already deducted it -
                    # would report material we never held as short.
                    if item["source_type"] != "outward":
                        needs[item["chemical_id"]] = (
                            needs.get(item["chemical_id"], 0.0) + qty)
            else:
                unsafe[item["chemical_id"]] = True
            item["required_qty"] = None if qty is None else round(qty, 6)

    for product in products:
        for item in product["items"]:
            chemical_id = item["chemical_id"]
            if item["source_type"] == "outward":
                item["stock_remaining"] = None
            elif unsafe.get(chemical_id):
                item["stock_remaining"] = None
            else:
                item["stock_remaining"] = round(
                    float(item["current_stock"] or 0.0)
                    - needs.get(chemical_id, 0.0), 6)


def get_recipe_detail(conn: psycopg.Connection,
                      recipe_id: int) -> Dict[str, Any]:
    recipe = _require_recipe(conn, recipe_id)
    products = list_recipe_products(conn, recipe_id)
    return {"recipe": recipe, "products": products}


def get_invoice_production_source(conn: psycopg.Connection,
                                  invoice_id: int) -> Dict[str, Any]:
    """Prefill data for the production modal: remaining quantity per product."""
    invoice = conn.execute(
        "SELECT id, invoice_number FROM invoices WHERE id = %s",
        (invoice_id,)).fetchone()
    if not invoice:
        raise InvoiceNotFound(f"Invoice {invoice_id} not found")
    recipe = get_recipe_by_invoice(conn, invoice_id)
    if recipe:
        products = [p for p in list_recipe_products(conn, recipe["id"])
                    if p["on_invoice"]]
    else:
        products = [
            _product_row(group, None, {}, {})
            for group in _invoice_product_lines(conn, invoice_id)
        ]
        products.sort(key=lambda p: (p["product_name"] or "").lower())
    return {
        "invoice_id": invoice[0],
        "invoice_number": invoice[1],
        "recipe": recipe,
        "products": products,
    }


def list_recipe_items_by_id(conn: psycopg.Connection,
                            recipe_id: int) -> List[Dict[str, Any]]:
    """Flattened ingredients across every product (report/CLI compatibility)."""
    out = []
    for product in list_recipe_products(conn, recipe_id):
        for item in product["items"]:
            out.append({
                "product_name": product["product_name"],
                "recipe_id": recipe_id,
                **item,
            })
    return out


def list_recipe_items(conn: psycopg.Connection, company_id: int,
                      recipe_name: str) -> List[Dict[str, Any]]:
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    return list_recipe_items_by_id(conn, recipe["id"]) if recipe else []


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #


def sync_recipe_products(conn: psycopg.Connection, recipe_id: int) -> int:
    """Create a slot for every distinct product on the recipe's invoice.

    Idempotent, and called inside the caller's transaction: creating the recipe,
    adding an invoice line and producing all use the same slot mapping. The
    expression index on ``lower(product_name)`` is what makes the upsert safe
    under concurrency.
    """
    result = conn.execute(
        """INSERT INTO recipe_products (recipe_id, product_name, unit)
           SELECT %s, p.product_name, p.unit
             FROM (
                 SELECT DISTINCT ON (lower(ii.product_name))
                        ii.product_name,
                        COALESCE(NULLIF(trim(ii.unit), ''), 'KG') AS unit
                   FROM invoice_items ii
                   JOIN recipes r ON r.invoice_id = ii.invoice_id
                  WHERE r.id = %s
                    AND NULLIF(trim(ii.product_name), '') IS NOT NULL
                  ORDER BY lower(ii.product_name), ii.id
             ) p
           ON CONFLICT (recipe_id, lower(product_name)) DO NOTHING""",
        (recipe_id, recipe_id))
    return result.rowcount


def add_recipe(conn: psycopg.Connection, name: str, company_id: int,
               invoice_id: int) -> Dict[str, Any]:
    """Create the one recipe for an invoice, with a slot per product."""
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("name is required")
    if company_id is None:
        raise ValueError("company_id is required")
    if invoice_id is None:
        raise ValueError("invoice_id is required")
    if not conn.execute("SELECT 1 FROM companies WHERE id = %s",
                        (company_id,)).fetchone():
        raise ValueError("unknown company")

    invoice = conn.execute(
        """SELECT i.id, i.status FROM invoices i
             JOIN sales s ON s.id = i.sale_id
            WHERE i.id = %s AND s.company_id = %s""",
        (invoice_id, company_id)).fetchone()
    if not invoice:
        raise InvoiceNotFound(f"Invoice {invoice_id} not found for this company")
    if invoice[1] not in ("planned", "produced"):
        raise LifecycleConflict(
            f"invoice is {invoice[1]}; a recipe is created while it is "
            "planned or produced")
    if conn.execute("SELECT 1 FROM recipes WHERE invoice_id = %s",
                    (invoice_id,)).fetchone():
        raise RecipeInvoiceConflict(
            f"Invoice {invoice_id} already has a recipe")
    if not conn.execute(
            "SELECT 1 FROM invoice_items WHERE invoice_id = %s LIMIT 1",
            (invoice_id,)).fetchone():
        raise ValueError("invoice has no products to formulate")

    try:
        recipe_id = conn.execute(
            "INSERT INTO recipes (name, company_id, invoice_id) "
            "VALUES (%s, %s, %s) RETURNING id",
            (name, company_id, invoice_id)).fetchone()[0]
        sync_recipe_products(conn, recipe_id)
        log_audit_action(conn, "RECIPE_CREATE", "recipe", recipe_id,
                         new_value=f"{name} (invoice {invoice_id})",
                         atomic=False)
        conn.commit()
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise RecipeInvoiceConflict(
            f"Invoice {invoice_id} already has a recipe")
    logger.info("Created recipe '%s' for invoice %s", name, invoice_id)
    # Re-read for the derived counts; the row was committed just above.
    return _require_recipe(conn, recipe_id)


def _chemical(conn: psycopg.Connection, name: str):
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise ValueError("chemical is required")
    row = conn.execute(
        "SELECT id, name, unit, current_qty FROM chemicals "
        "WHERE lower(name) = lower(%s)", (name,)).fetchone()
    if not row:
        raise ValueError(f"chemical '{name}' not found")
    return row


def _slot_for_product(conn: psycopg.Connection, recipe: Dict[str, Any],
                      product_name: str) -> int:
    product_name = (product_name or "").strip() if isinstance(product_name, str) else ""
    if not product_name:
        raise ValueError("product_name is required")
    on_invoice = conn.execute(
        """SELECT 1 FROM invoice_items
            WHERE invoice_id = %s AND lower(product_name) = lower(%s) LIMIT 1""",
        (recipe["invoice_id"], product_name)).fetchone()
    if not on_invoice:
        raise ValueError(
            f"product '{product_name}' is not on invoice "
            f"{recipe['invoice_number']}")
    unit = conn.execute(
        """SELECT COALESCE(NULLIF(trim(unit), ''), 'KG')
             FROM invoice_items
            WHERE invoice_id = %s AND lower(product_name) = lower(%s)
            ORDER BY id LIMIT 1""",
        (recipe["invoice_id"], product_name)).fetchone()[0]
    conn.execute(
        """INSERT INTO recipe_products (recipe_id, product_name, unit)
           VALUES (%s, %s, %s)
           ON CONFLICT (recipe_id, lower(product_name)) DO NOTHING""",
        (recipe["id"], product_name, unit))
    return conn.execute(
        "SELECT id FROM recipe_products "
        "WHERE recipe_id = %s AND lower(product_name) = lower(%s)",
        (recipe["id"], product_name)).fetchone()[0]


def _slot_percentage_total(conn: psycopg.Connection,
                           recipe_product_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(percentage), 0) FROM recipe_items "
        "WHERE recipe_product_id = %s", (recipe_product_id,)).fetchone()
    return float(row[0] or 0)


def add_recipe_item(conn: psycopg.Connection, recipe_id: int, product_name: str,
                    chemical_name: str, percentage: float, *,
                    source_type: Optional[str] = "in_house",
                    outward_purchase_line_id: Optional[int] = None) -> bool:
    """Add (or replace) one chemical in one product's formulation.

    ``source_type`` defaults to in-house, so every Slice 1 caller is unchanged.
    An outward item is only accepted once ``validate_source`` has proved the
    line is this invoice's client's own outward delivery of the same material,
    in the material's registered unit, and not cancelled.
    """
    finite_values(percentage)
    if percentage < 0:
        raise ValueError("percentage must be 0 or greater")
    source = check_source_pairing(source_type, outward_purchase_line_id)
    recipe = _require_recipe(conn, recipe_id)
    # Resolve the chemical before touching a slot: an unknown chemical must not
    # leave an uncommitted ghost-slot INSERT for the caller's close() to commit.
    chem = _chemical(conn, chemical_name)
    if source == "outward":
        validate_source(conn, chemical_id=chem[0],
                        outward_purchase_line_id=outward_purchase_line_id,
                        invoice_id=recipe["invoice_id"])
    slot_id = _slot_for_product(conn, recipe, product_name)

    existing = conn.execute(
        "SELECT id FROM recipe_items WHERE recipe_product_id = %s AND chemical_id = %s",
        (slot_id, chem[0])).fetchone()
    if existing:
        raise RecipeItemConflict(
            f"'{chem[1]}' is already in the {product_name} formulation")
    total = _slot_percentage_total(conn, slot_id) + percentage
    if total > 100.0 + 1e-9:
        raise ValueError(
            f"ingredients for {product_name} total {total:g}% - the maximum is 100%")

    item_id = conn.execute(
        """INSERT INTO recipe_items (recipe_product_id, chemical_id, percentage,
                                     source_type, outward_purchase_line_id)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (slot_id, chem[0], percentage, source, outward_purchase_line_id)).fetchone()[0]
    log_audit_action(
        conn, "RECIPE_ITEM_ADD", "recipe_item", item_id,
        new_value=(f"{recipe['name']}:{product_name}:{chem[1]}:{percentage}"
                   f":{source}"
                   + (f":{outward_purchase_line_id}" if source == "outward" else "")),
        atomic=False)
    conn.commit()
    return True


def set_product_water(conn: psycopg.Connection, recipe_id: int,
                      product_name: str,
                      percentage: Optional[float]) -> bool:
    """Set (or clear, with ``None``) one product's water percentage.

    Display and gate only: the detail read reports ``100 - ingredients`` when
    this is NULL and the stored value otherwise. Nothing is deducted, but
    ``ingredients + water`` must total 100% (within 0.01) before production,
    so a stored value that misses that total blocks the product's run while
    still allowing the formulation to be saved and previewed.
    """
    if percentage is not None:
        finite_values(percentage)
        if percentage < 0 or percentage > 100:
            raise ValueError("water percentage must be between 0 and 100")
    recipe = _require_recipe(conn, recipe_id)
    slot_id = _slot_for_product(conn, recipe, product_name)
    old = conn.execute(
        "SELECT water_percentage FROM recipe_products WHERE id = %s",
        (slot_id,)).fetchone()[0]
    conn.execute(
        "UPDATE recipe_products SET water_percentage = %s WHERE id = %s",
        (percentage, slot_id))
    log_audit_action(
        conn, "RECIPE_WATER_SET", "recipe_product", slot_id,
        old_value="auto" if old is None else str(old),
        new_value="auto" if percentage is None else str(percentage),
        atomic=False)
    conn.commit()
    return True


def update_recipe_item(conn: psycopg.Connection, recipe_id: int, item_id: int,
                       new_percentage: float, *,
                       source_type: Optional[str] = None,
                       outward_purchase_line_id: Optional[int] = None) -> bool:
    """Edit one ingredient: its share and, for Slice 2, where it is sourced.

    A caller that names no source keeps the row's existing one — a formulation
    tuned from the Slice 1 screen sends only the percentage, and silently
    reverting an outward row to in-house there would make the run deduct stock
    the client's own delivery already covers.
    """
    finite_values(new_percentage)
    if new_percentage < 0:
        raise ValueError("percentage must be 0 or greater")
    recipe = _require_recipe(conn, recipe_id)
    row = conn.execute(
        """SELECT ri.id, ri.percentage, ri.recipe_product_id, ri.chemical_id,
                  ri.source_type, ri.outward_purchase_line_id
             FROM recipe_items ri
             JOIN recipe_products rp ON rp.id = ri.recipe_product_id
            WHERE ri.id = %s AND rp.recipe_id = %s""",
        (item_id, recipe_id)).fetchone()
    if not row:
        return False
    total = (_slot_percentage_total(conn, row[2]) - float(row[1] or 0)
             + new_percentage)
    if total > 100.0 + 1e-9:
        raise ValueError(
            f"ingredients for this product total {total:g}% - "
            "the maximum is 100%")

    old_source = row[4] or "in_house"
    old_line = row[5]
    if source_type is None and outward_purchase_line_id is None:
        source, line = old_source, old_line
    else:
        source = check_source_pairing(
            source_type if source_type is not None else old_source,
            outward_purchase_line_id)
        line = outward_purchase_line_id
    if source == "outward":
        # Validated before the UPDATE: a refused switch must leave the row
        # exactly as it was, not half-written with a source that fails.
        validate_source(conn, chemical_id=row[3],
                        outward_purchase_line_id=line,
                        invoice_id=recipe["invoice_id"])
    if (source, line) == (old_source, old_line):
        conn.execute(
            "UPDATE recipe_items SET percentage = %s WHERE id = %s",
            (new_percentage, item_id))
    else:
        conn.execute(
            """UPDATE recipe_items
                  SET percentage = %s, source_type = %s,
                      outward_purchase_line_id = %s
                WHERE id = %s""",
            (new_percentage, source, line, item_id))
    log_audit_action(conn, "RECIPE_ITEM_UPDATE", "recipe_item", item_id,
                     old_value=f"{row[1]}:{old_source}:{old_line}",
                     new_value=f"{new_percentage}:{source}:{line}",
                     atomic=False)
    conn.commit()
    return True


def delete_recipe_item(conn: psycopg.Connection, recipe_id: int,
                       item_id: int) -> bool:
    _require_recipe(conn, recipe_id)
    row = conn.execute(
        """SELECT ri.id, ri.percentage, ri.chemical_id, c.name
             FROM recipe_items ri
             JOIN recipe_products rp ON rp.id = ri.recipe_product_id
             JOIN chemicals c ON c.id = ri.chemical_id
            WHERE ri.id = %s AND rp.recipe_id = %s""",
        (item_id, recipe_id)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM recipe_items WHERE id = %s", (item_id,))
    log_audit_action(conn, "RECIPE_ITEM_DELETE", "recipe_item", item_id,
                     old_value=f"{row[3]}:{row[1]}", atomic=False)
    conn.commit()
    return True


def update_recipe(conn: psycopg.Connection, recipe_id: int,
                  new_name: str) -> bool:
    """Rename a recipe. The invoice it belongs to is not editable."""
    recipe = _require_recipe(conn, recipe_id)
    if new_name is None:
        return True
    final_name = new_name.strip() if isinstance(new_name, str) else ""
    if not final_name:
        raise ValueError("name is required")
    if final_name == recipe["name"]:
        return True
    dup = conn.execute(
        "SELECT id FROM recipes WHERE company_id = %s "
        "AND lower(name) = lower(%s) AND id <> %s",
        (recipe["company_id"], final_name, recipe_id)).fetchone()
    if dup:
        raise RecipeNameConflict(
            f"Recipe '{final_name}' already exists for this company")
    try:
        conn.execute("UPDATE recipes SET name = %s WHERE id = %s",
                     (final_name, recipe_id))
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise RecipeNameConflict(
            f"Recipe '{final_name}' already exists for this company")
    log_audit_action(conn, "RECIPE_UPDATE", "recipe", recipe_id,
                     old_value=recipe["name"], new_value=final_name,
                     atomic=False)
    conn.commit()
    return True


def delete_recipe(conn: psycopg.Connection, recipe_id: int) -> bool:
    """Delete a recipe that has never produced. Runs block the delete (409)."""
    recipe = _require_recipe(conn, recipe_id)
    if conn.execute("SELECT 1 FROM production_runs WHERE recipe_id = %s LIMIT 1",
                    (recipe_id,)).fetchone():
        raise RecipeHasRuns(
            "production has run from this recipe; it cannot be deleted")
    conn.execute("DELETE FROM recipes WHERE id = %s", (recipe_id,))
    log_audit_action(conn, "RECIPE_DELETE", "recipe", recipe_id,
                     old_value=recipe["name"], atomic=False)
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# production
# --------------------------------------------------------------------------- #


def _normalized_outputs(outputs) -> List[Dict[str, Any]]:
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("outputs must list at least one product")
    normalized = []
    seen = set()
    for raw in outputs:
        if not isinstance(raw, dict):
            raise ValueError("each output must be an object")
        name = (raw.get("product_name") or "").strip() \
            if isinstance(raw.get("product_name"), str) else ""
        qty = raw.get("quantity")
        if not name:
            raise ValueError("each output needs a product_name")
        if not isinstance(qty, (int, float)) or not math.isfinite(qty) or qty <= 0:
            raise ValueError(f"quantity for '{name}' must be finite and > 0")
        key = name.lower()
        if key in seen:
            raise ValueError(f"product '{name}' appears twice in outputs")
        seen.add(key)
        normalized.append({"product_name": name, "quantity": float(qty)})
    return normalized


def _aggregate_requirements(conn: psycopg.Connection, recipe: Dict[str, Any],
                            outputs: List[Dict[str, Any]],
                            require_balanced: bool = False
                            ) -> List[Dict[str, Any]]:
    """Convert per-product needs into one row per **source**, in stock units.

    A product's formulation percentages apply to that product's quantity. Each
    need is converted from the slot's unit to the chemical's stock unit before
    the rows are summed, so giving two products the same chemical produces
    exactly one deduction. A missing conversion is a 400 naming both units:
    guessing KG would silently over- or under-deduct.

    Grouping is by ``(chemical_id, source_type, outward_purchase_line_id)``,
    not by chemical alone: the same material drawn once from our store and once
    from the client's outward delivery is two consumption records, and folding
    them together would deduct the client's material from our shelf. Two
    different outward lines are two rows too — each one is its own evidence.

    A requirement is *always* ``percentage x output quantity``. It is never
    capped by, nor derived from, the quantity that was delivered: a 25 KG
    delivery does not make 40 KG of formulation impossible, and nothing here
    depletes a receipt.

    ``require_balanced`` is production-only: ingredients + water must total
    100% within ``_BALANCE_TOLERANCE``. Reports keep the default ``False`` so
    an unbalanced formulation is still previewed as a planning tool.
    """
    products = {p["product_name"].lower(): p
                for p in list_recipe_products(conn, recipe["id"])}
    aggregated: Dict[tuple, Dict[str, Any]] = {}
    for output in outputs:
        product = products.get(output["product_name"].lower())
        if not product or not product["on_invoice"]:
            raise ValueError(
                f"product '{output['product_name']}' is not on invoice "
                f"{recipe['invoice_number']}")
        if not product["formulated"]:
            raise ValueError(
                f"'{product['product_name']}' has no formulation yet")
        if product["unconverted"]:
            pairs = ", ".join(
                f"{u['from_unit']} -> {u['to_unit']}"
                for u in product["unconverted"])
            raise ValueError(
                f"invoice lines for '{product['product_name']}' need a unit "
                f"conversion ({pairs}) - add it in Unit Conversions")
        if require_balanced:
            total = product["grand_total"]
            # +1e-9 because 100 - 99.99 is 0.010000000000005 in binary float.
            if total is None or \
                    abs(total - 100.0) > _BALANCE_TOLERANCE + 1e-9:
                shown = "no total" if total is None else f"{total:g}%"
                raise ValueError(
                    f"'{product['product_name']}' formulation totals {shown} "
                    "- ingredients + water must equal 100% before production")
        for item in product["items"]:
            required = (item["percentage"] / 100.0) * output["quantity"]
            converted = convert_quantity(
                conn, required, product["unit"], item["unit"])
            if converted is None:
                raise ValueError(
                    f"no unit conversion from {product['unit']} to "
                    f"{item['unit']} for chemical '{item['chemical_name']}' "
                    f"on '{product['product_name']}' - add it in "
                    "Unit Conversions")
            source_type = item["source_type"] or "in_house"
            line_id = item["outward_purchase_line_id"]
            key = (item["chemical_id"], source_type, line_id)
            row = aggregated.get(key)
            if row is None:
                row = aggregated[key] = {
                    "chemical_id": item["chemical_id"],
                    "chemical_name": item["chemical_name"],
                    "unit": item["unit"],
                    "source_type": source_type,
                    "outward_purchase_line_id": line_id,
                    # An outward row has no in-house balance to test: the
                    # material never stood on our shelf, so showing a number
                    # here would invite a shortage warning about stock we were
                    # never meant to hold.
                    "current_stock": (None if source_type == "outward"
                                      else item["current_stock"]),
                    "required_qty": 0.0,
                    "products": [],
                }
                if source_type == "outward":
                    # The snapshot travels with the row so the run can record
                    # where the material came from without re-reading the
                    # purchase per product.
                    row["purchase_number"] = item["purchase_number"]
                    row["purchase_date"] = item["purchase_date"]
                    row["supplier_name"] = item["supplier_name"]
                    row["client_name"] = item["client_name"]
                    row["source_qty"] = item["source_qty"]
                    row["source_unit"] = item["source_unit"]
            row["required_qty"] += converted
            row["products"].append({
                "product_name": product["product_name"],
                "quantity": output["quantity"],
                "percentage": item["percentage"],
                "required_qty": converted,
            })
    for row in aggregated.values():
        # What this row takes out of the store: everything for an in-house
        # row, nothing for an outward one.
        row["deducted_qty"] = (0.0 if row["source_type"] == "outward"
                               else row["required_qty"])
    # Slice 1: lock chemicals in ascending chemical_id order, matching the
    # purchase service (purchases.create_purchase sorts entries by chem_id).
    # update_stock still locks by name (1:1 with id); only the call order changes,
    # so production and purchase corrections cannot deadlock on overlapping sets.
    # The source half of the key only breaks ties within one chemical, so the
    # lock walk stays ascending.
    return sorted(aggregated.values(),
                  key=lambda r: (r["chemical_id"],
                                 r["outward_purchase_line_id"] or 0))


def _stock_status(current_stock: Any, required: float, *,
                  outward: bool) -> tuple:
    """``(shortage, status)`` for one report row.

    An outward row answers "how much is used", not "what does the store have":
    the material arrived on the client's own delivery, so there is no balance
    to compare against, nothing to warn about, and reporting a shortage would
    invite a purchase that duplicates material already delivered.
    """
    if outward:
        return None, "OUTWARD"
    diff = current_stock - required
    status = ("SHORTAGE" if diff < 0
              else "EXACT" if diff == 0 else "SURPLUS")
    return diff, status


def generate_report(conn: psycopg.Connection, company_id: int, recipe_id: int,
                    outputs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Have/need per chemical for the given per-product production quantities.

    Gross use and stock impact are different questions: see `_stock_status`.
    """
    recipe = _require_recipe(conn, recipe_id)
    if recipe["company_id"] != company_id:
        raise ResourceMissing(f"Recipe {recipe_id} not found for this company")
    normalized = _normalized_outputs(outputs)
    report = []
    for row in _aggregate_requirements(conn, recipe, normalized):
        outward = row["source_type"] == "outward"
        shortage, status = _stock_status(row["current_stock"],
                                         row["required_qty"], outward=outward)
        report.append({
            "chemical_name": row["chemical_name"],
            "current_stock": row["current_stock"],
            "required_total": row["required_qty"],
            "unit": row["unit"],
            "shortage": shortage,
            "status": status,
            "source_type": row["source_type"],
            "outward_purchase_line_id": row["outward_purchase_line_id"],
            "purchase_number": row.get("purchase_number"),
            "purchase_date": row.get("purchase_date"),
            "supplier_name": row.get("supplier_name"),
            "client_name": row.get("client_name"),
            "source_qty": row.get("source_qty"),
            "source_unit": row.get("source_unit"),
            "products": row["products"],
        })
    return report


def generate_multi_recipe_report(conn: psycopg.Connection,
                                 selections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine several recipes into one chemical report.

    ``selections`` are ``{recipe_id, outputs}``; each row keeps the per-recipe
    breakdown so the CSV can show where a chemical is used.
    """
    combined: Dict[tuple, Dict[str, Any]] = {}
    for selection in selections:
        recipe = _require_recipe(conn, selection["recipe_id"])
        outputs = _normalized_outputs(selection.get("outputs") or [])
        for row in _aggregate_requirements(conn, recipe, outputs):
            # Same key rule as the single-recipe report: a chemical sourced
            # from a client's delivery and from our shelf are two lines, or the
            # outward need would be charged against stock we never hold.
            key = (row["chemical_name"].lower(), row["unit"].upper(),
                   row["source_type"], row["outward_purchase_line_id"])
            target = combined.setdefault(key, {
                "chemical_name": row["chemical_name"],
                "current_stock": row["current_stock"],
                "required_total": 0.0,
                "unit": row["unit"],
                "source_type": row["source_type"],
                "outward_purchase_line_id": row["outward_purchase_line_id"],
                "purchase_number": row.get("purchase_number"),
                "purchase_date": row.get("purchase_date"),
                "supplier_name": row.get("supplier_name"),
                "client_name": row.get("client_name"),
                "source_qty": row.get("source_qty"),
                "source_unit": row.get("source_unit"),
                "recipes": [],
                "products": [],
            })
            target["required_total"] += row["required_qty"]
            target["recipes"].append({
                "recipe_id": recipe["id"],
                "recipe_name": recipe["name"],
                "qty_from_this_recipe": row["required_qty"],
            })
            target["products"].extend(row["products"])

    report = []
    for data in combined.values():
        shortage, status = _stock_status(
            data["current_stock"], data["required_total"],
            outward=data["source_type"] == "outward")
        report.append({**data, "shortage": shortage, "status": status})
    report.sort(key=lambda x: x["chemical_name"])
    return report


def create_production_run(
    conn: psycopg.Connection,
    company_id: int,
    recipe_id: int,
    outputs: List[Dict[str, Any]],
    order_number: Optional[str] = None,
    batch_number: Optional[str] = None,
    production_date: Optional[str] = None,
    material_number: Optional[str] = None,
    packing: Optional[str] = None,
    invoice_number: Optional[str] = None,
    notes: Optional[str] = None,
    created_by: Optional[int] = None,
    atomic: bool = True,
    deferred_reorder: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Record one run: per-product outputs, one deduction per chemical.

    The links to the sale and invoice are derived from the recipe's own
    invoice — the request cannot name another invoice, so a run on invoice A
    can never unlock invoice B. The whole run is one transaction: the
    production_runs row, outputs, links, status flips and every deduction
    commit together or not at all.
    """
    if not atomic and deferred_reorder is None:
        raise ValueError("atomic=False requires a deferred_reorder list")
    normalized = _normalized_outputs(outputs)

    recipe = _require_recipe(conn, recipe_id)
    if recipe["company_id"] != company_id:
        raise ResourceMissing(f"Recipe {recipe_id} not found for this company")

    from raas_tracker.sales import _INVOICE_STATUS_ORDER, _now_str

    invoice_id = recipe["invoice_id"]
    sale_id = recipe["sale_id"]
    invoice_row = conn.execute(
        "SELECT status FROM invoices WHERE id = %s FOR UPDATE",
        (invoice_id,)).fetchone()
    if not invoice_row:
        raise InvoiceNotFound(f"Invoice {invoice_id} not found")
    current = invoice_row[0]
    if current in ("shipped", "paid"):
        raise InvoiceAlreadyAdvanced(f"invoice already {current}")

    requirements = _aggregate_requirements(
        conn, recipe, normalized, require_balanced=True)

    try:
        # Purchase headers -> chemicals -> sources, before the first write of
        # the run: a concurrent correction or cancel must lose this race, not
        # slip between validation and evidence.
        _lock_outward_sources(conn, requirements, invoice_id=invoice_id)
        prod_date = production_date or date.today().isoformat()
        total_qty = sum(o["quantity"] for o in normalized)
        run_id = conn.execute(
            """INSERT INTO production_runs
               (recipe_id, order_number, batch_number, production_date,
                qty_produced, notes, created_by, material_number, packing,
                invoice_number)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (recipe_id, order_number, batch_number, prod_date, total_qty,
             notes, created_by, material_number, packing,
             invoice_number or recipe["invoice_number"])).fetchone()[0]

        products = {p["product_name"].lower(): p
                    for p in list_recipe_products(conn, recipe_id)}
        for output in normalized:
            product = products[output["product_name"].lower()]
            conn.execute(
                """INSERT INTO production_run_outputs
                   (run_id, recipe_product_id, product_name, unit, qty_produced)
                   VALUES (%s, %s, %s, %s, %s)""",
                (run_id, product["id"], product["product_name"],
                 product["unit"], output["quantity"]))

        conn.execute(
            """INSERT INTO production_run_links (run_id, sale_id, invoice_id)
               VALUES (%s, %s, %s)""",
            (run_id, sale_id, invoice_id))
        if current in _INVOICE_STATUS_ORDER and \
                _INVOICE_STATUS_ORDER.index(current) < \
                _INVOICE_STATUS_ORDER.index("produced"):
            conn.execute("UPDATE invoices SET status = 'produced' WHERE id = %s",
                         (invoice_id,))
        conn.execute(
            """UPDATE sales
                  SET shipment_status = CASE
                        WHEN shipment_status IS NULL
                          OR shipment_status = 'production_running'
                        THEN 'production_done'
                        ELSE shipment_status END,
                      updated_at = %s
                WHERE id = %s""",
            (_now_str(), sale_id))

        for row in requirements:
            if row["source_type"] == "outward":
                # The client's delivery IS the source: nothing stands in our
                # store to deduct, and the receipt is not depleted by being
                # used. The row is the evidence, recorded exactly as the source
                # looked under the lock taken above, so the run stays
                # auditable after the purchase is corrected or the recipe is
                # re-formulated.
                conn.execute(
                    """INSERT INTO production_run_items
                       (run_id, chemical_id, chemical_name, required_qty,
                        deducted_qty, unit, source_type,
                        outward_purchase_line_id, purchase_number,
                        purchase_date, supplier_name, client_name,
                        source_qty, source_unit)
                       VALUES (%s, %s, %s, %s, 0, %s, 'outward', %s, %s,
                               %s, %s, %s, %s, %s)""",
                    (run_id, row["chemical_id"], row["chemical_name"],
                     row["required_qty"], row["unit"],
                     row["outward_purchase_line_id"], row["purchase_number"],
                     row["purchase_date"], row["supplier_name"],
                     row["client_name"], row["source_qty"],
                     row["source_unit"]))
                continue
            try:
                success = update_stock(
                    conn, row["chemical_name"], -row["required_qty"],
                    unit=row["unit"],
                    reason=f"Production {batch_number or run_id} for {recipe['name']}",
                    atomic=False)
            except ValueError as exc:
                if "insufficient stock" not in str(exc):
                    raise
                raise ValueError(
                    f"insufficient stock: {row['chemical_name']} needs "
                    f"{row['required_qty']:g} {row['unit']}, "
                    f"{row['current_stock']:g} {row['unit']} available") from exc
            if not success:
                raise ValueError(
                    f"chemical '{row['chemical_name']}' disappeared during production")
            conn.execute(
                """INSERT INTO production_run_items
                   (run_id, chemical_id, chemical_name, required_qty,
                    deducted_qty, unit, source_type,
                    outward_purchase_line_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'in_house', NULL)""",
                (run_id, row["chemical_id"], row["chemical_name"],
                 row["required_qty"], row["required_qty"], row["unit"]))

        log_audit_action(
            conn, "PRODUCTION_RUN_CREATE", "production_run", run_id,
            new_value=(f"{recipe['name']} "
                       + ", ".join(f"{o['product_name']} x{o['quantity']:g}"
                                   for o in normalized)),
            atomic=False)
    except Exception:
        conn.rollback()
        raise

    # Only an in-house deduction can pull a chemical below its reorder level.
    # Notifying for an outward row would raise a buy alert for material the
    # client has already delivered to us.
    in_house_names = [row["chemical_name"] for row in requirements
                      if row["source_type"] == "in_house"]
    if atomic:
        conn.commit()
        sync_reorder_notifications(conn, in_house_names)
    else:
        deferred_reorder.extend(in_house_names)

    final_report = generate_report(conn, company_id, recipe_id, normalized)
    logger.info("Created production run %s for recipe '%s' (%s outputs)",
                run_id, recipe["name"], len(normalized))
    return {
        "run_id": run_id,
        "recipe_id": recipe_id,
        "recipe_name": recipe["name"],
        "invoice_id": invoice_id,
        "sale_id": sale_id,
        "invoice_ids": [invoice_id],
        "sale_ids": [sale_id],
        "outputs": normalized,
        "production_qty": sum(o["quantity"] for o in normalized),
        "shortage_report": final_report,
        "warnings": [r for r in final_report if r["status"] == "SHORTAGE"],
    }


# --------------------------------------------------------------------------- #
# presentation helpers (CLI/CSV)
# --------------------------------------------------------------------------- #


def print_report(report_data: List[Dict[str, Any]], recipe_name: str,
                 production_qty) -> None:
    if not report_data:
        print("No report data available.")
        return
    print()
    print("=" * 72)
    print(f"  PRODUCTION REPORT: {recipe_name}")
    print(f"  Quantities: {production_qty}")
    print("=" * 72)
    print()
    print(f"  {'Chemical':<30} {'Have':>10} {'Need':>10} {'Diff':>10} {'Status':<10}")
    print("-" * 72)
    total_shortage = 0
    shortage_count = 0
    for item in report_data:
        unit = item["unit"]
        diff = item["shortage"]
        status = item["status"]
        if status == "SHORTAGE":
            status_str = "[SHORTAGE]"
            total_shortage += abs(diff)
            shortage_count += 1
        elif status == "EXACT":
            status_str = "[EXACT]"
        elif status == "OUTWARD":
            # Sourced from the client's own delivery: no balance, no diff.
            status_str = "[OUTWARD]"
        else:
            status_str = "[SURPLUS]"
        have = "     n/a" if item["current_stock"] is None \
            else f"{item['current_stock']:>8.1f}"
        delta = "     n/a" if diff is None else f"{diff:>+8.1f} {unit}"
        print(f"  {item['chemical_name']:<30} {have} {unit} "
              f"{item['required_total']:>8.1f} {unit} {delta} {status_str:<10}")
    print("-" * 72)
    print(f"  chemicals: {len(report_data)}  shortage: {shortage_count}")
    print("=" * 72)


def export_report_to_csv(report_data: List[Dict[str, Any]], recipe_name: str,
                         production_qty=None, output_path: str = None) -> str:
    """Write the production report, including the per-product breakdown."""
    import csv

    if output_path is None:
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", recipe_name)[:50] or "report"
        output_path = os.path.join(
            os.path.dirname(__file__), f"report_{safe_name}.csv")

    # ``shortage`` is None for an outward row (no stock impact at all), so the
    # summary must skip those rows rather than compare None to 0 - a numeric
    # format or comparison on None is a 500 in the export route.
    shortages = [abs(item["shortage"]) for item in report_data
                 if item["shortage"] is not None and item["shortage"] < 0]
    total_shortage = sum(shortages)
    shortage_count = len(shortages)

    with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Production Report", "", "", "", "", "", ""])
        writer.writerow(["Recipe(s)", csv_safe(recipe_name), "", "", "", "", ""])
        writer.writerow(["Quantities", csv_safe(str(production_qty)), "", "", "", "", ""])
        writer.writerow(["", "", "", "", "", "", ""])
        writer.writerow(["Chemical Name", "Current Stock", "Unit",
                         "Required Total", "Shortage/Surplus", "Status",
                         "Breakdown by Product"])
        for item in report_data:
            breakdown = " | ".join(
                f"{p['product_name']}: {p['required_qty']:.2f} {item['unit']}"
                for p in item.get("products") or [])
            writer.writerow([
                csv_safe(item["chemical_name"]),
                "" if item["current_stock"] is None else item["current_stock"],
                csv_safe(item["unit"]), item["required_total"],
                item["shortage"], csv_safe(item["status"]),
                csv_safe(breakdown)])
        writer.writerow(["", "", "", "", "", "", ""])
        writer.writerow(["SUMMARY", "", "", "", "", "", ""])
        writer.writerow(["Total unique chemicals", len(report_data), "", "", "", "", ""])
        writer.writerow(["Chemicals OK", len(report_data) - shortage_count, "", "", "", "", ""])
        writer.writerow(["Chemicals SHORTAGE", shortage_count, "", "", "", "", ""])
        if shortage_count > 0:
            writer.writerow(["Total shortage amount", total_shortage,
                             "KG", "", "", "", ""])
    print(f"Report exported to: {output_path}")
    return output_path


def list_chemicals_cli() -> None:
    conn = get_connection()
    chemicals = get_all_chemicals(conn)
    conn.close()
    if not chemicals:
        print("No chemicals in database.")
        return
    print(f"\n{'Chemical Name':<30} {'Stock':>8} {'Unit':>6} {'Last Updated':>12}")
    print("-" * 58)
    for chem in chemicals:
        print(f"{chem['name']:<30} {chem['qty']:>8} {chem['unit']:>6} "
              f"{chem['last_updated']:>12}")
