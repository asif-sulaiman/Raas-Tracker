"""PI Document Parser: PDF + DOCX (legacy .doc via Word) to structured extraction.

Primary path is PDF (text-based PIs, e.g. RAAS Biotech proforma invoices)
parsed with pdfplumber (tables) + PyMuPDF (text fallback) — pure Python, so it
works on the server/VPS where Microsoft Word is unavailable. .docx is parsed
with python-docx. Legacy .doc still converts via Word COM for local CLI use,
but the API no longer accepts it.
"""

import re
import io
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
    item_no: Optional[str] = None


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


def _parse_dayfirst(dt: str) -> Optional[str]:
    """Parse a date string to ISO format, preferring DD/MM/YYYY for slashes.

    RAAS PIs (Malaysia/Bangladesh) use day-first dates, so 05/09/2026 means
    5 August — not May 8. Falls back to month-first when day-first is invalid.
    """
    dt = dt.strip()
    if '-' in dt and len(dt.split('-')[0]) == 4:
        try:
            return datetime.strptime(dt, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    if '/' in dt:
        formats = ["%d/%m/%Y", "%m/%d/%Y"]
    elif '-' in dt:
        formats = ["%d-%m-%Y", "%m-%d-%Y"]
    elif '.' in dt:
        formats = ["%d.%m.%Y", "%m.%d.%Y"]
    else:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(dt, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def extract_pi_date(text: str) -> Optional[str]:
    """Extract PI date from document text.

    Prefers a date next to an 'Invoice Date'/'PI date' label, then falls back
    to scanning every date-like string (not just the first match).
    """
    labeled = re.search(
        r'(invoice\s*date|pi\s*date)\s*[:\-]?\s*'
        r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})',
        text, re.IGNORECASE)
    if labeled:
        parsed = _parse_dayfirst(labeled.group(2))
        if parsed:
            return parsed
    date_patterns = [
        r'\b(\d{4}-\d{1,2}-\d{1,2})\b',
        r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
        r'\b(\d{1,2}-\d{1,2}-\d{4})\b',
        r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b',
    ]
    for pattern in date_patterns:
        for dt in re.findall(pattern, text):
            parsed = _parse_dayfirst(dt)
            if parsed:
                return parsed
    return None


_PI_NO_LABEL = r'(?:invoice|proforma\s+invoice|p\.?\s*i\.?)\s*(?:number|no\.?)'


def extract_pi_number_from_text(text: str) -> Optional[str]:
    """Extract PI number from document body.

    RAAS PIs carry a bare number under an 'Invoice Number'/'Invoice No.' label
    (e.g. 99000001); generic PIs use a PI-YYYY-NNN pattern.
    """
    match = re.search(_PI_NO_LABEL + r'\s*[:\-]?\s*(\d[\d\-]*)',
                      text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Positional fallback: label row with the value on a following value row,
    # e.g. 'Invoice Number  Invoice Date' / '99000001  15/09/2026'.
    m = re.search(_PI_NO_LABEL, text, re.IGNORECASE)
    if m:
        for line in text[m.end():].split('\n')[:5]:
            de_dated = re.sub(r'\d{1,4}[\/\-.]\d{1,2}[\/\-.]\d{2,4}', ' ', line)
            tok = re.search(r'\b(\d{4,}(?:-\d+)?)\b', de_dated)
            if tok:
                return tok.group(1)
    match = re.search(r'(PI-\d{4}-\d+)', text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


def _dedupe_doubled_text(value: str) -> str:
    """Collapse 'X X' duplications from two-column PDF text extraction.

    Side-by-side mailing/delivery addresses often extract as
    'EXAMPLE CLIENT LTD EXAMPLE CLIENT LTD' — return one copy.
    """
    words = value.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        if words[:half] == words[half:]:
            return ' '.join(words[:half])
    return value


_LABEL_LIKE = ('invoice', 'delivery', 'mailing', 'address', 'number',
               'shipment', 'payment', 'destination', 'country', 'carriage',
               'port', 'partial', 'trans-shipment', 'transhipment',
               'third party', 'account', 'bank', 'swift')


def _looks_like_label(value: str) -> bool:
    """True when a candidate is another form label rather than a real value."""
    v = value.strip().lower()
    if len(v) <= 2:
        return True
    return any(re.search(r'\b' + re.escape(tok) + r'\b', v)
               for tok in _LABEL_LIKE)


def _scan_address_block(text: str, anchor: str) -> Optional[str]:
    """First real value line after an address anchor (mailing/delivery)."""
    m = re.search(anchor, text, re.IGNORECASE)
    if not m:
        return None
    for cand in text[m.end():].split('\n')[:4]:
        name = re.sub(r'(?i)delivery\s*address', '', cand)
        # Merged multi-column value rows carry the name plus the PI
        # number/date ('EXAMPLE ... LTD 99000001 15/09/2026') — strip
        # dates and standalone doc numbers before de-duplication.
        name = re.sub(r'\d{1,4}[\/\-.]\d{1,2}[\/\-.]\d{2,4}', ' ', name)
        name = re.sub(r'\b\d{5,}(?:-\d+)?\b', ' ', name)
        name = _dedupe_doubled_text(re.sub(r'\s+', ' ', name).strip())
        if name and not _looks_like_label(name):
            return name
    return None


def extract_client_name(text: str) -> Optional[str]:
    """Extract client name: RAAS 'Mailing Address' block first, then labels."""
    # The label often shares a header row with other labels
    # ('Mailing Address ... Invoice Number ...'), with values on the
    # rows below — scan the following lines for the first line that is
    # a real value rather than another label.
    for anchor in (r'mailing\s*address', r'delivery\s*address'):
        name = _scan_address_block(text, anchor)
        if name:
            return name
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


def _clean_cell(value) -> str:
    """Normalize a table cell to single-spaced text."""
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _parse_number(value: str) -> Optional[float]:
    """Parse '1,000' / '2.65' / 'US$48,880.00' to float; None if unparseable.

    Returns None for merged multi-number cells ('1 11,000') so callers fall
    back to total-anchored recovery instead of a concatenated wrong value.
    """
    if value is None:
        return None
    s = str(value).replace(',', '')
    if len([t for t in s.split() if re.search(r'\d', t)]) > 1:
        return None
    cleaned = re.sub(r'[^\d.\-]', '', s)
    if not cleaned or cleaned in ('.', '-', '-.'):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_last_number(value: str) -> Optional[float]:
    """Parse the last numeric token — for total cells merged with neighbors."""
    if value is None:
        return None
    tokens = [t for t in str(value).split() if re.search(r'\d', t)]
    if not tokens:
        return None
    return _parse_number(tokens[-1])


def _recover_pair(row: List[str], total_col: Optional[int],
                  total: float) -> Optional[tuple]:
    """Find the (qty, price) pair in a row whose product matches the total.

    Layout-agnostic fallback for merged/shifted columns: every numeric token
    in the row (except the total column, dates, and dotted codes like HS
    '3402.90.10') is a candidate. The pair with qty x price ~= total wins;
    the left-most token is taken as quantity (RAAS column order).
    """
    nums = []
    for idx, cell in enumerate(row):
        if idx == total_col:
            continue
        for tpos, tok in enumerate(str(cell).split()):
            if '/' in tok:
                continue  # dates like 15/09/2026
            v = _parse_number(tok)
            if v is None or v == 0:
                continue
            nums.append(((idx, tpos), v))
    threshold = max(0.05, 0.001 * abs(total))
    best = None
    for a in range(len(nums)):
        for b in range(a + 1, len(nums)):
            (pa, va), (pb, vb) = nums[a], nums[b]
            err = abs(va * vb - total)
            if err <= threshold and (best is None or err < best[0]):
                q, p = (va, vb) if pa <= pb else (vb, va)
                best = (err, q, p)
    return (best[1], best[2]) if best else None


def _derive_missing(total: float, known: float) -> float:
    """Derive the missing qty/price from total / known, preferring 2dp."""
    v = total / known
    if abs(round(v, 2) * known - total) <= max(0.05, 0.001 * abs(total)):
        return round(v, 2)
    return round(v, 4)


def _map_product_columns(header_cells: List[str]) -> Optional[dict]:
    """Map product-table columns by header name instead of fixed positions.

    Handles RAAS layouts (Sr.No | Quantity in Kg | Item No. | Description |
    HS Code | Unit Price | Total) as well as generic (Item | Qty | Price)
    tables. Returns None when the row is not a product-table header.
    """
    heads = [_clean_cell(c).lower() for c in header_cells]

    def find(*keys, exclude=()):
        for i, h in enumerate(heads):
            if any(k in h for k in keys) and not any(e in h for e in exclude):
                return i
        return None

    qty = find('quantit', 'qty')
    if qty is None:
        return None
    desc = find('descript', 'product')
    price = find('unit price', 'unitprice')
    if price is None:
        price = find('price', exclude=('total',))
    if desc is None and price is None:
        return None
    return {
        'qty': qty,
        'desc': desc,
        'price': price,
        'total': find('total'),
        'item': find('item'),
        'hs': find('hs'),
    }


_TOTAL_NAMES = {'total', 'total usd', 'subtotal', 'sub total', 'grand total',
                'net total', 'amount due', 'balance due'}


def extract_items_from_tables(tables: List[List[List[str]]],
                              warnings: List[str]) -> List[PIItem]:
    """Extract line items from tables using header-name column mapping."""
    items = []
    for table in tables:
        rows = [[_clean_cell(c) for c in row] for row in table]
        rows = [r for r in rows if any(r)]
        if len(rows) < 2:
            continue
        colmap = None
        header_idx = 0
        for i, row in enumerate(rows):
            colmap = _map_product_columns(row)
            if colmap:
                header_idx = i
                break
        if not colmap:
            continue
        if colmap['price'] is None and colmap['total'] is not None:
            # Unlabeled/clipped Unit Price header: assume the column just
            # left of Total (RAAS layout), unless already claimed.
            cand = colmap['total'] - 1
            taken = {v for v in (colmap['qty'], colmap['desc'],
                                 colmap['item'], colmap['hs']) if v is not None}
            if cand >= 0 and cand not in taken:
                colmap = dict(colmap, price=cand)
                warnings.append("Unit Price column inferred next to Total column")
        if colmap['price'] is None:
            warnings.append("Unit Price column not found; prices defaulted to 0")
        width = max(len(r) for r in rows)

        def cell(row, idx):
            return row[idx] if idx is not None and idx < len(row) else ''

        for lineno, row in enumerate(rows[header_idx + 1:], start=1):
            row = row + [''] * (width - len(row))
            raw_name = cell(row, colmap['desc']) if colmap['desc'] is not None else row[0]
            name = _clean_cell(raw_name)
            if not name:
                continue
            if name.lower() in _TOTAL_NAMES or name.lower().startswith('total '):
                continue
            qty = _parse_number(cell(row, colmap['qty'])) or 0.0
            price = _parse_number(cell(row, colmap['price'])) if colmap['price'] is not None else 0.0
            price = price or 0.0
            item_no = _clean_cell(cell(row, colmap['item'])) or None
            total = None
            if colmap['total'] is not None:
                total = _parse_last_number(cell(row, colmap['total']))
            if total is not None and total != 0:
                if qty == 0 and price == 0:
                    recovered = _recover_pair(row, colmap['total'], total)
                    if recovered:
                        qty, price = recovered
                        warnings.append(
                            f"Row {lineno} qty/price recovered from row values "
                            f"({qty:g} x {price:g} ~= {total:g}) — please verify")
                elif qty == 0:
                    qty = _derive_missing(total, price)
                    warnings.append(
                        f"Row {lineno} quantity derived from total/price — please verify")
                elif price == 0:
                    price = _derive_missing(total, qty)
                    warnings.append(
                        f"Row {lineno} unit price derived from total/quantity — please verify")
            if total is not None and abs(qty * price - total) > max(1.0, 0.005 * total):
                    warnings.append(
                        f"Row {lineno} total mismatch: {qty:g} x {price:g} != {total:g}")
            items.append(PIItem(product_name=name, quantity=qty,
                                unit_price=price, item_no=item_no))
    return items


def _pdf_to_text_and_tables(raw_bytes: bytes):
    """Return (full_text, tables) from PDF bytes.

    pdfplumber extracts ruled tables; PyMuPDF is the text fallback when
    pdfplumber finds no readable text.
    """
    tables: List[List[List[str]]] = []
    text = ''
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            page_texts = []
            for page in pdf.pages:
                page_texts.append(page.extract_text() or '')
                for tbl in page.extract_tables() or []:
                    tables.append([[c or '' for c in row] for row in tbl])
            text = '\n'.join(page_texts)
    except Exception:
        text = ''
    if not text.strip():
        import pymupdf
        with pymupdf.open(stream=raw_bytes, filetype='pdf') as doc:
            text = '\n'.join(page.get_text() for page in doc)
    return text, tables


def _docx_to_text_and_tables(doc: Document):
    """Return (full_text, tables) from a python-docx Document."""
    full_text = '\n'.join(p.text for p in doc.paragraphs)
    tables = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            rows.append([c.text for c in row.cells])
            for cell in row.cells:
                full_text += '\n' + cell.text
        tables.append(rows)
    return full_text, tables


def _build_extraction(filename: str, full_text: str,
                      tables: List[List[List[str]]],
                      legacy_doc: Optional[Document] = None,
                      extra_warnings: Optional[List[str]] = None) -> PIExtraction:
    """Assemble a PIExtraction from plain text + row-list tables."""
    warnings = list(extra_warnings or [])

    pi_number = extract_pi_number_from_filename(filename)
    if not pi_number:
        pi_number = extract_pi_number_from_text(full_text)
        if pi_number:
            warnings.append("PI number extracted from document body (not filename)")
    if not pi_number:
        warnings.append("Could not extract PI number from filename or document")

    pi_date = extract_pi_date(full_text)
    if not pi_date:
        warnings.append("Could not extract PI date")

    client_name = extract_client_name(full_text)
    if not client_name:
        warnings.append("Could not extract client name")

    items = extract_items_from_tables(tables, warnings)
    if not items and legacy_doc is not None:
        items = extract_product_table(legacy_doc)
    if not items:
        warnings.append("No product items extracted from tables")

    header = PIHeader(pi_number=pi_number or "UNKNOWN", pi_date=pi_date,
                      client_name=client_name)
    return PIExtraction(header=header, items=items, warnings=warnings)


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
    """Parse a PI file (.pdf, .docx, or legacy .doc) and return structured extraction."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    with open(filepath, "rb") as f:
        return parse_pi_stream(f, os.path.basename(filepath))


def parse_pi_stream(file_stream, filename: str) -> PIExtraction:
    """Parse a PI document (.pdf, .docx, or legacy .doc) from a file stream.

    For .pdf files: tables via pdfplumber, text fallback via PyMuPDF.
    For .docx files: parses directly with python-docx.
    For .doc files: converts to .docx via Word COM (local Windows + Word only).
    Nothing is written to disk permanently (temp files are cleaned up).
    """
    file_stream.seek(0)
    raw_bytes = file_stream.read()
    lname = filename.lower()

    if lname.endswith(".pdf"):
        full_text, tables = _pdf_to_text_and_tables(raw_bytes)
        if os.getenv("RAAS_PI_DEBUG") == "1":
            # TEMPORARY diagnostic: dump raw extraction to the server console
            # so real-PI layouts can be diagnosed without sharing the file.
            print(f"PI-DEBUG filename={filename} text={full_text[:20000]!r}",
                  flush=True)
            print(f"PI-DEBUG filename={filename} tables={tables!r}"[:20000],
                  flush=True)
        return _build_extraction(filename, full_text, tables)

    is_doc = lname.endswith(".doc") and not lname.endswith(".docx")
    extra_warnings: List[str] = []

    if is_doc:
        try:
            docx_bytes = _convert_doc_to_docx(raw_bytes, filename)
        except RuntimeError as e:
            extra_warnings.append(f".doc conversion failed: {e}")
            # Fallback: try to read as docx anyway (some .doc files are actually docx)
            try:
                doc = Document(io.BytesIO(raw_bytes))
            except Exception:
                raise ValueError(
                    "Could not read .doc file. Ensure Microsoft Word is installed "
                    "for .doc support, or save the file as .docx and retry."
                )
        else:
            doc = Document(io.BytesIO(docx_bytes))
            extra_warnings.append(".doc file converted to .docx via Word")
    else:
        doc = Document(io.BytesIO(raw_bytes))

    full_text, tables = _docx_to_text_and_tables(doc)
    return _build_extraction(filename, full_text, tables,
                             legacy_doc=doc, extra_warnings=extra_warnings)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_sales.py <pi_file.pdf|pi_file.docx|pi_file.doc>")
        sys.exit(1)
    extraction = parse_pi_document(sys.argv[1])
    print(extraction.model_dump_json(indent=2))