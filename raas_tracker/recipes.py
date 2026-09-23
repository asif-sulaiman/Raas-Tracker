"""Recipes plus production reports."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .db import get_connection, logger
from .stock import get_all_chemicals
from .uploads import csv_safe

def add_recipe(conn: psycopg.Connection, name: str, total_quantity: float = 1, water_percentage: float = 0) -> bool:
    """Create a new recipe.
    
    Args:
        conn: Database connection
        name: Recipe name (e.g. "Liquid Soap Batch A")
        total_quantity: Total quantity this recipe produces (e.g. 1000 liters, 15000 KG)
        water_percentage: Optional water percentage (default 0, calculated as 100 - sum(ingredient %) if left at 0)
    
    Returns:
        True if created, False if recipe already exists
    """
    try:
        conn.execute(
            "INSERT INTO recipes (name, total_quantity, water_percentage) VALUES (%s, %s, %s)",
            (name, total_quantity, water_percentage)
        )
        conn.commit()
        logger.info("Created recipe: '%s' (total quantity: %s)", name, total_quantity)
        return True
    except psycopg.IntegrityError:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Recipe '%s' already exists.", name)
        return False


def get_recipe_by_name(conn: psycopg.Connection, name: str) -> Optional[Dict[str, Any]]:
    """Get recipe info by name."""
    cursor = conn.execute(
        "SELECT id, name, total_quantity, water_percentage, created_date FROM recipes WHERE name = %s",
        (name,)
    )
    row = cursor.fetchone()
    if row:
        return {"id": row[0], "name": row[1], "total_quantity": row[2], "water_percentage": row[3], "created": row[4]}
    return None


def add_recipe_item(conn: psycopg.Connection, recipe_name: str, chemical_name: str, 
                    percentage: float) -> bool:
    """Add a chemical to a recipe with percentage of total product yield.
    
    Args:
        conn: Database connection
        recipe_name: Recipe name
        chemical_name: Chemical name
        percentage: Percentage of total batch (e.g., 20.0 for 20%)
    
    Returns:
        True if added successfully
    """
    # Check recipe exists
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found. Create it first.", recipe_name)
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
    conn.execute(
        "INSERT INTO recipe_items (recipe_id, chemical_id, percentage, required_qty_per_unit) VALUES (%s, %s, %s, %s)",
        (recipe["id"], chemical[0], percentage, required_qty_per_unit)
    )
    conn.commit()
    logger.info("Added '%s' to recipe '%s': %s%%", chemical_name, recipe_name, percentage)
    return True


def list_recipes(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """List all recipes with their total quantity info."""
    cursor = conn.execute(
        "SELECT id, name, total_quantity, water_percentage, created_date FROM recipes ORDER BY name"
    )
    return [
        {"id": row[0], "name": row[1], "total_quantity": row[2], "water_percentage": row[3], "created": row[4]}
        for row in cursor.fetchall()
    ]


def list_recipe_items(conn: psycopg.Connection, recipe_name: str) -> List[Dict[str, Any]]:
    """List all chemicals in a recipe with percentages and required quantities."""
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found.", recipe_name)
        return []
    
    cursor = conn.execute("""
        SELECT r.name as recipe_name, r.total_quantity, 
               c.name as chemical_name, c.current_qty, c.unit,
               ri.required_qty_per_unit, ri.percentage
         FROM recipe_items ri
         JOIN recipes r ON ri.recipe_id = r.id
         JOIN chemicals c ON ri.chemical_id = c.id
         WHERE r.name = %s
         ORDER BY c.name
     """, (recipe_name,))
    
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


def update_recipe_item(conn: psycopg.Connection, recipe_name: str, chemical_name: str,
                       new_percentage: float) -> bool:
    """Update the percentage of a chemical in a recipe."""
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found.", recipe_name)
        return False
    
    chemical = conn.execute("SELECT id FROM chemicals WHERE name = %s", (chemical_name,)).fetchone()
    if not chemical:
        logger.warning("Chemical '%s' not found.", chemical_name)
        return False

    new_qty_per_unit = new_percentage / 100.0
    result = conn.execute(
        "UPDATE recipe_items SET percentage = %s, required_qty_per_unit = %s WHERE recipe_id = %s AND chemical_id = %s",
        (new_percentage, new_qty_per_unit, recipe["id"], chemical[0])
    )
    conn.commit()
    
    if result.rowcount > 0:
        logger.info("Updated '%s' in '%s': now %s%%", chemical_name, recipe_name, new_percentage)
        return True
    else:
        logger.warning("'%s' not found in recipe '%s'.", chemical_name, recipe_name)
        return False


def delete_recipe_item(conn: psycopg.Connection, recipe_name: str, chemical_name: str) -> bool:
    """Remove a chemical from a recipe."""
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        logger.warning("Recipe '%s' not found.", recipe_name)
        return False
    
    chemical = conn.execute("SELECT id FROM chemicals WHERE name = %s", (chemical_name,)).fetchone()
    if not chemical:
        logger.warning("Chemical '%s' not found.", chemical_name)
        return False

    result = conn.execute(
        "DELETE FROM recipe_items WHERE recipe_id = %s AND chemical_id = %s",
        (recipe["id"], chemical[0])
    )
    conn.commit()
    
    if result.rowcount > 0:
        logger.info("Removed '%s' from recipe '%s'", chemical_name, recipe_name)
        return True
    else:
        logger.warning("'%s' was not in recipe '%s'.", chemical_name, recipe_name)
        return False


def delete_recipe(conn: psycopg.Connection, name: str) -> bool:
    """Delete a recipe and all its items."""
    recipe = get_recipe_by_name(conn, name)
    if not recipe:
        logger.warning("Recipe '%s' not found.", name)
        return False
    
    conn.execute("DELETE FROM recipe_items WHERE recipe_id = %s", (recipe["id"],))
    conn.execute("DELETE FROM recipes WHERE id = %s", (recipe["id"],))
    conn.commit()
    logger.info("Deleted recipe '%s' and all its items.", name)
    return True


def update_recipe(conn: psycopg.Connection, name: str, 
                  total_quantity: float = None, water_percentage: float = None) -> bool:
    """Update recipe metadata (total_quantity and/or water_percentage).
    
    Args:
        conn: Database connection
        name: Recipe name
        total_quantity: New total quantity (or None to keep current)
        water_percentage: New water percentage (or None to keep current)
    
    Returns:
        True if updated, False if recipe not found
    """
    recipe = get_recipe_by_name(conn, name)
    if not recipe:
        logger.warning("Recipe '%s' not found.", name)
        return False
    
    updates = []
    params = []
    if total_quantity is not None:
        updates.append("total_quantity = %s")
        params.append(total_quantity)
    if water_percentage is not None:
        updates.append("water_percentage = %s")
        params.append(water_percentage)

    if not updates:
        return True

    params.append(recipe["id"])
    conn.execute(f"UPDATE recipes SET {', '.join(updates)} WHERE id = %s", params)
    conn.commit()
    logger.info("Updated recipe '%s': %s", name, updates)
    return True


# ============================================================
# PHASE 3: PRODUCTION REPORT GENERATION
# ============================================================


def generate_report(conn: psycopg.Connection, recipe_name: str, production_qty: float) -> List[Dict[str, Any]]:
    """Generate a production report showing have vs need for each chemical.
    
    Args:
        conn: Database connection
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
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        return []
    
    items = list_recipe_items(conn, recipe_name)
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
        recipe_selections: List of dicts with {"recipe_name": str, "production_qty": float}
    
    Returns:
        Combined list of report items with same chemicals merged together.
    """
    combined = {}
    
    for selection in recipe_selections:
        recipe_name = selection["recipe_name"]
        production_qty = selection["production_qty"]
        
        items = list_recipe_items(conn, recipe_name)
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
