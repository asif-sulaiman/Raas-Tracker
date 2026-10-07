"""P1-18: SQL `to_char()` uses different pattern codes than `strftime()`.

The pattern below looks like a strftime format and is not one:

    to_char(ts, 'YYYY-MM-DD HH' + ':MM:SS')     # deliberately not a literal

In `to_char`:

- `MM` = MONTH      (minutes are `MI`)
- `HH` = 12-hour    (24-hour is `HH24`)

So every timestamp this schema produced carried the *month* in the minute slot
and lost both the real minutes and any AM/PM distinction:

    real 2026-10-07 07:06:58 -> stored 2026-10-07 07:10:58
    real 2026-03-15 14:22:41 -> stored 2026-03-15 02:03:41

Because the columns are TEXT and every window is a string comparison, that
turned each time-window control into a comparison of the *seconds* field:

- `is_login_blocked` (10-minute window) became `second_row >= second_now`, so a
  fresh attempt could be judged outside the window the moment the clock ticked
  past its recorded second -> the ~6%-of-runs 401x6 lockout failure in
  test_proxy_trust.py
- `check_api_key_rate_limit` (300/min) silently shed hits every minute
- `sessions`/`api_keys` expiry was compared against a floor whose minute slot
  held the month, so a 1-hour TTL was really ~10 hours

The fix is `'YYYY-MM-DD HH24:MI:SS'` everywhere in SQL. Python `strftime()`
already used `%Y-%m-%d %H:%M:%S` and was always correct -- these tests pin the
two sides together so the formats cannot drift apart again.

Where a test needs to know how the *production* code formats a timestamp it
asks the live database for the format rather than assuming it, so the test
tracks the DEFAULT instead of restating it.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chem_stock import get_session_user, is_login_blocked

REPO_ROOT = Path(__file__).resolve().parent.parent

# The one canonical SQL format, and the Python spelling that must match it.
SQL_FMT = "YYYY-MM-DD HH24:MI:SS"
PY_FMT = "%Y-%m-%d %H:%M:%S"

# The defect, spelled out so it can be searched for. Assembled from two parts
# so this file does not match its own scan.
BAD_SQL_FMT = "YYYY-MM-DD HH" + ":MM:SS"
BAD_PY_FMT = "%Y-%m-%d %H" + ":%M:%S"

# A `to_char(...)` call whose format argument is the malformed pattern. The
# needle must be the literal directly inside a to_char( call, which is what
# keeps the deliberately-preserved occurrences out of scope:
#   * Python strftime() calls are a different function entirely;
#   * auth.py's two docstrings and its ValueError message describe the
#     *client-supplied* 24-hour format and contain no to_char( call.
_TO_CHAR_BAD = re.compile(r"to_char\([^;]*?'" + re.escape(BAD_SQL_FMT) + r"'", re.I)

# Every column whose DEFAULT the schema owns a timestamp format for.
_TIMESTAMP_DEFAULT_COLUMNS = [
    ("companies", "created_at"),
    ("uploads", "upload_date"),
    ("approval_workflow", "reviewed_at"),
    ("audit_logs", "timestamp"),
    ("reconciliation_periods", "created_at"),
    ("sales", "created_at"),
    ("sales", "updated_at"),
    ("sales_stage_history", "changed_at"),
    ("sale_payments", "created_at"),
    ("shipments", "created_at"),
    ("production_runs", "created_at"),
    ("users", "created_at"),
    ("sessions", "created_at"),
    ("login_attempts", "attempted_at"),
    ("api_keys", "created_at"),
    ("api_key_rate_limits", "hit_at"),
    ("notifications", "created_at"),
    ("notification_reads", "read_at"),
    ("invoices", "created_at"),
    ("letters_of_credit", "created_at"),
    ("letters_of_credit", "updated_at"),
]


def _repo_python_files():
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if any(part in ("node_modules", ".venv", "venv", "site-packages", "build", ".git")
               for part in path.parts):
            continue
        yield path


def _live_default(db, table, column):
    """The literal DEFAULT expression PostgreSQL holds for a column."""
    row = db.execute(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
        (table, column)).fetchone()
    return row[0] if row and row[0] else None


def _live_default_format(db, table, column):
    """The `to_char` format pattern the live DEFAULT renders with.

    Read out of the live schema rather than hardcoded, so every behavioural
    test below exercises the format production actually uses. If the DEFAULT
    stops being a to_char call the tests fail loudly instead of silently
    testing a format nobody ships.
    """
    default = _live_default(db, table, column)
    assert default, f"{table}.{column} has no DEFAULT in the live schema"
    found = re.search(r"'([^']*)'", default)
    assert found, (
        f"{table}.{column} DEFAULT is {default!r}, which carries no format "
        "literal these tests can exercise")
    return found.group(1)


def _insert_attempts(db, prefix, ip, count=5):
    for i in range(count):
        db.execute("INSERT INTO login_attempts (username, ip_address, success) "
                   "VALUES (%s, %s, 0)", (f"{prefix}{i}", ip))
    db.commit()


def _wait_past_this_second(db, second_before, timeout=2.0):
    """Block until the database clock's seconds field differs from `second_before`."""
    deadline = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < deadline:
        now_second = int(db.execute(
            "SELECT to_char(clock_timestamp(), 'HH24:MI:SS')").fetchone()[0]
            .split(":")[2])
        if now_second != second_before:
            return now_second
    pytest.fail("the database clock did not advance past the recorded second")


# ==================== the reported symptom ====================

def test_fresh_attempts_are_counted_by_the_lockout(db):
    """The exact flake: 5 attempts written now must trip the 10-minute window.

    Written through the column DEFAULT, i.e. the production write path, and
    evaluated only after the clock's seconds field has moved past the one they
    were written in. Under the defect that made the seconds field decide the
    window and dropped every row recorded in an earlier second, which is the
    401x6 failure in test_proxy_trust.py once per ~15 runs.
    """
    second_before = int(db.execute(
        "SELECT to_char(clock_timestamp(), 'HH24:MI:SS')").fetchone()[0].split(":")[2])
    _insert_attempts(db, "ghost", "127.0.0.1")
    _wait_past_this_second(db, second_before)

    assert is_login_blocked(db, "ghost0", "127.0.0.1") is True, (
        "5 attempts written a second ago fell outside the 10-minute lockout "
        "window: the window is being decided by the seconds field")


def test_window_really_is_ten_minutes_not_the_seconds_field(db):
    """An attempt 11 minutes old must be OUTSIDE the 10-minute window.

    The seconds field is pinned to 59 so that under the defect -- where the
    window degenerated to `second_row >= second_floor` and 59 >= anything -- the
    row is judged INSIDE and this fails. With the real format the value is a
    true 11-minute-old timestamp (11m00s to 11m59s old, still outside 10
    minutes) and the row is correctly excluded.
    """
    _insert_attempts(db, "ancient", "10.9.9.9")
    db.execute("UPDATE login_attempts SET attempted_at = "
               "to_char(NOW() - INTERVAL '11 minutes', %s) || '59'", (SQL_FMT,))
    db.commit()

    assert is_login_blocked(db, "ancient0", "10.9.9.9") is False, (
        "attempts 11 minutes old are still counted as inside a 10-minute window")


def test_fresh_attempt_is_inside_the_window(db):
    """The other side of the same boundary: a row written now IS inside."""
    _insert_attempts(db, "recent", "10.8.8.8", count=1)
    assert is_login_blocked(db, "recent0", "10.8.8.8") is False

    stored = db.execute("SELECT attempted_at FROM login_attempts "
                        "WHERE username = 'recent0'").fetchone()[0]
    # Python cannot strptime to_char's codes, so parse with the Python spelling
    # and require an exact shape: 19 chars, real minutes, no 12-hour rollover.
    expected = datetime.now(timezone.utc).strftime(PY_FMT)
    assert len(stored) == 19, f"stored timestamp has the wrong width: {stored!r}"
    minute = int(stored[14:16])
    assert 0 <= minute <= 59, (
        f"stored {stored!r} has no real minute in the minute field")
    parsed = datetime.strptime(stored, PY_FMT).replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 120, (
        f"the column DEFAULT stored {stored!r}, which is {delta:.0f}s from the "
        f"real clock (~{expected}): it is not writing real minutes")


def test_stored_timestamp_reads_back_as_the_real_clock(db):
    """A value written now must parse back to now, not to some other minute."""
    _insert_attempts(db, "rt", "10.7.7.7", count=1)
    stored = db.execute("SELECT attempted_at FROM login_attempts "
                        "WHERE username = 'rt0'").fetchone()[0]
    parsed = datetime.strptime(stored, PY_FMT).replace(tzinfo=timezone.utc)
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 120, (
        f"stored {stored!r} is {delta:.0f}s away from the real clock")


# ==================== hour field and TEXT ordering ====================

def test_pm_hours_are_not_rendered_as_am(db):
    """`HH` is a 12-hour clock, so 14:00 rendered as 02:00.

    Rendered with the format the live DEFAULT uses, from fixed instants, so
    this cannot pass by accident on the hour the suite happens to run in.
    """
    fmt = _live_default_format(db, "login_attempts", "attempted_at")
    pm = db.execute("SELECT to_char(%s::timestamp, %s)",
                    ("2026-10-07 14:00:00", fmt)).fetchone()[0]
    assert pm == "2026-10-07 14:00:00", f"14:00 rendered as {pm!r} (format {fmt!r})"


def test_timestamps_four_hours_apart_sort_chronologically(db):
    """TEXT ordering must be chronological across the afternoon.

    Under the 12-hour `HH` the 13:00 row rendered as `01:00:00` and sorted
    *before* the 09:00 row, inverting every afternoon-ordered list. Values are
    rendered with the live DEFAULT's format so this tracks production.
    """
    fmt = _live_default_format(db, "login_attempts", "attempted_at")
    rendered = {}
    for label, instant in (("early", "2026-10-07 09:00:00"),
                           ("late", "2026-10-07 13:00:00")):
        rendered[label] = db.execute("SELECT to_char(%s::timestamp, %s)",
                                     (instant, fmt)).fetchone()[0]
    db.execute("INSERT INTO login_attempts (username, ip_address, success, attempted_at) "
               "VALUES ('ordering-late', '10.6.6.6', 0, %s)", (rendered["late"],))
    db.execute("INSERT INTO login_attempts (username, ip_address, success, attempted_at) "
               "VALUES ('ordering-early', '10.6.6.6', 0, %s)", (rendered["early"],))
    db.commit()

    order = [r[0] for r in db.execute(
        "SELECT username FROM login_attempts WHERE username LIKE 'ordering-%' "
        "ORDER BY attempted_at ASC").fetchall()]
    assert order == ["ordering-early", "ordering-late"], (
        f"timestamps 4 hours apart sort as {order} (rendered "
        f"{rendered['early']!r} / {rendered['late']!r}, format {fmt!r})")


# ==================== the DEFAULTs in the live schema ====================

@pytest.mark.parametrize("table,column", _TIMESTAMP_DEFAULT_COLUMNS)
def test_live_column_default_uses_the_correct_format(db, table, column):
    """The fix must reach the LIVE database, not just the CREATE TABLE source.

    This is what catches a `CREATE TABLE IF NOT EXISTS` that no-ops against an
    already-migrated table: the text would be corrected while the live DEFAULT
    kept the old format, so the fix would only work on a fresh database.
    """
    default = _live_default(db, table, column)
    assert default, f"{table}.{column} has no DEFAULT in the live schema"
    assert BAD_SQL_FMT not in default, (
        f"live DEFAULT for {table}.{column} still uses the malformed format: {default}")
    assert SQL_FMT in default, (
        f"live DEFAULT for {table}.{column} is {default!r}, expected {SQL_FMT}")


def test_schema_version_was_bumped_for_this_change(db):
    """A DEFAULT change is DDL: the recorded version must differ from a pre-fix DB.

    Guards the fast path in `_schema_current()`, which skips the whole
    migration when the stored version matches the code's.
    """
    from raas_tracker.db import _SCHEMA_VERSION, _SCHEMA_VERSION_KEY

    stored = db.execute("SELECT value FROM app_settings WHERE key = %s",
                        (_SCHEMA_VERSION_KEY,)).fetchone()
    assert stored, "no schema version recorded in the live database"
    assert int(stored[0]) == _SCHEMA_VERSION, (
        f"stored schema version {stored[0]} != code's {_SCHEMA_VERSION}")
    assert _SCHEMA_VERSION > 3, (
        "the P1-18 DEFAULT change shipped without bumping _SCHEMA_VERSION, so an "
        "already-migrated database would keep the old DEFAULTs")


# ==================== no malformed literal anywhere ====================

def test_no_malformed_to_char_literal_in_sql():
    """Zero SQL `to_char()` calls may still pass a strftime pattern.

    Scope: the literal must appear inside a `to_char(` call. Python
    `strftime()` calls are a different function, and the auth.py docstrings /
    ValueError message describe the client-supplied 24-hour format and contain
    no `to_char(` call, so none of the deliberately-unchanged occurrences can
    match here.
    """
    offenders = []
    for path in _repo_python_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _TO_CHAR_BAD.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "malformed to_char() format literals remain:\n" + "\n".join(offenders)


def test_python_writers_keep_the_correct_strftime_format():
    """Guard the other half of the contract.

    `create_session`, `_reset_expiry_text` and the sales/uploads `_now_str`
    helpers use `%Y-%m-%d %H:%M:%S`, which is already correct and is what
    `sessions.expires_at` and `api_keys.expires_at` hold. Rewriting those to
    the SQL spelling would silently start writing a wrong format, so pin them.
    """
    from raas_tracker import auth, sales, uploads

    def _src(module):
        return Path(module.__file__).read_text(encoding="utf-8")

    auth_src = _src(auth)
    for marker, label in (
        ("_reset_expiry_text", "reset-token expiry"),
        ("create_session", "session expiry"),
    ):
        body = auth_src.split("def " + marker, 1)[1].split("\ndef ", 1)[0]
        assert f'strftime("{PY_FMT}")' in body, (
            f"{label} must keep writing {PY_FMT}, which matches the corrected "
            "SQL floor")
    for module in (sales, uploads):
        assert f'strftime("{PY_FMT}")' in _src(module), (
            f"{module.__name__}._now_str must keep writing {PY_FMT}")

    # Nothing anywhere may rewrite the Python format as the SQL spelling.
    for path in _repo_python_files():
        text = path.read_text(encoding="utf-8")
        assert f'strftime("{SQL_FMT}")' not in text, (
            f"{path.relative_to(REPO_ROOT)}: strftime() was given the SQL "
            f"format {SQL_FMT!r}; Python needs {PY_FMT!r}")


# ==================== Python-written values vs the SQL floor ====================

def _mint_session(db, user_id, expires_at):
    raw = "p118-" + hashlib.sha256(expires_at.encode()).hexdigest()[:24]
    db.execute("INSERT INTO sessions (token_hash, user_id, expires_at) "
               "VALUES (%s, %s, %s)",
               (hashlib.sha256(raw.encode()).hexdigest(), user_id, expires_at))
    db.commit()
    return raw


def test_session_expiry_is_judged_against_a_real_clock(db):
    """`expires_at` is written by Python; the floor it is compared to was not.

    A 1-hour-TTL session with 1 minute of life left must be accepted and one
    that expired 1 minute ago must be rejected. The margins straddle the
    minute boundary the malformed floor compared against, so this cannot be
    decided by luck.
    """
    uid = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    now = datetime.now(timezone.utc)

    fresh = _mint_session(db, uid, (now + timedelta(minutes=1)).strftime(PY_FMT))
    stale = _mint_session(db, uid, (now - timedelta(minutes=1)).strftime(PY_FMT))

    assert get_session_user(db, fresh) is not None, (
        "a session with 1 minute of life left was rejected: the SQL expiry "
        "floor is not a real clock reading")
    assert get_session_user(db, stale) is None, (
        "a session that expired 1 minute ago was accepted")


def test_api_key_expiry_is_judged_against_a_real_clock(db):
    """Same boundary for API keys: `expires_at` is client-supplied, correct format."""
    from chem_stock import create_api_key

    uid = db.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    now = datetime.now(timezone.utc)
    valid = create_api_key(db, "P118Valid", created_by=uid,
                           expires_at=(now + timedelta(minutes=1)).strftime(PY_FMT))
    expired = create_api_key(db, "P118Expired", created_by=uid,
                             expires_at=(now - timedelta(minutes=1)).strftime(PY_FMT))

    from raas_tracker.auth import validate_api_key
    assert validate_api_key(db, valid, ip="127.0.0.1") is not None, (
        "an API key with 1 minute of life left was rejected")
    assert validate_api_key(db, expired, ip="127.0.0.1") is None, (
        "an expired API key was accepted")