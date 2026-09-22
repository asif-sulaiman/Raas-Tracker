"""PI Document Parser using python-docx + Pydantic for structured extraction.

Supports both .docx (Office Open XML) and .doc (legacy OLE2) formats.
.doc files are converted to .docx via Word COM automation (doc2docx) before parsing.
"""

import re
import os
import tempfile
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from docx import Document


class PIItem(BaseModel):
    """Single product line item from PI."""
    product_name: str
    quantity: float = 0.0
    unit_price: float = 0.0


class PIHeader(BaseModel):
    """PI header metadata."""
    pi_number: str
    pi_date: Optional[str] = None
    client_name: Optional[str] = None


class PIExtraction(BaseModel):
    """Complete PI extraction result."""
    header: PIHeader
    items: List[PIItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def is_valid(self) -> bool:
        """Check if extraction has minimum required data."""
        return bool(self.header.pi_number and self.items)


def extract_pi_number_from_filename(filepath: str) -> Optional[str]:
    """Extract PI number from filename (e.g., 'PI-2026-001_ClientName.docx')."""
    filename = os.path.basename(filepath)
    match = re.search(r'(PI-\d{4}-\d+)', filename, re.IGNORECASE)
    return match.group(1).upper() if match else None


def extract_pi_date(text: str) -> Optional[str]:
    """Extract PI date from document text."""
    date_patterns = [
        r'\b(\d{4}-\d{2}-\d{2})\b',
        r'\b(\d{2}/\d{2}/\d{4})\b',
        r'\b(\d{2}-\d{2}-\d{4})\b',
        r'\b(\d{2}\.\d{2}\.\d{4})\b',
    ]
    for pattern in date_patterns:
        matches = re.findall(pattern, text)
        if matches:
            dt = matches[0]
            try:
                if '-' in dt and dt.count('-') == 2 and len(dt.split('-')[0]) == 4:
                    return dt
                elif '/' in dt:
                    return datetime.strptime(dt, "%m/%d/%Y").strftime("%Y-%m-%d")
                elif '-' in dt:
                    return datetime.strptime(dt, "%d-%m-%Y").strftime("%Y-%m-%d")
                elif '.' in dt:
                    return datetime.strptime(dt, "%d.%m.%Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def extract_client_name(text: str) -> Optional[str]:
    """Extract client name from 'Sold to:', 'Bill to:', 'To:' labels."""
    labels = [
        r'Sold\s+to[:\s]+([^\n\r]+)',
        r'Bill\s+to[:\s]+([^\n\r]+)',
        r'\bTo[:\s]+([^\n\r]+)',
        r'Customer[:\s]+([^\n\r]+)',
        r'Client[:\s]+([^\n\r]+)',
    ]
    for pattern in labels:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            name = re.sub(r'\s+', ' ', name)
            if name and len(name) > 2:
                return name
    return None


def extract_product_table(doc: Document) -> List[PIItem]:
    """Extract product rows from document tables."""
    items = []
    for table in doc.tables:
        if not table.rows:
            continue
        header_cells = [cell.text.strip().lower() for cell in table.rows[0].cells]
        header_text = ' '.join(header_cells)
        if any(kw in header_text for kw in ['product', 'item', 'description', 'qty', 'quantity', 'price', 'unit', 'amount']):
            for row in table.rows[1:]:
                cells = [cell.text.strip() for cell in row.cells]
                if len(cells) < 2:
                    continue
                product_name = cells[0] if cells[0] else ""
                if not product_name or product_name.lower() in ['product', 'item', 'description', '']:
                    continue
                qty_str = cells[1] if len(cells) > 1 else "0"
                price_str = cells[2] if len(cells) > 2 else "0"
                try:
                    qty = float(re.sub(r'[^\d\.]', '', qty_str))
                    price = float(re.sub(r'[^\d\.]', '', price_str))
                    items.append(PIItem(
                        product_name=product_name,
                        quantity=qty,
                        unit_price=price
                    ))
                except ValueError:
                    continue
    return items


def _convert_doc_to_docx(doc_bytes: bytes, original_filename: str) -> bytes:
    """Convert a .doc file to .docx bytes using Word COM automation.

    Requires Microsoft Word installed on the machine.
    Raises RuntimeError if conversion fails.
    """
    try:
        from doc2docx import convert as _doc2docx_convert
    except ImportError:
        raise RuntimeError(".doc conversion needs doc2docx + Microsoft Word (Windows only)")

    with tempfile.TemporaryDirectory() as tmpdir:
        doc_path = os.path.join(tmpdir, original_filename)
        with open(doc_path, "wb") as f:
            f.write(doc_bytes)

        try:
            _doc2docx_convert(doc_path)
        except Exception as e:
            raise RuntimeError(f"Word COM conversion failed: {e}")

        # doc2docx writes the .docx next to the original file
        docx_path = os.path.splitext(doc_path)[0] + ".docx"
        if not os.path.exists(docx_path):
            raise RuntimeError("Conversion produced no .docx output")

        with open(docx_path, "rb") as f:
            return f.read()


def parse_pi_document(filepath: str) -> PIExtraction:
    """Parse a PI .docx file and return structured extraction."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    with open(filepath, "rb") as f:
        return parse_pi_stream(f, os.path.basename(filepath))


def parse_pi_stream(file_stream, filename: str) -> PIExtraction:
    """Parse a PI document (.doc or .docx) from an in-memory file stream.

    For .doc files: converts to .docx via Word COM, then parses.
    For .docx files: parses directly with python-docx.
    Nothing is written to disk permanently (temp files are cleaned up).
    """
    warnings = []

    pi_number = extract_pi_number_from_filename(filename)
    if not pi_number:
        warnings.append("Could not extract PI number from filename")

    file_stream.seek(0)
    raw_bytes = file_stream.read()
    is_doc = filename.lower().endswith(".doc") and not filename.lower().endswith(".docx")

    if is_doc:
        try:
            docx_bytes = _convert_doc_to_docx(raw_bytes, filename)
        except RuntimeError as e:
            warnings.append(f".doc conversion failed: {e}")
            # Fallback: try to read as docx anyway (some .doc files are actually docx)
            try:
                doc = Document(__import__("io").BytesIO(raw_bytes))
            except Exception:
                raise ValueError(
                    "Could not read .doc file. Ensure Microsoft Word is installed "
                    "for .doc support, or save the file as .docx and retry."
                )
        else:
            doc = Document(__import__("io").BytesIO(docx_bytes))
            warnings.append(".doc file converted to .docx via Word")
    else:
        doc = Document(__import__("io").BytesIO(raw_bytes))

    full_text = '\n'.join(p.text for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                full_text += '\n' + cell.text

    if not pi_number:
        match = re.search(r'(PI-\d{4}-\d+)', full_text, re.IGNORECASE)
        if match:
            pi_number = match.group(1).upper()
            warnings.append("PI number extracted from document body (not filename)")

    pi_date = extract_pi_date(full_text)
    if not pi_date:
        warnings.append("Could not extract PI date")

    client_name = extract_client_name(full_text)
    if not client_name:
        warnings.append("Could not extract client name")

    items = extract_product_table(doc)
    if not items:
        warnings.append("No product items extracted from tables")

    header = PIHeader(pi_number=pi_number or "UNKNOWN", pi_date=pi_date, client_name=client_name)
    return PIExtraction(header=header, items=items, warnings=warnings)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_sales.py <pi_file.docx|pi_file.doc>")
        sys.exit(1)
    extraction = parse_pi_document(sys.argv[1])
    print(extraction.model_dump_json(indent=2))