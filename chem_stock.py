"""Chemical Stock Tracker - Phase 2: Recipe Management & Stock Tracking."""

import sqlite3
import json
import os
import re
from datetime import date
from typing import Optional, List, Dict, Any, Union


DB_PATH = os.path.join(os.path.dirname(__file__), "chem_stock.db")
JSON_PATH = os.path.join(os.path.dirname(__file__), "stock_data.json")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Get or create database connection with table creation."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    """Create all required tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chemicals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            current_qty REAL NOT NULL DEFAULT 0,
            balance_last_month REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'KG',
            last_updated TEXT
        );
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            total_quantity REAL NOT NULL DEFAULT 1,
            water_percentage REAL NOT NULL DEFAULT 0,
            created_date TEXT DEFAULT (date('now'))
        );
        CREATE TABLE IF NOT EXISTS recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            chemical_id INTEGER NOT NULL,
            percentage REAL NOT NULL DEFAULT 0,
            required_qty_per_unit REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY (chemical_id) REFERENCES chemicals(id) ON DELETE CASCADE
        );
    """)
    # Migration for existing databases - add total_quantity column and drop product_yield
    cursor = conn.execute("PRAGMA table_info(recipes)")
    columns = [col[1] for col in cursor.fetchall()]
    if "total_quantity" not in columns:
        if "product_yield" in columns:
            # Migrate: copy product_yield data to total_quantity, then drop product_yield
            conn.execute("ALTER TABLE recipes ADD COLUMN total_quantity REAL DEFAULT 0")
            conn.execute("UPDATE recipes SET total_quantity = product_yield WHERE total_quantity = 0")
            conn.execute("ALTER TABLE recipes DROP COLUMN product_yield")
        else:
            # Add new column if neither exists
            conn.execute("ALTER TABLE recipes ADD COLUMN total_quantity REAL DEFAULT 1")
        conn.commit()
    # Migration for existing databases - add water_percentage to recipes
    cursor = conn.execute("PRAGMA table_info(recipes)")
    columns = [col[1] for col in cursor.fetchall()]
    if "water_percentage" not in columns:
        conn.execute("ALTER TABLE recipes ADD COLUMN water_percentage REAL DEFAULT 0")
        conn.commit()
    # Migration for existing databases
    cursor = conn.execute("PRAGMA table_info(recipe_items)")
    columns = [col[1] for col in cursor.fetchall()]
    if "percentage" not in columns:
        conn.execute("ALTER TABLE recipe_items ADD COLUMN percentage REAL DEFAULT 0")
        conn.execute("UPDATE recipe_items SET percentage = required_qty_per_unit * 100 WHERE percentage = 0")
        conn.commit()
    # Migration for existing databases - add balance_last_month to chemicals
    cursor = conn.execute("PRAGMA table_info(chemicals)")
    columns = [col[1] for col in cursor.fetchall()]
    if "balance_last_month" not in columns:
        conn.execute("ALTER TABLE chemicals ADD COLUMN balance_last_month REAL DEFAULT 0")
        conn.commit()
    # Create upload tracking tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            upload_date TEXT DEFAULT (datetime('now')),
            file_type TEXT,
            status TEXT DEFAULT 'uploaded',
            total_chemicals INTEGER DEFAULT 0,
            matched INTEGER DEFAULT 0,
            last_month_mismatches INTEGER DEFAULT 0,
            this_month_mismatches INTEGER DEFAULT 0,
            both_mismatches INTEGER DEFAULT 0,
            not_in_db INTEGER DEFAULT 0,
            not_in_upload INTEGER DEFAULT 0,
            match_percentage REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS upload_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER NOT NULL,
            chemical_name TEXT,
            batch_number TEXT,
            expiry_date TEXT,
            upload_unit TEXT,
            balance_last_month REAL,
            balance_this_month REAL,
            matched_in_db INTEGER DEFAULT 0,
            unit_match INTEGER DEFAULT 1,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS unit_conversions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_unit TEXT NOT NULL,
            to_unit TEXT NOT NULL,
            factor REAL NOT NULL,
            UNIQUE(from_unit, to_unit)
        );
        CREATE TABLE IF NOT EXISTS reason_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            description TEXT,
            category TEXT
        );
        CREATE TABLE IF NOT EXISTS approval_workflow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER NOT NULL,
            upload_row_id INTEGER,
            status TEXT DEFAULT 'pending',
            reason_code TEXT,
            comments TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE,
            FOREIGN KEY (upload_row_id) REFERENCES upload_rows(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            user_id TEXT DEFAULT 'system',
            old_value TEXT,
            new_value TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            ip_address TEXT
        );
        CREATE TABLE IF NOT EXISTS reconciliation_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_name TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            status TEXT DEFAULT 'open',
            locked_by TEXT,
            locked_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL DEFAULT 'pi_issued',
            pi_number TEXT,
            pi_date TEXT,
            client_name TEXT,
            pi_file_path TEXT,
            lc_number TEXT,
            lc_date TEXT,
            shipment_date TEXT,
            payment_date TEXT,
            payment_amount REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            unit_price REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sales_stage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            from_stage TEXT,
            to_stage TEXT NOT NULL,
            changed_at TEXT DEFAULT (datetime('now')),
            notes TEXT,
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sale_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL,
            payment_date TEXT,
            payment_amount REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (sale_id) REFERENCES sales(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            ip_address TEXT,
            attempted_at TEXT DEFAULT (datetime('now')),
            success INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT,
            allowed_ips TEXT DEFAULT '',
            revoked INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            last_used_ip TEXT,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS api_key_rate_limits (
            key_id INTEGER NOT NULL,
            hit_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (key_id) REFERENCES api_keys(id) ON DELETE CASCADE
        );
    """)
    # Migration for existing databases - add batch/unit columns to upload_rows
    cursor = conn.execute("PRAGMA table_info(upload_rows)")
    columns = [col[1] for col in cursor.fetchall()]
    if "batch_number" not in columns:
        conn.execute("ALTER TABLE upload_rows ADD COLUMN batch_number TEXT")
        conn.execute("ALTER TABLE upload_rows ADD COLUMN expiry_date TEXT")
        conn.execute("ALTER TABLE upload_rows ADD COLUMN upload_unit TEXT")
        conn.execute("ALTER TABLE upload_rows ADD COLUMN unit_match INTEGER DEFAULT 1")
        conn.commit()
    # Seed default reason codes
    cursor = conn.execute("SELECT COUNT(*) FROM reason_codes")
    if cursor.fetchone()[0] == 0:
        default_reasons = [
            ("MEASUREMENT_ERROR", "Small measurement difference", "quantity"),
            ("UNIT_CONVERSION", "Unit conversion discrepancy", "unit"),
            ("DATA_ENTRY_ERROR", "Data entry mistake", "quantity"),
            ("NEW_PRODUCT", "New product not in system", "missing"),
            ("DISPOSED", "Product was disposed/expired", "missing"),
            ("TRANSFERRED", "Product transferred to another location", "missing"),
            ("COUNTED_WRONG", "Physical count was incorrect", "quantity"),
            ("SYSTEM_ERROR", "System calculation error", "system"),
            ("BATCH_SPLIT", "Batch was split", "batch"),
            ("EXPIRY_UPDATED", "Expiry date was updated", "expiry"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO reason_codes (code, description, category) VALUES (?, ?, ?)",
            default_reasons
        )
        conn.commit()
    # Seed default unit conversions
    cursor = conn.execute("SELECT COUNT(*) FROM unit_conversions")
    if cursor.fetchone()[0] == 0:
        default_conversions = [
            ("KG", "G", 1000), ("G", "KG", 0.001),
            ("L", "ML", 1000), ("ML", "L", 0.001),
            ("KG", "L", 1), ("L", "KG", 1),
            ("DRUM", "L", 200), ("DRUM", "KG", 200),
            ("BOTTLE", "L", 2.5), ("BOTTLE", "KG", 2.5),
            ("BOX", "PCS", 12), ("PCS", "BOX", 1/12),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO unit_conversions (from_unit, to_unit, factor) VALUES (?, ?, ?)",
            default_conversions
        )
        conn.commit()


def import_from_json(json_path: str = JSON_PATH) -> Dict[str, int]:
    """Import chemical stock from parsed JSON data.
    
    Expected JSON structure from parse_stock.py:
    [
        {"item_no": "1", "product_name": "PRODUCT A", "balance_last_month": 0, 
         "last_month_unit": "KG", "balance_this_month": 0, "this_month_unit": "KG"},
        ...
    ]
    
    Returns dict of {name: current_qty} for successfully imported chemicals.
    """
    if not os.path.exists(json_path):
        print(f"JSON file not found: {json_path}")
        return {}
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    conn = get_connection()
    imported = 0
    skipped = 0
    
    for item in data:
        name = item.get("product_name", "").strip()
        qty = item.get("balance_this_month", 0)
        last_qty = item.get("balance_last_month", 0)
        unit = item.get("this_month_unit", "KG").strip().upper()
        
        if not name:
            skipped += 1
            continue
        
        try:
            existing = conn.execute(
                "SELECT id FROM chemicals WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE chemicals SET current_qty = ?, balance_last_month = ?, unit = ?, last_updated = ? WHERE name = ?",
                    (float(qty), float(last_qty), unit, date.isoformat(date.today()), name)
                )
            else:
                conn.execute(
                    "INSERT INTO chemicals (name, current_qty, balance_last_month, unit, last_updated) VALUES (?, ?, ?, ?, ?)",
                    (name, float(qty), float(last_qty), unit, date.isoformat(date.today()))
                )
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1
    
    conn.commit()
    conn.close()
    
    print(f"Import complete: {imported} chemicals imported, {skipped} skipped")
    return {item["product_name"]: item["balance_this_month"] for item in data[:5]}  # sample


def get_all_chemicals(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return all chemicals with current stock levels."""
    cursor = conn.execute(
        "SELECT id, name, current_qty, balance_last_month, unit, last_updated FROM chemicals ORDER BY name"
    )
    return [
        {"id": row[0], "name": row[1], "qty": row[2], "balance_last_month": row[3], "unit": row[4], "last_updated": row[5]}
        for row in cursor.fetchall()
    ]


# ==================== UNIT CONVERSION FUNCTIONS ====================
def get_unit_conversion(conn: sqlite3.Connection, from_unit: str, to_unit: str) -> Optional[float]:
    """Get conversion factor between two units.
    
    Args:
        conn: Database connection
        from_unit: Source unit (e.g., 'KG', 'L', 'DRUM')
        to_unit: Target unit (e.g., 'G', 'ML', 'L')
    
    Returns:
        Conversion factor or None if conversion not possible
    """
    from_unit = from_unit.upper().strip()
    to_unit = to_unit.upper().strip()
    
    if from_unit == to_unit:
        return 1.0
    
    # Direct conversion
    cursor = conn.execute(
        "SELECT factor FROM unit_conversions WHERE from_unit = ? AND to_unit = ?",
        (from_unit, to_unit)
    )
    row = cursor.fetchone()
    if row:
        return row[0]
    
    # Try reverse conversion
    cursor = conn.execute(
        "SELECT factor FROM unit_conversions WHERE from_unit = ? AND to_unit = ?",
        (to_unit, from_unit)
    )
    row = cursor.fetchone()
    if row:
        return 1.0 / row[0]
    
    return None  # Conversion not possible


def convert_quantity(conn: sqlite3.Connection, qty: float, from_unit: str, to_unit: str) -> Optional[float]:
    """Convert quantity from one unit to another.
    
    Args:
        conn: Database connection
        qty: Quantity to convert
        from_unit: Source unit
        to_unit: Target unit
    
    Returns:
        Converted quantity or None if conversion not possible
    """
    factor = get_unit_conversion(conn, from_unit, to_unit)
    if factor is None:
        return None
    return qty * factor


def get_all_unit_conversions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return all unit conversions."""
    cursor = conn.execute("SELECT id, from_unit, to_unit, factor FROM unit_conversions ORDER BY from_unit")
    return [{"id": row[0], "from_unit": row[1], "to_unit": row[2], "factor": row[3]} for row in cursor.fetchall()]


def add_unit_conversion(conn: sqlite3.Connection, from_unit: str, to_unit: str, factor: float) -> bool:
    """Add a new unit conversion."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO unit_conversions (from_unit, to_unit, factor) VALUES (?, ?, ?)",
            (from_unit.upper().strip(), to_unit.upper().strip(), factor)
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error adding conversion: {e}")
        return False


def delete_unit_conversion(conn: sqlite3.Connection, from_unit: str, to_unit: str) -> bool:
    """Delete a unit conversion."""
    try:
        conn.execute(
            "DELETE FROM unit_conversions WHERE from_unit = ? AND to_unit = ?",
            (from_unit.upper().strip(), to_unit.upper().strip())
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error deleting conversion: {e}")
        return False


def validate_expiry_date(expiry_date_str: str) -> tuple:
    """Validate expiry date format and check if expired.
    
    Args:
        expiry_date_str: Date string in various formats
    
    Returns:
        Tuple of (is_valid, parsed_date, status)
    """
    from datetime import datetime, date
    
    if not expiry_date_str:
        return True, None, "No expiry date"
    
    try:
        # Try common formats
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
            try:
                expiry_date = datetime.strptime(expiry_date_str, fmt).date()
                break
            except ValueError:
                continue
        else:
            return False, None, "Invalid date format"
        
        # Check if expired
        today = date.today()
        if expiry_date < today:
            return True, expiry_date, "EXPIRED"
        elif (expiry_date - today).days <= 30:
            return True, expiry_date, "EXPIRING_SOON"
        else:
            return True, expiry_date, "VALID"
    except Exception as e:
        return False, None, str(e)


def compare_stock_upload(conn: sqlite3.Connection, upload_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare uploaded stock data against database.
    
    Args:
        conn: Database connection
        upload_data: List of dicts with keys: name, balance_last_month, balance_this_month,
                     and optional: batch_number, expiry_date, upload_unit
    
    Returns:
        Dict with categorized results and statistics
    """
    # Get all chemicals from DB (including unit)
    db_chemicals = {}
    cursor = conn.execute("SELECT name, current_qty, balance_last_month, unit FROM chemicals")
    for row in cursor.fetchall():
        db_chemicals[row[0].strip().upper()] = {
            "name": row[0],
            "current_qty": row[1] or 0,
            "balance_last_month": row[2] or 0,
            "unit": row[3] or "KG"
        }
    
    matches = []
    last_month_mismatches = []
    this_month_mismatches = []
    both_mismatches = []
    not_in_db = []
    not_in_upload = []
    
    uploaded_names = set()
    
    for item in upload_data:
        name = item.get("name", "").strip()
        upload_last = float(item.get("balance_last_month", 0) or 0)
        upload_this = float(item.get("balance_this_month", 0) or 0)
        batch_number = item.get("batch_number", "")
        expiry_date = item.get("expiry_date", "")
        upload_unit = item.get("upload_unit", "").upper().strip()
        
        if not name:
            continue
        
        uploaded_names.add(name.strip().upper())
        name_upper = name.strip().upper()
        
        # Validate unit conversion
        unit_match = True
        converted_upload_last = upload_last
        converted_upload_this = upload_this
        if upload_unit and name_upper in db_chemicals:
            db_unit = db_chemicals[name_upper]["unit"]
            conversion = get_unit_conversion(conn, upload_unit, db_unit)
            if conversion is not None:
                converted_upload_last = upload_last * conversion
                converted_upload_this = upload_this * conversion
            else:
                unit_match = False
        
        # Validate expiry date
        expiry_valid, expiry_parsed, expiry_status = validate_expiry_date(expiry_date)
        
        if name_upper in db_chemicals:
            db = db_chemicals[name_upper]
            db_last = db["balance_last_month"]
            db_this = db["current_qty"]
            
            # Use converted values for comparison
            last_match = abs(db_last - converted_upload_last) < 0.01
            this_match = abs(db_this - converted_upload_this) < 0.01
            
            # Build result item with batch/unit info
            result_item = {
                "name": db["name"],
                "db_last": db_last,
                "db_this": db_this,
                "upload_last": upload_last,
                "upload_this": upload_this,
                "converted_last": converted_upload_last,
                "converted_this": converted_upload_this,
                "batch_number": batch_number,
                "expiry_date": expiry_date,
                "expiry_status": expiry_status,
                "upload_unit": upload_unit,
                "db_unit": db["unit"],
                "unit_match": unit_match
            }
            
            if last_match and this_match:
                result_item["status"] = "matched"
                matches.append(result_item)
            elif last_match and not this_match:
                result_item["diff_this"] = converted_upload_this - db_this
                result_item["status"] = "this_month_mismatch"
                this_month_mismatches.append(result_item)
            elif not last_match and this_match:
                result_item["diff_last"] = converted_upload_last - db_last
                result_item["status"] = "last_month_mismatch"
                last_month_mismatches.append(result_item)
            else:
                result_item["diff_last"] = converted_upload_last - db_last
                result_item["diff_this"] = converted_upload_this - db_this
                result_item["status"] = "both_mismatch"
                both_mismatches.append(result_item)
        else:
            not_in_db.append({
                "name": name,
                "upload_last": upload_last,
                "upload_this": upload_this,
                "batch_number": batch_number,
                "expiry_date": expiry_date,
                "expiry_status": expiry_status,
                "upload_unit": upload_unit
            })
    
    # Find chemicals in DB but not in upload
    for name_upper, db in db_chemicals.items():
        if name_upper not in uploaded_names:
            not_in_upload.append({
                "name": db["name"],
                "db_last": db["balance_last_month"],
                "db_this": db["current_qty"]
            })
    
    total = len(matches) + len(last_month_mismatches) + len(this_month_mismatches) + len(both_mismatches) + len(not_in_db) + len(not_in_upload)
    matched_count = len(matches)
    match_percentage = (matched_count / total * 100) if total > 0 else 0
    
    return {
        "matches": matches,
        "last_month_mismatches": last_month_mismatches,
        "this_month_mismatches": this_month_mismatches,
        "both_mismatches": both_mismatches,
        "not_in_db": not_in_db,
        "not_in_upload": not_in_upload,
        "stats": {
            "total": total,
            "matched": matched_count,
            "last_month_mismatches": len(last_month_mismatches),
            "this_month_mismatches": len(this_month_mismatches),
            "both_mismatches": len(both_mismatches),
            "not_in_db": len(not_in_db),
            "not_in_upload": len(not_in_upload),
            "match_percentage": round(match_percentage, 2)
        }
    }


def save_upload(conn: sqlite3.Connection, filename: str, results: Dict[str, Any]) -> int:
    """Save upload results to database.
    
    Args:
        conn: Database connection
        filename: Name of uploaded file
        results: Output from compare_stock_upload()
    
    Returns:
        Upload ID
    """
    stats = results["stats"]
    cursor = conn.execute(
        """INSERT INTO uploads (filename, status, total_chemicals, matched, 
           last_month_mismatches, this_month_mismatches, both_mismatches, 
           not_in_db, not_in_upload, match_percentage)
           VALUES (?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (filename, stats["total"], stats["matched"], stats["last_month_mismatches"],
         stats["this_month_mismatches"], stats["both_mismatches"],
         stats["not_in_db"], stats["not_in_upload"], stats["match_percentage"])
    )
    upload_id = cursor.lastrowid
    
    # Save all rows with batch/unit info
    for row in results.get("matches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"], 
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("last_month_mismatches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"],
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("this_month_mismatches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"],
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("both_mismatches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"],
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("not_in_db", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"])
        )
    
    conn.commit()
    return upload_id


def get_upload_history(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent upload history."""
    cursor = conn.execute(
        """SELECT id, filename, upload_date, status, total_chemicals, matched, 
           last_month_mismatches, this_month_mismatches, both_mismatches,
           not_in_db, not_in_upload, match_percentage 
           FROM uploads ORDER BY upload_date DESC LIMIT ?""",
        (limit,)
    )
    return [
        {
            "id": row[0], "filename": row[1], "upload_date": row[2],
            "status": row[3], "total_chemicals": row[4], "matched": row[5],
            "last_month_mismatches": row[6], "this_month_mismatches": row[7],
            "both_mismatches": row[8], "not_in_db": row[9],
            "not_in_upload": row[10], "match_percentage": row[11]
        }
        for row in cursor.fetchall()
    ]


def get_upload_results(conn: sqlite3.Connection, upload_id: int) -> List[Dict[str, Any]]:
    """Get upload rows for a specific upload."""
    cursor = conn.execute(
        "SELECT id, chemical_name, balance_last_month, balance_this_month, matched_in_db FROM upload_rows WHERE upload_id = ?",
        (upload_id,)
    )
    return [
        {"id": row[0], "name": row[1], "balance_last_month": row[2], "balance_this_month": row[3], "matched": row[4]}
        for row in cursor.fetchall()
    ]


def csv_safe(value: Any) -> Any:
    """V6: neutralize spreadsheet formula injection.

    Prefixes values starting with = + - @ (or tab/CR) so Excel/Sheets
    treat them as text. Non-strings pass through untouched.
    """
    if not isinstance(value, str):
        return value
    if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_comparison_report(results: Dict[str, Any], output_path: str) -> str:
    """Export comparison report to Excel with color-coded sheets.
    
    Args:
        results: Output from compare_stock_upload()
        output_path: Path to save the Excel file
    
    Returns:
        Path to the saved file
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill, Font, Alignment
    except ImportError:
        print("openpyxl not installed. Run: pip install openpyxl")
        return None
    
    wb = Workbook()
    
    # Color definitions
    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    orange_fill = PatternFill(start_color="F4B084", end_color="F4B084", fill_type="solid")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    # Sheet 1: Summary
    ws_summary = wb.active
    ws_summary.title = "Summary"
    
    stats = results["stats"]
    ws_summary.append(["Comparison Report Summary"])
    ws_summary.append([])
    ws_summary.append(["Total Chemicals", stats["total"]])
    ws_summary.append(["Matched", stats["matched"]])
    ws_summary.append(["Last Month Mismatches", stats["last_month_mismatches"]])
    ws_summary.append(["This Month Mismatches", stats["this_month_mismatches"]])
    ws_summary.append(["Both Months Mismatches", stats["both_mismatches"]])
    ws_summary.append(["Not in DB", stats["not_in_db"]])
    ws_summary.append(["Not in Upload", stats["not_in_upload"]])
    ws_summary.append(["Match Percentage", f"{stats['match_percentage']}%"])
    
    # Sheet 2: Matches (Green)
    ws_matches = wb.create_sheet("Matches")
    ws_matches.append(["Chemical", "DB Last Month", "DB This Month", "Upload Last", "Upload This", "Batch", "Expiry", "Unit Match"])
    for row in results.get("matches", []):
        ws_matches.append([
            csv_safe(row["name"]), row["db_last"], row["db_this"],
            row["upload_last"], row["upload_this"],
            csv_safe(row.get("batch_number", "")), csv_safe(row.get("expiry_date", "")),
            "Yes" if row.get("unit_match", True) else "No"
        ])
    # Apply green fill
    for row in ws_matches.iter_rows(min_row=2):
        for cell in row:
            cell.fill = green_fill
    
    # Sheet 3: Mismatches (Red)
    ws_mismatches = wb.create_sheet("Mismatches")
    ws_mismatches.append(["Chemical", "Status", "DB Last", "Upload Last", "Diff Last", 
                          "DB This", "Upload This", "Diff This", "Batch", "Expiry", "Unit"])
    for row in results.get("last_month_mismatches", []):
        ws_mismatches.append([
            csv_safe(row["name"]), "Last Month Mismatch", row["db_last"], row["upload_last"], row.get("diff_last", 0),
            row["db_this"], row["upload_this"], 0,
            csv_safe(row.get("batch_number", "")), csv_safe(row.get("expiry_date", "")), csv_safe(row.get("upload_unit", ""))
        ])
    for row in results.get("this_month_mismatches", []):
        ws_mismatches.append([
            csv_safe(row["name"]), "This Month Mismatch", row["db_last"], row["upload_last"], 0,
            row["db_this"], row["upload_this"], row.get("diff_this", 0),
            csv_safe(row.get("batch_number", "")), csv_safe(row.get("expiry_date", "")), csv_safe(row.get("upload_unit", ""))
        ])
    for row in results.get("both_mismatches", []):
        ws_mismatches.append([
            csv_safe(row["name"]), "Both Months Mismatch", row["db_last"], row["upload_last"], row.get("diff_last", 0),
            row["db_this"], row["upload_this"], row.get("diff_this", 0),
            csv_safe(row.get("batch_number", "")), csv_safe(row.get("expiry_date", "")), csv_safe(row.get("upload_unit", ""))
        ])
    # Apply red fill
    for row in ws_mismatches.iter_rows(min_row=2):
        for cell in row:
            cell.fill = red_fill
    
    # Sheet 4: Not in DB (Yellow)
    ws_not_in_db = wb.create_sheet("Not in DB")
    ws_not_in_db.append(["Chemical", "Upload Last", "Upload This", "Batch", "Expiry", "Unit"])
    for row in results.get("not_in_db", []):
        ws_not_in_db.append([
            csv_safe(row["name"]), row["upload_last"], row["upload_this"],
            csv_safe(row.get("batch_number", "")), csv_safe(row.get("expiry_date", "")), csv_safe(row.get("upload_unit", ""))
        ])
    for row in ws_not_in_db.iter_rows(min_row=2):
        for cell in row:
            cell.fill = yellow_fill
    
    # Sheet 5: Not in Upload (Orange)
    ws_not_in_upload = wb.create_sheet("Not in Upload")
    ws_not_in_upload.append(["Chemical", "DB Last Month", "DB This Month", "Unit"])
    for row in results.get("not_in_upload", []):
        ws_not_in_upload.append([csv_safe(row["name"]), row["db_last"], row["db_this"], csv_safe(row.get("unit", ""))])
    for row in ws_not_in_upload.iter_rows(min_row=2):
        for cell in row:
            cell.fill = orange_fill
    
    # Auto-adjust column widths
    for ws in wb.worksheets:
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[column_letter].width = min(max_length + 2, 30)
    
    wb.save(output_path)
    return output_path


# ==================== APPROVAL WORKFLOW FUNCTIONS ====================
def get_all_reason_codes(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return all reason codes."""
    cursor = conn.execute("SELECT id, code, description, category FROM reason_codes ORDER BY code")
    return [{"id": row[0], "code": row[1], "description": row[2], "category": row[3]} for row in cursor.fetchall()]


def create_approval_workflow(conn: sqlite3.Connection, upload_id: int, upload_row_id: int = None) -> int:
    """Create an approval workflow entry for a upload row.
    
    Args:
        conn: Database connection
        upload_id: Upload ID
        upload_row_id: Optional row ID (if None, approves entire upload)
    
    Returns:
        Workflow ID
    """
    cursor = conn.execute(
        "INSERT INTO approval_workflow (upload_id, upload_row_id, status) VALUES (?, ?, 'pending')",
        (upload_id, upload_row_id)
    )
    conn.commit()
    return cursor.lastrowid


def approve_upload_row(conn: sqlite3.Connection, workflow_id: int, reason_code: str, 
                       comments: str, reviewed_by: str = "system") -> bool:
    """Approve a single upload row.
    
    Args:
        conn: Database connection
        workflow_id: Workflow ID
        reason_code: Reason code for approval
        comments: Additional comments
        reviewed_by: Name of reviewer
    
    Returns:
        True if approved successfully
    """
    try:
        conn.execute(
            """UPDATE approval_workflow 
               SET status = 'approved', reason_code = ?, comments = ?, reviewed_by = ?, reviewed_at = datetime('now')
               WHERE id = ?""",
            (reason_code, comments, reviewed_by, workflow_id)
        )
        
        # Get workflow info for audit log
        workflow = conn.execute(
            "SELECT upload_id, upload_row_id FROM approval_workflow WHERE id = ?",
            (workflow_id,)
        ).fetchone()
        
        if workflow:
            # Log the approval
            log_audit_action(conn, "APPROVE_ROW", "upload_row", workflow[1], 
                           reviewed_by, None, f"Approved with reason: {reason_code}")
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error approving row: {e}")
        return False


def reject_upload_row(conn: sqlite3.Connection, workflow_id: int, reason_code: str,
                      comments: str, reviewed_by: str = "system") -> bool:
    """Reject a single upload row.
    
    Args:
        conn: Database connection
        workflow_id: Workflow ID
        reason_code: Reason code for rejection
        comments: Additional comments
        reviewed_by: Name of reviewer
    
    Returns:
        True if rejected successfully
    """
    try:
        conn.execute(
            """UPDATE approval_workflow 
               SET status = 'rejected', reason_code = ?, comments = ?, reviewed_by = ?, reviewed_at = datetime('now')
               WHERE id = ?""",
            (reason_code, comments, reviewed_by, workflow_id)
        )
        
        # Get workflow info for audit log
        workflow = conn.execute(
            "SELECT upload_id, upload_row_id FROM approval_workflow WHERE id = ?",
            (workflow_id,)
        ).fetchone()
        
        if workflow:
            # Log the rejection
            log_audit_action(conn, "REJECT_ROW", "upload_row", workflow[1],
                           reviewed_by, None, f"Rejected with reason: {reason_code}")
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error rejecting row: {e}")
        return False


def approve_upload(conn: sqlite3.Connection, upload_id: int, reviewed_by: str = "system") -> bool:
    """Approve entire upload (all rows).
    
    Args:
        conn: Database connection
        upload_id: Upload ID
        reviewed_by: Name of reviewer
    
    Returns:
        True if approved successfully
    """
    try:
        # Update upload status
        conn.execute(
            "UPDATE uploads SET status = 'approved' WHERE id = ?",
            (upload_id,)
        )
        
        # Create workflow entries for all rows
        rows = conn.execute(
            "SELECT id FROM upload_rows WHERE upload_id = ?",
            (upload_id,)
        ).fetchall()
        
        for row in rows:
            create_approval_workflow(conn, upload_id, row[0])
            approve_upload_row(conn, 
                             conn.execute("SELECT last_insert_rowid()").fetchone()[0],
                             "AUTO_APPROVED", "Auto-approved with upload", reviewed_by)
        
        # Log the approval
        log_audit_action(conn, "APPROVE_UPLOAD", "upload", upload_id,
                       reviewed_by, None, f"Approved entire upload {upload_id}")
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error approving upload: {e}")
        return False


def get_pending_approvals(conn: sqlite3.Connection, upload_id: int = None) -> List[Dict[str, Any]]:
    """Get pending approval workflows.
    
    Args:
        conn: Database connection
        upload_id: Optional upload ID to filter by
    
    Returns:
        List of pending workflows
    """
    if upload_id:
        cursor = conn.execute(
            """SELECT aw.id, aw.upload_id, aw.upload_row_id, aw.status, 
                      aw.reason_code, aw.comments, aw.reviewed_by, aw.reviewed_at,
                      ur.chemical_name, ur.balance_last_month, ur.balance_this_month
               FROM approval_workflow aw
               LEFT JOIN upload_rows ur ON aw.upload_row_id = ur.id
               WHERE aw.upload_id = ? AND aw.status = 'pending'
               ORDER BY aw.reviewed_at""",
            (upload_id,)
        )
    else:
        cursor = conn.execute(
            """SELECT aw.id, aw.upload_id, aw.upload_row_id, aw.status,
                      aw.reason_code, aw.comments, aw.reviewed_by, aw.reviewed_at,
                      ur.chemical_name, ur.balance_last_month, ur.balance_this_month
               FROM approval_workflow aw
               LEFT JOIN upload_rows ur ON aw.upload_row_id = ur.id
               WHERE aw.status = 'pending'
               ORDER BY aw.upload_id, aw.reviewed_at"""
        )
    
    return [
        {
            "id": row[0], "upload_id": row[1], "upload_row_id": row[2],
            "status": row[3], "reason_code": row[4], "comments": row[5],
            "reviewed_by": row[6], "reviewed_at": row[7],
            "chemical_name": row[8], "balance_last_month": row[9], "balance_this_month": row[10]
        }
        for row in cursor.fetchall()
    ]


import threading as _threading

_audit_state = _threading.local()


def set_audit_actor(name: Optional[str]) -> None:
    """Set the acting user for the current thread (called per request by Flask)."""
    _audit_state.name = name or "system"


def log_audit_action(conn: sqlite3.Connection, action: str, entity_type: str = None,
                     entity_id: int = None, user_id: str = "system",
                     old_value: str = None, new_value: str = None,
                     ip_address: str = None) -> int:
    """Log an audit action.
    
    Args:
        conn: Database connection
        action: Action performed (e.g., 'APPROVE_ROW', 'REJECT_ROW', 'ADJUST_STOCK')
        entity_type: Type of entity (e.g., 'upload', 'upload_row', 'chemical')
        entity_id: ID of entity
        user_id: User performing action
        old_value: Previous value
        new_value: New value
        ip_address: IP address of user
    
    Returns:
        Log ID
    """
    if user_id == "system":
        user_id = getattr(_audit_state, "name", "system")
    cursor = conn.execute(
        """INSERT INTO audit_logs (action, entity_type, entity_id, user_id, old_value, new_value, ip_address)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (action, entity_type, entity_id, user_id, old_value, new_value, ip_address)
    )
    conn.commit()
    return cursor.lastrowid


def get_audit_logs(conn: sqlite3.Connection, entity_type: str = None, 
                   entity_id: int = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Get audit logs.
    
    Args:
        conn: Database connection
        entity_type: Optional filter by entity type
        entity_id: Optional filter by entity ID
        limit: Maximum number of logs to return
    
    Returns:
        List of audit logs
    """
    query = "SELECT id, action, entity_type, entity_id, user_id, old_value, new_value, timestamp, ip_address FROM audit_logs"
    params = []
    
    if entity_type:
        query += " WHERE entity_type = ?"
        params.append(entity_type)
        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)
    elif entity_id:
        query += " WHERE entity_id = ?"
        params.append(entity_id)
    
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    
    cursor = conn.execute(query, params)
    return [
        {
            "id": row[0], "action": row[1], "entity_type": row[2],
            "entity_id": row[3], "user_id": row[4], "old_value": row[5],
            "new_value": row[6], "timestamp": row[7], "ip_address": row[8]
        }
        for row in cursor.fetchall()
    ]


def create_reconciliation_period(conn: sqlite3.Connection, period_name: str,
                                 period_start: str, period_end: str) -> int:
    """Create a new reconciliation period.
    
    Args:
        conn: Database connection
        period_name: Name of period (e.g., "September 2026")
        period_start: Start date (YYYY-MM-DD)
        period_end: End date (YYYY-MM-DD)
    
    Returns:
        Period ID
    """
    cursor = conn.execute(
        "INSERT INTO reconciliation_periods (period_name, period_start, period_end) VALUES (?, ?, ?)",
        (period_name, period_start, period_end)
    )
    conn.commit()
    return cursor.lastrowid


def lock_reconciliation_period(conn: sqlite3.Connection, period_id: int, locked_by: str = "system") -> bool:
    """Lock a reconciliation period (no more changes allowed).
    
    Args:
        conn: Database connection
        period_id: Period ID
        locked_by: Name of person locking
    
    Returns:
        True if locked successfully
    """
    try:
        conn.execute(
            """UPDATE reconciliation_periods 
               SET status = 'locked', locked_by = ?, locked_at = datetime('now')
               WHERE id = ?""",
            (locked_by, period_id)
        )
        
        # Log the lock
        log_audit_action(conn, "LOCK_PERIOD", "reconciliation_period", period_id,
                       locked_by, "open", "locked")
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error locking period: {e}")
        return False


def adjust_stock_from_upload(conn: sqlite3.Connection, upload_id: int, 
                             reviewed_by: str = "system") -> bool:
    """Apply stock adjustments based on approved upload data.
    
    Args:
        conn: Database connection
        upload_id: Upload ID
        reviewed_by: Name of person making adjustment
    
    Returns:
        True if adjustments applied successfully
    """
    try:
        # Get upload rows that were approved
        cursor = conn.execute(
            """SELECT ur.chemical_name, ur.balance_this_month, ur.upload_unit
               FROM upload_rows ur
               LEFT JOIN approval_workflow aw ON ur.id = aw.upload_row_id
               WHERE ur.upload_id = ? AND (aw.status = 'approved' OR aw.id IS NULL)""",
            (upload_id,)
        )
        
        adjustments = 0
        for row in cursor.fetchall():
            chemical_name = row[0]
            new_qty = row[1]
            upload_unit = row[2]
            
            # Get current stock
            chemical = conn.execute(
                "SELECT id, current_qty, unit FROM chemicals WHERE name = ?",
                (chemical_name,)
            ).fetchone()
            
            if chemical:
                chem_id, old_qty, chem_unit = chemical
                
                # Convert unit if needed
                if upload_unit and upload_unit.upper() != chem_unit.upper():
                    converted_qty = convert_quantity(conn, new_qty, upload_unit, chem_unit)
                    if converted_qty is not None:
                        new_qty = converted_qty
                
                # Update stock
                conn.execute(
                    "UPDATE chemicals SET current_qty = ?, last_updated = ? WHERE id = ?",
                    (new_qty, date.isoformat(date.today()), chem_id)
                )
                
                # Log the adjustment
                log_audit_action(conn, "ADJUST_STOCK", "chemical", chem_id,
                               reviewed_by, str(old_qty), str(new_qty),
                               f"Adjusted from upload {upload_id}")
                
                adjustments += 1
        
        # Update upload status
        conn.execute(
            "UPDATE uploads SET status = 'adjusted' WHERE id = ?",
            (upload_id,)
        )
        
        conn.commit()
        print(f"Applied {adjustments} stock adjustments from upload {upload_id}")
        return True
    except Exception as e:
        print(f"Error adjusting stock: {e}")
        return False


def update_stock(conn: sqlite3.Connection, name: str, delta: float, unit: str = "KG") -> bool:
    """Update chemical stock by delta (positive or negative).
    
    Args:
        conn: Database connection
        name: Chemical name
        delta: Quantity change (positive = add, negative = remove)
        unit: Unit of delta (must match chemical's unit or be 'KG')
    
    Returns:
        True if updated successfully, False if chemical not found
    """
    # Get chemical info
    chemical = conn.execute(
        "SELECT id, name, current_qty, unit FROM chemicals WHERE name = ?",
        (name,)
    ).fetchone()
    
    if not chemical:
        print(f"Chemical '{name}' not found in database")
        return False
    
    chem_id, chem_name, current_qty, chem_unit = chemical
    
    # Normalize unit - if user provides unit, validate it matches or is KG
    if unit.upper() != chem_unit.upper() and unit.upper() != "KG":
        print(f"Unit mismatch: chemical is '{chem_unit}', provided '{unit}'")
        # Try converting: if chemical is KG and user says KG, that's fine
        # If different units, we need conversion logic
        # For now, just use the chemical's unit and ignore provided unit
        unit = chem_unit
    
    new_qty = current_qty + delta
    if new_qty < 0:
        print(f"Warning: Stock would go negative ({new_qty} {chem_unit}). Setting to 0.")
        new_qty = 0
    
    conn.execute(
        "UPDATE chemicals SET current_qty = ?, last_updated = ? WHERE id = ?",
        (new_qty, date.isoformat(date.today()), chem_id)
    )
    conn.commit()
    
    print(f"Updated '{chem_name}': {current_qty} {chem_unit} -> {new_qty} {chem_unit} (change: {delta} {chem_unit})")
    return True


def add_chemical(conn: sqlite3.Connection, name: str, qty: float, unit: str = "KG") -> bool:
    """Add a new chemical to the database."""
    try:
        conn.execute(
            "INSERT INTO chemicals (name, current_qty, unit, last_updated) VALUES (?, ?, ?, ?)",
            (name, qty, unit, date.isoformat(date.today()))
        )
        conn.commit()
        print(f"Added chemical: {name} = {qty} {unit}")
        return True
    except sqlite3.IntegrityError:
        print(f"Chemical '{name}' already exists. Use update_stock instead.")
        return False


# ============================================================
# PHASE 2: RECIPE MANAGEMENT
# ============================================================

def add_recipe(conn: sqlite3.Connection, name: str, total_quantity: float = 1, water_percentage: float = 0) -> bool:
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
            "INSERT INTO recipes (name, total_quantity, water_percentage) VALUES (?, ?, ?)",
            (name, total_quantity, water_percentage)
        )
        conn.commit()
        print(f"Created recipe: '{name}' (total quantity: {total_quantity})")
        return True
    except sqlite3.IntegrityError:
        print(f"Recipe '{name}' already exists.")
        return False


def get_recipe_by_name(conn: sqlite3.Connection, name: str) -> Optional[Dict[str, Any]]:
    """Get recipe info by name."""
    cursor = conn.execute(
        "SELECT id, name, total_quantity, water_percentage, created_date FROM recipes WHERE name = ?",
        (name,)
    )
    row = cursor.fetchone()
    if row:
        return {"id": row[0], "name": row[1], "total_quantity": row[2], "water_percentage": row[3], "created": row[4]}
    return None


def add_recipe_item(conn: sqlite3.Connection, recipe_name: str, chemical_name: str, 
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
        print(f"Recipe '{recipe_name}' not found. Create it first.")
        return False
    
    # Check chemical exists
    chemical = conn.execute(
        "SELECT id FROM chemicals WHERE name = ?", (chemical_name,)
    ).fetchone()
    if not chemical:
        print(f"Chemical '{chemical_name}' not found in database.")
        return False
    
    # Check if item already exists in recipe
    existing = conn.execute(
        "SELECT id FROM recipe_items WHERE recipe_id = ? AND chemical_id = ?",
        (recipe["id"], chemical[0])
    ).fetchone()
    if existing:
        print(f"'{chemical_name}' already in recipe '{recipe_name}'. Use update_recipe_item instead.")
        return False
    
    required_qty_per_unit = percentage / 100.0
    conn.execute(
        "INSERT INTO recipe_items (recipe_id, chemical_id, percentage, required_qty_per_unit) VALUES (?, ?, ?, ?)",
        (recipe["id"], chemical[0], percentage, required_qty_per_unit)
    )
    conn.commit()
    print(f"Added '{chemical_name}' to recipe '{recipe_name}': {percentage}% ({required_qty_per_unit * recipe['total_quantity']} KG for total quantity {recipe['total_quantity']})")
    return True


def list_recipes(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """List all recipes with their total quantity info."""
    cursor = conn.execute(
        "SELECT id, name, total_quantity, water_percentage, created_date FROM recipes ORDER BY name"
    )
    return [
        {"id": row[0], "name": row[1], "total_quantity": row[2], "water_percentage": row[3], "created": row[4]}
        for row in cursor.fetchall()
    ]


def list_recipe_items(conn: sqlite3.Connection, recipe_name: str) -> List[Dict[str, Any]]:
    """List all chemicals in a recipe with percentages and required quantities."""
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        print(f"Recipe '{recipe_name}' not found.")
        return []
    
    cursor = conn.execute("""
        SELECT r.name as recipe_name, r.total_quantity, 
               c.name as chemical_name, c.current_qty, c.unit,
               ri.required_qty_per_unit, ri.percentage
        FROM recipe_items ri
        JOIN recipes r ON ri.recipe_id = r.id
        JOIN chemicals c ON ri.chemical_id = c.id
        WHERE r.name = ?
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


def update_recipe_item(conn: sqlite3.Connection, recipe_name: str, chemical_name: str,
                       new_percentage: float) -> bool:
    """Update the percentage of a chemical in a recipe."""
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        print(f"Recipe '{recipe_name}' not found.")
        return False
    
    chemical = conn.execute("SELECT id FROM chemicals WHERE name = ?", (chemical_name,)).fetchone()
    if not chemical:
        print(f"Chemical '{chemical_name}' not found.")
        return False
    
    new_qty_per_unit = new_percentage / 100.0
    result = conn.execute(
        "UPDATE recipe_items SET percentage = ?, required_qty_per_unit = ? WHERE recipe_id = ? AND chemical_id = ?",
        (new_percentage, new_qty_per_unit, recipe["id"], chemical[0])
    )
    conn.commit()
    
    if result.rowcount > 0:
        print(f"Updated '{chemical_name}' in '{recipe_name}': now {new_percentage}%")
        return True
    else:
        print(f"'{chemical_name}' not found in recipe '{recipe_name}'.")
        return False


def delete_recipe_item(conn: sqlite3.Connection, recipe_name: str, chemical_name: str) -> bool:
    """Remove a chemical from a recipe."""
    recipe = get_recipe_by_name(conn, recipe_name)
    if not recipe:
        print(f"Recipe '{recipe_name}' not found.")
        return False
    
    chemical = conn.execute("SELECT id FROM chemicals WHERE name = ?", (chemical_name,)).fetchone()
    if not chemical:
        print(f"Chemical '{chemical_name}' not found.")
        return False
    
    result = conn.execute(
        "DELETE FROM recipe_items WHERE recipe_id = ? AND chemical_id = ?",
        (recipe["id"], chemical[0])
    )
    conn.commit()
    
    if result.rowcount > 0:
        print(f"Removed '{chemical_name}' from recipe '{recipe_name}'")
        return True
    else:
        print(f"'{chemical_name}' was not in recipe '{recipe_name}'.")
        return False


def delete_recipe(conn: sqlite3.Connection, name: str) -> bool:
    """Delete a recipe and all its items."""
    recipe = get_recipe_by_name(conn, name)
    if not recipe:
        print(f"Recipe '{name}' not found.")
        return False
    
    conn.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe["id"],))
    conn.execute("DELETE FROM recipes WHERE id = ?", (recipe["id"],))
    conn.commit()
    print(f"Deleted recipe '{name}' and all its items.")
    return True


def update_recipe(conn: sqlite3.Connection, name: str, 
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
        print(f"Recipe '{name}' not found.")
        return False
    
    updates = []
    params = []
    if total_quantity is not None:
        updates.append("total_quantity = ?")
        params.append(total_quantity)
    if water_percentage is not None:
        updates.append("water_percentage = ?")
        params.append(water_percentage)
    
    if not updates:
        return True
    
    params.append(recipe["id"])
    conn.execute(f"UPDATE recipes SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    print(f"Updated recipe '{name}': {updates}")
    return True


# ============================================================
# PHASE 3: PRODUCTION REPORT GENERATION
# ============================================================

def generate_report(conn: sqlite3.Connection, recipe_name: str, production_qty: float) -> List[Dict[str, Any]]:
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


def generate_multi_recipe_report(conn: sqlite3.Connection, recipe_selections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

SALE_STAGE_ORDER = ["pi_issued", "lc_received", "shipment_ongoing", "payment_due", "completed"]


def _next_stage(current: str) -> Optional[str]:
    """Return the next stage in the pipeline, or None if at end."""
    try:
        idx = SALE_STAGE_ORDER.index(current)
        return SALE_STAGE_ORDER[idx + 1] if idx + 1 < len(SALE_STAGE_ORDER) else None
    except ValueError:
        return None


def add_sale(conn: sqlite3.Connection, sale_data: Dict[str, Any],
             items: List[Dict[str, Any]], initial_stage: str = "pi_issued") -> int:
    """Insert a new sale with line items. Returns the new sale ID."""
    cursor = conn.execute(
        """INSERT INTO sales (stage, pi_number, pi_date, client_name, pi_file_path)
           VALUES (?, ?, ?, ?, ?)""",
        (initial_stage, sale_data.get("pi_number"), sale_data.get("pi_date"),
         sale_data.get("client_name"), sale_data.get("pi_file_path"))
    )
    sale_id = cursor.lastrowid
    for item in items:
        conn.execute(
            """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price)
               VALUES (?, ?, ?, ?)""",
            (sale_id, item["product_name"], item.get("quantity", 0), item.get("unit_price", 0))
        )
    conn.execute(
        """INSERT INTO sales_stage_history (sale_id, from_stage, to_stage, notes)
           VALUES (?, NULL, ?, 'Created')""",
        (sale_id, initial_stage)
    )
    log_audit_action(conn, "SALE_CREATE", "sale", sale_id,
                     new_value=sale_data.get("pi_number"))
    conn.commit()
    return sale_id


def get_all_sales(conn: sqlite3.Connection, stage: Optional[str] = None,
                  search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all sales (optionally filtered by stage and/or search text).

    Search matches PI number, client name, LC number, and product names.
    """
    query = """
        SELECT s.id, s.stage, s.pi_number, s.pi_date, s.client_name, s.pi_file_path,
               s.lc_number, s.lc_date, s.shipment_date, s.payment_date, s.payment_amount,
               s.created_at, s.updated_at,
               COALESCE(SUM(si.quantity * si.unit_price), 0) AS total_value,
               COUNT(si.id) AS item_count,
               COALESCE((SELECT SUM(sp.payment_amount) FROM sale_payments sp
                         WHERE sp.sale_id = s.id), 0) AS total_paid
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
    """
    params: list = []
    clauses = []
    if stage:
        clauses.append("s.stage = ?")
        params.append(stage)
    if search:
        like = f"%{search}%"
        clauses.append("""(s.pi_number LIKE ? OR s.client_name LIKE ? OR s.lc_number LIKE ?
            OR EXISTS (SELECT 1 FROM sale_items si2 WHERE si2.sale_id = s.id
                       AND si2.product_name LIKE ?))""")
        params.extend([like, like, like, like])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " GROUP BY s.id ORDER BY s.created_at DESC, s.id DESC"
    return [
        {"id": r[0], "stage": r[1], "pi_number": r[2], "pi_date": r[3],
         "client_name": r[4], "pi_file_path": r[5], "lc_number": r[6],
         "lc_date": r[7], "shipment_date": r[8], "payment_date": r[9],
         "payment_amount": r[10], "created_at": r[11], "updated_at": r[12],
         "total_value": r[13], "item_count": r[14], "total_paid": r[15],
         "balance": r[13] - r[15]}
        for r in conn.execute(query, params).fetchall()
    ]


def get_sale_by_id(conn: sqlite3.Connection, sale_id: int) -> Optional[Dict[str, Any]]:
    """Return a single sale with its items and stage history."""
    row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not row:
        return None
    sale = {
        "id": row[0], "stage": row[1], "pi_number": row[2], "pi_date": row[3],
        "client_name": row[4], "pi_file_path": row[5], "lc_number": row[6],
        "lc_date": row[7], "shipment_date": row[8], "payment_date": row[9],
        "payment_amount": row[10], "created_at": row[11], "updated_at": row[12],
    }
    sale["items"] = [
        {"id": r[0], "sale_id": r[1], "product_name": r[2],
         "quantity": r[3], "unit_price": r[4]}
        for r in conn.execute(
            "SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id", (sale_id,)
        ).fetchall()
    ]
    sale["history"] = [
        {"id": r[0], "sale_id": r[1], "from_stage": r[2], "to_stage": r[3],
         "changed_at": r[4], "notes": r[5]}
        for r in conn.execute(
            "SELECT * FROM sales_stage_history WHERE sale_id = ? ORDER BY changed_at",
            (sale_id,)
        ).fetchall()
    ]
    sale["payments"] = [
        {"id": r[0], "sale_id": r[1], "payment_date": r[2],
         "payment_amount": r[3], "notes": r[4], "created_at": r[5]}
        for r in conn.execute(
            "SELECT * FROM sale_payments WHERE sale_id = ? ORDER BY payment_date, id",
            (sale_id,)
        ).fetchall()
    ]
    sale["invoice_total"] = sum(
        (i["quantity"] or 0) * (i["unit_price"] or 0) for i in sale["items"]
    )
    sale["total_paid"] = sum(p["payment_amount"] or 0 for p in sale["payments"])
    sale["balance"] = sale["invoice_total"] - sale["total_paid"]
    return sale


def move_sale_to_stage(conn: sqlite3.Connection, sale_id: int, new_stage: str,
                       notes: Optional[str] = None) -> bool:
    """Move a sale to the next (or specified) stage. Logs the transition."""
    row = conn.execute("SELECT stage FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not row:
        return False
    current = row[0]
    if new_stage not in SALE_STAGE_ORDER:
        return False
    conn.execute(
        "UPDATE sales SET stage = ?, updated_at = datetime('now') WHERE id = ?",
        (new_stage, sale_id)
    )
    conn.execute(
        """INSERT INTO sales_stage_history (sale_id, from_stage, to_stage, notes)
           VALUES (?, ?, ?, ?)""",
        (sale_id, current, new_stage, notes)
    )
    log_audit_action(conn, "SALE_MOVE", "sale", sale_id,
                     old_value=current, new_value=new_stage)
    conn.commit()
    return True


def advance_sale(conn: sqlite3.Connection, sale_id: int,
                 notes: Optional[str] = None) -> Optional[str]:
    """Advance a sale to the next pipeline stage. Returns the new stage or None."""
    row = conn.execute("SELECT stage FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if not row:
        return None
    nxt = _next_stage(row[0])
    if nxt and move_sale_to_stage(conn, sale_id, nxt, notes):
        return nxt
    return None


def update_sale_lc(conn: sqlite3.Connection, sale_id: int, lc_number: str,
                   lc_date: str, shipment_date: str) -> bool:
    """Enter LC details for a sale."""
    conn.execute(
        """UPDATE sales
           SET lc_number = ?, lc_date = ?, shipment_date = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (lc_number, lc_date, shipment_date, sale_id)
    )
    log_audit_action(conn, "SALE_LC", "sale", sale_id, new_value=lc_number)
    conn.commit()
    return True


def get_sale_invoice_total(conn: sqlite3.Connection, sale_id: int) -> float:
    """Sum of quantity * unit_price over all line items of a sale."""
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity * unit_price), 0) FROM sale_items WHERE sale_id = ?",
        (sale_id,)
    ).fetchone()
    return row[0] if row else 0


def get_sale_total_paid(conn: sqlite3.Connection, sale_id: int) -> float:
    """Sum of all recorded payments for a sale."""
    row = conn.execute(
        "SELECT COALESCE(SUM(payment_amount), 0) FROM sale_payments WHERE sale_id = ?",
        (sale_id,)
    ).fetchone()
    return row[0] if row else 0


def _sync_sale_payment_totals(conn: sqlite3.Connection, sale_id: int) -> None:
    """Keep legacy sales.payment_amount/date in sync with payment records."""
    row = conn.execute(
        """SELECT COALESCE(SUM(payment_amount), 0), MAX(payment_date)
           FROM sale_payments WHERE sale_id = ?""",
        (sale_id,)
    ).fetchone()
    conn.execute(
        """UPDATE sales SET payment_amount = ?, payment_date = ?,
           updated_at = datetime('now') WHERE id = ?""",
        (row[0], row[1], sale_id)
    )


def record_sale_payment(conn: sqlite3.Connection, sale_id: int, payment_date: str,
                        payment_amount: float, notes: Optional[str] = None) -> Dict[str, Any]:
    """Append a (possibly partial) payment. Auto-completes from payment_due
    when total paid reaches the invoice total. Returns totals + stage."""
    if payment_amount is None or payment_amount <= 0:
        raise ValueError("payment_amount must be positive")
    cursor = conn.execute(
        """INSERT INTO sale_payments (sale_id, payment_date, payment_amount, notes)
           VALUES (?, ?, ?, ?)""",
        (sale_id, payment_date, payment_amount, notes)
    )
    payment_id = cursor.lastrowid
    _sync_sale_payment_totals(conn, sale_id)
    log_audit_action(conn, "SALE_PAYMENT", "sale", sale_id,
                     new_value=str(payment_amount))
    total_paid = get_sale_total_paid(conn, sale_id)
    invoice_total = get_sale_invoice_total(conn, sale_id)
    row = conn.execute("SELECT stage FROM sales WHERE id = ?", (sale_id,)).fetchone()
    stage = row[0] if row else None
    if _complete_if_paid(conn, sale_id, stage, total_paid, invoice_total):
        stage = "completed"
    conn.commit()
    return {"payment_id": payment_id, "total_paid": total_paid,
            "balance": invoice_total - total_paid, "stage": stage}


def _complete_if_paid(conn: sqlite3.Connection, sale_id: int, stage: Optional[str],
                      total_paid: float, invoice_total: float) -> bool:
    """Move a payment_due sale to completed once payments cover the invoice."""
    if stage == "payment_due" and total_paid >= invoice_total:
        move_sale_to_stage(conn, sale_id, "completed",
                           f"Paid in full (${total_paid})")
        return True
    return False


def update_sale_payment(conn: sqlite3.Connection, sale_id: int, payment_date: str,
                        payment_amount: float) -> bool:
    """Legacy single-payment API — now appends a payment record."""
    record_sale_payment(conn, sale_id, payment_date, payment_amount)
    return True


def update_sale_payment_record(conn: sqlite3.Connection, payment_id: int,
                               payment_date: Optional[str] = None,
                               payment_amount: Optional[float] = None,
                               notes: Optional[str] = None) -> bool:
    """Edit a payment record. Reverts a completed sale to payment_due
    if the new total no longer covers the invoice."""
    row = conn.execute("SELECT sale_id FROM sale_payments WHERE id = ?",
                       (payment_id,)).fetchone()
    if not row:
        return False
    sale_id = row[0]
    fields, vals = [], []
    if payment_date is not None:
        fields.append("payment_date = ?")
        vals.append(payment_date)
    if payment_amount is not None:
        if payment_amount <= 0:
            raise ValueError("payment_amount must be positive")
        fields.append("payment_amount = ?")
        vals.append(payment_amount)
    if notes is not None:
        fields.append("notes = ?")
        vals.append(notes)
    if fields:
        vals.append(payment_id)
        conn.execute(f"UPDATE sale_payments SET {', '.join(fields)} WHERE id = ?", vals)
    _sync_sale_payment_totals(conn, sale_id)
    log_audit_action(conn, "SALE_PAYMENT_EDIT", "sale", sale_id,
                     new_value=str(payment_id))
    row = conn.execute("SELECT stage FROM sales WHERE id = ?", (sale_id,)).fetchone()
    stage = row[0] if row else None
    _complete_if_paid(conn, sale_id, stage,
                      get_sale_total_paid(conn, sale_id),
                      get_sale_invoice_total(conn, sale_id))
    _revert_if_unpaid(conn, sale_id)
    conn.commit()
    return True


def delete_sale_payment_record(conn: sqlite3.Connection, payment_id: int) -> bool:
    """Delete a payment record and re-sync totals."""
    row = conn.execute("SELECT sale_id, payment_amount FROM sale_payments WHERE id = ?",
                       (payment_id,)).fetchone()
    if not row:
        return False
    sale_id = row[0]
    conn.execute("DELETE FROM sale_payments WHERE id = ?", (payment_id,))
    _sync_sale_payment_totals(conn, sale_id)
    log_audit_action(conn, "SALE_PAYMENT_DELETE", "sale", sale_id,
                     old_value=str(row[1]))
    _revert_if_unpaid(conn, sale_id)
    conn.commit()
    return True


def _revert_if_unpaid(conn: sqlite3.Connection, sale_id: int) -> None:
    """Move a completed sale back to payment_due if payments no longer cover it."""
    row = conn.execute("SELECT stage FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if row and row[0] == "completed":
        if get_sale_total_paid(conn, sale_id) < get_sale_invoice_total(conn, sale_id):
            move_sale_to_stage(conn, sale_id, "payment_due",
                               "Payment edited/deleted — balance outstanding")


def add_sale_item(conn: sqlite3.Connection, sale_id: int, product_name: str,
                  quantity: float, unit_price: float) -> int:
    """Add a line item to an existing sale. Returns the new item ID."""
    cursor = conn.execute(
        """INSERT INTO sale_items (sale_id, product_name, quantity, unit_price)
           VALUES (?, ?, ?, ?)""",
        (sale_id, product_name, quantity, unit_price)
    )
    conn.commit()
    return cursor.lastrowid


def update_sale_item(conn: sqlite3.Connection, item_id: int,
                     product_name: Optional[str] = None,
                     quantity: Optional[float] = None,
                     unit_price: Optional[float] = None) -> bool:
    """Update fields of a sale line item."""
    fields, vals = [], []
    if product_name is not None:
        fields.append("product_name = ?")
        vals.append(product_name)
    if quantity is not None:
        fields.append("quantity = ?")
        vals.append(quantity)
    if unit_price is not None:
        fields.append("unit_price = ?")
        vals.append(unit_price)
    if not fields:
        return False
    vals.append(item_id)
    conn.execute(f"UPDATE sale_items SET {', '.join(fields)} WHERE id = ?", vals)
    conn.commit()
    return True


def delete_sale_item(conn: sqlite3.Connection, item_id: int) -> bool:
    """Delete a single line item from a sale."""
    conn.execute("DELETE FROM sale_items WHERE id = ?", (item_id,))
    conn.commit()
    return True


def delete_sale(conn: sqlite3.Connection, sale_id: int) -> bool:
    """Delete a sale and cascade-delete its items and history."""
    row = conn.execute("SELECT pi_number FROM sales WHERE id = ?", (sale_id,)).fetchone()
    conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
    log_audit_action(conn, "SALE_DELETE", "sale", sale_id,
                     old_value=row[0] if row else None)
    conn.commit()
    return True


def get_sales_summary(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Aggregate counts per stage + total pipeline value."""
    summary: Dict[str, Any] = {stage: {"count": 0, "value": 0} for stage in SALE_STAGE_ORDER}
    rows = conn.execute("""
        SELECT s.stage,
               COUNT(DISTINCT s.id) AS cnt,
               COALESCE(SUM(si.quantity * si.unit_price), 0) AS val
        FROM sales s
        LEFT JOIN sale_items si ON si.sale_id = s.id
        GROUP BY s.stage
    """).fetchall()
    for r in rows:
        summary[r[0]] = {"count": r[1], "value": r[2]}
    total_value = sum(v["value"] for v in summary.values())
    total_sales = sum(v["count"] for v in summary.values())
    summary["total_pipeline_value"] = total_value
    summary["total_sales"] = total_sales
    return summary


# ==================== USER AUTH FUNCTIONS ====================

import hashlib as _hashlib
import secrets as _secrets
from datetime import datetime as _datetime, timedelta as _timedelta

BCRYPT_ROUNDS = 13
SESSION_TTL_HOURS = 1
MAX_LOGIN_FAILS = 5
LOGIN_WINDOW_MINUTES = 10


def _hash_password(password: str) -> str:
    """bcrypt-12 over a SHA256 pre-hash (avoids bcrypt's 72-byte truncation)."""
    import bcrypt
    digest = _hashlib.sha256(password.encode("utf-8")).hexdigest()
    return bcrypt.hashpw(digest.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def _check_password(password: str, password_hash: str) -> bool:
    import bcrypt
    digest = _hashlib.sha256(password.encode("utf-8")).hexdigest()
    try:
        return bcrypt.checkpw(digest.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def validate_username(username: str) -> str:
    """Normalize + validate a username. Raises ValueError."""
    import re as _re
    name = (username or "").strip()
    if not (3 <= len(name) <= 32):
        raise ValueError("username must be 3-32 characters")
    if not _re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("username may only contain letters, digits, _, . and -")
    return name


def validate_password(password: str) -> None:
    """Raises ValueError if the password is unacceptable."""
    if password is None or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if len(password) > 512:
        raise ValueError("password must be at most 512 characters")


def create_user(conn: sqlite3.Connection, username: str, password: str,
                role: str = "user") -> int:
    """Create a user with a bcrypt-12 hash. Returns the new user ID."""
    name = validate_username(username)
    validate_password(password)
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (name, _hash_password(password), role)
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"username '{name}' already exists")
    conn.commit()
    log_audit_action(conn, "USER_CREATE", "user", cursor.lastrowid, new_value=f"{name}:{role}")
    conn.commit()
    return cursor.lastrowid


def get_setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def ensure_setup_token(conn: sqlite3.Connection) -> Optional[str]:
    """Generate the single-use setup token (raw) on first need.

    Stores only its SHA256 hash. Prints the raw token to the server console
    for the operator. Returns None when setup is closed or a token exists.
    """
    if count_users(conn) > 0:
        return None
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'setup_token_hash'").fetchone()
    if row:
        return None
    raw = _secrets.token_urlsafe(32)
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('setup_token_hash', ?)",
                 (_hashlib.sha256(raw.encode("utf-8")).hexdigest(),))
    conn.commit()
    import sys as _sys
    print("=" * 64, file=_sys.stderr)
    print("  SETUP TOKEN (single-use - enter it on the /setup page):", file=_sys.stderr)
    print(f"  {raw}", file=_sys.stderr)
    print("  If lost, delete the 'setup_token_hash' row from app_settings", file=_sys.stderr)
    print("  to regenerate. Set DISABLE_SETUP=true to disable setup.", file=_sys.stderr)
    print("=" * 64, file=_sys.stderr)
    return raw


def check_setup_token(conn: sqlite3.Connection, presented: Optional[str]) -> bool:
    """Constant-time comparison of the presented setup token."""
    import hmac as _hmac
    if not presented:
        return False
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'setup_token_hash'").fetchone()
    if not row:
        return False
    digest = _hashlib.sha256(presented.encode("utf-8")).hexdigest()
    return _hmac.compare_digest(digest, row[0])


def create_first_admin(conn: sqlite3.Connection, username: str, password: str) -> int:
    """Race-safe first-admin creation.

    Holds a write lock (BEGIN IMMEDIATE) across the count-then-insert so two
    simultaneous submits cannot both succeed. Flips the persistent
    setup_completed flag in the same transaction.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if count_users(conn) > 0:
            raise ValueError("setup already completed")
        name = validate_username(username)
        validate_password(password)
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            (name, _hash_password(password))
        )
        uid = cursor.lastrowid
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('setup_completed', 'true')")
        log_audit_action(conn, "USER_CREATE", "user", uid, new_value=f"{name}:admin")
        conn.commit()
        return uid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[Dict[str, Any]]:
    """Return public user fields (never the password hash)."""
    row = conn.execute(
        "SELECT id, username, role, created_at FROM users WHERE username = ?",
        (username,)
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}


def verify_user(conn: sqlite3.Connection, username: str, password: str) -> Optional[Dict[str, Any]]:
    """Check credentials. Returns the public user dict or None (generic failure)."""
    row = conn.execute(
        "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
        ((username or "").strip(),)
    ).fetchone()
    if not row:
        return None
    if not _check_password(password or "", row[2]):
        return None
    return {"id": row[0], "username": row[1], "role": row[3], "created_at": row[4]}


def list_users(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """All users, public fields only."""
    return [
        {"id": r[0], "username": r[1], "role": r[2], "created_at": r[3]}
        for r in conn.execute("SELECT id, username, role, created_at FROM users ORDER BY id").fetchall()
    ]


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete a user (cascades sessions). Refuses to remove the last admin."""
    row = conn.execute("SELECT role, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    if row[0] == "admin":
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        if admins <= 1:
            raise ValueError("cannot delete the last admin")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    log_audit_action(conn, "USER_DELETE", "user", user_id, old_value=row[1])
    conn.commit()
    return True


def create_session(conn: sqlite3.Connection, user_id: int,
                   ttl_hours: int = SESSION_TTL_HOURS) -> str:
    """Mint a session token. Returns the raw token (only time it is visible)."""
    token = _secrets.token_urlsafe(32)
    token_hash = _hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = (_datetime.utcnow() + _timedelta(hours=ttl_hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (token_hash, user_id, expires)
    )
    conn.commit()
    return token


def get_session_user(conn: sqlite3.Connection, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate a session token. Returns the user dict or None."""
    if not token:
        return None
    token_hash = _hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute(
        """SELECT u.id, u.username, u.role, u.created_at, s.expires_at, s.revoked
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ?""",
        (token_hash,)
    ).fetchone()
    if not row or row[5]:
        return None
    if row[4] <= _datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"):
        return None
    return {"id": row[0], "username": row[1], "role": row[2], "created_at": row[3]}


def revoke_session(conn: sqlite3.Connection, token: Optional[str]) -> None:
    """Revoke a single session (logout / kill switch)."""
    if not token:
        return
    conn.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?",
                 (_hashlib.sha256(token.encode("utf-8")).hexdigest(),))
    conn.commit()


def revoke_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    """Revoke all sessions of a user."""
    conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()


def cleanup_expired_sessions(conn: sqlite3.Connection) -> int:
    """Delete expired sessions. Returns rows removed."""
    cursor = conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    conn.commit()
    return cursor.rowcount


def _api_key_hash(raw_key: str) -> str:
    return _hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(conn: sqlite3.Connection, name: str, created_by: Optional[int] = None,
                   expires_at: Optional[str] = None, allowed_ips: str = "") -> str:
    """Create a script API key. Returns the raw key ONCE (never stored)."""
    import re as _re
    name = (name or "").strip()
    if not (1 <= len(name) <= 64):
        raise ValueError("key name must be 1-64 characters")
    raw = "ck_live_" + _secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO api_keys (key_hash, name, created_by, expires_at, allowed_ips)
           VALUES (?, ?, ?, ?, ?)""",
        (_api_key_hash(raw), name, created_by, expires_at, allowed_ips or "")
    )
    conn.commit()
    return raw


def list_api_keys(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """All keys WITHOUT hashes. Includes a fingerprint (first 12 hash chars)."""
    return [
        {"id": r[0], "fingerprint": r[1][:12], "name": r[2], "created_by": r[3],
         "created_at": r[4], "expires_at": r[5], "allowed_ips": r[6],
         "revoked": bool(r[7]), "last_used_at": r[8], "last_used_ip": r[9]}
        for r in conn.execute(
            """SELECT id, key_hash, name, created_by, created_at, expires_at,
                      allowed_ips, revoked, last_used_at, last_used_ip
               FROM api_keys ORDER BY id""").fetchall()
    ]


def _ip_allowed(allowed_ips: str, ip: Optional[str]) -> bool:
    """Comma-separated exact IPs/CIDRs. Empty means any."""
    import ipaddress as _ip
    spec = (allowed_ips or "").strip()
    if not spec:
        return True
    if not ip:
        return False
    for entry in [e.strip() for e in spec.split(",") if e.strip()]:
        try:
            if "/" in entry:
                if _ip.ip_address(ip) in _ip.ip_network(entry, strict=False):
                    return True
            elif ip == entry:
                return True
        except ValueError:
            continue
    return False


def validate_api_key(conn: sqlite3.Connection, raw_key: Optional[str],
                     ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Stateless per-request validation: hash, revocation, expiry, IP allowlist."""
    if not raw_key:
        return None
    row = conn.execute(
        "SELECT id, name, expires_at, allowed_ips, revoked FROM api_keys WHERE key_hash = ?",
        (_api_key_hash(raw_key),)
    ).fetchone()
    if not row or row[4]:
        return None
    now = _datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    if row[2] and row[2] <= now:
        return None
    if not _ip_allowed(row[3], ip):
        return None
    conn.execute("UPDATE api_keys SET last_used_at = datetime('now'), last_used_ip = ? WHERE id = ?",
                 (ip, row[0]))
    conn.commit()
    return {"id": row[0], "name": row[1], "type": "api-key"}


def revoke_api_key(conn: sqlite3.Connection, key_id: int) -> bool:
    """Permanent revocation. Independent of user sessions (separate kill switch)."""
    cursor = conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
    conn.commit()
    return cursor.rowcount > 0


def record_login_attempt(conn: sqlite3.Connection, username: Optional[str],
                         ip_address: Optional[str], success: bool) -> None:
    conn.execute(
        "INSERT INTO login_attempts (username, ip_address, success) VALUES (?, ?, ?)",
        ((username or "").strip() or None, ip_address, 1 if success else 0)
    )
    # Periodic cleanup: remove entries older than 1 day
    conn.execute("DELETE FROM login_attempts WHERE attempted_at < datetime('now', '-1 day')")
    conn.commit()


def is_login_blocked(conn: sqlite3.Connection, username: Optional[str],
                     ip_address: Optional[str], max_fails: int = MAX_LOGIN_FAILS,
                     window_minutes: int = LOGIN_WINDOW_MINUTES) -> bool:
    """True when failures in the window reach the limit (per username OR per IP)."""
    name = (username or "").strip() or None
    row = conn.execute(
        """SELECT COUNT(*) FROM login_attempts
           WHERE success = 0 AND attempted_at >= datetime('now', ?)
           AND (username = ? OR ip_address = ?)""",
        (f"-{window_minutes} minutes", name, ip_address)
    ).fetchone()
    return (row[0] if row else 0) >= max_fails


def check_api_key_rate_limit(conn: sqlite3.Connection, key_id: int,
                              max_hits: int = 300, window_seconds: int = 60) -> bool:
    """True if rate limit exceeded for this API key (DB-backed, survives restarts)."""
    row = conn.execute(
        """SELECT COUNT(*) FROM api_key_rate_limits
           WHERE key_id = ? AND hit_at >= datetime('now', ?)""",
        (key_id, f"-{window_seconds} seconds")
    ).fetchone()
    return (row[0] if row else 0) >= max_hits


def record_api_key_hit(conn: sqlite3.Connection, key_id: int) -> None:
    """Record an API key usage hit and clean old entries."""
    conn.execute("INSERT INTO api_key_rate_limits (key_id) VALUES (?)", (key_id,))
    conn.execute("DELETE FROM api_key_rate_limits WHERE hit_at < datetime('now', '-1 day')")
    conn.commit()


def main():
    """Main CLI entry point."""
    import sys
    
    if len(sys.argv) < 2:
        print("Chemical Stock Tracker")
        print("Usage: python chem_stock.py <command> [args]")
        print()
        print("Stock Commands:")
        print("  import-json          Import stock from stock_data.json")
        print("  list                 List all chemicals with current stock")
        print("  update <name> <qty>  Add/subtract quantity from stock")
        print("  add <name> <qty> <unit>  Add new chemical")
        print()
        print("Recipe Commands:")
        print("  recipe-create <name> [yield]   Create new recipe")
        print("  recipe-add <recipe> <chem> <qty>  Add chemical to recipe")
        print("  recipe-list                    List all recipes")
        print("  recipe-show <name>             Show recipe details")
        print("  recipe-update <recipe> <chem> <qty>  Update recipe item")
        print("  recipe-delete-item <recipe> <chem>   Remove chemical from recipe")
        print("  recipe-delete <name>          Delete entire recipe")
        print()
        print("Report Commands:")
        print("  report <recipe> <qty>         Generate production report")
        print("  export <recipe> <qty> [path]  Export report to CSV")
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
            if len(sys.argv) < 3:
                print("Usage: python chem_stock.py recipe-create <name> [yield]")
                sys.exit(1)
            name = sys.argv[2]
            product_yield = float(sys.argv[3]) if len(sys.argv) > 3 else 1
            add_recipe(conn, name, product_yield)
            
        elif command == "recipe-add":
            if len(sys.argv) < 5:
                print("Usage: python chem_stock.py recipe-add <recipe> <chemical> <qty_per_unit>")
                sys.exit(1)
            recipe_name = sys.argv[2]
            chemical_name = sys.argv[3]
            qty_per_unit = float(sys.argv[4])
            add_recipe_item(conn, recipe_name, chemical_name, qty_per_unit)
            
        elif command == "recipe-list":
            recipes = list_recipes(conn)
            if not recipes:
                print("No recipes found.")
            else:
                print(f"\n{'Recipe Name':<30} {'Yield':>8} {'Created':>12}")
                print("-" * 52)
                for r in recipes:
                    print(f"{r['name']:<30} {r['yield']:>8} {r['created']:>12}")
                    
        elif command == "recipe-show":
            if len(sys.argv) < 3:
                print("Usage: python chem_stock.py recipe-show <name>")
                sys.exit(1)
            items = list_recipe_items(conn, sys.argv[2])
            if items:
                print(f"\nRecipe: {items[0]['recipe_name']} (Yield: {items[0]['yield']})")
                print(f"{'Chemical':<30} {'Current Stock':>14} {'Need/Unit':>10}")
                print("-" * 56)
                for item in items:
                    print(f"{item['chemical_name']:<30} {item['current_stock']:>10} {item['unit']:>4} {item['required_per_unit']:>10}")
            else:
                print("Recipe not found or empty.")
                
        elif command == "recipe-update":
            if len(sys.argv) < 5:
                print("Usage: python chem_stock.py recipe-update <recipe> <chemical> <new_qty>")
                sys.exit(1)
            update_recipe_item(conn, sys.argv[2], sys.argv[3], float(sys.argv[4]))
            
        elif command == "recipe-delete-item":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py recipe-delete-item <recipe> <chemical>")
                sys.exit(1)
            delete_recipe_item(conn, sys.argv[2], sys.argv[3])
            
        elif command == "recipe-delete":
            if len(sys.argv) < 3:
                print("Usage: python chem_stock.py recipe-delete <name>")
                sys.exit(1)
            delete_recipe(conn, sys.argv[2])
        
        # Report commands
        elif command == "report":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py report <recipe_name> <production_qty>")
                sys.exit(1)
            recipe_name = sys.argv[2]
            production_qty = float(sys.argv[3])
            report = generate_report(conn, recipe_name, production_qty)
            if report:
                print_report(report, recipe_name, production_qty)
            else:
                print(f"Could not generate report for recipe '{recipe_name}'.")
                
        elif command == "export":
            if len(sys.argv) < 4:
                print("Usage: python chem_stock.py export <recipe_name> <production_qty> [output_path]")
                sys.exit(1)
            recipe_name = sys.argv[2]
            production_qty = float(sys.argv[3])
            output_path = sys.argv[4] if len(sys.argv) > 4 else None
            report = generate_report(conn, recipe_name, production_qty)
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