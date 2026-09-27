"""Command-line entry point."""

import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .db import get_connection
from .recipes import add_recipe, add_recipe_item, delete_recipe, delete_recipe_item, export_report_to_csv, generate_report, list_chemicals_cli, list_recipe_items, list_recipes, print_report, update_recipe_item
from .stock import add_chemical, import_from_json, update_stock

def main():
    """Main CLI entry point."""
    import sys
    
    if len(sys.argv) < 2:
        print("RAAS Tracker")
        print("Usage: python chem_stock.py <command> [args]")
        print()
        print("Stock Commands:")
        print("  import-json          Import stock from stock_data.json")
        print("  list                 List all chemicals with current stock")
        print("  update <name> <qty>  Add/subtract quantity from stock")
        print("  add <name> <qty> <unit>  Add new chemical")
        print()
        print("Recipe Commands:")
        print("  recipe-create <company_id> <name> [yield]   Create new recipe")
        print("  recipe-add <company_id> <recipe> <chem> <qty>  Add chemical to recipe")
        print("  recipe-list                    List all recipes")
        print("  recipe-show <company_id> <name>             Show recipe details")
        print("  recipe-update <company_id> <recipe> <chem> <qty>  Update recipe item")
        print("  recipe-delete-item <company_id> <recipe> <chem>   Remove chemical from recipe")
        print("  recipe-delete <company_id> <name>          Delete entire recipe")
        print()
        print("Report Commands:")
        print("  report <company_id> <recipe> <qty>         Generate production report")
        print("  export <company_id> <recipe> <qty> [path]  Export report to CSV")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    conn = get_connection()
    
    try:
        # Stock commands
        if command == "import-json":
            import_from_json()
            
        elif command == "list":
            list_chemicals_cli()
            
        elif command == "update":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py update <chemical_name> <+/-qty>")
                sys.exit(1)
            name = sys.argv[2]
            delta = float(sys.argv[3])
            update_stock(conn, name, delta)
            
        elif command == "add":
            if len(sys.argv) < 5:
                print("Usage: python chem_stock.py add <name> <qty> <unit>")
                sys.exit(1)
            name = sys.argv[2]
            qty = float(sys.argv[3])
            unit = sys.argv[4].upper()
            add_chemical(conn, name, qty, unit)
        
        # Recipe commands
        elif command == "recipe-create":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py recipe-create <company_id> <name> [yield]")
                sys.exit(1)
            company_id = int(sys.argv[2])
            name = sys.argv[3]
            product_yield = float(sys.argv[4]) if len(sys.argv) > 4 else 1
            # Note: add_recipe requires product_name and validates it against sales
            # For CLI, we'll skip this validation or require product_name
            if len(sys.argv) > 5:
                product_name = sys.argv[5]
            else:
                print("Usage: python chem_stock.py recipe-create <company_id> <name> [yield] <product_name>")
                sys.exit(1)
            add_recipe(conn, name, product_yield, 0, company_id, product_name)
            
        elif command == "recipe-add":
            if len(sys.argv) < 6:
                print("Usage: python chem_stock.py recipe-add <company_id> <recipe> <chemical> <percentage>")
                sys.exit(1)
            company_id = int(sys.argv[2])
            recipe_name = sys.argv[3]
            chemical_name = sys.argv[4]
            percentage = float(sys.argv[5])
            add_recipe_item(conn, company_id, recipe_name, chemical_name, percentage)
            
        elif command == "recipe-list":
            recipes = list_recipes(conn)
            if not recipes:
                print("No recipes found.")
            else:
                print(f"\n{'Recipe Name':<30} {'Yield':>8} {'Company':<20} {'Created':>12}")
                print("-" * 72)
                for r in recipes:
                    print(f"{r['name']:<30} {r['total_quantity']:>8} {r.get('company_name', ''):<20} {r['created']:>12}")
                    
        elif command == "recipe-show":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py recipe-show <company_id> <name>")
                sys.exit(1)
            company_id = int(sys.argv[2])
            items = list_recipe_items(conn, company_id, sys.argv[3])
            if items:
                print(f"\nRecipe: {items[0]['recipe_name']} (Yield: {items[0]['total_quantity']})")
                print(f"{'Chemical':<30} {'Current Stock':>14} {'Need/Unit':>10}")
                print("-" * 56)
                for item in items:
                    print(f"{item['chemical_name']:<30} {item['current_stock']:>10} {item['unit']:>4} {item['required_per_unit']:>10}")
            else:
                print("Recipe not found or empty.")
                
        elif command == "recipe-update":
            if len(sys.argv) < 6:
                print("Usage: python chem_stock.py recipe-update <company_id> <recipe> <chemical> <new_percentage>")
                sys.exit(1)
            company_id = int(sys.argv[2])
            update_recipe_item(conn, company_id, sys.argv[3], sys.argv[4], float(sys.argv[5]))
            
        elif command == "recipe-delete-item":
            if len(sys.argv) < 5:
                print("Usage: python chem_stock.py recipe-delete-item <company_id> <recipe> <chemical>")
                sys.exit(1)
            company_id = int(sys.argv[2])
            delete_recipe_item(conn, company_id, sys.argv[3], sys.argv[4])
            
        elif command == "recipe-delete":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py recipe-delete <company_id> <name>")
                sys.exit(1)
            company_id = int(sys.argv[2])
            delete_recipe(conn, company_id, sys.argv[3])
        
        # Report commands
        elif command == "report":
            if len(sys.argv) < 5:
                print("Usage: python chem_stock.py report <company_id> <recipe_name> <production_qty>")
                sys.exit(1)
            company_id = int(sys.argv[2])
            recipe_name = sys.argv[3]
            production_qty = float(sys.argv[4])
            report = generate_report(conn, company_id, recipe_name, production_qty)
            if report:
                print_report(report, recipe_name, production_qty)
            else:
                print(f"Could not generate report for recipe '{recipe_name}'.")
                
        elif command == "export":
            if len(sys.argv) < 5:
                print("Usage: python chem_stock.py export <company_id> <recipe_name> <production_qty> [output_path]")
                sys.exit(1)
            company_id = int(sys.argv[2])
            recipe_name = sys.argv[3]
            production_qty = float(sys.argv[4])
            output_path = sys.argv[5] if len(sys.argv) > 5 else None
            report = generate_report(conn, company_id, recipe_name, production_qty)
            if report:
                export_report_to_csv(report, recipe_name, production_qty, output_path)
            else:
                print(f"Could not generate report for recipe '{recipe_name}'.")
        
        else:
            print(f"Unknown command: {command}")
            print("Run without arguments to see available commands.")
    
    finally:
        conn.close()


if __name__ == "__main__":
    main()
