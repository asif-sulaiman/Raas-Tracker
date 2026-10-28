# Session Handoff

> Written at the end of a session so the next one starts informed.
> Newest entry first.

---

## 2026-10-28 — INVOICE-MATURITY Phase 2 (per-invoice Payment Due + board visibility)

Worktree `O:/Projects/Raas-Tracker-worktrees/invoice-shipment-gate`, branch
`feat/invoice-start-at-shipment`. Phase 2 of the approved 7-phase plan
(`~/.qoder/plans/warm-cave-stag.md`): the **actual ship date** — not the booking —
is what opens Payment Due, and invoices are visible on the board.

### What shipped

- **Sale gate = ≥1 shipped invoice.** `shipped_invoice_readiness(conn, sale_ids)`
  in `sales.py` returns `shipped_ok` (any non-void invoice `shipped`/`paid`),
  `all_shipped_ok` (every non-void invoice `shipped`/`paid`) and a detail count.
  Moving a sale into `payment_due` (explicitly or via `move_sale_to_stage`)
  raises `SaleNotReadyPastShipment` → **409** unless `shipped_ok`. A
  booked-but-unshipped invoice no longer qualifies.
- **`ship_invoice` auto-advances.** After the shipment INSERT and the two
  audits (`SHIPMENT_RECORD`, `INVOICE_SHIP`), a sale still in
  `shipment_ongoing` is moved to `payment_due` through `move_sale_to_stage`,
  which writes the stage history and the `SALE_MOVE` audit. Shipping one
  invoice does **not** wait for its siblings. A sale with no LC advances on its
  own ship.
- **LC is a container.** `check_lc_shipment_ready` now returns `shipped_ok` and
  `all_shipped_ok`; the LC payment_due gate (`move_lc_stage`, Flask's LC move
  route, and both 409 payloads) uses **`all_shipped_ok`** and the missing-item
  text `invoices: every invoice must ship first`. `_sync_lc_payment_due` runs
  after a ship and advances the LC only when every non-void invoice under it has
  shipped, moves any child PI still behind with the LC (stage history
  "Every invoice under the LC has shipped" + `SALE_MOVE`), and leaves PIs with
  uninvoiced quantity untouched — they carry forward.
- **`GET /api/sales` attaches invoices.** `_attach_invoice_summaries` joins the
  LC terms and the paid total, so each row gains
  `invoices[{invoice_id, invoice_number, status, amount, paid_amount,
  maturity_date, payment_terms, tenor_days}]` (LC rows too, through the
  nesting) plus `has_uninvoiced_remainder` (PI quantity > invoiced quantity).
- **Board UI.** `SaleCard` renders an invoice section (number, status chip,
  amount, `paid $X`, maturity + rose **Overdue** chip, per-invoice next-action
  button, inline form). `InvoiceStepForm` is the shared book/ship/pay form,
  extracted from `InvoicePanel`; `utils/invoices.js` holds the status→label and
  status→next-step maps that `InvoicePanel` now reuses. `utils/sales.js` drops
  the sale-level action for `shipment_ongoing` (`actionLabel: null`), so there
  is no "Move to Payment Due" button. `Sales.jsx` posts the three invoice
  routes, refetches, and on a ship with `has_uninvoiced_remainder` asks
  "Create more invoices?" before opening the sale's invoice panel.

### Decisions made & why

- **Ship, not booking, opens Payment Due** — the plan's core rule: a booking can
  slip, an actual ship date cannot.
- **The LC gate is stricter than the approved plan** (plan said "one shipped
  invoice"; we use every-invoice-shipped). Per the product rule, the LC is a
  container whose PIs must not move forward until all invoices created on that
  basis have real ship dates. The sale-level gate stays "≥1" — a PI advances on
  its own shipment.
- **No force-move path.** The gate refuses rather than warns; the UI removed the
  manual button, so the only way into Payment Due is a real shipment.
- **Asymmetric advancement is a rendering problem, solved globally.** Board
  grouping runs once for the whole list (`groupSalesByLc`), the LC card renders
  in its own column, and advanced or lagging PIs that are not in the LC's column
  render as standalone `SaleCard`s — so a PI can never disappear off the board.
- **Invoice summaries carry the LC terms** so the board's Ship form prefills
  deferred maturity without a second fetch; the server still recomputes.

### Known follow-up

- **Invoices on a PI still nested under its LC card are not rendered inline.**
  The LC card's nested PI rows keep the LC's own affordances; those invoices are
  reached through the LC's **Manage invoices** action. Inline rows there need a
  shared invoice-rows extraction out of `SaleCard` — recorded in `PRD`/`SPEC` as
  a tracked follow-up, deliberately not done in this phase.

### Gate (all green)

| Gate | Result |
|---|---|
| Full backend `python -m pytest tests/ -q` | **1311 passed, 8 skipped** in 1613.58s, exit 0 |
| New tests | `tests/test_invoice_payment_due.py` (**9**), `SaleCard.test.jsx` (**7**) |
| `python -m ruff check .` | 63 findings repo-wide, all pre-existing; unchanged from the Phase 1 baseline and none in the changed hunks |
| `python -m pyright` | 6 errors / 18 warnings — identical to baseline |
| Frontend `npm test` | **355 passed / 40 files** |
| `npx oxlint` | 0 errors / 0 warnings |
| `npx vite build` | succeeded (chunk-size warning only) |

### Next

Phase 3 of the approved plan: the daily reminder cron (at sight +7 days,
deferred at maturity; in-app bell + email to `NOTIFY_EMAILS` directors only,
deduped, one 7-day escalation). Phase 4's "late invoicing reopens a completed
sale" and Phase 5's report grain come after.

---

## 2026-10-27 — INVOICE-MATURITY Phase 1 (LC terms + per-invoice maturity)

Worktree `O:/Projects/Raas-Tracker-worktrees/invoice-shipment-gate`, branch
`feat/invoice-start-at-shipment`. Phase 1 of the approved 7-phase plan
(`~/.qoder/plans/warm-cave-stag.md`): **schema + API + UI**, no reminders yet.

### What shipped

- **Schema v7.** `letters_of_credit.payment_terms` (TEXT, NULL = at sight) and
  `tenor_days` (INTEGER); `invoices.maturity_date` (TEXT ISO). `_SCHEMA_VERSION`
  6 → 7; guarded additive `ALTER TABLE`s inside `_create_tables()`; **no
  backfill** — a legacy NULL row reads as at sight.
- **LC terms service/API.** `create_lc(payment_terms, tenor_days)` defaults
  `at_sight` and forces `tenor_days = NULL`; `deferred` requires 1..720;
  `POST /api/lcs` and `PUT /api/lcs/<id>` validate with a field-shaped **400**
  (`payment_terms` / `tenor_days`), and a PUT back to at sight clears the tenor.
  `GET /api/lcs/<id>` and the list expose both.
- **Ship-time maturity.** `invoice_terms_for_sale` reads the sale's LC terms;
  `resolve_invoice_maturity` computes at sight = actual ship date, deferred =
  actual + tenor (leap-safe, UTC date math), and accepts an operator override
  only if it is strict `YYYY-MM-DD` and **not before** the actual ship date.
  Resolution happens **before** the shipment INSERT; `ship_invoice` persists
  `maturity_date`, audits `Shipped on <date> (maturity <date>)`, and returns it.
  `GET /api/sales/<id>/invoices` rows and `GET /api/invoices/<id>/readiness`
  carry `maturity_date` + the LC's `payment_terms`/`tenor_days` (a sale with no
  LC reads `at_sight`, never null).
- **UI.** `LinkPIsModal` (new LC) and `LcDetailModal` (edit) carry a
  **Payment terms** select with a conditional **Tenor days** input (1..720);
  the LC view shows `Deferred · N days` / `At sight`. `InvoicePanel`'s Ship
  form prefills an editable **Maturity date** from the row's terms and follows
  the actual ship date until the operator edits it; rows show
  `matures <date>` plus an **Overdue** chip for a shipped invoice whose maturity
  has passed.

### Decisions made & why

- **Terms live on the LC**, one tenor per LC, not per invoice — every invoice of
  that LC inherits it, so an operator never re-enters terms per shipment.
- **Maturity is computed at ship time, not at invoice creation.** Before the
  actual ship date exists there is nothing to add a tenor to.
- **The override is a correction, not a contract.** It cannot precede the
  actual ship date and it is audited with the ship event.
- **The UI prefill is convenience only.** The server recomputes/re-validates, so
  a stale or hand-edited payload cannot store a wrong maturity.
- **No backfill.** Legacy rows read as at sight; rewriting history would invent
  ship dates for invoices that never shipped.
- **All money stays USD.** Reminder email recipients will be the company's
  directors only (never clients) — Phase 3.

### Gate (all green)

| Gate | Result |
|---|---|
| Full backend `python -m pytest tests/ -q` | **1302 passed, 8 skipped** in 1640.74s, exit 0 |
| New tests | `tests/test_lc_terms.py` (**11**) + `tests/test_invoice_maturity.py` (**8**) |
| `python -m ruff check .` | 63 findings repo-wide, all pre-existing; none in the changed hunks (baseline was 64) |
| `python -m pyright` | 6 errors / 18 warnings — identical to baseline |
| Frontend `npm test` | **348 passed / 39 files** |
| `npx oxlint` | 0 errors / 0 warnings |
| `npx vite build` | succeeded (chunk-size warning only) |

### Not run

- No manual browser pass and no isolated-Postgres E2E script for this phase; the
  backend suite drives the real Flask app and the component tests cover the
  three UI surfaces.
- **Production is still at `raas_schema_version` 6.** The first boot after this
  branch is deployed must apply v7; verify `payment_terms`/`tenor_days` on
  `letters_of_credit` and `maturity_date` on `invoices`, then confirm the gate
  returns to its fast path.

### Next

Phases 2–7 of the same approved plan, in order: **2** per-invoice Payment Due +
board visibility (sale enters Payment Due on its first actual ship date, invoice
list + inline forms on every card, manual move button removed in the UI),
**3** daily reminder cron (at sight +7 days, deferred at maturity; in-app bell +
email to `NOTIFY_EMAILS` directors only, deduped, one 7-day escalation),
**4** late invoicing reopens a completed sale, **5** sheet-like report grain
(per invoice line + provisional remainder), **6** report UI, **7** docs +
verification.

---

## 2026-10-27 — Phase 7 verification + push (SHIP-FIRST complete)

Worktree `O:/Projects/Raas-Tracker-worktrees/invoice-shipment-gate`, branch
`feat/invoice-start-at-shipment`. All six feature/docs commits are in the
branch; this entry adds the Phase 7 evidence commit and the push.

### Full gate (all green)

| Gate | Result |
|---|---|
| Targeted backend group 1 (shipment gates, integrity, production, order, completion, LC) | **137 passed** in 150.16s |
| Targeted backend group 2 (commercial report ×2, sales, typed errors) | **183 passed** in 150.11s |
| Full backend `python -m pytest tests/ -q` | **1283 passed, 8 skipped** in 1096.86s (18:16), exit 0 |
| `python -m ruff check .` | 64 pre-existing repo-wide findings, unchanged; the two backend files this branch touched (`sales.py`, `flask_app.py`) carry only baseline import/unused findings outside the changed hunks |
| `python -m pyright` | **6 errors, 18 warnings** — identical to the recorded baseline |
| Frontend `npm test` | **339 passed / 39 files** |
| `npx oxlint` | **0 errors, 0 warnings** |
| `npx vite build` | succeeded (JS 1,051.63 kB; chunk-size warning only) |

Copied `raas-tracker-frontend/dist/` → `react_frontend/` (gitignored) so Flask
serves the SPA under test.

### Isolated-Postgres manual E2E (never the production DSN)

Harness lives outside the repo (`%TEMP%\raas_e2e\server.py`, `flow.py`,
`seed_ui.py`). `server.py` pops the inherited `DATABASE_URL`, starts an
embedded `pgserver` in a fresh temp dir, asserts the DSN contains `raas_e2e`
and not `supabase`, pins `DATABASE_URL`/`RAAS_SECRET`/`COOKIE_SECURE=0`
before importing the app, seeds `e2e-admin`, and serves on
`127.0.0.1:5099`. `flow.py` drove the plan's A=100/B=100 PI end to end:
**61/61 checks PASS**, exit 0.

Evidence highlights:

- LC Received → Shipment Ongoing with **zero invoices**; sale
  `shipment_status=production_running`.
- Invoice A1 = 80 KG of A: product/unit price **inherited** from the PI
  (`$5.00`), amount derived `400.00`, born `planned`.
- Refusals before any write: ship before booking **409**, pay before shipping
  **409**, book before production **409**; no shipment row was created.
- Produce from the recipe page → `produced` → book **200**; LC
  Shipment Ongoing → Payment Due with **one booked invoice**.
- Ship A1 → shipment row carries the invoice's `invoice_date` (2026-10-20);
  partial payment keeps `shipped`; balance flips `paid`.
- Invoice-defined completion: A1's paid invoice set settles the sale even
  though PI line B was never invoiced. Report then shows A final (80) and B
  **provisional** (100).
- A2 (100 KG of B) → produce/book/ship/pay → sale `completed`,
  `invoice_total = 1100`, LC auto-completed, board archived the sale,
  `include_completed=true` returns it, report keeps both rows (B now final).
- Report CSV export returns JSON `{content, row_count}`: 20 columns, trailing
  `Provisional` column, 2 rows for the completed PI.
- Payment correction on the completed sale: `700 → 600` reopens it to
  `payment_due` and B back to `shipped`; restoring `700` completes it again
  (stage history records both crossings).

**Plan deviation (expected).** The approved plan's E2E step said "verify the
sale does not complete while A2 is uninvoiced". That step predates the locked
Q4 decision — completion is the invoice set (≥ 1 invoice, all paid), so A1
settling *does* complete the sale. The run asserts the shipped/documented
contract instead; PRD Flow 6 step 9 and SPEC say the same.

### UI pass (Playwright MCP against the same server)

- Board renders exactly **four** live columns (PI Issued, LC Received,
  Shipment Ongoing, Payment Due); the completed LC is absent. LC Received
  card shows **Start Shipment**; Shipment Ongoing card shows **Move to
  Payment Due** + **Manage invoices**.
- Sale detail from the board shows Invoices (0) with **Add invoice**; the
  composer pre-fills today's invoice date, Product/PI qty/Remaining, and
  Unit price + Total are `readOnly`. Typing **120** against a PI line of 50
  produced `$600.00` with no tolerance/exceed copy — uncapped quantities are
  live in the UI.
- Commercial report shows Provisional badges on every uninvoiced row,
  keeps the completed PI-E2E-1 rows, renders the legend, and each row's
  Actions pencil opens `SaleDetailModal`; the completed editor is
  invoice-**read-only** ("2/2 invoices paid · $1,100 of $1,100"), keeps the
  payments list with **Edit payment / Delete payment** controls, and its
  stage history shows Payment Due → Completed → Payment Due → Completed.
- No console errors on the reports/sales pages.

### Known follow-up (not touched in Phase 7)

Creating a **new** invoice on an already `completed` sale does not re-sync
completion (`create_invoice` / `create_invoices_batch` do not call
`_sync_sale_completion`), so the sale can briefly read `completed` while
holding a `planned` invoice. Payment/void/delete edits do re-sync. This is
pre-existing behaviour, not a regression of this branch; closing it needs a
red test plus a product decision (reopen vs refuse) and belongs in its own
task.

### Push

`feat/invoice-start-at-shipment` (7 commits incl. this one) pushed to origin
on 2026-10-27 under the owner's explicit "PUSH ALL". **Not merged** to `main`.

---

## 2026-10-27 — Invoice-first pipeline (SHIP-FIRST phases 1–6)

Worktree `O:/Projects/Raas-Tracker-worktrees/invoice-shipment-gate`, branch
`feat/invoice-start-at-shipment`. Nothing pushed or merged.

### What was completed

- **Phase 1 (`eb18d5e`) — shipment before invoicing.** `lc_received →
  shipment_ongoing` no longer needs an invoice; the sale and LC readiness
  barriers and the frontend barrier modal were removed. Payment Due kept its
  old production gate until Phase 2.
- **Phase 2 (`b4cc66c`) — strict Book → Ship → Pay.** `ship_invoice` requires
  `booked` and stamps the invoice's `invoice_date` on the shipment;
  `mark_invoice_paid` requires `shipped`. Typed `InvoiceNotBooked` /
  `InvoiceNotShipped` / `SaleNotReadyPastShipment` map to **409**.
  `booked_invoice_readiness` powers the sale and LC Payment Due gates; LC
  readiness gained `booked_ok`; invoice readiness `can_pay` only for `shipped`.
- **Phase 3 (`df07754`) — completion defined by invoices.** A sale completes
  only with ≥ 1 invoice and every invoice `paid`, compared on
  `SUM(invoice_items.line_total)`. Payment edits/deletes and voids re-sync
  through `_sync_sale_completion`; an uncovered correction reopens to Payment
  Due. LC auto-completion (`_sync_lc_completion`) closes with its last
  completed child and reverts on reopen.
- **Phase 4 (`591bc48`) — report fallback + retention.** Shared
  `_commercial_report_base_sql` CTE gives each PI line invoice quantity/price
  once invoiced and PI quantity with `is_provisional` before; completed sales
  stay in the report while `/api/sales` hides them (`include_completed=true`
  opts back in); CSV gained the trailing `Provisional` column (20 columns).
- **Phase 5 (`7f3a2bc`) — UI.** Four live board columns; Shipment Ongoing =
  “Move to Payment Due”, Payment Due = “Manage Invoices” opening
  `SaleDetailModal`. LC crossing gates on `booked_ok`; `ProductionRequiredModal`
  and the sale-level `PaymentModal` deleted. `InvoicePanel` takes `readOnly`
  for completed sales; add-line targets are only `planned`/`produced`. The
  report has an `Actions` column + Provisional badge + legend; a row opens the
  editor and a save reloads the report/KPIs.
- **Phase 6 (this commit) — docs.** PRD Flow 3/6/7, SPEC move gates + 409s,
  payment completion re-sync, report money/columns/CSV + UI contract,
  `include_completed`, refreshed test coverage; plan rows 1–6 checked;
  locked decisions recorded.

### Gates (evidence)

- Backend full suite (end of Phase 4, no backend file touched since):
  **1283 passed, 8 skipped** in 19:17, exit 0. Ruff/pyright unchanged from the
  pre-existing baseline (repo-wide ruff findings and pyright errors were
  verified identical at Phase 2; Phase 5 is frontend-only).
- Phase 5 targeted: 5 files / **91 tests** green.
- Frontend full suite: **339 passed / 39 files**.
- `oxlint`: **0 errors, 0 warnings**. `vite build`: succeeded.
- No DDL, no `_SCHEMA_VERSION` bump, no auth/gate edits.

### Next steps

- **Phase 7** — full pytest + ruff + pyright + vitest + oxlint + build, then
  the isolated-Postgres manual E2E in the approved plan (A=100/B=100 PI →
  zero-invoice shipment → partial invoices → book/ship/pay → completion →
  report provisional/final rows → edit a completed row → CSV).
- Manual E2E must never point at the production `DATABASE_URL`.
- Commit 6 (this docs commit) then Phase 7. Push/merge/deploy left to the
  owner.

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
