# PRD: RAAS Tracker — Chemical Inventory + Sales CRM

## 1. Summary

RAAS Tracker is a web-based application for chemical warehouse/factory inventory management, monthly reconciliation, recipe/production reports, file-upload-based stock comparison (PDF/Excel), and a sales pipeline (PI → LC → shipment → payment → completed) with per-invoice production/shipment tracking and PDF/`.docx` proforma invoice (PI) parsing. Flask backend serves a React 19 + Vite frontend. PostgreSQL (Supabase) is the database in every environment.

## 2. Problem & Goals

**Problem:** Chemical factories rely on manual/Excel-based stock tracking — error-prone, time-consuming, and monthly reconciliation is difficult. Proforma invoices (PI) entered manually cause data errors. No centralized system tracks the sales pipeline (PI through payment).

**Goals:**
- Build a centralized, real-time chemical inventory system
- Automate monthly stock reconciliation (upload → compare → approve → apply)
- Auto-extract data from PI documents (PDF/`.docx`)
- Track the full sales pipeline (PI → LC → shipment → payment)
- Generate recipe-based production reports
- Secure multi-user access with session + API-key authentication

**Non-goals (what we are NOT building):**
- ERP-level financial management (GL, AP/AR, bank reconciliation)
- Multi-warehouse/location tracking (single warehouse assumed)
- Batch/lot-level traceability (chemical-level only)
- Real-time IoT sensor integration
- Mobile app (responsive web is sufficient)

## 3. Target Users

| Persona | Needs | Problems Solved |
|---|---|---|
| **Warehouse Manager** | Daily stock updates, monthly reconciliation, reorder levels | Freedom from manual Excel, real-time stock visibility |
| **Production Manager** | Recipe creation, ingredient calculation by batch count | Eliminate manual calculation errors |
| **Sales Team** | PI creation, pipeline tracking, payment follow-up | Freedom from scattered spreadsheets |
| **Admin/Manager** | User/key management, audit logs, security | Role-based access, compliance |

## 4. Features (Priority-Ordered)

### Must-have (MVP)
1. **Chemical Inventory:** CRUD, unit conversion, reorder levels, stock delta updates
2. **Monthly Reconciliation:** PDF/Excel upload → parse → DB compare → mismatch flag → approve → apply to stock
3. **Recipe Management:** Recipe CRUD, items (chemical + %), water %, total quantity, production reports (CSV/Excel)
4. **Sales Pipeline:** Stages (PI → LC → shipment → payment → completed), PI parse (PDF/`.docx`), LC/shipment/payment entry, CSV export
5. **Unit Conversions:** Custom factor bidirectional conversions, default seeds (KG↔G, L↔ML, DRUM↔L/KG…)
6. **Audit Log:** Log all important actions (who, what, when, old/new values)
7. **Authentication:** Sessions (humans), API keys (scripts), roles (admin/user), setup token (first admin), login throttle (5 fails/10 min), API-key rate limit (300/min)

### Should-have
8. **Notification System:** Real-time bell icon, admin-scoped critical alerts (login failures)
9. **Reason Codes:** Categorized reasons for mismatches/approvals (MEASUREMENT_ERROR, UNIT_CONVERSION…)
10. **Setup Token:** First-admin creation (single-use, 60-min TTL, console-printed)
11. **Go for Production & Invoicing:** one-click production run from a recipe, linked to the sale's invoices; per-invoice lifecycle (planned → produced → booked → shipped → paid), shipment sub-step badges on the pipeline, invoice panel + paid/total completion on the sale detail

### Future
12. Multi-warehouse support
13. Batch/lot tracking
14. Role-based field-level permissions
15. Advanced reporting (dashboard charts, trends)
16. Email/webhook notifications

## 5. User Flows

### Flow 1: Monthly Stock Reconciliation
1. User goes to **Upload** page → selects PDF/Excel → clicks **Upload**
2. System parses file → compares with current DB stock → shows results (Matched / Mismatch / Not in DB / Not in Upload)
3. User reviews mismatches → **Unit Mapping** modal to map units (or assign reason code)
4. All mismatches resolved → click **Approve** → status `approved`
5. Click **Apply** → system updates stock (`adjust_stock_from_upload`) → upload status `applied`

### Flow 2: Proforma Invoice (PI) Entry & Parsing
1. User goes to **Sales** page
2. **Parse PI** → uploads PDF/`.docx` → system parses in-memory → returns extracted data (PI number, date, client, item list) → user reviews → **Create** → entry created at `pi_issued`
3. **Add multiple PIs** → one form holds N PI documents (each with its own PI number, date and item rows), created atomically: if any PI fails, nothing is saved
4. **Link PIs → LC** → select one or more unlinked PIs and attach them to a new or existing LC. A PI created but never covered by an LC is transient — delete it

### Flow 3: Sales Pipeline Advance (LC-driven)
1. `pi_issued` → **Link PIs to LC** (the LC record carries the number, dates, bank ref) → the LC and all its PIs move to `lc_received`
2. `lc_received` → **invoices are created on the LC's PIs** (see Flow 6 — invoice lines carry per-product quantities)
3. `lc_received → shipment_ongoing` is **gated**: refused unless a recipe exists for every PI product **and** at least one invoice exists. The refusal names each missing precondition. Jumping over `shipment_ongoing` is gated too
4. `shipment_ongoing` → payments recorded per invoice (falling back to per PI) → balance 0 → `completed`
5. All PIs under one LC progress together — the pipeline board shows one LC card with its PIs nested

### Flow 4: Recipe/Production Reports
1. User goes to **Reports** → selects recipes → enters production quantity → **Generate**
2. System calculates chemical quantities per recipe item % for the batch
3. **Export CSV** → file downloads

### Flow 5: Admin Setup & User Management
1. First run → `/setup` page → enter setup token printed to console → first admin created
2. Admin goes to **Users & Keys** → create/revoke users and API keys
3. Audit logs (`/api/audit-logs`) trace all actions

### Flow 6: Go for Production & Invoice Tracking
1. An invoice is created against a PI (existing or new number). Its **lines** carry the product, the invoiced quantity, the unit price and the line total. Quantity is **free relative to the PI**: it may be the full PI quantity, less than it, or split across several invoices. Unit price is inherited from the PI line and enforced server-side — the client cannot set it
2. Because quantity may differ per invoice, a product can be partly shipped and the remainder abandoned. Nothing records "abandoned": a quantity that was never invoiced simply never becomes due
3. The invoice's money is **always derived**, never typed: `amount` = the PI total minus what the invoice lines already carry, and the invoice is seeded with one line per remaining uninvoiced PI line before its header is restated as the sum of those lines. So `invoices.amount` always equals the sum of its lines, and every invoice reads correctly on the commercial report. A typed amount is not accepted — a header carrying money no line backs is invisible to the report, which showed a real receivable as "Paid" and dropped it from gross sales. To invoice less than the remainder, create the invoice and then adjust its lines
4. Recipes → detail → **Go for Production** → company → sale/PI → invoice → material number (auto-filled from item no.), packing, batch, date, quantity, optional multi-recipe rows → production run created and linked
5. Linked invoices advance `planned → produced`; pipeline card shows the shipment sub-step badge (Production running → Production done → Ship booked)
6. Per invoice: **Book** (approx. ship date) → `booked`; **Ship** (actual date) → `shipped` + shipment record; **Pay** → `paid` once payments cover the amount — `paid` never reverts

### Flow 7: Commercial Report (invoice-driven)
- One row per **PI line** (unchanged grain), same columns as before
- Quantity, total price and due come from **invoice lines**, not the PI: a proforma is an offer, not a receivable
- Only sales with at least one invoice appear; a PI line with zero invoiced quantity shows 0
- `due = max(invoiced_total − received, 0)`; payment status follows the invoice totals
- Filters add **LC** (exact match on the LC), alongside quick search, dates, company, stage and status

## 6. Data Model

### Core Tables
| Table | Key Columns | Purpose |
|---|---|---|
| `chemicals` | id, name (unique, case-insensitive idx), current_qty, balance_last_month, unit, last_updated, reorder_level | Chemical master data |
| `recipes` | id, name (unique), total_quantity, water_percentage, created_date | Recipe header |
| `recipe_items` | id, recipe_id (FK), chemical_id (FK), percentage, required_qty_per_unit | Recipe ingredients |
| `uploads` | id, filename, upload_date, file_type, status, total_chemicals, matched, mismatches..., match_percentage | Upload metadata |
| `upload_rows` | id, upload_id (FK), chemical_name, batch_number, expiry_date, upload_unit, balance_last_month, balance_this_month, matched_in_db, unit_match | Upload detail rows |
| `unit_conversions` | id, from_unit, to_unit, factor (unique pair) | Custom unit conversions |
| `reason_codes` | id, code (unique), description, category | Mismatch/approval reasons |
| `approval_workflow` | id, upload_id, upload_row_id, status, reason_code, comments, reviewed_by, reviewed_at | Approval workflow |
| `audit_logs` | id, action, entity_type, entity_id, user_id, old_value, new_value, timestamp, ip_address | Full audit trail |
| `sales` | id, stage, pi_number, pi_date, client_name, pi_file_path, lc_number, lc_date, shipment_date, payment_date, payment_amount, company_id (FK), maturity_date, comments, shipment_status, created_at, updated_at | Sales header |
| `sale_items` | id, sale_id (FK), item_no, product_name, quantity, unit_price, unit | Sales line items |
| `companies` | id, name (unique), code, country, address, contact_person, swift, lc_bank | Customer master (P0) |
| `shipments` | id, sale_id (FK), ship_date, invoice_number, invoice_date, notes | Actual shipments, partials as rows (P1) |
| `invoices` | id, sale_id (FK), invoice_number, status (planned → produced → booked → shipped → paid), amount, approx/actual ship dates, notes | Per-invoice shipment lifecycle; a sale may hold several |
| `production_runs` + `production_run_items` | run: recipe/order/batch/dates/material_number/packing/invoice_number/notes; items: frozen formula + deducted qtys | Immutable batch log (P1 DDL, P3 executes) |
| `production_run_links` | id, run_id (FK), sale_id (FK), invoice_id (FK) | Ties a production run to the sale(s)/invoice(s) it fulfils |
| `sale_payments` | id, sale_id, invoice_id (FK), payment_date, payment_amount, notes, created_at | Payment records |
| `users` | id, username (unique), password_hash, role (admin/user), created_at | Users |
| `sessions` | id, token_hash (unique), user_id, created_at, expires_at, revoked | Sessions |
| `api_keys` | id, key_hash, name, created_by, expires_at, allowed_ips, revoked, last_used_at | API keys |
| `notifications` | id, type, title, body, severity, role_scope, entity_type, entity_id, dedupe_key | Notifications |

### Identity & Access
- **Case-insensitive chemical name**: `idx_chemicals_name_lower` (lower(name) unique index)
- **RLS not used** — application-level authorization (`_gate_api`, `admin_required`)
- **API keys**: `ck_live_` prefix, SHA-256 hash, IP allowlist, expiry, revocation

## 7. Edge Cases & Error Handling

| Situation | Handling |
|---|---|
| User gives bad input (wrong type, missing field) | Pydantic validation → 400 `{error, details:[{field, message}]}` |
| Network/DB failure | try/except → 500 `internal server error` (detail in log, generic to client) |
| No permission (non-admin delete) | `@admin_required` → 403 `admin required` |
| Login fails 5 times (user/IP) | 10-min block, admin notification |
| Upload file parse fails | 400, file auto-deleted |
| Approve upload with unmapped units | 400 `unmapped units` with list |
| Duplicate PI number | Warning shown, but saves anyway |
| Delete last admin | 400 `cannot delete the last admin` |
| Expired session/key | Auto-rejected (DB `expires_at` check) |

## 8. Non-functional Requirements

- **Performance:** API response < 500ms (p95), page load < 3s
- **Mobile Responsive:** Tailwind CSS + mobile-first
- **Security:** bcrypt-13 (SHA-256 pre-hash), SHA-256 session/key hashes, parameterized queries, secure headers (CSP, HSTS, X-Frame-Options), CSRF-safe cookies (SameSite=Lax, HttpOnly, Secure)
- **Accessibility:** Labels, keyboard nav, color contrast
- **Observability:** Structured logging (`raas` logger), audit logs, notifications

## 9. Tech Stack (Proposed)

| Layer | Technology | Reason |
|---|---|---|
| **Backend** | Flask 3 + psycopg3 | Lightweight, simple, pgserver testable |
| **Database** | PostgreSQL 16+ (Supabase) | Managed, pooler, free tier, RLS optional |
| **Frontend** | React 19 + Vite 8 + Tailwind 4 | Modern, fast HMR, type-safe (JS) |
| **State/Auth** | React Context + cookie-based session | Simple, server-side validation |
| **Charts** | Recharts | Lightweight, declarative |
| **Notifications** | Sonner (Toaster) | Clean, accessible |
| **Testing** | pytest (backend), Vitest (frontend) | Isolated PG, fast |
| **Lint** | oxlint (frontend), ruff (backend plan) | Fast, low config |
| **CI/CD** | GitHub Actions | postgres:16 service, plain YAML |
| **Deploy Target** | PaaS (Render/Railway/Fly) + Supabase | Stateless, container-ready |
| **Local Dev** | `pgserver` (embedded PG) | Isolation without serialization |

## 10. Milestones

| Milestone | Deliverable | Status |
|---|---|---|
| M0: Foundation | Git, PRD, SPEC, AGENTS.md, permissions, CI | ✅ Done |
| M1: Chemicals + Upload | CRUD, upload/parse/compare/approve/apply | ✅ Done |
| M2: Recipes + Reports | Recipe CRUD, production reports, CSV export | ✅ Done |
| M3: Sales Pipeline | PI parse (PDF/`.docx`), pipeline stages, payment, CSV export | ✅ Done |
| M4: Auth + Security | Session/API-key, roles, throttle, setup token, audit, RLS-equivalent | ✅ Done |
| M5: Operational | CI/CD, deploy guide, Vercel preview, password rotation, backup | ✅ Done |
| M6: Vibe Coding Alignment | PRD, SPEC, AGENTS.md, plan.md, commands, skills | 🔄 In Progress |

## 11. Success Criteria

- [ ] All CI green (pytest 116+, vitest 73+, oxlint 0 errors, build OK)
- [ ] Local and preview (Vercel): login → dashboard → CRUD → upload → approve → apply → export report → pipeline advance → logout — all work
- [ ] Password rotation guide works for local/Vercel updates
- [ ] Security audit: no secrets in git, RLS-equivalent enforced, parameterized queries, secure headers, throttle works
- [ ] Documentation (PRD, SPEC, AGENTS.md, plan.md) complete and committed

## 12. Open Questions

1. Should `warehouse_id` column be added for multi-warehouse? (In non-goals, but may be needed later)
2. Recipe item `required_qty_per_unit` is currently auto-calculated (percentage × total_quantity) — should it be persisted?
3. Vercel 4.5MB upload limit — strategy for large PI files? The app is currently served from Vercel, where the platform caps bodies at 4.5MB at the edge before Flask sees them; `MAX_CONTENT_LENGTH_MB` (M7.1) aligns the app's own cap and 413 JSON with it. Still open: stand up a non-Vercel host (waitress, 50MB) for large PI files, or require PI files under 4.5MB?
4. `stock.py` missing from `raas_tracker` package — `chem_stock.py` shim used; should package be renamed?

---

*Last updated: September 2026 — based on actual codebase state (commit 998b841)*