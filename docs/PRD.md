# PRD: RAAS Tracker — Chemical Inventory + Sales CRM

## 1. Summary

RAAS Tracker is a web-based application for chemical warehouse/factory inventory management, monthly reconciliation, recipe/production reports, file-upload-based stock comparison (PDF/Excel), and a sales pipeline (PI → LC → shipment → payment → completed) with PDF/`.docx` proforma invoice (PI) parsing. Flask backend serves a React 19 + Vite frontend. PostgreSQL (Supabase) is the database in every environment.

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

### Future
11. Multi-warehouse support
12. Batch/lot tracking
13. Role-based field-level permissions
14. Advanced reporting (dashboard charts, trends)
15. Email/webhook notifications

## 5. User Flows

### Flow 1: Monthly Stock Reconciliation
1. User goes to **Upload** page → selects PDF/Excel → clicks **Upload**
2. System parses file → compares with current DB stock → shows results (Matched / Mismatch / Not in DB / Not in Upload)
3. User reviews mismatches → **Unit Mapping** modal to map units (or assign reason code)
4. All mismatches resolved → click **Approve** → status `approved`
5. Click **Apply** → system updates stock (`adjust_stock_from_upload`) → upload status `applied`

### Flow 2: Proforma Invoice (PI) Parsing
1. User goes to **Sales** page → **Parse PI** → uploads PDF/`.docx`
2. System parses in-memory → returns extracted data (PI number, date, client, item list)
3. User reviews data → clicks **Create Sale** → entry created in pipeline at `pi_issued`

### Flow 3: Sales Pipeline Advance
1. `pi_issued` → enter LC details → **Update LC** → `lc_received`
2. `lc_received` → enter shipment date → **Move** → `shipped`
3. `shipped` → enter payment (date, amount, note) → **Record Payment** → `payment_received` (balance 0 → `completed`)

### Flow 4: Recipe/Production Reports
1. User goes to **Reports** → selects recipes → enters production quantity → **Generate**
2. System calculates chemical quantities per recipe item % for the batch
3. **Export CSV** → file downloads

### Flow 5: Admin Setup & User Management
1. First run → `/setup` page → enter setup token printed to console → first admin created
2. Admin goes to **Users & Keys** → create/revoke users and API keys
3. Audit logs (`/api/audit-logs`) trace all actions

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
| `sales` | id, stage, pi_number, pi_date, client_name, pi_file_path, lc_number, lc_date, shipment_date, payment_date, payment_amount, created_at, updated_at | Sales header |
| `sale_items` | id, sale_id (FK), product_name, quantity, unit_price | Sales line items |
| `sale_payments` | id, sale_id, payment_date, payment_amount, notes, created_at | Payment records |
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
3. Vercel preview has 4.5MB upload limit — strategy for large PI files in production? (Production host uses waitress, Vercel preview only)
4. `stock.py` missing from `raas_tracker` package — `chem_stock.py` shim used; should package be renamed?

---

*Last updated: September 2026 — based on actual codebase state (commit 998b841)*