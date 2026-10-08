# Implementation Plan — RAAS Tracker (Post-M6 Alignment)

This plan captures future work aligned with the Vibe Coding guidebook. The core product (M0–M5) is **complete and deployed**. Remaining items are hardening, polish, and the M6 documentation alignment you're doing now.

---

## ✅ Completed (M0–M5)

| Milestone | Scope | Verified |
|---|---|---|
| M0 | Git, CI, PRD/SPEC stubs, permissions | ✅ |
| M1 | Chemicals CRUD, Upload/Parse/Compare/Approve/Apply | ✅ 116 backend tests |
| M2 | Recipes CRUD, Production Reports (multi-recipe), CSV Export | ✅ |
| M3 | Sales Pipeline (PI→LC→Shipment→Payment), PI Parse (PDF/.docx), CSV Export | ✅ |
| M4 | Auth (Session/API Key), Roles, Throttle, Setup Token, Audit, Notifications | ✅ |
| M5 | CI/CD, Vercel Preview, Deploy Guide, Password Rotation, Backup/Restore | ✅ |
| M6 (partial) | PRD, SPEC, AGENTS.md, plan.md, commands, skills | 🔄 This doc |

---

## ✅ Multi-PI under a single LC (2026-10)

Delivered in four phases, each gated by a full test run + an Oracle review.

| Phase | Scope | Gate |
|---|---|---|
| 1 | Schema foundation — `letters_of_credit` (UNIQUE company+number), `sales.lc_id`, `invoice_items`, idempotent legacy auto-group backfill, `_SCHEMA_VERSION` 1→2 | Oracle ✅ (1 re-review-free remediation pass) |
| 2 | Domain services + API — `lcs.py` (CRUD, attach/detach, stage propagation with `FOR UPDATE`), `POST /api/sales/batch`, invoice-line CRUD with server-owned price, `lc_received → shipment_ongoing` gate | Oracle ✅ (attempt 1 blockers → 1 residual → closed with evidence) |
| 3 | Frontend — LC board card with nested PIs, multi-PI create, LC linking, LC detail modal, partial-qty invoice line editor | Oracle ✅ PASS |
| 4 | Reporting shift — commercial report/summary/exports compute quantity, total and due from `invoice_items` (row grain and columns unchanged, `lc_id` filter); LC filter in Reports; docs updated | ✅ gate green |

**Business rules now in force**
- A proforma is an offer, not a receivable: `due = max(invoiced_total − received, 0)`.
- Invoice line quantity is independent of the PI — partial, split across invoices, or abandoned. Nothing records "abandoned"; an uninvoiced quantity never becomes due.
- Unit price is always inherited from the PI line and enforced server-side.
- An LC owns the journey for its PIs; all PIs under one LC progress together.
- PIs without an LC are transient — created in batches, linked later, deleted if the LC never arrives.

---

## 🔄 Phase M6: Vibe Coding Alignment (Current)

**Goal:** Create missing docs, codify agent rules, add custom commands/skills per guidebook.

| Task | Description | Files | Acceptance |
|---|---|---|---|
| M6.1 | Write PRD.md (from codebase) | `docs/PRD.md` | Complete, matches current features |
| M6.2 | Write SPEC.md (tech spec) | `docs/SPEC.md` | Complete, matches DB/API/Stack |
| M6.3 | Write AGENTS.md (opencode rules) | `AGENTS.md` (root, auto-loaded) + `docs/AGENTS.md` pointer | Complete, covers workflow/security/git |
| M6.4 | Write plan.md (this file) | `docs/plan.md` | Complete, vertical slices |
| M6.5 | Add custom commands | `.opencode/commands/{start-task,security-review,handoff,review}.md` | Usable via `/start-task` etc. |
| M6.6 | Add skills | `.opencode/skills/{secure-api-route,vertical-slice-task}/SKILL.md` | Loadable by agent |
| M6.7 | Commit docs + rules to git | `AGENTS.md`, `docs/`, `.opencode/` | Tracked, clean tree |

**Status:** M6.1–M6.6 done. M6.7 pending (awaits user's "push" instruction).

> **These docs are living.** Whenever a new plan changes scope, architecture, or
> roadmap, update `docs/PRD.md`, `docs/SPEC.md`, `docs/plan.md`, and `AGENTS.md`
> in the same task as the code change.

---

## 📦 Phase M7: Hardening & Polish (Next 1–2 Weeks)

Vertical slices, each independently testable.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| M7.1 ✓ | **Upload Size Guard** — Env-configurable `MAX_CONTENT_LENGTH` (default 50MB via `MAX_CONTENT_LENGTH_MB`, Vercel 4.5MB override) + 413 JSON that reports the real cap | `flask_app.py`, `.env.example`, `docs/SPEC.md` | `MAX_CONTENT_LENGTH_MB` env respected (floats OK); 413 JSON message matches configured cap |
| M7.2 | **Frontend Error Boundary Logging** — Send React errors to `/api/notifications` (type `frontend_error`) | `ErrorBoundary.jsx`, `NotificationContext.jsx`, `flask_app.py` | Uncaught React errors appear in admin notifications |
| M7.3 | **Recipe Item `required_qty_per_unit` Persistence** — Store computed `percentage * total_quantity / 100` on create/update | `recipes.py`, `flask_app.py` (recipe item PUT), `recipes.jsx` | Field persisted, editable, used in reports |
| M7.4 | **Sales Duplicate PI Warning → Configurable** — Add `ALLOW_DUPLICATE_PI` env (default true) to suppress warning | `flask_app.py:_duplicate_pi_warning`, `.env.example` | Env controls warning behavior |
| M7.5 ✓ | **Frontend Oxlint Cleanup** — Fix 39 baseline warnings (unused imports, exhaustive-deps, refs in render) | `raas-tracker-frontend/src/**/*.jsx`, `.oxlintrc.json` | `oxlint` → 0 warnings |
| M7.6 | **Vercel `public/` Assets Verify** — Ensure `favicon.svg`, `icons.svg` served with correct MIME | `vercel.json` (buildCommand), `public/` | `/favicon.svg` → `image/svg+xml` |
| M7.7 | **OpenAPI Spec Generation** — Add `flask-openapi3` or manual `openapi.json` for client SDKs | New file `openapi.json` (generated) | Valid OpenAPI 3.0 spec for all `/api/*` |
| M7.8 | **Add Chemical UI (admin-only)** — Add Chemical modal on `/chemicals` (name, qty, unit, reorder); Pydantic validation + 403 backstop on `POST /api/chemicals`; Adjust button + `update`/`reorder` endpoints admin-only | `flask_app.py`, `Chemicals.jsx`, `utils/units.js`, `tests/test_chemicals_api.py`, `Chemicals.test.jsx` | Admin adds/adjusts stock end-to-end; 400/403/409 covered; gate green |
| M7.9 | **Stock History (admin-only)** — Datewise inventory movements on `/chemicals` (range calendar + Day/Week/Month tabs, chart + feed with purpose); reason persistence, `ADD_CHEMICAL` birth audit, `GET /api/chemicals/history` | `stock.py`, `db.py`, `flask_app.py`, `StockHistory.jsx`, `utils/history.js`, `Chemicals.jsx` | Movements with purpose render grouped; filters work; gate green |
| M7.10 | **Low-stock alarm control** — Alarm editor inside Info modal (admin), reorder editing removed from Adjust (qty-only), "Low-stock alarm" relabels, buy alert on silent-add | `Chemicals.jsx`, `flask_app.py`, `tests/test_chemicals_api.py`, `Chemicals.test.jsx` | Alarm set per chemical; below-alarm notifies; gate green |

---

## 🔐 Phase P0: Audit Attribution Hardening (Completed 2026-10-06)

Fixes audit-trail defects found by a read-only security review.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| P0-1 ✓ | **Actor no longer leaks across requests** — `_gate_api` seeds `anonymous` at the *top* of every `/api` request (before the retired-token branch) and a `teardown_request` hook clears the thread-local. Pre-fix a public `forgot-password` recorded `user_id='admin'` (the last authenticated user on that waitress thread) | `flask_app.py`, `raas_tracker/audit.py` | Public/rejected writes record `anonymous`, never a prior user |
| P0-2 ✓ | **Audit `ip_address` populated** — defaults to the request's `remote_addr` for all 62 call sites; a caller-supplied value still wins (`ADJUST_STOCK` reason) | `raas_tracker/audit.py` | Mutating rows carry an IP; no per-call-site opt-in needed |
| P0-3 ✓ | **Distinct actor labels** — `username` / `api-key:<name>` / `anonymous` / `cron` / `system`; `_actor()` falls back to `anonymous`, never `system` | `flask_app.py`, `raas_tracker/audit.py` | A verified write can never be downgraded to `anonymous` |
| P0-4 ✓ | **Maturity cron audited** — `CRON_MATURITY_CHECK` row with `checked`/`notified`; was the one mutating route with no audit trail | `flask_app.py` | Every cron run leaves a row attributed to `cron` |
| P0-5 ✓ | **Conn-leak fix in the audited legacy-token path** — `conn.close()` in a `finally`; previously skipped whenever the INSERT failed | `flask_app.py` | No pooled connection lost per rejected request |

---

## 🔐 Phase P1: Audit Coverage & Value Bounds (Completed 2026-10-06)

Baseline established by enumeration: **64 mutating routes — 49 covered, 1 partial,
10 uncovered**, 4 read-only POSTs. Payloads classified across all 62 call sites:
**zero** secrets and effectively zero PII (every `auth.py` row logs a username;
`redeem_reset_token` logs `row[1]`=username, one column from the hash).

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| P1-1 ✓ | **Credential lifecycle audited** — `API_KEY_CREATE` / `API_KEY_REVOKE`; `create_api_key` gained `RETURNING id`. Raw key and hash never audited | `raas_tracker/auth.py`, `tests/test_api_key_audit.py` | Mint/revoke leave a row naming the admin and the key name |
| P1-2 ✓ | **Unit-conversion factors audited** — `UNIT_CONVERSION_UPSERT` with old→new factor. Route was unaudited *and* reachable by any session **or API key** | `raas_tracker/stock.py`, `tests/test_stock_audit.py` | Factor changes leave a before/after trail |
| P1-3 ✓ | **Central value bounds** — `_bounded()` caps every text column, strips control chars **including CR**, marks truncation. Also fixed: `LEGACY_TOKEN_USED` used the request path as `entity_type`; `SALE_UPDATE` leaked `pi_file_path` on **both** paths | `audit.py`, `flask_app.py`, `sales.py`, `tests/test_audit_limits.py` | No unbounded value; a NUL can no longer abort the INSERT |
| P1-6 ✓ | **Reason moved out of `ip_address`** into `new_value` as `"<qty> (<reason>)"`, restoring the real client IP. Stock-history feed taught to parse it, with a fallback so pre-P1-6 rows still render | `stock.py`, `uploads.py`, `tests/test_stock_audit.py` | Adjustments record reason **and** IP; history keeps delta + purpose |
| P1-4 ✓ | **Company field coverage** — `COMPANY_UPDATE` was gated on a *name* change, so moving `swift`/`lc_bank`/`address`/`contact_person`/`code`/`country` left no trace. Records the changed **field names** (values would push bank/contact PII into the audit trail), only for genuine changes. Patch fields derived from `_FIELDS` so a new column cannot skip auditing | `companies.py`, `tests/test_company_audit.py` | Any field edit leaves a row; no-op resubmits do not; no bank/contact value in `audit_logs` |
| P1-5 ✓ | **Admin deletes + kill switch audited** — `SALE_ITEM_DELETE`, `SHIPMENT_DELETE`, `UPLOAD_DELETE` (with cascaded row count), `SESSION_REVOKE` (with live-session count). Each captures an identifying summary read **before** the DELETE, since the delete destroys its own evidence; gated on `rowcount` so a refused or no-op delete leaves no row. Revoke audited at the admin route only, not in the shared `revoke_user_sessions`. Follow-ups: records `runs=N` — the production runs the cascade destroys, placed **first** so the 2000-char bound can never clip it — and names the PI; the delete is scoped by `sale_id`, so a line cannot be removed through another PI's URL, and 404s when nothing was deleted; the kill switch 404s an unknown user and carries `@admin_required` | `sales.py`, `flask_app.py`, `tests/test_delete_audit.py` | Every admin delete leaves a truthful row; `AuditLogs.jsx` renders `old_value` so the evidence is actually visible |
| P1-7 ✓ | **`X-Forwarded-For` trust gated** — `ProxyFix`'s `x_for` is now conditional on `TRUSTED_PROXY=1`; unset means the header is ignored and `remote_addr` is the socket address. Scope was **wider** than recorded: `remote_addr` gates the API-key IP allowlist (an authorisation bypass), login lockout-by-IP **and** Flask-Limiter's IP-keyed buckets. `docker-compose.yml` binds 5000 to loopback and now expects a TLS proxy in front. `x_proto`/`x_host`/`x_prefix` deliberately stay unconditional — they drive the HTTPS redirect and URL building, where over-trust is a correctness issue, not an authz bypass | `flask_app.py`, `docker-compose.yml`, `.env.example`, `tests/test_proxy_trust.py` | A forged header cannot satisfy the API-key IP allowlist; a declared proxy still yields real client IPs |

**Corrections made during P1** (recorded because both were stated earlier and were wrong):

- ~~P1-d: `api-key:<name>` is unreachable~~ — **wrong**. `POST /api/sales`,
  `/api/recipes`, `/api/recipes/<name>/items` have no admin gate, so API keys
  reach audited mutations. Now proven by a test that stamps a real key.
- ~~Flip the `atomic` default~~ — rejected; would silently lose audit rows.

- ~~P1-13 ~~ and ~~P1-16~~: **both closed** — see the corrected backlog rows
  below, which state exactly what is fixed and which two commit-before-audit
  sites are deliberately left (load-bearing route-level commits). The audit
  listing now orders by `id`, recorded in "P1-18 data migration" below.
- ~~P1-13 as originally written: "8 of 62 sites pass `atomic=False`; review the
  other ~54 for ghost rows that survive a rollback"~~ — **inverted.** With
  `if atomic: conn.commit()` (`audit.py:141-142`), `atomic=True` is the
  *committing* mode: it flushes the caller's entire open transaction. `False`
  leaves the INSERT inside the caller's transaction, where a rollback takes it
  along — the safe direction. Ghost rows and partial commits are therefore an
  `atomic=True` problem. Counts restated as **69** sites (50 `True` / 18
  literal `False` / 1 forwarded at `stock.py:278`). Worst site is
  `uploads.py:891`, inside the `adjust_stock_from_upload` row loop, whose
  `except` at `uploads.py:906` returns `False` without a rollback.
  The wording was corrected first, then P1-13 was **closed** for the enumerated
  sites — see the corrected row above.

**Tail batch completed** — P1-8, P1-10, P1-12, P1-14, P1-15 (see git log).

### P1-18 data migration (decided, implemented)

The DEFAULT change is **DDL**, and `CREATE TABLE IF NOT EXISTS` is a no-op
against an already-migrated table — correcting the format inside the CREATE
statements alone would have fixed only a *fresh* database while every live
deployment kept writing malformed timestamps. So `_SCHEMA_VERSION` went 3 → 4
and `_fix_timestamp_defaults()` re-asserts all 21 DEFAULTs with idempotent
`ALTER TABLE ... ALTER COLUMN ... SET DEFAULT`. Verified against a database
migrated by the pre-fix code: version 3 → 4 detected, all 21 live DEFAULTs
repaired, correct format on a new row. The test suite builds a fresh database
every run and could **not** have caught this — hence
`test_live_column_default_uses_the_correct_format` asserts against
`information_schema`.

| Table | Action | Why |
|---|---|---|
| `login_attempts` | **DELETE all rows** | Ephemeral, rebuilt on the next failure. A malformed row whose month-slot read as "in the future" made the lockout *under*-count, so reinterpreting it was not an option. |
| `api_key_rate_limits` | **DELETE all rows** | Same, and a leaked hit is a rate-limit bypass. |
| `audit_logs` | **untouched — no backfill, no rewrite** | Immutable ledger. Historical `timestamp` values keep their malformed shape **by decision**; the ledger stays byte-untouched. |
| All business `created_at` / `updated_at` / `hit_at` / `read_at` / `reviewed_at` / `changed_at` / `upload_date` | historical rows left as-is, **DEFAULT corrected going forward** | These are display/sort data with no control attached; rewriting history is not worth the risk. |
| `sessions.expires_at`, `api_keys.expires_at` | **no migration needed** | Written by Python `strftime("%Y-%m-%d %H:%M:%S")`, which was always correct. They became *correct in comparison* once the SQL floor was fixed. Confirmed, not assumed. |

**Resolved (the residual previously flagged here):** the audit-log listing now
orders by `id` — `raas_tracker/audit.py:180`, `ORDER BY id DESC`. Deciding
between backfilling the ledger and reordering was unnecessary: backfilling is
still rejected (the ledger is immutable), so the listing uses `id`, the
append-only IDENTITY that is both monotonic and the order rows were actually
written in. The `timestamp` column is still returned as evidence, just never
used as a sort key. `/api/audit-logs` therefore reads newest-first for rows
spanning the migration and for rows written across an afternoon (which the old
textual sort inverted outright, `HH` being a 12-hour clock). Pinned by
`test_audit_listing_orders_by_insertion_not_by_the_timestamp_text`, which
inserts two rows whose timestamps sort the opposite way to their ids.
Historical `audit_logs` rows remain untouched — no rewrite, ever.

**Remaining backlog (not started):**

| Task | Description |
|---|---|
| ~~P1-9~~ ✓ | **Concurrent regression test added** — `tests/test_concurrency.py` drives the per-thread actor store (`raas_tracker/audit._audit_state`) from 8 barrier-synchronised threads, one DB connection each: each thread reads back its own actor, a peer's `clear_audit_actor` never disturbs another thread mid-flight, an unset thread falls back to `system` rather than inheriting, and 8 concurrent `UPLOAD`-style audit writes stay attributed to their own writer. Exercises the resolver directly (not HTTP) because the Flask test client's cookie jar is shared mutable state and is not thread-safe | `tests/test_concurrency.py` | The `threading.local` bug class has concurrent coverage, not just sequential |
| ~~P1-11~~ ✓ | **CLOSED — one canonical name for the whole request.** `api_upload` computed `secure_filename` for the extension gate (then discarded it), let `save_upload` apply `_safe_upload_filename` for the stored column, and returned the **raw** client name in the JSON response — so the caller was told a different string from the one stored and audited. Two live defects followed, both now fixed: a legitimate non-Latin filename was **rejected 400** (werkzeug flattens Arabic/Hebrew to a bare `xlsx`, so the gate saw no extension at all), and U+202E RTL-override survived into the stored name and rendered in `UploadHistory` — a name-spoofing vector in an audit-visible list. `_safe_upload_filename` is now the single canonical sanitiser (unicode-preserving, plus bidi controls, dotfiles and Windows reserved names), computed once and reused for gate, stored column, audit and response. The on-disk name stays `uuid4().hex` and is deliberately NOT this value — there is no traversal sink. Verified non-vacuous: reverting only the source gave 19 failed / 23 passed | `raas_tracker/uploads.py`, `flask_app.py`, `tests/test_upload_filename.py` (42 tests) | Gate, stored column, audit and response always agree |
| ~~P1-13~~ ✓ | **CLOSED for the enumerated sites — mutation and audit now share one transaction.** 24 `atomic=True` call sites that committed *before* auditing now pass `atomic=False` and commit once, after the audit write. Highest severity first: `adjust_stock_from_upload` (`uploads.py:896`) no longer audits inside its row loop — the audit rows are **collected** and written after the loop with `atomic=False`, and the `except` now `conn.rollback()`s, so a failure on row N applies **nothing** (before: rows 1..N-1 durably flushed, audited, and reported as a failure the caller could not distinguish from success). Also fixed: the 6 delete paths (see P1-16) plus `create_user`, `create_company`, `update_company`, `add_recipe`, `add_recipe_item`, `update_recipe_item`, `update_recipe`, `add_shipment`, `add_sale_item`, `update_sale_item`, `create_invoice`, `ship_invoice` (2 rows), `void_invoice`, `save_upload`, `update_stock` (its own `atomic` param now forwarded to the audit instead of committing first), `set_reorder_level`, `add_chemical` (whose `except psycopg.IntegrityError` `rollback()` was dead code while the INSERT was already committed, and is now live). Class-3 swallowed rollbacks added in `approve_upload_row`, `reject_upload_row`, `approve_upload`, `lock_reconciliation_period`, `api_cron_maturity_check`. **Deliberately NOT changed** (each read in full first): `log_audit_action`'s global `atomic=True` default — changing it would silently alter ~50 sites (rejected in a prior decision); `update_sale_full` (`sales.py:814` commit → `:832` audit) and `book_invoice` (`update_invoice_status` commits at `:1835` → audit at `:1846`) still commit before auditing — both are **load-bearing commits that the route depends on for its own one-transaction guarantee** (`flask_app.py:2213-2230` books the sale badge and the invoice together via `book_invoice`'s commit; `test_production_atomicity.py:221` pins it), so moving them is a route-level redesign, not a call-site edit; `revoke_user_sessions` commits internally (`auth.py:527`), which is why the `SESSION_REVOKE` audit at `flask_app.py:731` commits separately — documented on P1-16. Test: `tests/test_audit_atomicity.py` (23 tests, TDD red-first) |
| ~~P1-17~~ ✓ | `tests/test_auth.py:16` assigned `auth_mod._DUMMY_HASH = None` as a bare module global instead of via `monkeypatch`, so it was never restored across tests (`conftest.py:88` does it correctly). Now set with `monkeypatch.setattr(auth_mod, "_DUMMY_HASH", None)` so it is restored at teardown; the anti-timing-oracle assertion is unchanged |
| ~~P1-18~~ ✓ | **CLOSED — a production timestamp defect, not a flaky test.** Root cause: `to_char(ts, 'YYYY-MM-DD HH:MM:SS')` is a *strftime* pattern, and `to_char` reads the codes differently — `MM` = MONTH (minutes are `MI`) and `HH` = 12-hour (24-hour is `HH24`). So every datetime this schema produced stored the **month** in the minute slot and lost both the real minutes and any AM/PM distinction: real `2026-10-07 07:06:58` → stored `2026-10-07 07:10:58`, real `2026-03-15 14:22:41` → `2026-03-15 02:03:41`. Because the columns are TEXT and every window is a **string comparison**, each time-window control silently degraded into a comparison of the **seconds field**: `is_login_blocked`'s 10-minute window became `second_row >= second_now`, so an attempt written seconds earlier could be judged outside the window the moment the clock ticked past its recorded second. That is exactly the `401×6` flake — it reproduces in `tests/test_proxy_trust.py` **alone** (~6% of runs, 3/48 measured), and captured failures showed all 6 rows present, all `ip=127.0.0.1`, `route_eval=4` vs 5. Fix: `'YYYY-MM-DD HH24:MI:SS'` in every SQL `to_char`, `db.py` docstring rewritten to name the `MM`/`MI` and `HH`/`HH24` traps, `_SCHEMA_VERSION` 3 → 4. Same defect also broke `check_api_key_rate_limit` (300/min shed hits every minute) and made session/API-key expiry wrong by ~9 min early / ~59 min late (a 1-hour TTL was really ~10 hours). See "P1-18 data migration" below |
| ~~P1-16~~ ✓ | **CLOSED — all 6 delete paths now commit AFTER the audit.** `api_delete_upload` (`flask_app.py:1397`), `delete_company`, `delete_recipe_item`, `delete_recipe`, `delete_shipment`, `delete_sale_item` each pass `atomic=False` and commit once, so a failing audit INSERT leaves the row **intact and unrecorded** rather than deleted and unrecorded. The rowcount gates are preserved, so a delete that matched no row still writes no audit row. The CTE idea in the original wording was not needed: each site already captured its evidence with a SELECT *before* the DELETE (P1-5), so capture + delete + audit is one transaction without rewriting the deletes. Not applicable to the session-revoke route, as originally noted: `revoke_user_sessions` commits internally (`auth.py:527`) — see P1-13 |

**P1 backlog (identified, not started):**

| Task | Description |
|---|---|
| ~~P1-b~~ | **Resolved as P1-6** — the operator reason was moved out of `ip_address` into `new_value` as `"<qty> (<reason>)"`, restoring the real client IP. A separate column was not needed; the overload was the bug, not the schema |
| ~~P1-c~~ | **Duplicate of P1-9** — the concurrent `Barrier` test for the `threading.local` bug class. Closed as P1-9 (`tests/test_concurrency.py`) |
| ~~P1-d~~ | **Wrong — retracted.** `POST /api/sales`, `/api/recipes` and `/api/recipes/<name>/items` carry no admin gate, so API keys *do* reach audited mutations and the `api-key:<name>` actor label is reachable in practice. Now proven by a test that stamps a real key |
| ~~P1-e~~ | **Duplicate of P1-13** — same `atomic=True` row, same inverted wording. Removed in favour of the corrected P1-13 above |

---

## 🚀 Phase M9: Sales–Production–Live Epic (Current)

Locked: USD only · admin-only financials/history/mutations · partial shipments real ·
fail-closed shortage handling (a short ingredient rejects the whole run) · manual master recipes (no auto-create) · frozen batch
snapshots · companies master · single shipment header per record.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| P0 ✅ | **Companies master** — `companies` table (name required, rest optional), `sales.company_id` FK RESTRICT, idempotent backfill merging case variants, CRUD UI + API (mutations admin) | `db.py`, `companies.py`, `chem_stock.py`, `flask_app.py`, `Companies.jsx`, `tests/test_companies.py` | CRUD + gates + backfill green; gate green |
| P1 ✅ | **Capture fields** — `sales.maturity_date/comments` (columns; maturity wired in P6), `sale_items.unit`, `shipments` table + record/delete UI, `production_runs` + `run_items` DDL; company select + unit inputs on PI entry; comments editor | `db.py`, `sales.py`, `flask_app.py`, `ReviewModal.jsx`, `ShipmentModal.jsx`, `SaleDetailModal.jsx` | New fields round-trip; gate green |
| P2 ✅ | **Master recipe from register** — company→product selects, auto-name, `UNIQUE(company_id, name)`, 409 → existing; `recipes.company_id/product_name` | `flask_app.py`, `Recipes.jsx`, register endpoints | One master per company×product; gate green |
| P3 ✅ | **Snapshot production** — run form (order/batch/date/qty/notes) → formula snapshot → atomic deduction → run record | `flask_app.py`, `recipes.py`, `stock.py`, Recipes UI | Deduction + snapshot + audit; gate green |
| P4 ✅ | **Live commercial report** — USD KPIs + 13-col table + export, admin-only Commercial tab | `flask_app.py`, `Reports.jsx` | KPIs/rows/export correct; gate green |
| P5 ✅ | **Consistency lock** — pipeline mutations admin-only; every write dated + actor | `flask_app.py`, `raas_tracker/sales.py`, sales UI | Non-admin blocked; gate green |
| P6 ✅ | **Maturity reminders** — maturity at payment entry + daily cron → bell (maturity + 7-day escalation, dedupe) | `flask_app.py`, `vercel.json`, `raas_tracker/sales.py`, `raas_tracker/notifications.py`, sales UI | Reminders fire, escalation fires, paid silent; gate green |

---

## 🔐 Phase M10: Password Reset & Forced Change (Current)

Backend only (frontend is a separate design lane).

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| M10 ✅ | **Password reset/change** — voluntary change (keeps current session), forgot-password with generic-200 anti-enumeration + delay floor, single-use redeem (`FOR UPDATE`), admin force-reset + admin token (one-time secrets, `must_change_password`), `_gate_api` 403 enforcement (API keys exempt), login/me/users surface flags | `raas_tracker/db.py`, `raas_tracker/auth.py`, `chem_stock.py`, `flask_app.py`, `tests/test_password_reset.py`, `docs/SPEC.md`, `AGENTS.md` | 27 new tests green; full gate green |

---

## 📊 Phase CR: Commercial Report Enhancement (Completed)

Vertical slices for filtering, grouping, calendar navigation, and export on the live commercial report.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| CR-1 ✅ | **Backend Filter Engine** — CTE-based query with date_anchor, date_from/to, customer_name (ILIKE), product_name (EXISTS), company_id, stage, payment_status, pagination | `sales.py`, `flask_app.py`, `tests/test_commercial_report.py` | `/api/reports/live/filtered` returns filtered detail rows |
| CR-2 ✅ | **Frontend URL-Synced Filter Bar** — Quick Search, Date Anchor, Preset Range, Custom From/To, Stage, Payment Status, Company, Group By, Clear; state in URL | `CommercialFilters.jsx`, `datePresets.js`, `Reports.jsx` | Filters persist on refresh, shareable links, group_by toggles view |
| CR-3 ✅ | **Expandable Period Table + Pagination** — Detail mode (19 cols) + Grouped mode (period headers with KPIs, expandable detail rows), skeleton loading | `CommercialTable.jsx`, `Reports.jsx` | Expand/collapse works, pagination, both modes render correctly |
| CR-4 ✅ | **Backend Group-By Summary + Detail Items** — Period aggregation (month/week/year) with KPIs; detail items for expandable rows | `sales.py`, `flask_app.py` | `/api/reports/live/summary` returns periods with items for CR-3 |
| CR-5 ✅ | **Frontend Calendar Navigator** — Prev/Next period, Date Anchor, Quick Presets (Month/Quarter/Year), Custom Dates, Clear; URL-synced | `CommercialCalendarNavigator.jsx`, `Reports.jsx` | Navigates periods, presets update dates, anchor changes refetch |
| CR-6 ✅ | **Filtered Export** — Export button sends current filters; GET/POST endpoint returns CSV of filtered data | `flask_app.py`, `Reports.jsx`, `test_commercial_report.py` | Exported CSV matches UI filters |
| CR-7 ✅ | **Full Gate + Docs** — pytest + vitest + oxlint + build pass; SPEC.md updated with new endpoints | `docs/SPEC.md`, `docs/plan.md` | All gates green, docs current |

---

## 🏭 Phase GP: Go for Production & Invoicing (Completed)

Vertical slices linking production runs to sale invoices: schema, lifecycle backend, Recipes
"Go for Production" modal, pipeline invoice panel, amount/auto-paid rules.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| S1 ✅ | **Production/invoice schema** — `invoices` (+ unique `(sale_id, invoice_number)`), `production_run_links`, `sales.shipment_status`, `sale_items.item_no`, `sale_payments.invoice_id`; `production_runs` gains `material_number/packing/invoice_number`, `sale_item_id` nullable; `_SCHEMA_VERSION` gate so additive DDL is never skipped by the schema fast path (replaced the unenforced `_REQUIRED_SIG_TOKENS` sentinel) | `raas_tracker/db.py` | Migrates existing DBs; gate green |
| S2–S3 ✅ | **Invoice lifecycle + run links backend** — invoice create/list, book/ship/pay, sale completion, `production-source`, produce links run→sales/invoices with advance-only statuses (`paid` terminal); sale payloads gain `shipment_status`/`invoices[]`/`item_no` | `flask_app.py`, `raas_tracker/sales.py`, `raas_tracker/recipes.py`, `tests/test_invoices_production.py` | 12 new tests green; gate green |
| S4 ✅ | **Go for Production modal** — company → PI/sale → invoice dropdown/new number → material number (auto from `item_no`) → packing → batch → date → qty → multi-recipe rows; Recipes detail calls now pass `company_id` (scoping fix) | `ProductionRunModal.jsx`, `Recipes.jsx` | Run created + linked; gate green |
| S5 ✅ | **Pipeline invoice panel + sub-step badges** — InvoicePanel in SaleDetailModal (`shipment_ongoing`) with per-invoice book/ship/pay, amount + balance, completion money header; SaleCard badges (Production running / Production done / Ship booked); non-blocking no-invoice toast on `lc_received → shipment_ongoing` | `InvoicePanel.jsx`, `SaleCard.jsx`, `SaleDetailModal.jsx`, `Sales.jsx` | Actions + badges render; gate green |
| S6 ✅ | **Invoice amount + auto-paid** — `invoices.amount` (NUMERIC(14,2), NULL = legacy), POST default = sale total, paid flip when `paid_amount >= amount` (0 → paid at creation, NULL → any payment), no-downgrade rules | `raas_tracker/db.py`, `flask_app.py`, `raas_tracker/sales.py`, `InvoicePanel.jsx`, `tests/test_invoice_amount.py` | 10 new tests green; backend 304 passed/8 skipped, frontend 234, oxlint 0, build OK |

---

## 🧾 Phase INV-500: LC invoice-barrier 500 (Completed 2026-10-08)

Creating an invoice from the `lc_received → shipment_ongoing` barrier modal
failed with `500 {"error": "internal server error"}`.

Root cause (reproduced locally with the test client): `Sales.jsx` always
passed `saleId={null}` to the modal, so it POSTed to
`/api/sales/null/invoices`. No POST route matches that shape, so Werkzeug
raised 405 — and with no 405 handler the catch-all `Exception` handler
masked it as a 500. Ruled out with evidence: no file writes on the invoice
path (read-only-FS theory), zero `?` placeholders (SQLite-syntax theory),
ProxyFix already correct (auth failures are 401/403, never this 500).

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| INV-1 ✅ | **405 handler** — `/api` wrong-method requests return 405 `{"error":"method not allowed"}` instead of falling into the catch-all 500 | `flask_app.py`, `tests/test_invoice_errors.py`, `docs/SPEC.md` | `POST /api/sales/null/invoices` → 405 JSON |
| INV-2 ✅ | **Non-finite money guards** — `remaining_invoice_money` / `_invoice_money_already_on_sale` raise `ValueError` (400) on NaN/Inf PI or invoiced totals instead of `InvalidOperation` (500); nothing persisted on the 400 path | `raas_tracker/sales.py`, `tests/test_invoice_errors.py` | NaN qty / Inf price / NaN legacy line → 400 + zero invoice rows |
| INV-3 ✅ | **Barrier modal takes the LC's PIs** — one invoice-number row per PI (single invoice or one per PI; same-PI splits stay in the PI detail line editor, since each create seeds the whole remainder), `apiFetch` instead of raw `fetch`, per-row errors, and auto-retry of the blocked `POST /api/lcs/<id>/move` on success | `InvoicesRequiredModal.jsx` (+ new `.test.jsx`, 5 tests), `Sales.jsx` (barrierPis/barrierMove, `handleBarrierInvoicesSuccess`) | Per-PI URLs asserted, never `null`; 299 frontend tests green, oxlint 0 errors, build OK |

Business rule restated: one LC carries one **or many** invoices across its
PIs (gate counts invoices across ALL of the LC's sales).

---

## 🔮 Phase M8: Future Features (Backlog)

Not yet sliced — awaiting prioritization.

| Idea | Description | Est. Slices |
|---|---|---|
| Multi-Warehouse | `warehouse_id` on chemicals/uploads/sales; user-warehouse assignment | 4–5 |
| Batch/Lot Tracking | `batch_number`, `expiry_date` on `chemicals` + FIFO consumption | 3–4 |
| Role-Based Field Permissions | Field-level ACL (e.g., only admin edits `unit_price`) | 2–3 |
| Email/Webhook Notifications | SMTP/SendGrid + webhook endpoints for critical alerts | 2–3 |
| Advanced Dashboard | Recharts trends (stock turnover, sales velocity, mismatch trends) | 2–3 |
| Mobile PWA | Service worker, offline queue for uploads, install prompt | 3–4 |
| Audit Log Export | CSV/Excel export with filters (date, user, entity) | 1–2 |
| PI Template Library | Save/reuse PI parsing templates for recurring vendors | 2–3 |

---

## 📋 Per-Task Execution Template

When starting any task above:

```
1. /clear  (new session)
2. /start-task <Task ID>  → shows plan, asks for approval
3. Write failing test(s)  (red)
4. Implement (green)
5. pytest + npm test + oxlint  (all pass)
6. Manual browser verify
7. git commit -m "feat/fix: <Task ID> - <short desc>"
8. Update plan.md checkbox
8. /handoff  (if switching tasks)
```

---

## 📌 Notes for Agent

- **Current branch:** `main` (commit `998b841`)
- **Local DB:** `DATABASE_URL` set via `setx` (Supabase pooler, encoded pw)
- **Vercel Preview:** `https://chemcalc-omega.vercel.app/` — auto-deploys on push
- **Test DB:** `pgserver` embedded (offline) or `TEST_DATABASE_URL`
- **Secrets:** `RAAS_SECRET` (local + Vercel), `DATABASE_URL` (local `setx` + Vercel), `PRODUCTION=1` (Vercel)
- **Password rotation:** Run `setx` locally + update Vercel env → redeploy
- **Do not push** without explicit user instruction

---

*Generated September 2026 — aligned with Vibe Coding Guidebook v2026*