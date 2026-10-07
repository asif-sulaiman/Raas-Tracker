"""P1-11: one canonical identity for the upload filename.

``POST /api/upload`` used to compute THREE different names for one file:

1. ``secure_filename(file.filename)`` — used only for the extension gate, then
   discarded (``flask_app.py`` ``safe_display``).
2. the raw ``file.filename`` — passed to ``save_upload``, which re-sanitised it
   through ``_safe_upload_filename`` for the ``uploads.filename`` column, the
   ``UPLOAD_CREATE`` audit row, and the upload notifications.
3. the raw ``file.filename`` again — echoed in the JSON response.

Two concrete defects fell out of that split:

- A legitimate non-Latin name was **rejected 400**, because ``secure_filename``
  flattens non-ASCII to nothing and ``os.path.splitext("xlsx")[1]`` is empty.
- U+202E (RIGHT-TO-LEFT OVERRIDE) survived into the stored name and renders in
  the upload history — a name-spoofing vector in an audit-visible list.

These tests drive a real multipart POST, so they also close the fact that the
extension gate had no coverage at all. The on-disk name (``uuid4().hex + ext``)
is deliberately not one of the three identities and is unchanged.

The non-ASCII names below are named constants so the intent of each case is
readable; they are real characters, not escapes, and the file is UTF-8 LF.
"""
import io

import pytest

from raas_tracker.uploads import _safe_upload_filename

ALLOWED = (".pdf", ".xlsx", ".xls")

# A legitimate Arabic name (U+062A "te", U+0643 "kaf", ... ) — DEFECT 1.
ARABIC = "تكرار.xlsx"
# Hebrew.
HEBREW = "מלאי.xlsx"
# Chinese.
CJK = "库存-2026年10月.xlsx"
RLO = "‮"   # U+202E RIGHT-TO-LEFT OVERRIDE
LRM = "‎"   # U+200E LEFT-TO-RIGHT MARK
RLM = "‏"   # U+200F RIGHT-TO-LEFT MARK
NUL = "\x00"


def _xlsx_bytes(rows=2):
    """A real, parseable .xlsx so the route reaches save_upload."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Product", "Last Month", "This Month"])
    for i in range(rows):
        ws.append([f"Chemical {i}", 100, 90])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_bytes():
    """Minimal PDF text so parse_pdf returns rows rather than crashing."""
    return (b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            b"4 0 obj<</Length 90>>stream\n"
            b"BT /F1 12 Tf 72 700 Td (Chemical A 100 KG 90 KG) Tj ET\n"
            b"endstream endobj\n"
            b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"trailer<</Root 1 0 R>>\n")


def _post(client, filename, content=None):
    """POST a real multipart body to /api/upload under `filename`."""
    data = {
        "file": (io.BytesIO(content if content is not None else _xlsx_bytes()),
                 filename),
    }
    return client.post("/api/upload", data=data,
                       content_type="multipart/form-data")


def _stored(db, upload_id):
    return db.execute(
        "SELECT filename FROM uploads WHERE id = %s", (upload_id,)).fetchone()[0]


def _audit_new_value(db, upload_id):
    return db.execute(
        "SELECT new_value FROM audit_logs "
        "WHERE action = 'UPLOAD_CREATE' AND entity_id = %s "
        "ORDER BY id DESC LIMIT 1", (upload_id,)).fetchone()[0]


# ==================== the two P1-11 defects ====================

@pytest.mark.parametrize("name", [ARABIC, HEBREW, CJK],
                         ids=["arabic", "hebrew", "cjk"])
def test_legitimate_non_latin_filename_is_accepted(admin_client, db, name):
    """DEFECT 1: a real non-Latin name must be accepted, not 400'd.

    ``secure_filename`` flattens these to a bare "xlsx", whose splitext is
    empty, so the gate rejected them. Operators work in Arabic and Hebrew;
    their files are real files.
    """
    r = _post(admin_client, name)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["filename"] == name
    assert _stored(db, r.get_json()["upload_id"]) == name


def test_bidi_override_is_stripped_from_the_stored_name(admin_client, db):
    """DEFECT 2: U+202E must not survive into an audit-visible name.

    The RTL override reorders how the name renders, so a stored
    "invoice<U+202E>gpj.xlsx" reads as a different name in the upload history.
    """
    r = _post(admin_client, f"invoice{RLO}gpj.xlsx")
    assert r.status_code == 200, r.get_json()
    stored = _stored(db, r.get_json()["upload_id"])
    assert RLO not in stored, f"bidi override survived: {stored!r}"
    # ...and the response agrees with what was stored.
    assert r.get_json()["filename"] == stored


def test_other_bidi_marks_are_stripped_too(admin_client, db):
    """U+200E / U+200F are the LRM/RLM siblings of U+202E."""
    r = _post(admin_client, f"{LRM}report{RLM}.xlsx")
    assert r.status_code == 200, r.get_json()
    stored = _stored(db, r.get_json()["upload_id"])
    assert LRM not in stored and RLM not in stored, repr(stored)


# ==================== one identity, used everywhere ====================

def test_response_filename_equals_stored_filename(admin_client, db):
    """The three names are now one: response == uploads.filename."""
    r = _post(admin_client, "stock october.xlsx")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["filename"] == _stored(db, r.get_json()["upload_id"])


def test_audit_new_value_matches_the_stored_name(admin_client, db):
    """UPLOAD_CREATE must name the same file the operator will see later."""
    r = _post(admin_client, "stock october.xlsx")
    assert r.status_code == 200, r.get_json()
    assert _audit_new_value(db, r.get_json()["upload_id"]) == \
        r.get_json()["filename"]


def test_plain_name_survives_unchanged(admin_client, db):
    """The ordinary case: what was sent is what is stored and what is returned."""
    r = _post(admin_client, "stock october.xlsx")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["filename"] == "stock october.xlsx"
    assert _stored(db, r.get_json()["upload_id"]) == "stock october.xlsx"


# ==================== the hostile path ====================

@pytest.mark.parametrize("name", [
    "../../../etc/passwd.xlsx",
    "..\\..\\windows\\system32.xlsx",
    "/etc/hosts.xlsx",
    "sub/dir/nested.xlsx",
])
def test_traversal_name_is_stored_safely(admin_client, db, name):
    """A traversal attempt never reaches uploads.filename as a path.

    The on-disk name is a fresh uuid4 regardless, so there is no traversal
    sink; this pins the *stored display* name, which is what an operator sees
    and what the download route's Content-Disposition is later built from.
    """
    r = _post(admin_client, name)
    assert r.status_code == 200, r.get_json()
    stored = _stored(db, r.get_json()["upload_id"])
    assert ".." not in stored, f"traversal survived: {stored!r}"
    assert "/" not in stored and "\\" not in stored, \
        f"path separator survived: {stored!r}"
    # The one-identity property: the caller is told what was actually stored,
    # not the raw traversal string it sent.
    assert stored == r.get_json()["filename"]


def test_nul_byte_does_not_abort_the_insert(admin_client, db):
    """A NUL in the filename is a psycopg DataError, i.e. a 500, not a 400."""
    r = _post(admin_client, f"stock{NUL}october.xlsx")
    assert r.status_code == 200, r.get_json()
    stored = _stored(db, r.get_json()["upload_id"])
    assert NUL not in stored
    assert all(ord(ch) >= 32 for ch in stored), repr(stored)


def test_percent_encoded_traversal_is_not_decoded_into_a_path(admin_client, db):
    """`..%2f..%2fetc.xlsx` stays one literal component.

    The filename is already URL-decoded once by werkzeug before the route sees
    it; decoding a *second* time here would turn a stored name into a path, so
    the sanitiser must not. There is no traversal sink either way — the on-disk
    name is a uuid4 — so this pins "stays literal", not "rejected".
    """
    r = _post(admin_client, "..%2f..%2fetc.xlsx")
    assert r.status_code == 200, r.get_json()
    stored = _stored(db, r.get_json()["upload_id"])
    assert "/" not in stored and "\\" not in stored, repr(stored)


def test_dots_only_name_is_rejected_by_the_gate(admin_client):
    """`....xlsx` loses its dots and has no extension left, so the gate 400s.

    Honest consequence of making one name canonical: the gate now reads the
    *sanitised* name, so a name that is nothing but dots and an extension is
    rejected rather than stored. That is the same outcome `secure_filename`
    produced before (it also returned a bare "xlsx").
    """
    r = _post(admin_client, "....xlsx")
    assert r.status_code == 400, r.get_json()
    assert "Only PDF and Excel" in r.get_json()["error"]


def test_leading_dot_dotfile_is_not_stored_as_a_dotfile(admin_client, db):
    """``.hidden.xlsx`` must not keep the leading dot (a hidden file)."""
    r = _post(admin_client, ".hidden.xlsx")
    assert r.status_code == 200, r.get_json()
    stored = _stored(db, r.get_json()["upload_id"])
    assert not stored.startswith("."), f"stored as a dotfile: {stored!r}"


# ==================== the extension gate still works ====================

@pytest.mark.parametrize("name", ["evil.exe", "evil.sh", "evil.xlsm", "evil"])
def test_disallowed_extension_is_400(admin_client, name):
    r = _post(admin_client, name)
    assert r.status_code == 400, r.get_json()
    assert "Only PDF and Excel" in r.get_json()["error"]


@pytest.mark.parametrize("ext", ALLOWED)
def test_every_allowed_extension_passes_the_gate(admin_client, ext):
    """The gate accepts all three allowed extensions; parse is a separate 400.

    Scope note: this asserts the *filename gate* only. Whether the body then
    parses is a parser concern and not what P1-11 is about — a `.xls` body fails
    at `openpyxl.load_workbook` (which needs `xlrd`) and a scanned PDF needs
    `tesseract`, both independent of the filename handling. The stored-name
    assertions elsewhere therefore use `.xlsx`, which parses for real.
    """
    content = _pdf_bytes() if ext == ".pdf" else _xlsx_bytes()
    r = _post(admin_client, f"allowed{ext}", content)
    assert "Only PDF and Excel" not in (r.get_json() or {}).get("error", ""), \
        r.get_json()


# ==================== the helper itself ====================

@pytest.mark.parametrize("raw,expected", [
    ("stock.xlsx", "stock.xlsx"),
    (ARABIC, ARABIC),                # unicode preserved (see docstring)
    ("a/b/stock.xlsx", "stock.xlsx"),        # posix traversal
    ("a\\b\\stock.xlsx", "stock.xlsx"),      # windows traversal
    ("../../stock.xlsx", "stock.xlsx"),
    (f"st{NUL}ock.xlsx", "stock.xlsx"),      # NUL
    ("  stock.xlsx  ", "stock.xlsx"),        # surrounding whitespace
    (".hidden.xlsx", "hidden.xlsx"),         # dotfile
    ("..", "upload"),                        # sanitises to nothing
    ("", "upload"),
    (None, "upload"),
])
def test_helper_is_the_canonical_sanitiser(raw, expected):
    assert _safe_upload_filename(raw) == expected


@pytest.mark.parametrize("raw", [
    "CON.xlsx", "PRN.pdf", "nul.xls", "com1.xlsx", "LPT3.pdf",
])
def test_windows_reserved_names_are_neutralised(raw):
    """A reserved device name would be a live device on Windows, not text."""
    cleaned = _safe_upload_filename(raw)
    assert cleaned.split(".")[0].upper() not in {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
        "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
        "LPT6", "LPT7", "LPT8", "LPT9",
    }, f"reserved name survived: {cleaned!r}"


@pytest.mark.parametrize("raw", [
    f"invoice{RLO}gpj.xlsx",
    f"{LRM}report{RLM}.xlsx",
    f"a{RLO}b.xlsx",
])
def test_helper_strips_bidi_controls(raw):
    cleaned = _safe_upload_filename(raw)
    assert not set(cleaned) & {RLO, LRM, RLM}, repr(cleaned)
