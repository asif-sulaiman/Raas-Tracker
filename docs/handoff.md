# Session Handoff

> Written at the end of a session so the next one starts informed.
> Newest entry first.

---

## 2026-10-21 — Invoice overhaul Phases 0–5 (editor built, call sites not swapped)

### What was completed
- **Phase 0** (`a92b01a`): baseline on `8a35c2f`; red WIP UI work parked in
  `stash@{0}` ("wip: unified invoice editor (restore at Phase 6)"); production
  schema facts recorded.
- **Phase 1** (`712f66a`, `9e89c60`, `0cd51bf`): the batch-vs-add-line race was
  **already closed** (`sales.py:1933-1935` locks `sale_items FOR UPDATE`);
  added `TestWriterLockDiscipline` + transaction-local `lock_timeout`.
- **Phase 2** (`2cc7b0f`, `9a7881b`, `f687c08`, `9a2be16`): deleted
  `backfill_invoice_lines` and `tests/test_lc_schema.py:353-457`; guard test
  that no migration invents invoice lines; corrected PRD/SPEC/plan claims.
- **Phase 3** (`866e870`, `c2b45a5`): `list_sale_invoice_lines` +
  `GET /api/sales/<id>/invoice-lines`; 8 contract tests.
- **Phase 4** (`d79cab6`, `e27dfdb`): 5 characterisation pins (forged price,
  endpoint/writer ceiling parity, audit shape, non-finite guard).
- **Phase 5** (`f6c7fe4`, `23b8743`): `InvoiceEditor` (create | add-line) +
  `useInvoiceBudget` + 12 tests; `code` on the add-line 404
  (`invoice_gone` | `pi_line_gone`); `ApiError.code`.

### Decisions made & why
- **The ceiling is a hard stop, not advertised headroom.** Over-limit is
  `typed > 0 && invoiced + typed > ceiling + 1e-9`; the maximum comes from the
  payload's absolute `ceiling`, never `pi * 1.05`.
- **Refusals are classified structurally** — status code plus the reloaded
  budget, plus `code` for the ambiguous 404. No message parsing anywhere.
- **No `_SCHEMA_VERSION` bump in Phase 2.** The rule (`db.py:122-124`) covers new
  DDL, a column-default change, or a one-shot data migration; deleting a
  one-shot migration is none of those.
- **The editor is unrendered on purpose.** Nothing imports it yet, so the
  composer rewrite waits for Phase 6 rather than leaving two dead composers.

### What remains + next steps
1. **Phase 6 (Oracle gate 4).** Swap the four call sites (`InvoicePanel.jsx:502`,
   `SaleDetailModal.jsx:39-99`, `InvoicesRequiredModal.jsx:61`,
   `ProductionRunModal.jsx:222`), rewrite `InvoiceLineComposer.jsx`, then delete
   `InvoiceCreateForm`, `InvoiceLineEditor` (+test), `InvoiceBuilderModal`,
   `fetchSaleInvoiceLines`, `useSaleInvoiceLines`. Also: rewrite
   `InvoicesRequiredModal.test.jsx:93` (asserts the broken payload) and the
   `SaleDetailModal.test.jsx` F10 case, fix the `aria-label="Invoice number"`
   collision, drop the dead `lcId` props, add `@limited()` to
   `api_list_invoices` (`flask_app.py:2497`). Zero-post runs must not toast
   success.
2. **Phase 7 — docs:** PRD Flow 6, SPEC §4/§6 (`SPEC:449` wording), the stale
   `useSaleInvoiceLines` docstring at `InvoicePanel.jsx:84`.
3. **Production deploy check (open since Phase 0):** prod is at
   `raas_schema_version` 5 with no `invoices.invoice_date`. After deploying,
   re-read the version (expect 6) and confirm the column exists — observed, not
   assumed.

### Known issues
- **`npm test` flakes under full-suite load** (1–4 tests in the slowest files,
  different set each run, all pass alone and as a group). Worker starvation, not
  behaviour — see `docs/plan.md`.
- **Backend suite is ~25 min serially** (real PG, fixtures TRUNCATE) — never
  parallelise, and never pipe it through `| Select-Object`.
- **`stash@{0}` is reference only** — keep until Phase 6 lands, then drop.
- **Line-less legacy invoices contribute 0 quantity** by design; a sale whose
  only invoice is line-less reports `gross_sales = 0`, `due = 0`, "Paid".

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
