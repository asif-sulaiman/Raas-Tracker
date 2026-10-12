# Session Handoff

> Written at the end of a session so the next one starts informed.
> Newest entry first.

---

## 2026-10-12 — Docs currency pass (INV-REVISION + PBB gate)

### What was completed
- Audited **all** project `*.md` files (`README.md`, `AGENTS.md`, `docs/PRD.md`,
  `docs/SPEC.md`, `docs/plan.md`, `docs/AGENTS.md`, frontend `README.md`)
  against the current code and found the docs stale by two workstreams.
- Updated the four living docs to match the code:
  - `docs/plan.md` — added **Phase INV-REVISION** and **Phase PBB
    (Production-Before-Booking)** completed tables; removed a duplicated
    business-rule paragraph; refreshed the commit footer to `c3400c6`.
  - `docs/SPEC.md` — `_SCHEMA_VERSION` now documented as **5**; added missing
    DDL (`invoice_items`, `letters_of_credit`, `invoices.shipment_status`);
    corrected the invoice-item quantity rule (**may exceed** the PI line, no
    409 cap); `book` endpoint now documents the **409 production gate**; new
    `GET /api/invoices/<id>/readiness`; fixed "0 → paid at creation"; refreshed
    test counts and footer.
  - `docs/PRD.md` — Flow 6/7 now state the flexible-quantity rule (quantity may
    exceed the PI) and the **production-before-booking** rule; refreshed footer.
  - `README.md` — added `companies.py`/`lcs.py` to the layout, added the missing
    env vars (`TRUSTED_PROXY`, `MAX_CONTENT_LENGTH_MB`, `REDIS_URL`,
    `CORS_ALLOWED_ORIGINS`, `RAAS_RATE_LIMITS`, `RAAS_DATA_DIR`, `DB_POOL_MAX`),
    fixed the test count, and noted production-before-booking in the intro.
- Created this `docs/handoff.md` (it was referenced by `docs/AGENTS.md` and
  `AGENTS.md` but did not exist).

### Decisions made & why
- **Docs-only task** — no application code was touched.
- **Measured, not guessed, the test counts:** backend `pytest tests/ -q` →
  **740 passed / 8 skipped** (748 collected); frontend 40 test files /
  **305** tests. Both recorded in `docs/SPEC.md`.

### What remains + next steps
- Commit the docs update plus the working-tree **PBB** changes (`flask_app.py`,
  `raas_tracker/sales.py`, `tests/*`) — still uncommitted at handoff.
- Working tree also has `.opencode/opencode.json` and `opencode.json` modified
  (LSP/semgrep plugin config) — decide whether to commit or leave.
- Semgrep plugin integration was only audited, not completed.
- Backlog in `docs/plan.md` (M7 hardening, M8 future) is otherwise current.

### Known issues
- The full backend suite takes ~14 minutes serially (real PG, fixtures TRUNCATE)
  — never parallelise it.
- `docs/SPEC.md` §2 folder listing and §3 DDL are hand-maintained; they can drift
  from `raas_tracker/db.py:_create_tables` again if a task forgets to bump them.
