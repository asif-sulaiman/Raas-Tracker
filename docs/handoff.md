# Session Handoff

> Written at the end of a session so the next one starts informed.
> Newest entry first.

---

## 2026-10-25 — Uncapped invoice quantities

### What was completed
- **Tolerance removed end to end.** `OVER_INVOICE_TOLERANCE`,
  `check_invoice_tolerance`, `ToleranceExceeded` and the `/invoice-lines`
  `ceiling` field are gone from all three writers and the endpoint. Quantity
  may equal, exceed, or fall below the PI line; the only rule is finite and > 0.
- **Composer no longer caps a write.** `EPSILON`, `overflow()`, `overRows`, the
  over-limit copy and the create-mode 409 branch are gone. `Remaining` stays,
  live and informational under PI qty. Save needs a number + one staged row in
  create mode, and one staged row in add-line mode.
- **Locks kept.** Batch still locks every PI line up front; create and add-line
  locks stay as write serialisation. `tests/test_invoice_writer_locks.py`
  replaces the deleted `tests/test_invoice_tolerance.py`.
- **Tests/docs:** over-PI writes now assert 201 and `"ceiling" not in payload`;
  SPEC/PRD/plan updated; old ceiling bullets marked superseded.

### Gates
- Full backend: **1237 passed, 8 skipped** (`pytest tests/ -q`, 20:49).
- Full frontend: **331 tests across 40 files**; `oxlint` 0 errors / 7
  pre-existing warnings; `vite build` ok.
- Ruff: no new findings on the changed files (the 19 reported are pre-existing
  at HEAD and fixed by the main tree's uncommitted work).
- Pyright: no new errors from this change (HEAD has 6 pre-existing errors in
  `audit.py` / `uploads.py`, fixed by the main tree's uncommitted work).

### Next steps
- Merge `feat/invoice-uncapped-quantities` (worktree); push/deploy left to the
  owner.
- Ship backend and rebuilt `react_frontend/` together: an old cached SPA
  expects `ceiling` and would refuse every quantity.

---

## 2026-10-22 — Invoice composer column model

### What was completed
- **`InvoiceEditor` row** (`10f9759`): `Product | PI qty | Invoice qty | Unit
  price | Total price`. One open quantity input (empty on load, no placeholder),
  live `Remaining` under the PI qty, read-only computed `Total price`.
- **`Remaining`** is `max(server remaining − typed, 0)` and updates on every
  keystroke; the server's figure already nets out every invoice, so a later
  invoice draws from the same number. It is **PI-based**: the +5% ceiling is a
  hidden cap, not headroom. *(Superseded 2026-10-25: the ceiling was removed;
  `Remaining` is informational.)*
- **`Invoiced` is no longer rendered in the row** — no column, no
  already-invoiced figure (operator request). The create-success toast still
  says `Invoiced $X`; that is the new invoice's amount, not this column.
  `Remaining` still accounts for prior invoices, so nothing is double-counted;
  the `has_line_less_invoices` banner still warns about legacy header-only
  money.
- **`Total price`** is `lineTotal(typed, unit_price)` in integer cents,
  `$0.00` before input; the old "Line total $" helper is gone.
- **Over-limit copy** is `Over the invoicing limit — this line can add at most
  X` — no hardcoded percentage, no "still add" that read as headroom beside a
  `Remaining 0.00` row. *(Superseded 2026-10-25: the message is gone.)*

### Decisions made & why
- **Remaining is PI-based and informational.** The removed +5% ceiling was an
  exception the operator discovered by typing; since 2026-10-25 there is no
  cap.
- **The quantity box is deliberately empty on load.** Auto-seeding `remaining`
  was the original complaint; the operator types the number.
- **`Invoiced` is hidden, `Remaining` is kept** — the request to hide it
  targeted the figure that looked copied from the PI, while the live leftover is
  what the next invoice draws from.
- **Backend untouched then; superseded 2026-10-25** — the tolerance and the
  payload's `ceiling` were removed in the uncapped-quantity change.

### Gates
- Oracle gate 1: **approve with non-blocking**; remediation folded into the same
  commit (subline contrast + `aria-live`, `remainingAfter()`/`readOnlyCls`
  extraction, tighter tests).
- Full frontend suite **338/338** across 40 files; `oxlint` 0 errors / 7
  pre-existing warnings; `vite build` ok. Backend untouched.

### Next steps
- Optional: add `pool: 'vmThreads'` to the vitest config (jsdom is created 40
  times, ~76% of suite time); delete the untracked
  `src/components/sales/fetchSaleInvoiceLines.js` (nothing imports it).

---

## 2026-10-21 — Invoice overhaul Phases 6–7 (all call sites on `InvoiceEditor`)

### What was completed
- **Phase 6, call sites** (`d016093`, `96c2520`, `925a814`): `InvoicePanel` and
  `SaleDetailModal` render `InvoiceEditor`; the two create shortcuts state
  `invoice_date` + `lines`; `InvoiceBuilderModal` deleted, dead `lcId` props
  dropped. Deleted with their last caller: `InvoiceCreateForm`,
  `InvoiceLineEditor` (+ test), `InvoiceLineComposer`, `useSaleInvoiceLines`.
- **Phase 6, Oracle review fixes** (`a1d272a`): partial success is reported in
  both places (the barrier no longer calls `onSuccess` on a partially failed
  batch; add-line calls `onDone` whenever ≥1 row landed), add-line 404
  `pi_line_gone` resyncs and drops the vanished row, both shortcuts refuse a PI
  with a header-only invoice, and the write-only `barrierLcId` state is gone.
- **Phase 7 docs** : `plan.md` Phase 6 section + phase table, PRD Flow 6 item 3
  (the two shortcuts invoice the whole remainder and refuse when nothing is
  left), SPEC `POST /api/sales/<id>/invoices` (`invoice_date` required), SPEC
  frontend coverage line (40 files / 335 tests, 7 warnings).
- **Gates:** frontend full suite **335/335**, `oxlint` 0 errors, `vite build` ok.
  Backend untouched since `f6c7fe4` (no `.py` in the diff), so the 1261-passed
  suite still stands.

### Decisions made & why
- **`InvoiceLineComposer` was deleted, not rewritten.** Its one-line
  select+quantity model does not exist in the table the editor renders, and its
  only caller was `InvoiceCreateForm`.
- **`api_list_invoices` does not get `@limited()`.** It is a read, it already
  runs under the 300/min default, and the editor reloads after every 409 — a
  15/min stamp would break that recovery path.
- **Header-only invoices are refused by both shortcuts**, because `remaining`
  cannot see their money and auto-invoicing would bill the goods twice. Invoicing
  them is still possible from the sale, by adding lines.
- **A partially failed barrier batch stays open.** `onSuccess` closes the modal
  and retries the LC move; the gate only needs one invoice across the LC, so
  calling it early would advance an LC whose PI still has none.

### What remains + next steps
1. **Production schema check: closed.** Deploying `35a9426` applied the v6 DDL on
   first boot — re-read `raas_schema_version` = **`6`** and `invoices.invoice_date`
   exists (row counts unchanged: 1 sale, 1 sale_item, 0 invoices). `_create_tables()`
   is back on its fast path.
2. **Pushed:** `main` is level with `origin/main` at `35a9426`. CI: frontend green,
   Supabase preview green, backend running at the time of the push.
3. **`fetchSaleInvoiceLines.js`** is still untracked (user's file; nothing
   imports it, and its docstring names the two deleted components). Delete when
   convenient.
4. **Optional:** `npm test` worker starvation — `pool: 'vmThreads'` would cut the
   jsdom cost that makes the slow files flake under load.

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
  endpoint/writer ceiling parity *(removed 2026-10-25 with the ceiling)*, audit
  shape, non-finite guard).
- **Phase 5** (`f6c7fe4`, `23b8743`): `InvoiceEditor` (create | add-line) +
  `useInvoiceBudget` + 12 tests; `code` on the add-line 404
  (`invoice_gone` | `pi_line_gone`); `ApiError.code`.

### Decisions made & why
- **The ceiling is a hard stop, not advertised headroom.** *(Superseded
  2026-10-25: the ceiling was removed.)* Over-limit was
  `typed > 0 && invoiced + typed > ceiling + 1e-9`; the maximum came from the
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
