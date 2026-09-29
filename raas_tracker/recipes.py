"""Recipes plus production reports."""

import psycopg
from .audit import log_audit_action
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .db import get_connection, logger
from .stock import get_all_chemicals, update_stock
from .uploads import csv_safe

def add_recipe(conn: psycopg.Connection, name: str, total_quantity: float = 1, water_percentage: float = 0,
             company_id: int = None, product_name: str = None) -> bool:
    """Create a new recipe.

    Args:
        conn: Database connection
        name: Recipe name (e.g. "Liquid Soap Batch A")
        total_quantity: Total quantity this recipe produces (e.g. 1000 liters, 15000 KG)
        water_percentage: Optional water percentage (default 0, calculated as 100 - sum(ingredient %) if left at 0)
        company_id: Owning company from the register (required for new masters)
        product_name: Registered product name for that company

    Returns:
        True if created, False if recipe already exists
    """
    if water_percentage < 0 or water_percentage > 100:
        raise ValueError("water_percentage must be between 0 and 100")
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if company_id is None:
        raise ValueError("company_id is required")
    company = conn.execute("SELECT id FROM companies WHERE id = %s",
                           (company_id,)).fetchone()
    if not company:
        raise ValueError("unknown company")
    product_name = (product_name or "").strip()
    if not product_name:
        raise ValueError("product_name is required")
    registered = conn.execute(
        """SELECT 1 FROM sale_items si JOIN sales s ON s.id = si.sale_id
           WHERE s.company_id = %s AND lower(si.product_name) = lower(%s)""",
        (company_id, product_name)).fetchone()
    if not registered:
        raise ValueError(f"product '{product_name}' is not registered for this company")
    try:
        cursor = conn.execute(
            "INSERT INTO recipes (name, total_quantity, water_percentage, company_id, product_name) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, total_quantity, water_percentage, company_id, product_name)
        )
        recipe_id = cursor.fetchone()[0]
        conn.commit()
        logger.info("Created recipe: '%s' (total quantity: %s)", name, total_quantity)
        log_audit_action(conn, "RECIPE_CREATE", "recipe", recipe_id, new_value=name)
        return True
    except psycopg.IntegrityError:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Recipe '%s' already exists.", name)
        return False


def get_recipe_by_name(conn: psycopg.Connection, company_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Get recipe info by name for a specific company."""
    cursor = conn.execute(
        "SELECT r.id, r.name, r.total_quantity, r.water_percentage, r.created_date, "
        "r.company_id, r.product_name, c.name FROM recipes r "
        "LEFT JOIN companies c ON c.id = r.company_id WHERE r.company_id = %s AND lower(r.name) = lower(%s)",
        (company_id, name)
    )
    row = cursor.fetchone()
    if row:
        return {"id": row[0], "name": row[1], "total_quantity": row[2], "water_percentage": row[3], "created": row[4],
                "company_id": row[5], "product_name": row[6], "company_name": row[7]}
    return None


def find_recipe(conn: psycopg.Connection, company_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Find a company's master recipe by name (for duplicate deep-links)."""
    row = conn.execute(
        "SELECT id, name FROM recipes WHERE company_id = %s AND lower(name) = lower(%s)",
        (company_id, (name or "").strip())).fetchone()
    if row:
        return {"id": row[0], "name": row[1]}
    return None


def _check_percentage_total(conn: psycopg.Connection, recipe_id: int,
                            water_percentage: float,
                            items_total: float) -> None:
    """Reject negative percentages and totals over 100% (water + items)."""
    if water_percentage < 0 or items_total < 0:
        raise ValueError("percentages must be 0 or greater")
    if water_percentage + items_total > 100.0 + 1e-9:
        raise ValueError(
            f"water ({water_percentage:g}%) + ingredients ({items_total:g}%) "
            f"exceed 100%")


def _items_percentage_total(conn: psycopg.Connection, recipe_id: int) -> float:
    """Sum of item percentages for a recipe."""
    row = conn.execute(
        "SELECT COALESCE(SUM(percentage), 0) FROM recipe_items WHERE recipe_id = %s",
        (recipe_id,)
    ).fetchone()
    return float(row[0] or 0)


def add_recipe_item(conn: psycopg.Connection, company_id: int, recipe_name: str, chemical_name: str, 
                    percentage: float) -> bool:
    """Add a chemical to a recipe with percentage of total product yield.
    
    Args:
        conn: Database connection
        company_id: Company ID (required for isolation)
        recipe_name: Recipe name
        chemical_name: Chemical name
        percentage: Percentage of total batch (e.g., 20.0 for 20%)
    
    Returns:
        True if added successfully
    """
    # Check recipe exists
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found for company %s. Create it first.", recipe_name, company_id)
        return False
    
    # Check chemical exists
    chemical = conn.execute(
        "SELECT id FROM chemicals WHERE name = %s", (chemical_name,)
    ).fetchone()
    if not chemical:
        logger.warning("Chemical '%s' not found in database.", chemical_name)
        return False

    # Check if item already exists in recipe
    existing = conn.execute(
        "SELECT id FROM recipe_items WHERE recipe_id = %s AND chemical_id = %s",
        (recipe["id"], chemical[0])
    ).fetchone()
    if existing:
        logger.warning("'%s' already in recipe '%s'. Use update_recipe_item instead.",
                       chemical_name, recipe_name)
        return False
    
    required_qty_per_unit = percentage / 100.0
    if percentage < 0:
        raise ValueError("percentage must be 0 or greater")
    _check_percentage_total(conn, recipe["id"], recipe.get("water_percentage") or 0,
                            _items_percentage_total(conn, recipe["id"]) + percentage)
    cursor = conn.execute(
        "INSERT INTO recipe_items (recipe_id, chemical_id, percentage, required_qty_per_unit) VALUES (%s, %s, %s, %s) RETURNING id",
        (recipe["id"], chemical[0], percentage, required_qty_per_unit)
    )
    item_id = cursor.fetchone()[0]
    conn.commit()
    logger.info("Added '%s' to recipe '%s': %s%%", chemical_name, recipe_name, percentage)
    log_audit_action(conn, "RECIPE_ITEM_ADD", "recipe_item", item_id,
                     new_value=f"{recipe_name}:{chemical_name}:{percentage}")
    return True


def list_recipes(conn: psycopg.Connection,
                 company_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """List all recipes with owning company (optionally one company's only)."""
    query = (
        "SELECT r.id, r.name, r.total_quantity, r.water_percentage, "
        "r.created_date, r.company_id, r.product_name, c.name "
        "FROM recipes r LEFT JOIN companies c ON c.id = r.company_id "
    )
    params: list = []
    if company_id is not None:
        query += "WHERE r.company_id = %s "
        params.append(company_id)
    query += "ORDER BY r.name"
    cursor = conn.execute(query, params)
    return [
        {"id": row[0], "name": row[1], "total_quantity": row[2], "water_percentage": row[3], "created": row[4],
         "company_id": row[5], "product_name": row[6], "company_name": row[7]}
        for row in cursor.fetchall()
    ]


def list_recipe_items(conn: psycopg.Connection, company_id: int, recipe_name: str) -> List[Dict[str, Any]]:
    """List all chemicals in a recipe with percentages and required quantities."""
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found for company %s.", recipe_name, company_id)
        return []
    
    cursor = conn.execute("""
        SELECT r.name as recipe_name, r.total_quantity, 
               c.name as chemical_name, c.current_qty, c.unit,
               ri.required_qty_per_unit, ri.percentage
          FROM recipe_items ri
          JOIN recipes r ON ri.recipe_id = r.id
          JOIN chemicals c ON ri.chemical_id = c.id
          WHERE r.company_id = %s AND r.name = %s
          ORDER BY c.name
      """, (company_id, recipe_name))
    
    items = []
    for row in cursor.fetchall():
        yield_val = row[1]
        pct = row[6] if len(row) > 6 and row[6] is not None else row[5] * 100
        req_unit = row[5]
        batch_qty = yield_val * req_unit
        items.append({
            "recipe_name": row[0],
            "total_quantity": yield_val,
            "chemical_name": row[2],
            "current_stock": row[3],
            "unit": row[4],
            "required_per_unit": req_unit,
            "percentage": pct,
            "batch_qty": batch_qty
        })
    return items


def update_recipe_item(conn: psycopg.Connection, company_id: int, recipe_name: str, chemical_name: str,
                       new_percentage: float) -> bool:
    """Update the percentage of a chemical in a recipe."""
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found for company %s.", recipe_name, company_id)
        return False
    
    chemical = conn.execute("SELECT id FROM chemicals WHERE name = %s", (chemical_name,)).fetchone()
    if not chemical:
        logger.warning("Chemical '%s' not found.", chemical_name)
        return False

    old = conn.execute(
        "SELECT id, percentage FROM recipe_items WHERE recipe_id = %s AND chemical_id = %s",
        (recipe["id"], chemical[0])
    ).fetchone()
    if not old:
        logger.warning("'%s' not found in recipe '%s'.", chemical_name, recipe_name)
        return False

    new_qty_per_unit = new_percentage / 100.0
    if new_percentage < 0:
        raise ValueError("percentage must be 0 or greater")
    current_total = _items_percentage_total(conn, recipe["id"])
    _check_percentage_total(conn, recipe["id"], recipe.get("water_percentage") or 0,
                            current_total - (old[1] or 0) + new_percentage)
    result = conn.execute(
        "UPDATE recipe_items SET percentage = %s, required_qty_per_unit = %s WHERE recipe_id = %s AND chemical_id = %s",
        (new_percentage, new_qty_per_unit, recipe["id"], chemical[0])
    )
    conn.commit()

    if result.rowcount > 0:
        logger.info("Updated '%s' in '%s': now %s%%", chemical_name, recipe_name, new_percentage)
        log_audit_action(conn, "RECIPE_ITEM_UPDATE", "recipe_item", old[0],
                         old_value=str(old[1]), new_value=str(new_percentage))
        return True
    else:
        logger.warning("'%s' not found in recipe '%s'.", chemical_name, recipe_name)
        return False


def delete_recipe_item(conn: psycopg.Connection, company_id: int, recipe_name: str, chemical_name: str) -> bool:
    """Remove a chemical from a recipe."""
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found for company %s.", recipe_name, company_id)
        return False
    
    chemical = conn.execute("SELECT id FROM chemicals WHERE name = %s", (chemical_name,)).fetchone()
    if not chemical:
        logger.warning("Chemical '%s' not found.", chemical_name)
        return False

    old = conn.execute(
        "SELECT id, percentage FROM recipe_items WHERE recipe_id = %s AND chemical_id = %s",
        (recipe["id"], chemical[0])
    ).fetchone()
    if not old:
        logger.warning("'%s' was not in recipe '%s'.", chemical_name, recipe_name)
        return False

    result = conn.execute(
        "DELETE FROM recipe_items WHERE recipe_id = %s AND chemical_id = %s",
        (recipe["id"], chemical[0])
    )
    conn.commit()

    if result.rowcount > 0:
        logger.info("Removed '%s' from recipe '%s'", chemical_name, recipe_name)
        log_audit_action(conn, "RECIPE_ITEM_DELETE", "recipe_item", old[0],
                         old_value=f"{recipe_name}:{chemical_name}:{old[1]}")
        return True
    else:
        logger.warning("'%s' was not in recipe '%s'.", chemical_name, recipe_name)
        return False


def delete_recipe(conn: psycopg.Connection, company_id: int, name: str) -> bool:
    """Delete a recipe and all its items."""
    recipe = get_recipe_by_name(conn, company_id, name)
    if not recipe:
        logger.warning("Recipe '%s' not found for company %s.", name, company_id)
        return False
    
    conn.execute("DELETE FROM recipe_items WHERE recipe_id = %s", (recipe["id"],))
    conn.execute("DELETE FROM recipes WHERE id = %s", (recipe["id"],))
    conn.commit()
    logger.info("Deleted recipe '%s' and all its items.", name)
    log_audit_action(conn, "RECIPE_DELETE", "recipe", recipe["id"], old_value=name)
    return True


def update_recipe(conn: psycopg.Connection, company_id: int, name: str, 
                  total_quantity: float = None, water_percentage: float = None) -> bool:
    """Update recipe metadata (total_quantity and/or water_percentage).
    
    Args:
        conn: Database connection
        company_id: Company ID (required for isolation)
        name: Recipe name
        total_quantity: New total quantity (or None to keep current)
        water_percentage: New water percentage (or None to keep current)
    
    Returns:
        True if updated, False if recipe not found
    """
    recipe = get_recipe_by_name(conn, company_id, name)
    if not recipe:
        logger.warning("Recipe '%s' not found for company %s.", name, company_id)
        return False
    
    updates = []
    params = []
    if total_quantity is not None:
        updates.append("total_quantity = %s")
        params.append(total_quantity)
    if water_percentage is not None:
        if water_percentage < 0 or water_percentage > 100:
            raise ValueError("water_percentage must be between 0 and 100")
        _check_percentage_total(conn, recipe["id"], water_percentage,
                                _items_percentage_total(conn, recipe["id"]))
        updates.append("water_percentage = %s")
        params.append(water_percentage)

    if not updates:
        return True

    params.append(recipe["id"])
    conn.execute(f"UPDATE recipes SET {', '.join(updates)} WHERE id = %s", params)
    conn.commit()
    logger.info("Updated recipe '%s': %s", name, updates)
    log_audit_action(conn, "RECIPE_UPDATE", "recipe", recipe["id"],
                     old_value=name, new_value=",".join(updates))
    return True


# ============================================================
# PHASE 3: PRODUCTION REPORT GENERATION
# ============================================================


def generate_report(conn: psycopg.Connection, company_id: int, recipe_name: str, production_qty: float) -> List[Dict[str, Any]]:
    """Generate a production report showing have vs need for each chemical.
    
    Args:
        conn: Database connection
        company_id: Company ID (required for isolation)
        recipe_name: Name of the recipe to report on
        production_qty: How many units to produce
    
    Returns:
        List of dicts with:
        - chemical_name: str
        - current_stock: float (what we have)
        - required_total: float (total needed for production_qty)
        - unit: str (KG or PCS)
        - shortage: float (negative = not enough, positive = surplus)
        - status: str ("OK", "SHORTAGE", "EXACT", "SURPLUS")
    """
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    if not recipe:
        return []
    
    items = list_recipe_items(conn, company_id, recipe_name)
    if not items:
        return []
    
    report = []
    for item in items:
        current = item["current_stock"]
        required = item["required_per_unit"] * production_qty
        shortage = current - required  # negative = not enough
        
        if shortage < 0:
            status = "SHORTAGE"
        elif shortage == 0:
            status = "EXACT"
        else:
            status = "SURPLUS"
        
        report.append({
            "chemical_name": item["chemical_name"],
            "current_stock": current,
            "required_total": required,
            "unit": item["unit"],
            "shortage": shortage,
            "status": status
        })
    
    return report


def generate_multi_recipe_report(conn: psycopg.Connection, recipe_selections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate a combined production report for multiple recipes.
    
    Args:
        conn: Database connection
        recipe_selections: List of dicts with {"company_id": int, "recipe_name": str, "production_qty": float}
    
    Returns:
        Combined list of report items with same chemicals merged together.
    """
    combined = {}
    
    for selection in recipe_selections:
        company_id = selection["company_id"]
        recipe_name = selection["recipe_name"]
        production_qty = selection["production_qty"]
        
        items = list_recipe_items(conn, company_id, recipe_name)
        for item in items:
            chem_name = item["chemical_name"]
            required = item["required_per_unit"] * production_qty
            
            if chem_name in combined:
                combined[chem_name]["required_total"] += required
            else:
                combined[chem_name] = {
                    "chemical_name": chem_name,
                    "current_stock": item["current_stock"],
                    "required_total": required,
                    "unit": item["unit"],
                    "recipes": []
                }
            
            combined[chem_name]["recipes"].append({
                "recipe_name": recipe_name,
                "production_qty": production_qty,
                "percentage": item["percentage"],
                "qty_from_this_recipe": required
            })
    
    report = []
    for chem_name, data in combined.items():
        shortage = data["current_stock"] - data["required_total"]
        if shortage < 0:
            status = "SHORTAGE"
        elif shortage == 0:
            status = "EXACT"
        else:
            status = "SURPLUS"
        
        report.append({
            "chemical_name": data["chemical_name"],
            "current_stock": data["current_stock"],
            "required_total": data["required_total"],
            "unit": data["unit"],
            "shortage": shortage,
            "status": status,
            "recipes": data["recipes"]
        })
    report.sort(key=lambda x: x["chemical_name"])
    return report


def _owned_sale_ids(conn: psycopg.Connection, company_id: int,
                    sale_ids: List[int]) -> List[int]:
    """Fail closed unless every referenced sale belongs to ``company_id``.

    ``production_run_links`` writes drive a customer-visible status flip
    (``sales.shipment_status``), so an unscoped id from the request body would
    let a run for customer A advance customer B's pipeline. Mirrors the recipe
    lookup discipline (``get_recipe_by_name`` is scoped by company_id).
    """
    owned: List[int] = []
    for sale_id in dict.fromkeys(sale_ids):
        row = conn.execute(
            "SELECT id FROM sales WHERE id = %s AND company_id = %s",
            (sale_id, company_id)
        ).fetchone()
        if not row:
            raise ValueError(f"linked sale {sale_id} not found for this company")
        owned.append(row[0])
    return owned


def _owned_invoice_ids(conn: psycopg.Connection, company_id: int,
                       invoice_ids: List[int]) -> List[int]:
    """Fail closed unless every referenced invoice belongs to ``company_id``."""
    owned: List[int] = []
    for invoice_id in dict.fromkeys(invoice_ids):
        row = conn.execute(
            """SELECT i.id, i.sale_id FROM invoices i
               JOIN sales s ON s.id = i.sale_id
               WHERE i.id = %s AND s.company_id = %s""",
            (invoice_id, company_id)
        ).fetchone()
        if not row:
            raise ValueError(f"linked invoice {invoice_id} not found for this company")
        owned.append(row[0])
    return owned


def create_production_run(
    conn: psycopg.Connection,
    company_id: int,
    recipe_name: str,
    production_qty: float,
    order_number: str = None,
    batch_number: str = None,
    production_date: str = None,
    material_number: str = None,
    packing: str = None,
    invoice_number: str = None,
    sale_ids: list[int] | None = None,
    invoice_ids: list[int] | None = None,
    notes: str = None,
    created_by: int = None,
    atomic: bool = True,
    deferred_reorder: list[str] | None = None,
) -> dict:
    """
    Create a production run by snapshotting the recipe formula and deducting stock.

    The whole run — the ``production_runs`` row, every ``production_run_links``
    row, the linked invoice/sale status flips and EVERY ingredient deduction —
    is ONE transaction. Nothing is committed until the last ingredient is
    written, so a shortage on the Nth ingredient leaves no partially deducted
    batch and no orphan run behind (the caller retries cleanly).

    Args:
        conn: Database connection
        company_id: Company ID (required for isolation)
        recipe_name: Name of the recipe to produce
        production_qty: Quantity to produce
        order_number: Optional order reference (e.g., sale order number)
        batch_number: Optional batch number
        production_date: Production date (ISO format, defaults to today)
        notes: Optional notes
        created_by: User ID who initiated the run
        sale_ids: Sales to link — must belong to ``company_id`` or the call fails
        invoice_ids: Invoices to link — must belong to ``company_id`` or the call fails.
            Producing advances an invoice ONLY forward (``planned`` ->
            ``produced``); an invoice already at or past ``produced``
            (``produced``/``booked``) keeps its status and ``shipped``/``paid``
            refuse the run outright (``ValueError`` -> 409).
        atomic: True (default) commits the run itself and flushes its reorder
            notifications. False leaves the transaction to the caller (the
            multi-recipe produce route commits once for the whole request);
            with False the caller MUST supply ``deferred_reorder`` (a list) and
            flush it with sync_reorder_notifications() after its own commit.

    Returns:
        Dict with run_id, shortage_report, and any warnings
    """
    from datetime import date as _date
    from raas_tracker.stock import update_stock, sync_reorder_notifications
    from raas_tracker.sales import _now_str, _INVOICE_STATUS_ORDER

    if not atomic and deferred_reorder is None:
        raise ValueError("atomic=False requires a deferred_reorder list")

    # Validate inputs
    if production_qty <= 0:
        raise ValueError("production_qty must be positive")

    # Get recipe
    recipe = get_recipe_by_name(conn, company_id, recipe_name)
    if not recipe:
        raise ValueError(f"Recipe '{recipe_name}' not found for company {company_id}")

    # Get recipe items for snapshotting
    items = list_recipe_items(conn, company_id, recipe_name)
    if not items:
        raise ValueError(f"Recipe '{recipe_name}' has no ingredients")

    # Generate shortage report (preview)
    shortage_report = generate_report(conn, company_id, recipe_name, production_qty)

    # Calculate required quantities for each ingredient
    run_items = []
    total_required = 0
    for item in list_recipe_items(conn, company_id, recipe_name):
        required_qty = item["required_per_unit"] * production_qty
        total_required += required_qty
        run_items.append({
            "chemical_id": item.get("chemical_id"),
            "chemical_name": item["chemical_name"],
            "required_qty": required_qty,
            "unit": item["unit"],
        })

    # Fail closed on cross-company links BEFORE any write happens, so a
    # rejected request never touches stock, runs or links.
    verified_sale_ids = _owned_sale_ids(conn, company_id, sale_ids or [])
    verified_invoice_ids = _owned_invoice_ids(conn, company_id, invoice_ids or [])

    try:
        # Create production run record
        prod_date = production_date or _date.today().isoformat()
        cursor = conn.execute(
            """INSERT INTO production_runs
               (recipe_id, order_number, batch_number, production_date, qty_produced, notes, created_by,
                material_number, packing, invoice_number)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (recipe["id"], order_number, batch_number, prod_date, production_qty, notes, created_by,
             material_number, packing, invoice_number)
        )
        run_id = cursor.fetchone()[0]

        # Link production run to sales and invoices (company-scoped ids only)
        linked_sale_ids: List[int] = []
        for sale_id in verified_sale_ids:
            conn.execute(
                """INSERT INTO production_run_links (run_id, sale_id)
                   VALUES (%s, %s)""",
                (run_id, sale_id)
            )
            if sale_id not in linked_sale_ids:
                linked_sale_ids.append(sale_id)
        linked_invoice_ids: List[int] = []
        for invoice_id in verified_invoice_ids:
            # sale_id is derived from the invoice row itself — never guessed.
            invoice_row = conn.execute(
                "SELECT sale_id FROM invoices WHERE id = %s", (invoice_id,)
            ).fetchone()
            if not invoice_row:
                logger.warning("Invoice %s not found; skipping production run link", invoice_id)
                continue
            sale_id = invoice_row[0]
            conn.execute(
                """INSERT INTO production_run_links (run_id, sale_id, invoice_id)
                   VALUES (%s, %s, %s)""",
                (run_id, sale_id, invoice_id)
            )
            if sale_id not in linked_sale_ids:
                linked_sale_ids.append(sale_id)
            linked_invoice_ids.append(invoice_id)
            # Forward-only, same ordering the rest of the lifecycle uses
            # (raas_tracker.sales._INVOICE_STATUS_ORDER). Producing marks the
            # batch built; it must never drag an invoice BACKWARDS:
            #   planned  -> produced  (the one move produce owns)
            #   produced -> no-op     (re-run, already at that step)
            #   booked   -> no-op     (booking is later than producing; the
            #                             production fact lives on the sale's
            #                             shipment_status, see below)
            #   shipped/paid -> refused 409 (terminal, unchanged behaviour)
            inv_row = conn.execute(
                "SELECT status FROM invoices WHERE id = %s FOR UPDATE",
                (invoice_id,)
            ).fetchone()
            current = inv_row[0] if inv_row else None
            if current in ("shipped", "paid"):
                raise ValueError(f"invoice already {current}")
            if current in _INVOICE_STATUS_ORDER and \
                    _INVOICE_STATUS_ORDER.index(current) < _INVOICE_STATUS_ORDER.index("produced"):
                conn.execute(
                    "UPDATE invoices SET status = %s WHERE id = %s",
                    ("produced", invoice_id)
                )
            else:
                logger.info(
                    "Invoice %s is already at '%s'; production recorded without "
                    "moving its lifecycle status (forward-only).", invoice_id, current)

        # Production finished: advance shipment_status to production_done.
        # CASE keeps later states (production_done, ship_booked, ...) intact.
        if linked_sale_ids:
            conn.execute(
                """UPDATE sales
                      SET shipment_status = CASE
                            WHEN shipment_status IS NULL
                              OR shipment_status = 'production_running'
                            THEN 'production_done'
                            ELSE shipment_status END,
                        updated_at = %s
                    WHERE id = ANY(%s)""",
                (_now_str(), linked_sale_ids)
            )

        # Snapshot formula and deduct stock — every deduction in this
        # transaction (atomic=False), never one commit per ingredient.
        for item in run_items:
            required = item["required_qty"]
            chem_name = item["chemical_name"]

            # Deduce stock (warn-and-allow: clamps at zero)
            success = update_stock(conn, chem_name, -item["required_qty"],
                                   reason=f"Production {batch_number or run_id} for {recipe_name}",
                                   atomic=False)
            if not success:
                logger.warning("Chemical '%s' not found for deduction", chem_name)
                # Still record with deducted=0
                deducted = 0
            else:
                deducted = item["required_qty"]

            # Snapshot the formula (required and actual deducted)
            conn.execute(
                """INSERT INTO production_run_items
                   (run_id, chemical_id, chemical_name, required_qty, deducted_qty, unit)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (run_id, item.get("chemical_id"), item["chemical_name"],
                 item["required_qty"], deducted, item["unit"])
            )

        log_audit_action(conn, "PRODUCTION_RUN_CREATE", "production_run", run_id,
                         new_value=f"{recipe_name} x{production_qty}", atomic=False)
    except Exception:
        # Nothing above was committed: drop the whole run.
        conn.rollback()
        raise

    if atomic:
        conn.commit()
        # Committed: the reorder alerts for the touched ingredients can go out.
        sync_reorder_notifications(conn, [i["chemical_name"] for i in run_items])
    else:
        # Caller-owned transaction: hand the names over so the caller can flush
        # them once, after it commits (or drop them with a rollback).
        deferred_reorder.extend(i["chemical_name"] for i in run_items)

    # Re-generate shortage report after deduction for accuracy
    final_shortage_report = generate_report(conn, company_id, recipe_name, production_qty)

    logger.info("Created production run %s for recipe '%s' (qty: %s)",
                run_id, recipe_name, production_qty)

    return {
        "run_id": run_id,
        "recipe_name": recipe_name,
        "production_qty": production_qty,
        "material_number": material_number,
        "packing": packing,
        "invoice_number": invoice_number,
        "sale_ids": linked_sale_ids,
        "invoice_ids": linked_invoice_ids,
        "shortage_report": final_shortage_report,
        "warnings": [item for item in final_shortage_report if item["status"] == "SHORTAGE"],
    }


def print_report(report_data: List[Dict[str, Any]], recipe_name: str, production_qty: float) -> None:
    """Pretty print the production report to console."""
    if not report_data:
        print("No report data available.")
        return
    
    recipe_yield = report_data[0].get("yield", 1) if report_data else 1
    
    print()
    print("=" * 72)
    print(f"  PRODUCTION REPORT: {recipe_name}")
    print(f"  Production Quantity: {production_qty} units")
    print("=" * 72)
    print()
    print(f"  {'Chemical':<30} {'Have':>10} {'Need':>10} {'Diff':>10} {'Status':<10}")
    print("-" * 72)
    
    total_shortage = 0
    shortage_count = 0
    
    for item in report_data:
        unit = item["unit"]
        have = item["current_stock"]
        need = item["required_total"]
        diff = item["shortage"]
        status = item["status"]
        
        # Format diff with sign
        if diff >= 0:
            diff_str = f"+{diff:.1f}"
        else:
            diff_str = f"{diff:.1f}"
        
        # Status indicator
        if status == "SHORTAGE":
            status_str = "[SHORTAGE]"
            total_shortage += abs(diff)
            shortage_count += 1
        elif status == "EXACT":
            status_str = "[EXACT]"
        else:
            status_str = "[SURPLUS]"
        
        print(f"  {item['chemical_name']:<30} {have:>8.1f} {unit} {need:>8.1f} {unit} {diff_str:>8} {unit} {status_str:<10}")
    
    print("-" * 72)
    print()
    
    # Summary
    chemicals_ok = len(report_data) - shortage_count
    print(f"  SUMMARY:")
    print(f"  - Total chemicals in recipe: {len(report_data)}")
    print(f"  - Chemicals sufficient: {chemicals_ok}")
    print(f"  - Chemicals with shortage: {shortage_count}")
    if shortage_count > 0:
        print(f"  - Total shortage amount: {total_shortage:.1f} KG")
    print()
    
    if shortage_count > 0:
        print("  [!] ACTION REQUIRED: Order missing chemicals before production!")
    else:
        print("  [OK] All chemicals sufficient for this production run.")
    print()
    print("=" * 72)


def export_report_to_csv(report_data: List[Dict[str, Any]], recipe_name: str, 
                        production_qty: float, output_path: str = None) -> str:
    """Export production report to CSV file.
    
    Args:
        report_data: List of report items from generate_report() or generate_multi_recipe_report()
        recipe_name: Name of the recipe(s) - can be comma-separated for multi-recipe
        production_qty: Production quantity
        output_path: Optional custom path. If None, auto-generates filename.
    
    Returns:
        Path to the created CSV file
    """
    import csv
    
    if output_path is None:
        # Auto-generate filename (V4: strip traversal characters)
        safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", recipe_name)[:50] or "report"
        output_path = os.path.join(os.path.dirname(__file__),
                                   f"report_{safe_name}_{int(production_qty)}.csv")
    
    # Calculate summary stats
    total_shortage = sum(abs(item["shortage"]) for item in report_data if item["shortage"] < 0)
    shortage_count = sum(1 for item in report_data if item["shortage"] < 0)
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        
        # Header
        writer.writerow(["Production Report", "", "", "", "", "", ""])
        writer.writerow(["Recipe(s)", csv_safe(recipe_name), "", "", "", "", ""])
        writer.writerow(["Production Quantity (per recipe)", production_qty, "", "", "", "", ""])
        writer.writerow(["", "", "", "", "", "", ""])
        
        # Column headers
        writer.writerow(["Chemical Name", "Current Stock", "Unit", "Required Total", "Shortage/Surplus", "Status", "Breakdown by Recipe"])
        
        # Data rows
        for item in report_data:
            breakdown = ""
            if "recipes" in item and item["recipes"]:
                parts = []
                for r in item["recipes"]:
                    parts.append(f"{r['recipe_name']}: {r['qty_from_this_recipe']:.2f} {item['unit']}")
                breakdown = " | ".join(parts)
            
            writer.writerow([
                csv_safe(item["chemical_name"]),
                item["current_stock"],
                csv_safe(item["unit"]),
                item["required_total"],
                item["shortage"],
                csv_safe(item["status"]),
                csv_safe(breakdown)
            ])
        
        # Summary
        writer.writerow(["", "", "", "", "", "", ""])
        writer.writerow(["SUMMARY", "", "", "", "", "", ""])
        writer.writerow(["Total unique chemicals", len(report_data), "", "", "", "", ""])
        writer.writerow(["Chemicals OK", len(report_data) - shortage_count, "", "", "", "", ""])
        writer.writerow(["Chemicals SHORTAGE", shortage_count, "", "", "", "", ""])
        if shortage_count > 0:
            writer.writerow(["Total shortage amount", total_shortage, "KG", "", "", "", ""])
    
    print(f"Report exported to: {output_path}")
    return output_path


def list_chemicals_cli() -> None:
    """CLI command to list all chemicals with current stock."""
    conn = get_connection()
    chemicals = get_all_chemicals(conn)
    conn.close()
    
    if not chemicals:
        print("No chemicals in database.")
        return
    
    print(f"\n{'Chemical Name':<30} {'Stock':>8} {'Unit':>6} {'Last Updated':>12}")
    print("-" * 58)
    for chem in chemicals:
        print(f"{chem['name']:<30} {chem['qty']:>8} {chem['unit']:>6} {chem['last_updated']:>12}")


# ==================== SALES TRACKER FUNCTIONS ====================
