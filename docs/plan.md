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

**P1 backlog (identified, not started):**

| Task | Description |
|---|---|
| P1-a | `ProxyFix(x_for=1)` trusts one `X-Forwarded-For` hop unconditionally; that `remote_addr` gates the API-key IP allowlist and login lockout-by-IP. Fix = `x_for=0` or gate on `TRUSTED_PROXY`. Recorded in SPEC §9 |
| P1-b | `audit_logs.ip_address` is overloaded (real IP / operator reason / spoofable value); consider a separate `reason` column |
| P1-c | No concurrent regression test — the suite is single-threaded, so the `threading.local` bug class is only covered sequentially. Add a threaded `Barrier` test, and revisit if the app ever moves to gevent |
| P1-d | API keys can reach **no** audited mutation (every audited entity is `@admin_required`), so the `api-key:<name>` label is currently unreachable in practice |
| P1-e | `atomic=True` default: 8 of 62 sites pass `atomic=False`; review the other ~54 for ghost audit rows that survive a rollback |

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