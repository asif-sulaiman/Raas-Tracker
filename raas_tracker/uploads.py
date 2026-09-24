"""Upload parsing, comparison, approvals, and reconciliation."""

import psycopg
import json
import os
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Union

from .audit import log_audit_action
from .db import logger
from .stock import convert_quantity, get_unit_conversion


def _now_str() -> str:
    """Current UTC time as 'YYYY-MM-DD HH:MM:SS' for TEXT datetime columns."""
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

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


def compare_stock_upload(conn: psycopg.Connection, upload_data: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    # Rows whose units cannot be converted to the DB chemical's unit.
    # These must never reach stock: approval/apply gates block them, and
    # the saved upload is marked 'flagged' instead of 'completed'.
    unmapped_units = [
        {"name": row["name"], "upload_unit": row.get("upload_unit", ""),
         "db_unit": row.get("db_unit", "")}
        for row in (matches + last_month_mismatches + this_month_mismatches + both_mismatches)
        if not row.get("unit_match", True)
    ]

    return {
        "matches": matches,
        "last_month_mismatches": last_month_mismatches,
        "this_month_mismatches": this_month_mismatches,
        "both_mismatches": both_mismatches,
        "not_in_db": not_in_db,
        "not_in_upload": not_in_upload,
        "unmapped_units": unmapped_units,
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


def save_upload(conn: psycopg.Connection, filename: str, results: Dict[str, Any]) -> int:
    """Save upload results to database.
    
    Args:
        conn: Database connection
        filename: Name of uploaded file
        results: Output from compare_stock_upload()
    
    Returns:
        Upload ID
    """
    stats = results["stats"]
    status = "flagged" if results.get("unmapped_units") else "completed"
    cursor = conn.execute(
        """INSERT INTO uploads (filename, status, total_chemicals, matched, 
           last_month_mismatches, this_month_mismatches, both_mismatches, 
           not_in_db, not_in_upload, match_percentage)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (filename, status, stats["total"], stats["matched"], stats["last_month_mismatches"],
         stats["this_month_mismatches"], stats["both_mismatches"],
         stats["not_in_db"], stats["not_in_upload"], stats["match_percentage"])
    )
    upload_id = cursor.fetchone()[0]
    
    # Save all rows with batch/unit info
    for row in results.get("matches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"], 
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("last_month_mismatches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"],
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("this_month_mismatches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"],
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("both_mismatches", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"],
             1 if row.get("unit_match", True) else 0)
        )
    for row in results.get("not_in_db", []):
        conn.execute(
            """INSERT INTO upload_rows (upload_id, chemical_name, batch_number, expiry_date, 
               upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0)""",
            (upload_id, row["name"], row.get("batch_number", ""), row.get("expiry_date", ""),
             row.get("upload_unit", ""), row["upload_last"], row["upload_this"])
        )
    
    conn.commit()
    log_audit_action(conn, "UPLOAD_CREATE", "upload", upload_id, new_value=filename)
    _notify_upload_outcome(conn, upload_id, filename, results)
    return upload_id


def _notify_upload_outcome(conn: psycopg.Connection, upload_id: int,
                           filename: str, results: Dict[str, Any]) -> None:
    """Raise notifications for upload mismatches and expiry findings."""
    from .notifications import notify

    stats = results["stats"]
    total_mismatches = (stats["last_month_mismatches"] + stats["this_month_mismatches"]
                        + stats["both_mismatches"] + stats["not_in_db"]
                        + stats["not_in_upload"])
    if total_mismatches > 0:
        notify(
            conn, type="upload_mismatch",
            title=f"Upload has {total_mismatches} discrepancies: {filename}",
            body=(f"{stats['total']} rows checked, {stats['match_percentage']:.1f}% match. "
                  f"Review the comparison to resolve mismatches."),
            severity="warning", entity_type="upload", entity_id=upload_id,
            dedupe_key=f"upload:{upload_id}:mismatch",
        )

    expired = expiring = 0
    for key in ("matches", "last_month_mismatches", "this_month_mismatches", "both_mismatches"):
        for row in results.get(key, []):
            _, _, status = validate_expiry_date(row.get("expiry_date", "") or "")
            if status == "EXPIRED":
                expired += 1
            elif status == "EXPIRING_SOON":
                expiring += 1
    if expired:
        notify(
            conn, type="stock_expired",
            title=f"{expired} expired item{'s' if expired != 1 else ''} in {filename}",
            body="Expired stock found during reconciliation. Remove or dispose immediately.",
            severity="critical", entity_type="upload", entity_id=upload_id,
            dedupe_key=f"upload:{upload_id}:expired",
        )
    if expiring:
        notify(
            conn, type="stock_expiring",
            title=f"{expiring} item{'s' if expiring != 1 else ''} expiring within 30 days",
            body=f"Found in {filename}. Plan usage or disposal before expiry.",
            severity="warning", entity_type="upload", entity_id=upload_id,
            dedupe_key=f"upload:{upload_id}:expiring",
        )


def get_upload_history(conn: psycopg.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent upload history."""
    cursor = conn.execute(
        """SELECT id, filename, upload_date, status, total_chemicals, matched, 
           last_month_mismatches, this_month_mismatches, both_mismatches,
           not_in_db, not_in_upload, match_percentage 
           FROM uploads ORDER BY upload_date DESC LIMIT %s""",
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


def get_upload_results(conn: psycopg.Connection, upload_id: int) -> List[Dict[str, Any]]:
    """Get upload rows for a specific upload."""
    cursor = conn.execute(
        "SELECT id, chemical_name, balance_last_month, balance_this_month, matched_in_db FROM upload_rows WHERE upload_id = %s",
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
        logger.warning("openpyxl not installed. Run: pip install openpyxl")
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


def _row_unit_mappable(conn: psycopg.Connection, chemical_name: str,
                       upload_unit: str) -> bool:
    """True when an upload row's unit can be applied safely.

    No unit given, or chemical not in DB (adjust skips those anyway), means
    nothing to corrupt. Otherwise a conversion to the DB unit must exist.
    """
    if not (upload_unit or "").strip():
        return True
    row = conn.execute(
        "SELECT unit FROM chemicals WHERE UPPER(name) = %s",
        ((chemical_name or "").strip().upper(),)
    ).fetchone()
    if not row:
        return True
    return get_unit_conversion(conn, upload_unit.upper().strip(),
                               (row[0] or "KG").upper().strip()) is not None


def get_unmapped_rows(conn: psycopg.Connection, upload_id: int) -> List[Dict[str, Any]]:
    """Rows of an upload whose units cannot be converted (for mapping UI).

    Each entry carries row_id, chemical_name, upload_unit and db_unit so
    the frontend can render editors and post conversions.
    """
    rows = conn.execute(
        "SELECT ur.id, ur.chemical_name, ur.upload_unit, c.unit "
        "FROM upload_rows ur LEFT JOIN chemicals c "
        "ON UPPER(c.name) = UPPER(ur.chemical_name) "
        "WHERE ur.upload_id = %s",
        (upload_id,)
    ).fetchall()
    out = []
    for rid, name, unit, db_unit in rows:
        if (unit or "").strip() and db_unit and get_unit_conversion(
                conn, unit.upper().strip(), db_unit.upper().strip()) is None:
            out.append({"row_id": rid, "chemical_name": name,
                        "upload_unit": unit, "db_unit": db_unit})
    return out


def get_all_reason_codes(conn: psycopg.Connection) -> List[Dict[str, Any]]:
    """Return all reason codes."""
    cursor = conn.execute("SELECT id, code, description, category FROM reason_codes ORDER BY code")
    return [{"id": row[0], "code": row[1], "description": row[2], "category": row[3]} for row in cursor.fetchall()]


def create_approval_workflow(conn: psycopg.Connection, upload_id: int, upload_row_id: int = None) -> int:
    """Create an approval workflow entry for a upload row.
    
    Args:
        conn: Database connection
        upload_id: Upload ID
        upload_row_id: Optional row ID (if None, approves entire upload)
    
    Returns:
        Workflow ID
    """
    cursor = conn.execute(
        "INSERT INTO approval_workflow (upload_id, upload_row_id, status) VALUES (%s, %s, 'pending') RETURNING id",
        (upload_id, upload_row_id)
    )
    conn.commit()
    return cursor.fetchone()[0]


def approve_upload_row(conn: psycopg.Connection, workflow_id: int, reason_code: str, 
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
        # Atomic unit gate: refuse rows whose units cannot be converted.
        info = conn.execute(
            "SELECT upload_id, upload_row_id FROM approval_workflow WHERE id = %s",
            (workflow_id,)
        ).fetchone()
        if info and info[1] is not None:
            detail = conn.execute(
                "SELECT chemical_name, upload_unit FROM upload_rows WHERE id = %s",
                (info[1],)
            ).fetchone()
            if detail and not _row_unit_mappable(conn, detail[0], detail[1]):
                conn.rollback()
                log_audit_action(conn, "APPROVE_BLOCKED", "upload_row", info[1],
                                 reviewed_by, None,
                                 f"Unmapped unit cannot be approved: {detail[1]}")
                conn.commit()
                return False
        conn.execute(
            """UPDATE approval_workflow 
               SET status = 'approved', reason_code = %s, comments = %s, reviewed_by = %s, reviewed_at = %s
               WHERE id = %s""",
            (reason_code, comments, reviewed_by, _now_str(), workflow_id)
        )
        
        # Get workflow info for audit log
        workflow = conn.execute(
            "SELECT upload_id, upload_row_id FROM approval_workflow WHERE id = %s",
            (workflow_id,)
        ).fetchone()
        
        if workflow:
            # Log the approval
            log_audit_action(conn, "APPROVE_ROW", "upload_row", workflow[1], 
                           reviewed_by, None, f"Approved with reason: {reason_code}")
        
        conn.commit()
        return True
    except Exception as e:
        logger.exception("Error approving row")
        return False


def reject_upload_row(conn: psycopg.Connection, workflow_id: int, reason_code: str,
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
               SET status = 'rejected', reason_code = %s, comments = %s, reviewed_by = %s, reviewed_at = %s
               WHERE id = %s""",
            (reason_code, comments, reviewed_by, _now_str(), workflow_id)
        )
        
        # Get workflow info for audit log
        workflow = conn.execute(
            "SELECT upload_id, upload_row_id FROM approval_workflow WHERE id = %s",
            (workflow_id,)
        ).fetchone()
        
        if workflow:
            # Log the rejection
            log_audit_action(conn, "REJECT_ROW", "upload_row", workflow[1],
                           reviewed_by, None, f"Rejected with reason: {reason_code}")
        
        conn.commit()
        return True
    except Exception as e:
        logger.exception("Error rejecting row")
        return False


def approve_upload(conn: psycopg.Connection, upload_id: int, reviewed_by: str = "system") -> bool:
    """Approve entire upload (all rows).
    
    Args:
        conn: Database connection
        upload_id: Upload ID
        reviewed_by: Name of reviewer
    
    Returns:
        True if approved successfully
    """
    try:
        # Atomic unit gate: scan every row first. If any row carries an
        # unmapped unit, the whole batch is blocked (nothing approved,
        # status untouched) so a source document can never half-apply.
        rows = conn.execute(
            "SELECT id, chemical_name, upload_unit FROM upload_rows WHERE upload_id = %s",
            (upload_id,)
        ).fetchall()
        blocked = [r[0] for r in rows
                   if not _row_unit_mappable(conn, r[1], r[2])]
        if blocked:
            log_audit_action(conn, "APPROVE_BLOCKED", "upload", upload_id,
                             reviewed_by, None,
                             f"{len(blocked)} row(s) with unmapped units; batch blocked")
            conn.commit()
            return False

        # Update upload status
        conn.execute(
            "UPDATE uploads SET status = 'approved' WHERE id = %s",
            (upload_id,)
        )

        # Create workflow entries for all rows
        for row in rows:
            workflow_id = create_approval_workflow(conn, upload_id, row[0])
            approve_upload_row(conn, workflow_id,
                               "AUTO_APPROVED", "Auto-approved with upload", reviewed_by)
        
        # Log the approval
        log_audit_action(conn, "APPROVE_UPLOAD", "upload", upload_id,
                       reviewed_by, None, f"Approved entire upload {upload_id}")
        
        conn.commit()
        return True
    except Exception as e:
        logger.exception("Error approving upload")
        return False


def get_pending_approvals(conn: psycopg.Connection, upload_id: int = None) -> List[Dict[str, Any]]:
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
               WHERE aw.upload_id = %s AND aw.status = 'pending'
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


def create_reconciliation_period(conn: psycopg.Connection, period_name: str,
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
        "INSERT INTO reconciliation_periods (period_name, period_start, period_end) VALUES (%s, %s, %s) RETURNING id",
        (period_name, period_start, period_end)
    )
    conn.commit()
    return cursor.fetchone()[0]


def lock_reconciliation_period(conn: psycopg.Connection, period_id: int, locked_by: str = "system") -> bool:
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
               SET status = 'locked', locked_by = %s, locked_at = %s
               WHERE id = %s""",
            (locked_by, _now_str(), period_id)
        )
        
        # Log the lock
        log_audit_action(conn, "LOCK_PERIOD", "reconciliation_period", period_id,
                       locked_by, "open", "locked")
        
        conn.commit()
        return True
    except Exception as e:
        logger.exception("Error locking period")
        return False


def adjust_stock_from_upload(conn: psycopg.Connection, upload_id: int, 
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
               WHERE ur.upload_id = %s AND (aw.status = 'approved' OR aw.id IS NULL)""",
            (upload_id,)
        )
        
        adjustments = 0
        for row in cursor.fetchall():
            chemical_name = row[0]
            new_qty = row[1]
            upload_unit = row[2]
            
            # Get current stock (case-insensitive: chemical identity is
            # guarded unique on lower(name), uploads may differ in case).
            chemical = conn.execute(
                "SELECT id, current_qty, unit FROM chemicals WHERE UPPER(name) = %s",
                ((chemical_name or "").strip().upper(),)
            ).fetchone()
            
            if chemical:
                chem_id, old_qty, chem_unit = chemical
                
                # Convert unit if needed
                if upload_unit and upload_unit.upper() != chem_unit.upper():
                    converted_qty = convert_quantity(conn, new_qty, upload_unit, chem_unit)
                    if converted_qty is not None:
                        new_qty = converted_qty
                    else:
                        # Defense in depth: never write unconvertible units
                        # into stock (the approval gate should already have
                        # blocked such rows).
                        logger.warning("Skipping %s: cannot convert %s to %s",
                                       chemical_name, upload_unit, chem_unit)
                        continue
                
                # Update stock
                conn.execute(
                    "UPDATE chemicals SET current_qty = %s, last_updated = %s WHERE id = %s",
                    (new_qty, date.isoformat(date.today()), chem_id)
                )
                
                # Log the adjustment
                log_audit_action(conn, "ADJUST_STOCK", "chemical", chem_id,
                               reviewed_by, str(old_qty), str(new_qty),
                               f"Adjusted from upload {upload_id}")
                
                adjustments += 1
        
        # Update upload status
        conn.execute(
            "UPDATE uploads SET status = 'adjusted' WHERE id = %s",
            (upload_id,)
        )
        
        conn.commit()
        logger.info("Applied %s stock adjustments from upload %s", adjustments, upload_id)
        return True
    except Exception as e:
        logger.exception("Error adjusting stock")
        return False
