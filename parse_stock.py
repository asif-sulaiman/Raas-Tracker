import re
import json
import os
from typing import List, Dict, Any


def detect_file_type(filename: str) -> str:
    """Detect file type by extension.
    
    Args:
        filename: Name or path of the file
    
    Returns:
        'excel', 'pdf', or 'unknown'
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in ['.xlsx', '.xls']:
        return 'excel'
    elif ext == '.pdf':
        return 'pdf'
    return 'unknown'


def parse_excel(filepath: str) -> List[Dict[str, Any]]:
    """Parse Excel file and extract chemical stock data.
    
    Auto-detects columns by header names (fuzzy match).
    
    Args:
        filepath: Path to Excel file
    
    Returns:
        List of dicts with keys: name, balance_last_month, balance_this_month,
        and optional: batch_number, expiry_date, upload_unit
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("openpyxl not installed. Run: pip install openpyxl")
        return []
    
    wb = load_workbook(filepath, data_only=True)
    ws = wb.active
    
    # Get headers from first row
    headers = []
    for cell in ws[1]:
        headers.append(str(cell.value).strip().lower() if cell.value else "")
    
    # Map columns by header names (fuzzy match)
    name_col = None
    last_month_col = None
    this_month_col = None
    batch_col = None
    expiry_col = None
    unit_col = None
    
    for i, header in enumerate(headers):
        header_lower = header.lower()
        if any(kw in header_lower for kw in ['product', 'name', 'chemical', 'item', 'description']):
            name_col = i
        elif any(kw in header_lower for kw in ['last month', 'last_month', 'balance_last', 'prev', 'last']):
            last_month_col = i
        elif any(kw in header_lower for kw in ['this month', 'this_month', 'balance_this', 'current', 'this']):
            this_month_col = i
        elif any(kw in header_lower for kw in ['batch', 'lot']):
            batch_col = i
        elif any(kw in header_lower for kw in ['exp', 'expiry', 'expiration']):
            expiry_col = i
        elif any(kw in header_lower for kw in ['unit', 'uom', 'measure']):
            unit_col = i
    
    # If columns not found, try positional (assume first 3 columns)
    if name_col is None:
        name_col = 0
    if last_month_col is None:
        last_month_col = 1
    if this_month_col is None:
        this_month_col = 2
    
    result = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[name_col]:
            name = str(row[name_col]).strip()
            if not name or name.lower() in ['nan', 'none', '']:
                continue
            
            try:
                last_month = float(row[last_month_col]) if row[last_month_col] else 0
            except (ValueError, TypeError):
                last_month = 0
            
            try:
                this_month = float(row[this_month_col]) if row[this_month_col] else 0
            except (ValueError, TypeError):
                this_month = 0
            
            # Extract optional columns
            batch_number = str(row[batch_col]).strip() if batch_col is not None and row[batch_col] else ""
            expiry_date = str(row[expiry_col]).strip() if expiry_col is not None and row[expiry_col] else ""
            upload_unit = str(row[unit_col]).strip() if unit_col is not None and row[unit_col] else ""
            
            result.append({
                "name": name,
                "balance_last_month": last_month,
                "balance_this_month": this_month,
                "batch_number": batch_number,
                "expiry_date": expiry_date,
                "upload_unit": upload_unit
            })
    
    wb.close()
    return result


def detect_pdf_type(filepath: str) -> str:
    """Detect if PDF is digital, scanned, or mixed.
    
    Args:
        filepath: Path to PDF file
    
    Returns:
        'digital', 'scanned', or 'mixed'
    """
    try:
        import pdfplumber
    except ImportError:
        return 'unknown'
    
    digital_pages = 0
    scanned_pages = 0
    
    try:
        with pdfplumber.open(filepath) as pdf:
            total_pages = len(pdf.pages)
            
            # Check first 3 pages or all pages if less
            pages_to_check = min(3, total_pages)
            
            for i in range(pages_to_check):
                page = pdf.pages[i]
                
                # Try to extract text
                text = page.extract_text()
                tables = page.extract_tables()
                
                # If tables or substantial text found, it's digital
                if tables and len(tables) > 0:
                    digital_pages += 1
                elif text and len(text.strip()) > 100:
                    digital_pages += 1
                else:
                    scanned_pages += 1
            
            # Determine PDF type
            if digital_pages > scanned_pages:
                return 'digital'
            elif scanned_pages > digital_pages:
                return 'scanned'
            else:
                return 'mixed'
    except Exception as e:
        print(f"Error detecting PDF type: {e}")
        return 'unknown'


def parse_pdf(filepath: str) -> List[Dict[str, Any]]:
    """Parse PDF file and extract chemical stock data.
    
    Handles digital PDFs with tables, and falls back to OCR for scanned PDFs.
    
    Args:
        filepath: Path to PDF file
    
    Returns:
        List of dicts with keys: name, balance_last_month, balance_this_month,
        and optional: batch_number, expiry_date, upload_unit
    """
    # Detect PDF type
    pdf_type = detect_pdf_type(filepath)
    print(f"Detected PDF type: {pdf_type}")
    
    if pdf_type == 'digital' or pdf_type == 'mixed':
        # Try digital parsing first
        result = parse_pdf_digital(filepath)
        if result:
            return result
    
    # Fallback to OCR for scanned PDFs
    if pdf_type == 'scanned' or pdf_type == 'mixed' or pdf_type == 'unknown':
        result = parse_pdf_ocr(filepath)
        if result:
            return result
    
    # If nothing worked, try digital one more time
    return parse_pdf_digital(filepath)


def parse_pdf_digital(filepath: str) -> List[Dict[str, Any]]:
    """Parse digital PDF using pdfplumber for table extraction.
    
    Args:
        filepath: Path to PDF file
    
    Returns:
        List of dicts with chemical data
    """
    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber not installed. Run: pip install pdfplumber")
        return []
    
    result = []
    
    try:
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    
                    # Find header row
                    header_row = table[0]
                    if not header_row:
                        continue
                    
                    # Map columns - handle both header-based and positional mapping
                    name_col = None
                    last_month_col = None
                    this_month_col = None
                    batch_col = None
                    expiry_col = None
                    unit_col = None
                    
                    for i, cell in enumerate(header_row):
                        if cell:
                            cell_lower = str(cell).lower()
                            if any(kw in cell_lower for kw in ['product', 'name', 'chemical', 'item', 'description']):
                                name_col = i
                            elif any(kw in cell_lower for kw in ['last month', 'last_month', 'balance_last', 'prev', 'last']):
                                last_month_col = i
                            elif any(kw in cell_lower for kw in ['this month', 'this_month', 'balance_this', 'current', 'this']):
                                this_month_col = i
                            elif any(kw in cell_lower for kw in ['batch', 'lot']):
                                batch_col = i
                            elif any(kw in cell_lower for kw in ['exp', 'expiry', 'expiration']):
                                expiry_col = i
                            elif any(kw in cell_lower for kw in ['unit', 'uom', 'measure']):
                                unit_col = i
                    
                    # If headers not found, detect by data pattern
                    # Check if first row looks like item numbers (0, 1, 2...) and second row has names
                    if name_col is None and last_month_col is None:
                        # Try to detect by data pattern
                        test_row = table[1] if len(table) > 1 else None
                        if test_row and len(test_row) >= 4:
                            # Check if column 0 is item number and column 1 is name
                            if test_row[0] and str(test_row[0]).strip().isdigit():
                                name_col = 1  # Name is in column 1
                                last_month_col = 2  # Last month in column 2
                                this_month_col = 3  # This month in column 3
                            else:
                                # Default positional
                                name_col = 0
                                last_month_col = 1
                                this_month_col = 2
                        else:
                            # Default positional
                            name_col = 0
                            last_month_col = 1
                            this_month_col = 2
                    elif name_col is None:
                        name_col = 0
                    if last_month_col is None:
                        last_month_col = 1
                    if this_month_col is None:
                        this_month_col = 2
                    
                    # Parse data rows
                    for row in table[1:]:
                        if not row or len(row) <= max(name_col, last_month_col, this_month_col):
                            continue
                        
                        name = str(row[name_col]).strip() if row[name_col] else ""
                        
                        # Skip empty or very short names
                        if not name or len(name) < 2:
                            continue
                        
                        # Skip pure numbers (item numbers)
                        if name.isdigit():
                            continue
                        
                        # Skip header-like words
                        if name.lower() in ['nan', 'none', '', 'item', 'product', 'chemical', 'name', 'total']:
                            continue
                        
                        # Parse quantity - handle "0 KG" format (quantity with unit)
                        def parse_qty_with_unit(value):
                            if not value:
                                return 0, ""
                            val_str = str(value).strip()
                            # Try to extract number and unit
                            import re
                            match = re.match(r'^([\d,\.]+)\s*([A-Za-z]*)$', val_str)
                            if match:
                                qty_str = match.group(1).replace(',', '')
                                unit = match.group(2).upper()
                                try:
                                    return float(qty_str), unit
                                except ValueError:
                                    return 0, ""
                            # Try just number
                            try:
                                return float(val_str.replace(',', '')), ""
                            except ValueError:
                                return 0, ""
                        
                        last_month, last_unit = parse_qty_with_unit(row[last_month_col])
                        this_month, this_unit = parse_qty_with_unit(row[this_month_col])
                        
                        # Use last_unit as upload_unit if available
                        upload_unit = last_unit if last_unit else (this_unit if this_unit else "")
                        
                        # Extract optional columns with error handling
                        batch_number = ""
                        expiry_date = ""
                        
                        try:
                            if batch_col is not None and row[batch_col]:
                                batch_number = str(row[batch_col]).strip()
                        except (IndexError, TypeError):
                            pass
                        
                        try:
                            if expiry_col is not None and row[expiry_col]:
                                expiry_date = str(row[expiry_col]).strip()
                        except (IndexError, TypeError):
                            pass
                        
                        result.append({
                            "name": name,
                            "balance_last_month": last_month,
                            "balance_this_month": this_month,
                            "batch_number": batch_number,
                            "expiry_date": expiry_date,
                            "upload_unit": upload_unit
                        })
    except Exception as e:
        print(f"Error parsing digital PDF: {e}")
    
    return result


def parse_pdf_ocr(filepath: str) -> List[Dict[str, Any]]:
    """Parse scanned PDF using OCR.
    
    Uses pytesseract or EasyOCR for text extraction.
    
    Args:
        filepath: Path to PDF file
    
    Returns:
        List of dicts with chemical data
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("PyMuPDF not installed. Run: pip install PyMuPDF")
        return []
    
    try:
        import pytesseract
        from PIL import Image
        import io
        ocr_available = True
    except ImportError:
        print("pytesseract not installed. Run: pip install pytesseract")
        ocr_available = False
    
    if not ocr_available:
        return []
    
    result = []
    
    try:
        doc = fitz.open(filepath)
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            
            # Convert page to image
            pix = page.get_pixmap()
            img_data = pix.tobytes("png")
            
            # OCR the image
            image = Image.open(io.BytesIO(img_data))
            text = pytesseract.image_to_string(image)
            
            # Parse the text for chemical data
            # Look for patterns like: "Chemical Name 100 200"
            lines = text.split('\n')
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Try to parse line using regex
                # Pattern: name number number (optional batch/expiry/unit)
                import re
                # Match: name followed by 2 numbers
                match = re.match(r'^(.+?)\s+(\d+(?:,\d{3})*(?:\.\d+)?)\s+(\d+(?:,\d{3})*(?:\.\d+)?)', line)
                if match:
                    name = match.group(1).strip()
                    last_month = float(match.group(2).replace(',', ''))
                    this_month = float(match.group(3).replace(',', ''))
                    
                    if name and len(name) > 2:  # Skip too short names
                        result.append({
                            "name": name,
                            "balance_last_month": last_month,
                            "balance_this_month": this_month,
                            "batch_number": "",
                            "expiry_date": "",
                            "upload_unit": ""
                        })
        
        doc.close()
    except Exception as e:
        print(f"Error in OCR parsing: {e}")
    
    return result


def parse_stock_file(filepath: str) -> List[Dict[str, Any]]:
    """Parse stock file (Excel or PDF) based on file type.
    
    Args:
        filepath: Path to the file
    
    Returns:
        List of dicts with keys: name, balance_last_month, balance_this_month
    """
    file_type = detect_file_type(filepath)
    
    if file_type == 'excel':
        return parse_excel(filepath)
    elif file_type == 'pdf':
        return parse_pdf(filepath)
    else:
        print(f"Unsupported file type: {filepath}")
        return []


# Original TXT parser (kept for backward compatibility)
def parse_txt_file(txt_path: str, output_path: str = None) -> List[Dict[str, Any]]:
    """Parse the original text file format and save to JSON.
    
    Args:
        txt_path: Path to the text file
        output_path: Optional path to save JSON output
    
    Returns:
        List of parsed items
    """
    with open(txt_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    parsed = []
    for line in lines[2:126]:  # skip header and remarks
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(\d+)\s*(.*?)\s+(\d+)\s*(KG|PCS)\s+(\d+)\s*(KG|PCS|)', line)
        if m:
            item_no = m.group(1)
            product_name = m.group(2).strip()
            last_month = int(m.group(3))
            last_month_unit = m.group(4)
            this_month = int(m.group(5))
            this_month_unit = m.group(6) if m.group(6) else last_month_unit
            parsed.append({
                'item_no': item_no,
                'product_name': product_name,
                'balance_last_month': last_month,
                'last_month_unit': last_month_unit,
                'balance_this_month': this_month,
                'this_month_unit': this_month_unit
            })
        else:
            print(f'Could not parse: {line}')
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(parsed, f, indent=2)
        print(f'Parsed {len(parsed)} items. Saved to {output_path}')
    
    return parsed


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        result = parse_stock_file(filepath)
        print(f"Parsed {len(result)} items from {filepath}")
        for item in result[:3]:
            print(f"  {item['name']}: last={item['balance_last_month']}, this={item['balance_this_month']}")
    else:
        # Default: parse the original TXT file
        txt_path = r'O:\Logistics\Malaysia\Malaysia\Balance Product List\2026\AUGUST_2026.txt'
        output_path = r'O:\Study  Career\Python\ChemCalc\stock_data.json'
        parse_txt_file(txt_path, output_path)
