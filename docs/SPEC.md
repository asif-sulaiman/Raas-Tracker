# Tech Spec: RAAS Tracker

## 1. Tech Stack & Rationale

| Layer | Choice | Rationale |
|---|---|---|
| **Backend** | Flask 3.1 + psycopg3 | Minimal, plugin-free, stateless with waitress in production |
| **Database** | PostgreSQL 16 (Supabase session pooler) | Managed, auto-backups, pooler, free tier, RLS optional |
| **Frontend** | React 19 + Vite 8 + Tailwind 4 + React Router 7 | SPA, fast HMR, type-safe (JS), modern ecosystem |
| **State/Auth** | React Context + cookie-based session (SameSite=Lax, HttpOnly, Secure) | Simple, server-side validation, CSRF-safe |
| **Validation** | Pydantic v2 (API), Zod (frontend future) | Schema-first, runtime validation |
| **Charts** | Recharts | Lightweight, declarative, responsive |
| **Notifications** | Sonner (Toaster) | Accessible, animated, simple API |
| **Testing** | pytest + pgserver (backend), Vitest + jsdom (frontend) | Isolated PG, fast, deterministic |
| **Lint** | oxlint (frontend), plan: ruff (backend) | Fast, low config |
| **CI/CD** | GitHub Actions (postgres:16 service) | Plain YAML, no matrix |
| **Deploy** | PaaS (Render/Railway/Fly) + Supabase | Stateless, waitress, container-ready |
| **Local Dev** | `pgserver` (embedded PG) | Isolation without serialization |

## 2. Folder Structure

```
Raas-Tracker/
├── .github/workflows/ci.yml       # CI pipeline
├── .opencode/
│   ├── commands/                  # Custom opencode commands
│   └── skills/                    # Reusable skills
├── docs/
│   ├── PRD.md
│   ├── SPEC.md
│   ├── AGENTS.md
│   ├── plan.md
│   └── handoff.md
├── flask_app.py                   # Flask API + SPA hosting (entry)
├── wsgi.py                        # Waitress entry point
├── chem_stock.py                  # Compatibility shim (re-exports raas_tracker.*)
├── parse_sales.py                 # PI parser (PDF + .docx; legacy .doc via Word COM on Windows)
├── parse_stock.py                 # Stock file parsers (PDF/Excel)
├── requirements.txt               # Cross-platform deps
├── requirements-win.txt           # Windows-only extras (doc2docx, pywin32)
├── requirements-dev.txt           # Dev deps (pytest, pgserver, etc.)
├── vercel.json                    # Vercel build + SPA rewrite + security headers
├── .env.example                   # Env template
├── .gitignore
├── raas_tracker/                  # Data layer (PostgreSQL via psycopg)
│   ├── __init__.py
│   ├── db.py                      # Connection, schema, migrations, seeds, schema-sig
│   ├── audit.py                   # Audit log + per-request actor
│   ├── auth.py                    # Users, sessions, API keys, throttle, setup tokens
│   ├── stock.py                   # (via chem_stock shim) Chemicals, units, reorder
│   ├── uploads.py                 # Uploads, comparison, approvals, reconciliation
│   ├── recipes.py                 # Recipes + production reports
│   ├── sales.py                   # Pipeline, items, payments, summaries
│   ├── notifications.py           # Notifications + reads
│   └── cli.py                     # python chem_stock.py <command> entry
├── scripts/
│   └── migrate_sqlite_to_pg.py    # One-off SQLite → Postgres migration
├── raas-tracker-frontend/
│   ├── src/
│   │   ├── main.jsx               # React entry
│   │   ├── App.jsx                # Router + providers
│   │   ├── index.css              # Tailwind imports
│   │   ├── context/
│   │   │   ├── AuthContext.jsx    # Auth state + API
│   │   │   ├── NotificationContext.jsx
│   │   │   └── ConfirmContext.jsx
│   │   ├── pages/
│   │   │   ├── Login.jsx
│   │   │   ├── Home.jsx           # Dashboard KPIs
│   │   │   ├── Chemicals.jsx
│   │   │   ├── Upload.jsx
│   │   │   ├── Sales.jsx
│   │   │   ├── Reports.jsx
│   │   │   ├── Recipes.jsx
│   │   │   ├── Users.jsx
│   │   │   ├── AuditLogs.jsx
│   │   ├── components/
│   │   │   ├── ui/                # Button, Badge, Modal, ErrorBoundary
│   │   │   ├── layout/            # Header, Sidebar, Layout
│   │   │   ├── auth/              # ProtectedRoute
│   │   │   ├── forms/             # UploadArea, UnitMapping
│   │   │   ├── cards/             # KPI, ReconciliationChart, StockDistributionChart
│   │   │   ├── tables/            # DataTable, UploadHistory, ComparisonResults
│   │   │   ├── sales/             # PipelineColumn, SaleCard, LCModal, PaymentModal...
│   │   │   ├── modals/            # Modal
│   │   │   └── notifications/     # NotificationBell
│   │   └── utils/
│   │       ├── api.js             # fetch wrapper + auth
│   │       ├── format.js          # money/number formatting (cents-based)
│   │       ├── stock.js
│   │       ├── sales.js
│   │       ├── notifications.js
│   │       ├── dashboard.js
│   │       └── chime.js
│   ├── package.json
│   ├── vite.config.js
│   └── .oxlintrc.json
├── tests/                         # pytest suite (isolated PG per test)
│   ├── conftest.py                # fixtures: pg_dsn, db, client, admin_client, user_client
│   ├── test_db.py / test_db_pg.py
│   ├── test_auth.py
│   ├── test_stock.py
│   ├── test_upload.py
│   ├── test_sales.py
│   ├── test_rate_limits.py
│   ├── test_pi_parse.py
│   └── test_notifications.py
├── react_frontend/                # Built SPA output (gitignored, served by Flask)
├── public/                        # Vercel static assets (built by vercel.json buildCommand)
└── uploads/ / reports/            # Runtime dirs (gitignored)
```

## 3. Database Schema (Tables, Fields, Relations, Indexes)

### Core Tables (DDL from `raas_tracker/db.py:_create_tables`)

```sql
-- chemicals
CREATE TABLE chemicals (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    current_qty REAL NOT NULL DEFAULT 0,
    balance_last_month REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'KG',
    last_updated TEXT,
    reorder_level REAL NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_chemicals_name_lower ON chemicals (lower(name));

-- recipes
CREATE TABLE recipes (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    total_quantity REAL NOT NULL DEFAULT 1,
    water_percentage REAL NOT NULL DEFAULT 0,
    created_date TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD'))
);

-- recipe_items
CREATE TABLE recipe_items (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    chemical_id INTEGER NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
    percentage REAL NOT NULL DEFAULT 0,
    required_qty_per_unit REAL NOT NULL DEFAULT 0
);

-- uploads
CREATE TABLE uploads (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    filename TEXT NOT NULL,
    upload_date TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    file_type TEXT,
    status TEXT DEFAULT 'uploaded',  -- uploaded | approved | applied
    total_chemicals INTEGER DEFAULT 0,
    matched INTEGER DEFAULT 0,
    last_month_mismatches INTEGER DEFAULT 0,
    this_month_mismatches INTEGER DEFAULT 0,
    both_mismatches INTEGER DEFAULT 0,
    not_in_db INTEGER DEFAULT 0,
    not_in_upload INTEGER DEFAULT 0,
    match_percentage REAL DEFAULT 0
);

-- upload_rows
CREATE TABLE upload_rows (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    chemical_name TEXT,
    batch_number TEXT,
    expiry_date TEXT,
    upload_unit TEXT,
    balance_last_month REAL,
    balance_this_month REAL,
    matched_in_db INTEGER DEFAULT 0,
    unit_match INTEGER DEFAULT 1
);

-- unit_conversions
CREATE TABLE unit_conversions (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    from_unit TEXT NOT NULL,
    to_unit TEXT NOT NULL,
    factor REAL NOT NULL,
    UNIQUE(from_unit, to_unit)
);

-- reason_codes
CREATE TABLE reason_codes (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    description TEXT,
    category TEXT
);

-- approval_workflow
CREATE TABLE approval_workflow (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    upload_row_id INTEGER REFERENCES upload_rows(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'pending',
    reason_code TEXT,
    comments TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- audit_logs
CREATE TABLE audit_logs (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id INTEGER,
    user_id TEXT DEFAULT 'system',
    old_value TEXT,
    new_value TEXT,
    timestamp TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    ip_address TEXT
);

-- sales
CREATE TABLE sales (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'pi_issued',
    pi_number TEXT,
    pi_date TEXT,
    client_name TEXT,
    pi_file_path TEXT,
    lc_number TEXT,
    lc_date TEXT,
    shipment_date TEXT,
    payment_date TEXT,
    payment_amount REAL DEFAULT 0,
    company_id INTEGER REFERENCES companies(id) ON DELETE RESTRICT,
    maturity_date TEXT,
    comments TEXT,
    shipment_status TEXT,  -- NULL | production_running | production_done | ship_booked
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    updated_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- sale_items
CREATE TABLE sale_items (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    item_no TEXT,
    product_name TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    unit_price REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'KG'
);

-- sale_payments
CREATE TABLE sale_payments (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    payment_date TEXT,
    payment_amount REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- companies (P0 customer master; client_name on sales stays as fallback)
CREATE TABLE companies (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    code TEXT,
    country TEXT,
    address TEXT,
    contact_person TEXT,
    swift TEXT,
    lc_bank TEXT,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- shipments (P1: one row per actual, possibly partial, shipment)
CREATE TABLE shipments (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    ship_date TEXT NOT NULL,
    invoice_number TEXT,
    invoice_date TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- invoices (per-invoice shipment lifecycle; a sale may hold several)
CREATE TABLE invoices (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    invoice_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',  -- planned | produced | booked | shipped | paid
    amount NUMERIC(14,2),                     -- NULL = legacy/unknown; DERIVED cache of SUM(invoice_items.line_total). A create never sets 'paid' (INV-REVISION) — only a payment record may
    approx_ship_date TEXT,
    actual_ship_date TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    shipment_status TEXT DEFAULT 'pending'    -- INV-REVISION: per-invoice shipment sub-step
);
CREATE UNIQUE INDEX idx_invoices_sale_number ON invoices (sale_id, invoice_number);

-- invoice_items (INV-REVISION: invoice lines are the money source of truth)
CREATE TABLE invoice_items (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    sale_item_id INTEGER REFERENCES sale_items(id) ON DELETE SET NULL,
    product_name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'KG',
    quantity REAL NOT NULL DEFAULT 0,        -- may EXCEED the PI line quantity (shipment advantage)
    unit_price REAL NOT NULL DEFAULT 0,      -- inherited from the PI line, server-enforced
    line_total NUMERIC(14,2),                -- ROUND(quantity × unit_price, 2)
    source_type TEXT DEFAULT 'pi_line'       -- INV-REVISION: origin of the line
);
CREATE INDEX idx_invoice_items_invoice_id ON invoice_items(invoice_id);
CREATE INDEX idx_invoice_items_sale_item_id ON invoice_items(sale_item_id);

-- letters_of_credit (LC owns the pipeline journey for its PIs)
CREATE TABLE letters_of_credit (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    lc_number TEXT NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
    lc_date TEXT,
    expiry_date TEXT,
    bank_ref TEXT,
    stage TEXT NOT NULL DEFAULT 'lc_received',
    notes TEXT,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    updated_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    shipment_gate TEXT DEFAULT 'per_invoice'  -- INV-REVISION: per_invoice | all_invoices
);
CREATE UNIQUE INDEX idx_letters_of_credit_company_lc ON letters_of_credit (company_id, lc_number);

-- production_runs + run_items (P1 DDL; P3 executes; snapshots freeze batch formulas)
CREATE TABLE production_runs (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE RESTRICT,
    sale_item_id INTEGER REFERENCES sale_items(id) ON DELETE CASCADE,  -- nullable since S1
    order_number TEXT,
    batch_number TEXT,
    production_date TEXT,
    qty_produced REAL NOT NULL DEFAULT 0,
    material_number TEXT,
    packing TEXT,
    invoice_number TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE TABLE production_run_items (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    chemical_id INTEGER REFERENCES chemicals(id) ON DELETE RESTRICT,
    chemical_name TEXT NOT NULL,
    required_qty REAL NOT NULL DEFAULT 0,
    deducted_qty REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'KG'
);

-- production_run_links (which run fulfils which sale/invoice)
CREATE TABLE production_run_links (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES production_runs(id) ON DELETE CASCADE,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL
);

-- users
CREATE TABLE users (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    reset_token_hash TEXT,                       -- M10: SHA-256 of single-use reset token (raw shown once)
    reset_token_expires_at TEXT,                 -- M10: UTC 'YYYY-MM-DD HH:MI:SS' expiry (P1-18: written by Python strftime, so 24-hour + real minutes; equals the SQL to_char floor below)
    must_change_password INTEGER NOT NULL DEFAULT 0,  -- M10: forced-change flag, enforced in _gate_api
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- sessions
CREATE TABLE sessions (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

-- api_keys
CREATE TABLE api_keys (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    expires_at TEXT,
    allowed_ips TEXT DEFAULT '',
    revoked INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    last_used_ip TEXT
);

-- api_key_rate_limits
CREATE TABLE api_key_rate_limits (
    key_id INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    hit_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);

-- notifications
CREATE TABLE notifications (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    severity TEXT NOT NULL DEFAULT 'info',
    role_scope TEXT NOT NULL DEFAULT 'all',
    entity_type TEXT,
    entity_id INTEGER,
    dedupe_key TEXT,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX idx_notifications_dedupe ON notifications(dedupe_key);

-- notification_reads
CREATE TABLE notification_reads (
    user_id INTEGER NOT NULL,
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    read_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
    PRIMARY KEY (user_id, notification_id)
);

-- app_settings (schema signature + setup tokens)
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

### Key Indexes
- `idx_chemicals_name_lower` ON `chemicals (lower(name))` — case-insensitive uniqueness
- `idx_notifications_dedupe` ON `notifications(dedupe_key)` — dedupe lookup
- `idx_invoices_sale_number` ON `invoices (sale_id, invoice_number)` — one invoice number per sale
- FK indexes auto-created by PG

### Migration Strategy (`db.py:_create_tables`)
- **Code-owned version gate (`_SCHEMA_VERSION`, primary):** `_schema_current()` reads `raas_schema_version` from `app_settings` in the same round trip as the signature. A missing, unparseable, or stale value means *not current* → the full DDL runs exactly once and re-stamps the version. **Bump `_SCHEMA_VERSION` whenever `_create_tables()` gains DDL.** Current value is **5** (INV-REVISION: `invoice_items.source_type`, `invoices.shipment_status`, `letters_of_credit.shipment_gate`); 4 = P1-18 timestamp formats.
- Schema-signature fast path (secondary): `_schema_signature_live()` compares columns+indexes vs stored `raas_schema_sig` in `app_settings`, so an out-of-band `DROP` is still detected.
- Idempotent DDL only: `CREATE TABLE IF NOT EXISTS` for tables, `ALTER TABLE ... ADD COLUMN` guarded by `_table_columns()` for columns — safe on a fresh DB *and* an existing production one.
- **One transaction:** `_create_tables` is a transactional wrapper (`pg_advisory_xact_lock` → migration body → re-stamp signature+version → single `commit()`), so a mid-migration failure leaves nothing durable and the version un-advanced. Helpers it calls (e.g. `backfill_company_links`) must NOT commit — the caller owns transaction control. The advisory lock is transaction-scoped, so PG releases it on COMMIT *or* ROLLBACK and it cannot leak onto a pooled connection.
- Seeds (`reason_codes`, `unit_conversions`) run on every connection if tables empty (test isolation friendly)
- Legacy `_REQUIRED_SIG_TOKENS` (`"table.column:data_type"` substrings) is retained only as a secondary check; it is no longer the migration gate and nothing requires new entries.
- **Pooled connections:** `_PooledConnection.close()` commits (explicit close is the success path); `__exit__` rolls back when the block raised. Pre-pool, `psycopg.Connection.close()` rolled back, so any `except: conn.close()` path must `rollback()` explicitly first.

## 4. API Endpoints

### Auth
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/auth/status` | Public | Setup needed? + current identity |
| POST | `/api/auth/setup` | Public (token) | First-admin creation (single-use token) |
| POST | `/api/auth/login` | Public | Session cookie mint |
| POST | `/api/auth/logout` | Public | Revoke session cookie |
| GET | `/api/auth/me` | Session/Key | Current identity details + `must_change_password`, `has_pending_reset` |
| PUT | `/api/auth/password` | Session (human) | Voluntary change (`current_password`, `new_password`); keeps current session, revokes others |
| POST | `/api/auth/forgot-password` | Public (5/min) | Reset request (`username`); always generic 200; only `token_hash[:8]` logged, never the raw token |
| POST | `/api/auth/reset-password` | Public (10/min) | Single-use redeem (`token`, `new_password`); 200 or generic 400 |

### Users (admin only)
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/users` | Admin | List users |
| POST | `/api/users` | Admin | Create user |
| DELETE | `/api/users/<id>` | Admin | Delete user (not last admin) |
| POST | `/api/users/<id>/revoke` | Admin | Revoke all sessions — audited `SESSION_REVOKE`; 404 unknown user |
| POST | `/api/users/<id>/password` | Admin | Force-reset (`temp_password?`, generated if absent); temp returned ONCE; sets `must_change_password` |
| POST | `/api/users/<id>/reset-token` | Admin | Issue reset token; `{token, link}` returned ONCE; sets `must_change_password` |

### API Keys (admin only)
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/keys` | Admin | List keys (fingerprint only) |
| POST | `/api/keys` | Admin | Create key (returns raw once) |
| DELETE | `/api/keys/<id>` | Admin | Revoke key |

### Companies (list: any identity; mutations: admin)
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/companies` | Session/Key | List companies (dropdown source) |
| POST | `/api/companies` | Admin | Create (name required, rest optional); 409 duplicate |
| PUT | `/api/companies/<id>` | Admin | Update allowlisted fields; 404/409 |
| DELETE | `/api/companies/<id>` | Admin | Delete; 409 while sales reference it |

### Chemicals
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/chemicals` | Session/Key | List all |
| POST | `/api/chemicals` | Admin | Create (name, qty, unit, reorder_level); 400 validation, 409 duplicate |
| POST | `/api/chemicals/update` | Admin | Delta stock update |
| PUT | `/api/chemicals/reorder` | Admin | Set reorder level |
| GET | `/api/chemicals/history` | Admin | Stock movements (chemical_id, since/until, limit≤500) |

### Recipes
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/recipes` | Session/Key | List all |
| GET | `/api/recipes/<name>` | Session/Key | Detail + items |
| POST | `/api/recipes` | Session/Key | Create (name, yield, water%) |
| POST | `/api/recipes/<name>/items` | Session/Key | Add item (chem, %) |
| DELETE | `/api/recipes/<name>/items/<chem>` | Admin | Delete item |
| DELETE | `/api/recipes/<name>` | Admin | Delete recipe |
| PUT | `/api/recipes/<name>` | Session/Key | Update yield/water% |
| PUT | `/api/recipes/<name>/items/<chem>` | Session/Key | Update item % |
| GET | `/api/register/products` | Session/Key | Registered products for a company (master-recipe source, P2) |
| POST | `/api/recipes/<name>/produce` | Admin | Create production run(s): formula snapshot + atomic stock deduction (fail-closed on shortage) (P3). `company_id` required; optional `material_number`, `packing`, `invoice_number`, `sale_ids`, `invoice_ids`, multi-recipe `recipes:[{recipe_name, qty}]`. Linked invoices → `produced`, sale → `production_done` (advance-only) |
| GET | `/api/recipes/<name>/runs` | Session/Key | List production runs for a recipe (P3) |
| GET | `/api/recipes/<name>/runs/<id>` | Session/Key | Production run detail with snapshot (P3) |
| GET | `/api/production-source` | Session/Key | Sale + line items (with `item_no`) + company recipes for the Go for Production modal (`sale_id` required) |

### Uploads & Reconciliation
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/upload` | Session/Key (15/min) | Upload PDF/Excel → parse → compare |
| GET | `/api/uploads` | Session/Key | History |
| GET | `/api/uploads/<id>` | Session/Key | Detail + rows |
| DELETE | `/api/uploads/<id>` | Admin | Delete upload + rows |
| POST | `/api/uploads/<id>/approve` | Session/Key (15/min) | Approve (blocks if unmapped units) |
| POST | `/api/uploads/<id>/apply` | Session/Key (15/min) | Apply to stock (requires approved) |
| GET | `/api/uploads/<id>/export` | Session/Key (15/min) | Excel export |

### Reports
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/reports/generate` | Session/Key (15/min) | Multi-recipe report JSON |
| POST | `/api/reports/export` | Session/Key (15/min) | CSV export |
| GET | `/api/reports/live` | Admin | Live commercial report — one row per sale item, sale-level payment fields repeated (P4) |
| GET | `/api/reports/live/filtered` | Admin | Filtered commercial report — detail rows with date_anchor, date_from/to, customer_name, product_name, company_id, stage, payment_status, `lc_id`, `q` (quick search: PI number OR customer OR product), pagination. Non-integer `lc_id`/`company_id` is a 400, never a silently unfiltered result. Sets `X-Total-Count` header = rows matching the filters across ALL pages (the pager keys off it; a full page is never mistaken for the whole report) |
| GET | `/api/reports/live/summary` | Admin | Period-aggregated commercial report — month/week/year grouping with KPIs and optional detail items. Accepts the same filters as `/live/filtered` including `q` and `lc_id`. `group_by=none` returns one period of whole-filter KPIs (the detail view's KPI cards source) |
| GET | `/api/reports/live/export` | Admin (15/min) | Commercial report CSV export with same filter params as `/live/filtered` (including `q` and `lc_id`). Response `{success, filename, content, row_count, truncated}` — `row_count` is the full filtered total, `truncated` true when it exceeds the 10 000-row export cap. Client `page`/`page_size` are ignored (export always starts at page 1) |
| POST | `/api/reports/live/export` | Admin (15/min) | Commercial report CSV with filter params in body |

**Commercial report money contract** (the report is invoice-driven; the row grain and columns never change)
- Per-row `quantity` = `SUM(invoice_items.quantity)` matched on `sale_item_id`; a PI line with nothing invoiced shows `0`.
- Per-row `total_price` = `ROUND(quantity × sale_items.unit_price, 2)` — the price is always the PI's.
- Per-sale `sale_total` = `SUM(invoice_items.line_total)` over that sale's invoices.
- `received_amount` = `SUM(sale_payments.payment_amount)` for the sale (unchanged).
- `due_amount` = `GREATEST(sale_total − received_amount, 0)` — clamped at zero in the detail rows, the summary KPIs **and** the per-period drill-down alike.
- Inclusion requires at least one invoice row for the sale.
- `sale_rows` collapses the per-line `base` to one row per sale (`MAX` for the repeated sale scalars, `SUM` only for per-line gross), so sale-level money is never multiplied by the line count.
- **One source of truth:** because the row grain is the PI line while the money is `invoice_items`, a PI line that already has invoice lines can be neither deleted nor re-based. `DELETE /api/sales/<id>/items/<item_id>`, `PUT /api/sales/<id>/items/<item_id>` (quantity/price) and the bulk `PUT /api/sales/<id>` (both `removedIds` **and** an `items[].id` rewrite that changes quantity/price) all return **409** (`InvoicedLineConflict`) rather than silently desynchronising the money. `product_name`/`unit` and no-op writes stay editable — they are descriptive only.
- A legacy invoice with no lines is **not** reconciled. Nothing seeds lines for it: the invoice stays line-less, contributes nothing to `sale_total`, and a sale whose only invoice is line-less therefore reports `gross_sales = 0`, `due_amount = 0` and `payment_status = "Paid"` (`sales.py:1203-1206`, `:1240-1242`, `:1045-1046`). That is deliberate — synthesising lines from a header amount fabricates a receivable — and every deployment must verify the state is empty (`SELECT count(*) FROM invoices i WHERE NOT EXISTS (SELECT 1 FROM invoice_items ii WHERE ii.invoice_id = i.id)` = 0) rather than repair it silently.
- The create path is **one transaction**: the `sales` row and the PI lines are locked `FOR UPDATE` and held to commit; the caller must supply at least one line (`flask_app.py:2383`, `sales.py:1817-1818`), so there is no seeding step and no derived remainder.
- Quantities are free relative to the PI line: they may equal, exceed, or fall below it. The only rule is finite and > 0. Money is always exact to the cent (`ROUND(..., 2)`).

### Audit & Notifications
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/audit-logs` | Admin | Recent logs (limit) |
| GET | `/api/notifications` | Session | User's notifications + unread count |
| POST | `/api/notifications/read` | Session | Mark read (ids or all) |

#### Audit identity (who is recorded in `audit_logs.user_id`)

Labels are deliberately distinct so an unauthenticated action can never be
mistaken for a trusted background job:

| Label | Meaning |
|---|---|
| `<username>` | Verified human session |
| `api-key:<name>` | Verified API key |
| `anonymous` | No verified credential — any path in `_PUBLIC_API` (`/api/auth/login`, `/api/auth/setup`, `/api/auth/logout`, `/api/auth/status`, `/api/auth/forgot-password`, `/api/auth/reset-password`, `/api/cron/maturity-check`) **and** every rejected request (401/403/429, retired `X-API-Token`) |
| `cron` | The maturity-check scheduled job |
| `system` | No request context at all (CLI/seed/background work) |

Determinism rules:

- `_gate_api` stamps `anonymous` at the **start** of every `/api` request, so a
  public or rejected request can never inherit the previous request's identity
  from the worker thread. Only after authentication succeeds is it replaced by
  the verified actor.
- A `teardown_request` hook clears the thread-local, so waitress's thread pool
  carries no identity between requests.
- `log_audit_action`'s `atomic` default stays `True` (each caller commits);
  multi-statement mutations pass `atomic=False` and commit once at the end.

#### Audit `ip_address`

- Defaults to the live request's `remote_addr` for every call site, so no
  endpoint has to opt in. A caller-supplied `ip_address` still wins, but since
  P1-6 **no production caller does** — every previous override was free text
  that permanently suppressed the real IP.
- Operator-supplied text (a stock-adjustment reason, an upload provenance
  string) belongs in `new_value`, never in `ip_address`.
- With `TRUSTED_PROXY=1` (P1-7) `remote_addr` is `ProxyFix`'s rewrite of the
  rightmost `X-Forwarded-For` entry; with it unset the header is ignored and
  `remote_addr` is the socket address.

#### Audit value bounds

Every text column is bare `TEXT` and no Pydantic model in the app sets
`max_length`, so `log_audit_action` enforces the bounds centrally — current and
future call sites are covered without opting in:

| Column | Bound |
|---|---|
| `action`, `entity_type` | 64 chars |
| `user_id` | 128 chars |
| `old_value`, `new_value` | 2000 chars |
| `ip_address` | 255 chars |

- Control characters are stripped, **including CR**; `\n` and `\t` survive
  because legitimate values (`json.dumps` output) contain them. A NUL byte is
  not merely untidy — PostgreSQL rejects it, so an unfiltered control character
  turns an audit write into a failed statement.
- A truncated value is marked `...[truncated N chars]` so an investigator can
  tell a complete record from a clipped one.
- Two `SALE_UPDATE` paths `json.dumps` a whole request body; a payload above
  ~20 line items exceeds the 2000-char bound and is marked accordingly.
- `entity_type` must stay a **type name** — it is indexed and filtered on.
  Attacker-controlled text (a request path, a filename) goes in `new_value`.
  Server-side paths (`pi_file_path`) are excluded from the snapshot entirely.
- Some actions record **field names rather than values** when the value is
  sensitive. `COMPANY_UPDATE` writes `changed=swift,lc_bank`: `swift`,
  `lc_bank`, `address` and `contact_person` hold bank and contact data, so
  keeping them out of `audit_logs` is deliberate. A company *name* keeps its
  before/after because a name is an identifier, not PII. The field list is
  written **before** any free text so truncation can never clip it.
- **Deletes** capture an identifying summary *before* the `DELETE` (product,
  quantity and unit price; ship date and invoice number; upload filename and
  the number of cascaded rows; username and live-session count). A delete
  destroys its own evidence, so that row is the only surviving record. Each is
  gated on `rowcount`, so a refused or no-op delete leaves no row.
- `AuditLogs.jsx` renders `new_value || old_value`, since delete rows carry
  their evidence in `old_value`.

### Reference Data
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/reason-codes` | Session/Key | All codes |
| GET | `/api/unit-conversions` | Session/Key | All conversions |
| POST | `/api/unit-conversions` | Session/Key | Create conversion |

### Sales Pipeline
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/sales/parse` | Session/Key (15/min) | Parse PI PDF/`.docx` → extraction |
| GET | `/api/sales` | Session/Key | List (filter: stage, q, page, page_size) → `{sales, total}`; rows carry `lc_id` for grouping; includes `shipment_status` |
| POST | `/api/sales/batch` | Session/Key (15/min) | Create **N PIs in ONE transaction** — `{sales:[{sale, items}]}` → `{ids, warnings}`. Any item failure rolls the whole batch back |
| GET | `/api/sales/export` | Session/Key (15/min) | CSV export |
| GET | `/api/sales/summary` | Session/Key | Stage-wise summary |
| GET | `/api/sales/<id>` | Session/Key | Detail: items (incl. `item_no`), `invoices[]`, `shipment_status`, **invoiced** total/balance (`SUM(invoice_items.line_total)`, not the PI) |
| POST | `/api/sales` | Session/Key | Create (header + items; company_id, unit, item_no) |
| PUT | `/api/sales/<id>` | Admin | Full update (header+items+removals) or patch header (incl. comments) |
| DELETE | `/api/sales/<id>` | Admin | Delete |
| POST | `/api/sales/<id>/move` | Admin | Advance stage (with notes) |
| PUT | `/api/sales/<id>/lc` | Admin | Legacy per-PI LC entry. **409 when the PI is already linked to an LC** — linked PIs use the `/api/lcs` endpoints instead (a half-linked PI has no UI to undo) |
| PUT | `/api/sales/<id>/payment` | Admin | Record payment |
| PUT | `/api/sales/<id>/payments/<pid>` | Admin | Edit payment — **scoped to `<id>`** — 404 if `<pid>` belongs to another sale |
| DELETE | `/api/sales/<id>/payments/<pid>` | Admin | Delete payment — **scoped to `<id>`** — 404 if `<pid>` belongs to another sale |
| POST | `/api/sales/<id>/items` | Admin | Add item (product, qty, price, unit) |
| PUT | `/api/sales/<id>/items/<iid>` | Admin | Update item (incl. unit); **scoped to `<id>`** — 404 if the line belongs to another sale or is already gone (empty patch is a legitimate 200 no-op) |
| DELETE | `/api/sales/<id>/items/<iid>` | Admin | Delete item (min 1 item); **scoped to `<id>`** — 404 if the line belongs to another sale or is already gone |
| POST | `/api/sales/<id>/shipments` | Admin | Record shipment (date required) |
| DELETE | `/api/sales/<id>/shipments/<shid>` | Admin | Delete shipment |

### Letters of Credit (LC) — owner of the pipeline journey
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/lcs` | Session/Key | List LCs (optional `company_id`); each row carries `pi_count` |
| POST | `/api/lcs` | Admin (15/min) | Create `{company_id, lc_number, lc_date?, expiry_date?, bank_ref?, notes?}` — `lc_number` trimmed, UNIQUE per company |
| GET | `/api/lcs/<id>` | Session/Key | LC detail + `pis[]` (id, pi_number, stage, client_name, company_id) |
| PUT | `/api/lcs/<id>` | Admin (15/min) | Update `lc_date`/`expiry_date`/`bank_ref`/`notes` only (number + company immutable); mirrors `lc_date` onto linked PIs |
| DELETE | `/api/lcs/<id>` | Admin (15/min) | Delete — **409 while PIs are attached** (locked count + FK backstop) |
| POST | `/api/lcs/<id>/pis` | Admin (15/min) | Attach `{sale_ids:[...]}` — every PI must share the LC's company (400 otherwise); mirrors `lc_number`/`lc_date` onto the PIs; every attached PI still at `pi_issued` advances to `lc_received` (history + `SALE_MOVE` audit, same transaction; later-stage PIs untouched, never downgraded) |
| DELETE | `/api/lcs/<id>/pis/<sale_id>` | Admin (15/min) | Detach one PI (clears `lc_id` + mirrors) |
| POST | `/api/lcs/<id>/move` | Admin (15/min) | Move the LC stage; **one barrier** — crossing into `payment_due` requires **at least one non-void invoice past booking** (`booked`/`shipped`/`paid`) across the LC's child PIs. Crossing into `shipment_ongoing` requires **no invoice** (2026-10-26: invoices are created in Shipment Ongoing). Barrier enforced under row locks; stage propagated to all child PIs in one transaction |
| GET | `/api/lcs/<id>/readiness` | Session/Key | Readiness snapshot — `invoices_ok` is **informational** (shipment entry needs no invoice); `booked_ok` is the Payment Due gate; `recipes_ok`/`produced_ok` stay informational, `detail` carries `booked_count` + `missing_recipes`/`missing_production`, plus the linked `pis` |

### Invoice Lines
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/invoices/<id>/items` | Session/Key | Invoice lines (product, quantity, unit_price, line_total) |
| POST | `/api/invoices/<id>/items` | Admin (15/min) | Add a line `{sale_item_id, quantity}` — **unit_price is resolved server-side from the PI line**, never accepted from the client; quantity must be finite and > 0, and **may equal, exceed, or fall below the PI line quantity** — there is no ceiling (2026-10-25). Recomputes `invoices.amount` from its lines in the same transaction. A **404** carries a `code` so the two causes stay distinguishable without parsing prose: `{"error": ..., "code": "invoice_gone"}` when the invoice disappeared, `{"error": ..., "code": "pi_line_gone"}` when the PI line is no longer on the sale (the client drops/keeps the row accordingly) |
| GET | `/api/invoices/<id>/readiness` | Session/Key | Per-invoice readiness — `status`, `can_book`/`can_ship`/`can_pay`, `book_blocked_reason`, `recipes_ok`, `produced_ok`, `detail` (products needing a recipe or an invoice-linked production run). Production is evaluated for **this invoice only** (a sibling's production neither blocks nor unlocks it). `can_ship` is true only for `booked`; `can_pay` is true **only for `shipped`** (strict order) |

### Sales Invoices
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/sales/<id>/invoices` | Session/Key | List invoices with `amount` + `paid_amount` each, plus `can_book` + `book_blocked_reason` (PBB) so the panel renders Book from server truth. `book_blocked_reason` is `null` whenever the invoice is not bookable, including for closed (`booked`/`shipped`/`paid`) rows — `can_book` alone carries "past booking" |
| GET | `/api/sales/<id>/invoice-lines` | Session/Key (300/min) | One row per PI line: `pi_quantity`, `invoiced_quantity` (summed across every invoice of the sale, **no status filter**), `remaining` (floored at 0, never filtered out; informational — quantities are free relative to the PI), `unit_price`, `invoice_ids`. Top-level `has_line_less_invoices` warns that a legacy header-only invoice makes `remaining` overstate what was billed. 404 on an unknown sale; `{"lines": [], "has_line_less_invoices": false}` for a sale with no PI lines |
| POST | `/api/sales/<id>/invoices` | Admin | Create (`invoice_number` required; `lines:[{sale_item_id, quantity}]` is **required**, ≥ 1 entry; `invoice_date` required — the UI defaults it to today). **No `amount` is accepted** — money is always derived. Quantities are validated (finite and > 0; they may equal, exceed, or fall below the PI line; unit price resolved from the PI) and the header is restated as `SUM(invoice_items.line_total)`. A create is born `planned`, never `paid` (INV-REVISION). 400 on duplicate number; sets sale `shipment_status='production_running'` (COALESCE, never downgrades) |
| POST | `/api/sales/<id>/invoices/batch` | Admin (15/min) | Create N invoices in ONE transaction: body `{invoices:[{invoice_number, invoice_date, lines:[{sale_item_id, quantity}]}]}` — each invoice gets ≥1 line and its own `invoice_date`; headers restated as `SUM(line_total)`; atomic (one commit or full rollback). Quantities are free relative to the PI |
| POST | `/api/sales/<id>/invoices/<iid>/book` | Admin | Book (`approx_ship_date` required) → `booked`; sale → `ship_booked` (advance-only CASE). **Gated on production (PBB):** refused with **409** `invoice not produced yet` unless every product on the invoice has a company recipe and an invoice-linked production run, re-checked at both `planned` and `produced` (fail-closed; `invoices.status` alone is not trusted) |
| POST | `/api/sales/<id>/invoices/<iid>/ship` | Admin | Ship (`actual_ship_date` required) → `shipped` + creates a `shipments` row that carries the invoice's own `invoice_date`. **Requires `booked`:** a `planned`/`produced` invoice is refused with **409** (`InvoiceNotBooked`) before any shipment row is written; `shipped`/`paid` → **409** `invoice already <status>` |
| POST | `/api/sales/<id>/invoices/<iid>/pay` | Admin | Record payment (`payment_amount>0`, `payment_date`) → flips invoice to `paid` when covered. **Requires `shipped`:** a `planned`/`produced`/`booked` invoice is refused with **409** (`InvoiceNotShipped`); `paid` → **409** `invoice already paid`. Partial payments keep the invoice `shipped`. Paying the last open invoice completes the sale when it is in Payment Due (`_sync_sale_completion`) |
| GET | `/api/sales/<id>/completion` | Session/Key | `status` none/partial/full + `paid`/`total` counts; `total_amount`/`paid_amount` when ≥ 1 invoice |

### Unit Conversions
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/unit-conversions` | Session/Key | List |
| POST | `/api/unit-conversions` | Session/Key | Create (from, to, factor>0) |

### SPA + Static
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | Public | `index.html` |
| GET | `/<path>` | Public | Static file or `index.html` (SPA fallback) |

### Invoice Lifecycle & Shipment Sub-steps
- **Per-invoice statuses:** `planned → produced → booked → shipped → paid`, tracked independently — one sale may hold several invoices at different steps.
- **Strict order (2026-10-26):** Ship requires `booked`; Pay requires `shipped`. A skipped step is a **409**, not a silent jump, and a refused transition writes nothing (no shipment row, no payment row). `paid` is terminal.
- **Auto-advances (all advance-only; `paid` is terminal — book/ship/produce can never downgrade it):**
  - invoice create → sale `shipment_status = 'production_running'` (`COALESCE`, never overwrites a later value)
  - production run linked to invoices → those invoices `'produced'` (guarded `status <> 'paid'`) + sale `'production_done'` (CASE: only from NULL/`production_running`)
  - book (requires `approx_ship_date`) → `'booked'`; sale → `'ship_booked'` (advance-only CASE). **Production-gated (PBB):** an invoice whose products are not all produced (recipe + invoice-linked run) is refused with **409**, re-checked at `planned` and `produced`
  - ship (requires `actual_ship_date`) → `'shipped'` + `shipments` row (carrying the invoice's `invoice_date`); refused with **409** unless the invoice is `booked`
  - pay → `'paid'` when `paid_amount >= amount`; refused with **409** unless the invoice is `shipped`; a create-time `amount = 0` is **no longer** born paid (INV-REVISION — only a payment record sets `paid`); `amount IS NULL` (legacy) → any payment marks it paid. Partial payments report the actual `shipped` status
- **Sale `shipment_status` sub-steps** (surfaced as SaleCard badges in `shipment_ongoing`): `production_running` → `production_done` → `ship_booked`.
- **Stage hook:** `lc_received → shipment_ongoing` sets `shipment_status = 'production_running'`; the SaleCard badge then shows the shipment sub-step. The move needs no invoice and the UI no longer probes for one or warns after moving.
- **LC Stage Gates:**
  - **Shipment entry (`lc_received → shipment_ongoing`):** no invoice required (2026-10-26). The LC advances with zero invoices; invoices are created once it is in Shipment Ongoing, from the PI's products, unit price and chosen quantities.
  - **Barrier 2 (any earlier stage → `payment_due`):** requires **at least one non-void invoice past booking** (`booked`/`shipped`/`paid`) across the LC's child PIs (`booked_invoice_readiness`). A zero-invoice sale/LC cannot reach Payment Due. Unready sibling invoices stay behind and do not block the ready one. The refusal is `SaleNotReadyPastShipment` / `LcNotReadyPastShipment`, **409**.
  - **Completion (`payment_due → completed`):** invoice-defined (see below). A manual LC completion is refused (`LcNotReadyForCompletion`, **409**) while any child sale is unsettled. Automatic LC completion is a direct write in `_sync_lc_completion`: an LC in `payment_due` closes when every child sale is `completed`, and a `completed` LC reverts to `payment_due` when any child reopens. Both crossings log `LC_MOVE`.
  - **Readiness endpoint:** `GET /api/lcs/<id>/readiness` returns `{invoices_ok, booked_ok, recipes_ok, produced_ok, detail{booked_count, missing_recipes, missing_production}}` so the UI can render the correct form before the user attempts a move. `invoices_ok`/`recipes_ok`/`produced_ok` are informational; `booked_ok` is the gate.
- **New invariant:** a sale cannot advance to `lc_received` (or beyond) while its `lc_id` is NULL — the move is refused until an LC is attached.

### Invoice-defined completion (2026-10-26)
- **Predicate:** a sale completes iff it has **≥ 1 invoice** and **every invoice is `paid`** (`invoice_settlement.settled`). `all_paid` alone is vacuously true with zero invoices, which is why the gate asks `settled`.
- **Money:** the compared total is `SUM(invoice_items.line_total)` (`get_sale_invoiced_total`), not the PI proposal. `get_sale_pi_total` keeps the PI as an offer for the report's per-line fallback and the maturity dedupe fallback.
- **Entry points:** `move_sale_to_stage` refuses an unsettled `→ completed` move with `SaleNotReadyForCompletion` (**409**; "no invoices" or "N invoice(s) still open"). Entering `payment_due` re-evaluates an already-settled sale and completes it. `mark_invoice_paid`, payment edits/deletes, and `void_invoice` call `_sync_sale_completion`, which only acts from `payment_due`/`completed` — a payment never drags the stage forward on its own.
- **Reopen:** a payment edit/delete or a void that leaves an invoice unpaid moves a `completed` sale back to `payment_due`. Voiding one of several paid invoices keeps the sale completed while a paid invoice remains; voiding the last one reopens it.
- **Sale header:** `get_sale_by_id()["invoice_total"]` and the payment paths' balances report the invoiced total.

### Readiness Endpoint
- `GET /api/lcs/<id>/readiness` — Session/Key; returns `{lc_id, stage, invoices_ok, booked_ok, recipes_ok, produced_ok, detail:{booked_count, missing_recipes, missing_production, invoice_count, sale_count}, pis}` so the UI can render the correct barrier form before the user attempts a move. `invoices_ok` is informational — shipment entry no longer requires an invoice; `booked_ok` is the Payment Due gate.

## 5. Authentication & Authorization

### Session (Humans)
- Cookie: `raas_session` (HttpOnly, SameSite=Lax, Secure if `COOKIE_SECURE!=0`)
- TTL: **1 hour** (`SESSION_TTL_HOURS`), SHA-256 token hash in DB.
  `expires_at` is written by Python (`strftime("%Y-%m-%d %H:%M:%S")`) and
  compared in SQL against `to_char(clock_timestamp(), 'YYYY-MM-DD HH24:MI:SS')`
  (P1-18). Both sides must stay byte-identical: until P1-18 the SQL side used
  the strftime pattern `'YYYY-MM-DD HH:MM:SS'`, where `to_char` read `MM` as
  MONTH and `HH` as 12-hour, so the effective TTL was ~10 hours in some
  minutes of the day and under an hour in others.
- Auto-cleanup expired on login

### API Keys (Scripts)
- Format: `ck_live_<32-char>` (shown once at creation)
- Stored: SHA-256 hash (`key_hash`)
- Features: expiry, IP allowlist (CIDR), revocation, per-key rate limit (300/min), audit identity
- Header: `X-API-Key`

### Gates (`flask_app.py:_gate_api`)
- `/api/*` → identity required (except `_PUBLIC_API`: login, setup, logout, status, cron, forgot/reset-password)
- `OPTIONS` → pass through (CORS preflight)
- `X-API-Token` (legacy) → 401 + audit
- Admin paths (`/api/users`, `/api/keys`) → human admin only
- API keys → per-key rate limit (300/min, DB-backed)
- `must_change_password==1` human sessions → 403 `password change required` on all `/api/*` except `PUT /api/auth/password`, `GET /api/auth/me`, `POST /api/auth/logout` (API keys exempt)

### Write Role Model (P1-A)

`_gate_api` enforces the **admin role only** for `/api/users` and `/api/keys`. Every
other route's privilege comes from its own decorator, so write authorization used to be
**opt-in**: a mutating route with no decorator was reachable by any signed-in `user`.

Every mutating route now **declares** one of two classes:

| Class | Decorator | Who may call |
|---|---|---|
| **admin** | `@admin_required` | human admin only. **API keys never pass** (`type` must be `human`). |
| **operator** | `@operator_or_admin_required` | any authenticated identity — human admin, human `user`, **or API key** |

The operator decorator is **declarative, not restrictive**. It exists so a route's class
is stated rather than implied by an omission, and so a future tightening has one place
to land. It deliberately does *not* exclude API keys:
`tests/test_stock_audit.py::test_unit_conversion_audit_records_the_api_key_actor`
asserts an `X-API-Key` caller reaches `POST /api/unit-conversions` and is audited as
`api-key:<name>`. Anonymous callers are still refused — `_gate_api` returns 401 first,
and the decorator's own `None`-identity guard is a backstop.

`_ADMIN_PATHS` is retained as **defence in depth** — deliberately *not* load-bearing.
Every mutating route carries its own role decorator, and so do the two reads that
previously depended on the prefix alone (`GET /api/users`, `GET /api/keys`), so
removing the tuple must change nothing.
`tests/test_authorization_roles.py` reads the declared class from `app.url_map`
(each decorator stamps `_raas_role` on its wrapper) rather than parsing source, so a
reformat, a new module, `add_url_rule`, or a Blueprint cannot make the check drift
from what Flask actually routes.

The same argument applies to **rate limits**, which is why P1-B added `@limited()`:
Flask-Limiter holds no per-route metadata on the view, so `_raas_limit` is stamped
and read the same way. See [Rate Limiting](#rate-limiting-flask-limiter).

#### Operator routes (11)

| Method | Path |
|---|---|
| POST | `/api/sales` |
| POST | `/api/sales/batch` |
| POST | `/api/sales/parse` |
| POST | `/api/recipes` |
| POST | `/api/recipes/<path:name>/items` |
| PUT | `/api/recipes/<path:name>` |
| PUT | `/api/recipes/<path:name>/items/<chem>` |
| POST | `/api/upload` |
| POST | `/api/unit-conversions` |
| POST | `/api/reports/generate` |
| POST | `/api/reports/export` |
| POST | `/api/notifications/read` |

`POST /api/sales/batch` was demoted from admin to operator: identical intent and blast
radius to `POST /api/sales` (both create a PI at `pi_issued`; neither can reach money
without an admin action later — attach PI → LC → create invoice → book → pay), and the
commercial report is invoice-driven so an uninvoiced PI contributes 0 to gross sales.

#### Open decisions (deliberately unchanged, not yet ratified)

| Asymmetry | Current | Status |
|---|---|---|
| Recipe delete vs edit | operator may `PUT` yield/water %, may **not** `DELETE` (cascades into `production_run_items`) | kept admin; needs a decision |
| Appending sale items | `POST /api/sales` (operator) inserts `sale_items` inline, but `POST /api/sales/<id>/items` is admin | kept admin; `tests/test_consistency_lock.py:94` pins the 403 |

### Rate Limiting (Flask-Limiter)
- Default: 300/min per identity (user/key/IP) — applies to **every** route, so
  reads and low-risk writes share `READ_LIMIT_PER_MINUTE = 300`.
- **P1-B — write budget: `WRITE_LIMIT_PER_MINUTE = 15`.** P1-A gave `user` the
  PI/recipe/report write surface, and a trust change without a volume bound is
  not a control: those writes ran on the 300/min read default, so one operator
  request could amplify into thousands of rows. Every mutating operator route
  and the whole invoice lifecycle now declare 15/min explicitly.
- Declared via `@limited()`, never `@limiter.limit("15 per minute")`. Flask-Limiter
  keys limits by `module.__qualname__` in an internal manager and keeps **no
  per-route metadata on the view**, so a test asserting the limit by reading source
  would be fail-open exactly as P1-A's role test was. `limited()` applies the limit
  *and* stamps `_raas_limit`, so the policy test walks `app.url_map` →
  `view_functions` → the `__wrapped__` chain. `functools.wraps` copies both stamps onto
  the registered view, so for every route today the walk is a **no-op**; it is kept because
  it also covers either decorator order and a partial `@wraps(inner, updated=())` (which
  creates the chain link but copies no custom attributes).
- `test_volume_bounded_list_matches_the_stamped_routes` mirrors P1-A's role drift
  guard, but it is **one-sided**: it catches a route that carries `@limited()` while
  missing from the list. It cannot catch a write that carries no limit at all — ~27
  admin mutating routes (including `DELETE /api/sales/<id>`) are still on the 300/min
  default. That gap is tracked in `docs/plan.md`, not closed here.
- Tier 2 (15/min): `/api/upload`, `/api/uploads/*/approve|apply|export`,
  `/api/reports/generate|export`, `/api/reports/live{,/filtered,/summary,/export}`,
  `/api/sales`, `/api/sales/parse`, `/api/sales/batch`, `/api/sales/export`,
  `/api/recipes`(+`/<name>`, `/items`, `/items/<chem>`, `/produce`),
  `/api/unit-conversions`, `/api/notifications/read`, `/api/lcs`(+`/<id>`, `/pis`,
  `/pis/<sale_id>`, `/move`), `PUT /api/sales/<id>`, and the invoice
  lifecycle (`.../invoices`, `.../invoices/batch`, `/api/invoices/<id>/items`,
  `.../book`, `.../ship`, `.../pay`, `.../` DELETE). `invoices/batch` already had
  15/min; the lifecycle around it did not, and the inconsistency was the finding.
- `POST /api/notifications/read` is included: the `NotificationContext` poll is
  `POLL_MS = 30_000` (2/min) and drives a **GET**; read-marking is user-initiated
  from the bell, so 15/min has ample headroom.
- Enforcement lives in the view wrapper (inside the role decorator), so an
  unauthenticated flood gets 401 without consuming budget. Consequence: 429 is
  only observable with valid credentials on these routes. Login keeps its own
  DB-backed throttle.
- **Rate limit bounds requests, not rows.** A 15/min budget still permits 15
  arbitrarily large bodies, so the list fields that fan out into per-row inserts
  are bounded too (P1-B):
  | Field | Cap | Why |
  |---|---|---|
  | `SaleCreateIn.items` | 200 | `POST /api/sales` — the route P1-A opened to operators, and the operator backstop deliberately admits **API keys**, so a leaked low-trust credential reaches this |
  | `SaleFullUpdateIn.items` / `.removedIds` | 200 / 200 | `PUT /api/sales/<id>` replaces the item set and deletes one row per `removedIds`, inside a transaction holding the money-bearing `sales`/`sale_items` locks |
  | `SaleBatchIn.sales` | 50 | N sales × M items in one transaction; `min_length=1` kept. `POST /api/sales` is the path for anything larger |
  | `InvoiceBatchCreateIn.invoices` / `InvoiceBatchEntryIn.lines` | 50 / 200 | the financial write path; amplification is quadratic, so **both** factors are capped |
| `InvoiceCreateIn.lines` | 200 | single-invoice twin of the batch line list — one `SELECT … FOR UPDATE` per line inside the sale-row lock; capped in the same commit so the batch route is not bounded while its sibling is not |
| `LcAttachIn.sale_ids` | 200 | `/api/lcs/<id>/pis`: ~5 sequential round trips per id inside the LC lock, and each `pi_issued` sale is advanced to `lc_received` — a pipeline mutation, not just a link. `sorted(set(...))` de-duplicates but bounds nothing |
  | `ProductionRunCreateIn.sale_ids` / `.invoice_ids` | 200 / 200 | `/produce`: one sequential round trip per id inside a transaction holding a pool slot (`recipes.py` `_owned_sale_ids`, per-invoice SELECT/INSERT/`FOR UPDATE`) |
  | `ProductionRunCreateIn.recipes` | 50 | each row is a production run **plus its whole BOM** — `create_production_run` iterates `list_recipe_items` and does an `update_stock` + `production_run_items` insert per ingredient. 50 recipes x a 20-chemical BOM is ~1 000 ingredients in one transaction, and **`MAX_BATCH_ROWS` does not cover this route** (only `SaleBatchIn`/`InvoiceBatchCreateIn`) — tracked in `docs/plan.md` |
  | `/api/reports/{generate,export}` `recipes` | `MAX_REPORT_RECIPES = 200` | read from the raw body (no Pydantic model), so enforced inline → 400 `{error, details:[{field, message}]}`; each entry becomes a stock query, and `export` writes a file. Also rejects a non-list body before `len()` |
- **Product caps.** Per-factor caps bound the factors, not their product, and
  `SaleCreateIn` nests inside `SaleBatchIn` — so 50 sales × 200 items is still ~10 000
  sequential inserts in one transaction, from ~700 KB of body, well inside the Vercel
  limit. `MAX_BATCH_ROWS = 2000` caps the total instead of shrinking the factors, via a
  `field_validator` on the list field so the error still carries a named field in
  `{error, details:[{field, message}]}`:
  | Model | Counts | Error |
  |---|---|---|
  | `SaleBatchIn.sales` | `sum(len(sale.items))` | `at most 2000 total items per batch (got N); send fewer, larger PIs or use POST /api/sales` |
  | `InvoiceBatchCreateIn.invoices` | `sum(len(invoice.lines))` | `at most 2000 total lines per batch (got N)` |
  A realistic batch (20 PIs × 50 items = 1000) is unaffected.
  `MAX_CONTENT_LENGTH` (50MB local / 4.5MB on Vercel) remains the outer body bound.
- Tier 1: Login throttle (5 fails/**10 min** per username+IP, DB-backed).
  The window is a real 10-minute window since P1-18. Before it, `login_attempts.
  attempted_at` was written by `to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')` — `MM`
  is MONTH and `HH` is 12-hour in `to_char` — so no row ever recorded a minute
  and the window degenerated into `second_row >= second_now`: a fresh failure
  could be judged outside the window the moment the clock ticked past its
  recorded second. That was the intermittent `401x6` failure in
  `tests/test_proxy_trust.py`. `check_api_key_rate_limit` (300/min) had the
  same defect and silently shed hits partway through every minute.
- Storage: `memory://` unless `REDIS_URL` is set — counters are **per process**. On a
  multi-process/serverless host (Vercel, waitress with >1 worker) each process counts
  independently, so a client can exceed the tabled limits by a factor of N processes.
  Tier 1 (login) is DB-backed and unaffected. Set `REDIS_URL` in production to share
  counters across processes.
- Test bypass: `RAAS_RATE_LIMITS=off`

### Cron Jobs (Vercel)
- `POST /api/cron/maturity-check` — daily 09:00 UTC; checks sales with `maturity_date <= today` and unpaid; creates `maturity_due` (warning) and `maturity_escalated` (critical, at 7+ days overdue) notifications with dedupe; clears dedupe on full payment. Auth: `Authorization: Bearer <CRON_SECRET>`. Paid-vs-total compare uses two independent correlated subqueries (`sale_payments` and `sale_items` separately) — never a single JOIN, which inflates both sums via row fan-out when a sale has multiple payments × multiple items. Both sides `ROUND(::numeric, 2)` before comparing: a `REAL` sum of non-binary-exact amounts (8.2 stores as 8.199999809265137) can land a hair below the 2dp total and spuriously notify an exactly-paid sale.

### Notifications
- In-app notifications with dedupe (`notifications.py:notify`)
- Types: `stock_out` (critical), `stock_low` (warning), `sale_payment_due` (warning), `sale_completed` (info), `maturity_due` (warning), `maturity_escalated` (critical)
- Role scopes: `all` or `admin`
- Dedupe keys cleared when condition resolves (stock recovery, payment recorded, full payment clears maturity keys)

### Setup Token
- Single-use, 60-min TTL, printed to console on first need
- Stored: `sha256(token):timestamp` in `app_settings`
- Constant-time compare (`hmac.compare_digest`)
- Auto-burn on use; `DISABLE_SETUP=true` disables entirely

## 6. Error Handling Strategy

| Layer | Strategy |
|---|---|
| **Validation** | Pydantic models (`SaleCreateIn`, `SaleItemIn`, etc.) → 400 `{error, details:[{field, message}]}` |
| **Service refusals** | The status comes from the exception's **type**, never its message. `raas_tracker/_exceptions.py` holds the hierarchy — `ServiceError` (base, 400) → `LifecycleConflict` (409) / `ResourceMissing` (404) → one leaf per condition — and `status_for(exc)` is the single entry point; a plain `ValueError` falls back to 400. Every leaf is a `ValueError`, so an un-migrated `except ValueError` still catches it. See §6.2 |
| **DB Errors** | `psycopg.IntegrityError` → `ValueError` → 400/409; `OperationalError` → 500 |
| **Auth** | Missing/invalid → 401/403 JSON; login throttle → 429 + `Retry-After` |
| **Rate Limit** | 429 + `Retry-After` header; JSON `{error: "rate limit exceeded, slow down"}` |
| **File Upload** | Max 50MB by default (`MAX_CONTENT_LENGTH`, override with `MAX_CONTENT_LENGTH_MB`); allowed ext {.pdf,.xlsx,.xls}; **one canonical filename**, computed once per request by `_safe_upload_filename` (`raas_tracker/uploads.py`) and reused for the extension gate, the stored `uploads.filename`, the `UPLOAD_CREATE` audit row and the JSON response — it strips control chars, path traversal, bidi controls (U+202E/U+200E/U+200F), leading dots and Windows reserved device names, and **preserves non-ASCII** (werkzeug's `secure_filename` is deliberately not used: it flattened a legitimate Arabic name to a bare `xlsx`, so the gate rejected a real file). The on-disk name is separate and stays `uuid4().hex + ext`; parse failure → 400 + file cleanup |
| **PI Parse** | Only `.pdf`/`.docx` accepted; legacy `.doc` rejected with guidance |
| **Global** | `@app.errorhandler(404)` → 404 `{"error":"not found"}`; `@app.errorhandler(405)` → 405 `{"error":"method not allowed"}` (a wrong-method /api request must never fall into the catch-all and masquerade as a 500); `@app.errorhandler(400)` → 400 `{"error":"Invalid request body"}` (Werkzeug `BadRequest` — a body that will not decode; see §6.1); `@app.errorhandler(415)` → 415 `{"error":"unsupported media type (expected application/json)"}` (see §6.3); `@app.errorhandler(500)` + `@app.errorhandler(Exception)` → 500 `{"error":"internal server error"}` (log full trace). Registered **by status code, not by exception class** — the canonical statement of why is the `@app.errorhandler(415)` docstring at `flask_app.py:1012`, deliberately not restated here |
| **413** | File too large → 413 `{"error":"file too large (max N MB)"}` where N = `MAX_CONTENT_LENGTH_MB` (default 50). On Vercel the platform 4.5MB body limit fires at the edge first, so >4.5MB gets Vercel's own 413 page, not this JSON — set `MAX_CONTENT_LENGTH_MB=4.5` to align the app cap with the platform |

### 6.1 Numeric integrity (P2-A) — non-finite values cannot enter

`NaN` and `Infinity` are not display bugs: `inf >= invoice_total` is True, so a
payment written as `Infinity` completes a sale and marks an invoice paid without a
cent arriving, and the value is unrecoverable once it lands in a `REAL` column.
Two independent walls, both app-wide:

| Wall | Where | What it rejects |
|---|---|---|
| **JSON parser** | `flask_app.py:_FiniteJSONProvider`, installed as `app.json` | The **bare** literals `NaN`, `Infinity`, `-Infinity`, which Python's `json` accepts by default. Wired as an attribute assignment on a plain `Flask` app (not a `json_provider` setter, which Flask 3.x removed). A body-parse failure raises `ValueError` → `BadRequest` → the 400 handler above, before any route body runs |
| **Schema** | `flask_app.py:_FiniteModel` (`allow_inf_nan=False` in `model_config`), inherited by every money/quantity write model | Every float field of those models. A **quoted** `"Infinity"` is an ordinary string to the parser and Pydantic v2 lax mode coerces `str → float` via `float()`, which succeeds — so this wall is load-bearing, not defence-in-depth |

**Neither wall is complete on its own, and that is deliberate:**

- `1e309` is *valid JSON* that overflows to `inf`. `parse_constant` correctly never
  fires on it; only the schema (or a route guard) refuses it.
- Routes with **no Pydantic model** behind them get neither. `PUT /api/chemicals/reorder`
  and `PUT /api/recipes/<name>` read numbers straight from the raw dict, so they
  coerce through `_req_float` (`flask_app.py:_req_float`), which carries its own
  `math.isfinite` check. Any new raw-dict numeric field must do the same.
- The serializer still emits `Infinity` on the way **out** — only the parse boundary
  is hardened. Unreachable through guarded surfaces today; a column poisoned by a
  future path would render as invalid JSON to clients.

Phase 2-B adds the third wall (`math.isfinite` in the service layer), which is what
covers non-HTTP callers that bypass the parser entirely.

### 6.2 A refusal's status is its type, not its wording (Phase 4-A)

`flask_app._state_error_status`, `_invoice_item_error` and `_lc_attach_error`
each decided an HTTP status by matching the **words** of a service error
message (`"invoice already"` → 409, `"exceeds"` → 409, `"lc not found"` → 404,
…). Message wording is not a contract: re-typing "invoice not produced yet" as
"this invoice has not been produced" silently downgraded a 409 to a 400, and no
test asserted the status, so the drift was invisible. All of those mappers are
deleted. A route now calls `status_for(e)`, which reads the **type**:

| Exception | Status | Condition |
|---|---|---|
| `ValueError` (plain) | 400 | malformed input — still the honest answer, and deliberately untyped |
| `ServiceError` | 400 | base class; only this hierarchy carries a status |
| `LifecycleConflict` | 409 | valid request, conflicts with current state |
| `ResourceMissing` | 404 | no such resource, **or not this caller's** — the two are deliberately indistinguishable |
| `sales.InvoicedLineConflict` | 409 | a PI line `invoice_items` already point at cannot be re-based or deleted |

**Superseded (2026-10-25) — the 5% over-invoice tolerance was removed
entirely.** `check_invoice_tolerance`, `ToleranceExceeded` and the payload's
`ceiling` no longer exist. An invoice quantity may equal, exceed, or fall below
the PI line quantity; the only rule is finite and > 0 (over-invoicing is
managed manually). What remains from the change above is the typed-status rule
itself: routes call `status_for(e)` and never match message words. No
invoice-write route returns 409 for a quantity.

### 6.3 The outermost boundary: edge headers + 415 (Phase 4-B)

Two holes on the **unauthenticated** surface, both found by dumping live state
rather than reading this document.

#### 6.3.1 `vercel.json` had no `headers` block at all

Its entire content was `env`, `buildCommand`, `rewrites`, `functions`, `crons`.
Every SPA route is rewritten to `/index.html` at the edge and those responses
shipped with no `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
`Strict-Transport-Security` or `Permissions-Policy`. `TRUSTED_PROXY: "1"` was
already in `env`, so the app already assumed Vercel was the sole ingress —
trust was configured and response hardening was not. The block now carries:

| Header | Value | Why this value |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | The only defence against MIME confusion. `index.html` references hashed bundles under `/assets/`; a browser that is allowed to sniff could execute an attacker-named file as script |
| `X-Frame-Options` | `DENY` | Clickjacking. `DENY`, not `SAMEORIGIN`: nothing embeds this app, including itself. No iframe/`<object>`/`<embed>` exists anywhere under `src/` |
| `X-XSS-Protection` | `0` | Deliberately **disables** the legacy reflected-XSS filter rather than enabling it. That filter was itself a source of information-disclosure bugs, and this app's control is the CSP below, not a heuristic body rewriter. `flask_app.py` sends the same value so the layers agree |
| `Referrer-Policy` | `no-referrer` | Password-reset links carry a token in the URL (`/reset?token=…`), so the question is who ever receives that URL as a `Referer`. `strict-origin-when-cross-origin` answered only the cross-origin half: it still sends the **full** URL on same-origin requests, and `ResetPassword.jsx` reads `?token=` off the URL and then fetches `/api/auth/reset-password` same-origin — so the reset token lands in this app's own access log. `no-referrer` closes that too, and it costs nothing measurable here: nothing under `flask_app.py` or `src/` reads `document.referrer` or `Referer`, there is no analytics, no cross-origin asset and no external origin at all (next rows), so the cross-origin guarantee had no consumer to protect. `flask_app.py` sets the same value so the layers agree |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | One year is the OWASP floor, and HSTS only protects once browsers have cached it. **This is the only HSTS the production host gets:** Flask sends its own gated on `FORCE_HTTPS == "1"`, and `FORCE_HTTPS` is absent from `env` here — so the edge header is the whole of the protection rather than a duplicate of it, which is the strongest reason it matters. `includeSubDomains` is a no-op on the current host (Vercel preview deployments get `*.vercel.app` hostnames, not subdomains of this host) and is kept for the custom-domain case, with the trap recorded rather than left to be discovered: the same block copied onto an apex domain would force HTTPS on every sibling subdomain, and HSTS is only reversible by waiting out the browser cache. **`preload` is deliberately absent** — the domain is not on the browser preload list, and shipping the token would claim a commitment nobody made |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` | Each is denied because no code path uses it: no `getUserMedia`/`mediaDevices` (camera, microphone) and no `navigator.geolocation` anywhere under `raas-tracker-frontend/src/`. Denying an unused capability removes a permission-prompt surface — and a prompt the operator could be socially engineered into granting — at no cost |

**Which responses get them.** One rule, `source: "/((?!api/).*)"` — everything
that is not `/api/…`:

- It is **not** a copy of the rewrite's pattern. `rewrites` is
  `/((?!api/|.*\.).*)`: it excludes dotted paths because `/assets/*.js` must be
  served as a file, not rewritten to `index.html`. That is right for routing and
  wrong for headers — a hashed bundle needs `nosniff` exactly as much as
  `index.html` does — so the header rule deliberately drops the dotted-path
  exclusion and keys only on the `/api/` boundary. Copying the rewrite wholesale
  would have left every static asset unprotected.
- **`/api/*` is excluded, deliberately.** API responses are JSON, already covered
  by the app's own auth, rate-limit and audit controls; stamping document headers
  on them is at best noise and at worst a future CSP applied to a JSON body.
  Every `/api/…` route in the live `url_map`, plus the scheduled cron path, is
  asserted uncovered by `tests/test_edge_hardening.py` — read from `url_map`, so
  a route added later is covered automatically. Every non-API rule matches, which
  is the positive half of the same invariant (a pattern that accidentally matched
  nothing would satisfy the negative test alone).
- It is correct under either edge ordering: matched against the request path
  (SPA routes keep theirs, `/api/…` never matches) or against the rewritten
  destination (`/index.html` matches, `/api/…` is never rewritten and so never
  matches).

#### 6.3.2 Content-Security-Policy — hash-allowlisted, and now on the responses that needed it

The CSP ships **at the edge**, copied verbatim from `flask_app.py`'s
`after_request`. The policy is:

```
default-src 'self'; script-src 'self' 'sha256-i0ed3E9hYdck4GBiAzbjd2pnheK6j8fl472P8iayw1Q=';
style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self';
connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

**Why `script-src` is not `'unsafe-inline'`.** The SPA keeps exactly one inline
`<script>` — the theme bootstrap in `raas-tracker-frontend/index.html`, which Vite
copies into the build verbatim — and a bare `'self'` blocks inline scripts, which
is precisely how dark mode was silently broken in production before this policy
existed. The fix is a **hash allowlist**: `'unsafe-inline'` also disables the hash
mechanism outright, so it would be strictly weaker than what ships. The digest
above is recomputed from `index.html` on every test run, so editing the bootstrap
without updating both copies fails the gate rather than silently dropping the
script.

**Why the deferral that preceded this was wrong.** The earlier reasoning was that
the policy could not be shipped because Vercel "cannot hash or nonce" an inline
script — "the header is static, with no per-request nonce to bind". The premise
is false: a `sha256-…` source expression is a **compile-time constant**, which is
exactly what a static header expresses, and a nonce is not involved and never
was. The cost of the error was concrete rather than theoretical — Flask had
shipped this policy all along, but the edge serves the SPA document and
`/assets/*`, so the policy had never once applied to the responses it exists to
protect.

**`style-src` keeps `'unsafe-inline'`, deliberately — but not for the reason it
looks like.** The SPA has three inline `style={{…}}` props
(`MismatchSummary.jsx:142` for the mismatch bar's width,
`StockDistributionChart.jsx:16` and `ReconciliationChart.jsx:22` for the two chart
legend swatches), and it is tempting to assume they are what requires the
exception. **They are not.** React 19 writes those through the CSSOM
(`style.setProperty` / `style[name] =`), and CSP does not govern CSSOM writes —
they apply normally under a strict `style-src`. The actual consumer is
**`sonner`**, which injects a `<style>` element at module scope
(`__insertCSS` → `document.createElement("style")` → `head.appendChild`) carrying
the toast stylesheet, and an injected `<style>` element *is* blocked by a strict
`style-src`. Dropping the exception would silently strip the styling from every
toast in the app.

That failure mode is worth stating plainly: it would be **invisible**, because the
three components that look responsible would go on rendering correctly, and the
guard that would catch it is the one being removed. So removing `'unsafe-inline'`
needs sonner's stylesheet imported through the app's own build (rather than
injected at runtime) — not a Tailwind migration of those three props, which would
achieve nothing. Until then the exception is asserted by test so it cannot be
tightened by accident.

**No external origin at all**, which is what makes the remaining directives free:
grep for `https?://` across `src/` returns two hits, both inside a test file. No
CDN, no webfont host, no third-party API — so `connect-src`/`font-src`/`img-src`
are all `'self'`. `img-src` additionally carries `data:`, which nothing currently
needs: the icons are `lucide-react` inline `<svg>`, which `img-src` does not
govern, and the bundle contains no `data:` URI. It is kept because Vite inlines
assets under 4 KB as `data:` URLs by default, so without it the first small icon
or logo added would break in production only.

**Keeping the two copies honest.** `vercel.json` is static JSON and cannot import
`flask_app.py`, so the policy necessarily exists twice. Three tests hold that
duplication:

| Guard | What it prevents |
|---|---|
| `test_the_edge_csp_is_byte_identical_to_the_one_flask_sends` | The two copies diverging into one policy that means two different things depending on which layer answered |
| `test_content_security_policy_is_shipped_with_the_theme_hash` | The digest drifting from the bootstrap script — which would silently drop the script and dark mode with it |
| `test_style_src_keeps_the_inline_exception_the_spa_still_needs` | Someone "tightening" `style-src` while `sonner` still injects its stylesheet at runtime |

`tests/test_features.py` applies the digest guard independently to the Flask copy
(`test_csp_allows_the_inline_theme_script`); the other two are specific to the
edge config, which is where the duplication lives.

**Residual, recorded rather than glossed.** There is no staging deployment, so
enforcement is proven by digest and by review rather than observed live: a policy
that blanked the deployed SPA would still be green here. What *was* verified is
narrower than "the built output works" — the theme script's digest matches the
CSP, and that script is byte-identical across `raas-tracker-frontend/index.html`
(the source), `raas-tracker-frontend/dist/index.html` (the build output),
`public/index.html` and `react_frontend/index.html`. The digest guard reads the
**source** file, which is only sound because Vite copies that script verbatim
rather than transforming it; the four-way identity above is what makes that
assumption checkable rather than assumed. That is the strongest substitute
available without a staging environment.

#### 6.3.3 415 answered 500 for a permanently unfixable client bug

Werkzeug raises `UnsupportedMediaType` from `request.get_json()` when the body is
not declared as JSON, and every JSON route here reads its body that way
(`request.get_json() or {}`). No handler was registered for 415, so Flask's
lookup walked to the catch-all `Exception` handler: a client that sent
`Content-Type: text/plain` was told *"internal server error"* — "retry" for a
fault that is permanently its own and that no retry can fix.

`@app.errorhandler(415)` is registered **by code**, beside the 413 handler, and
answers `{"error": "unsupported media type (expected application/json)"}`.

**Reachable unauthenticated — and that is the improvement.** An earlier version
of this document said "reachable only by an **authenticated** caller". That is
wrong, and it understated what changed. Three routes in `_PUBLIC_API` —
`/api/auth/login`, `/api/auth/forgot-password`, `/api/auth/reset-password` —
take `_gate_api`'s `None` path, so their route bodies call `request.get_json()`
before any credential exists. All three used to fall through to the catch-all
handler and answer **500**; they now answer 415. Three unauthenticated 500s
removed, on exactly the surface this phase is named for. Pinned live by
`test_a_public_route_answers_415_unauthenticated`.

On registering by code rather than by exception class, the canonical statement is
the handler's own docstring in `flask_app.py:1012`. It is deliberately not
restated here: a rationale copied into four places is four places for it to
drift, so this document and `docs/plan.md` cite it instead. Live proof that the
status is 415 and not 500, that nothing is written, and that 400/404/405/413
still behave: `tests/test_edge_hardening.py`.

#### 6.3.4 The two header layers, and where they diverge

The edge (`vercel.json`) and Flask (`_set_security_headers`) both set security
headers, and they are **not** the same set. The divergence is deliberate but
load-bearing, so it is recorded rather than left to be discovered:

- **The edge is the only layer that reaches the SPA.** Vercel serves
  `index.html` and `/assets/*` itself, so Flask's `after_request` never runs for
  them. This is the reason 4-B existed.
- **Flask is the only layer that reaches `/api/*`**, which the edge rule excludes
  by design. That exclusion is lossless *only* while Flask owns `/api/*` and its
  `after_request` sets these headers. Move the API off Flask, or serve any part
  of it as a static file, and the exclusion becomes a hole rather than an
  optimisation.
- **HSTS is edge-only in production.** Flask's copy is gated on
  `FORCE_HTTPS == "1"`, which is not set in `vercel.json`'s `env` (see §8), so on
  the production host the edge header is the entire HSTS protection rather than a
  duplicate of Flask's.

Collapsing this to one authoritative layer is not available: static JSON cannot
import Python, and Flask cannot set headers on a response it never served. The
duplication is therefore held exact by the byte-identity test in §6.3.2 rather
than removed.

## 7. Testing Strategy

### Backend (pytest)
- **Isolation**: `conftest.py` — per-test `TRUNCATE ... RESTART IDENTITY CASCADE` (excludes `app_settings` for schema-sig fast path)
- **DB**: Embedded PG via `pgserver` (offline, zero-install) or `TEST_DATABASE_URL` (CI/Supabase)
- **Fixtures**: `db` (clean conn), `client` (Flask test client), `admin_client`, `user_client`
- **Bcrypt**: Rounds=4 in tests (vs 13 prod) for speed
- **Rate Limits**: `RAAS_RATE_LIMITS=off` globally; targeted tests re-enable
- **Coverage**: **1237 passed / 8 skipped** as of 2026-10-25 (auth, stock, upload, sales, invoices, production, LC, rate limits, PI parse, notifications, schema migration, upload-limit config) — includes `tests/test_invoices_production.py`, `tests/test_invoice_items.py`, `tests/test_schema_migration.py`, `tests/test_numeric_integrity.py`, `tests/test_qty_validation.py`, `tests/test_authorization_roles.py` (P1-A role classification + P1-B volume limits and payload caps), `tests/test_typed_errors.py` (Phase 4-A: every refusal's status is asserted to come from the exception type, not its message), and `tests/test_edge_hardening.py` (Phase 4-B: the `vercel.json` header block, that no header `source` captures any `/api/` route or the cron path, that the edge CSP carries the recomputed theme-bootstrap digest and is byte-identical to Flask's copy, that `style-src` keeps its deliberate exception, that 415 is registered by code without shadowing the existing handlers, and that a wrong `Content-Type` answers 415 rather than 500 — unauthenticated as well as authenticated).

### Frontend (Vitest)
- **Environment**: jsdom
- **Coverage**: 40 test files, 331 tests (utils, components, contexts, pages) - includes `InvoiceEditor.test.jsx` (the one composer, both modes: open quantity input with live `Remaining` under PI qty, read-only computed Total price, no cap — quantities are free relative to the PI, structural 400/404 handling, per-row reporting on partial success) and `InvoicePanel.test.jsx`
- **Lint**: oxlint (0 errors, 7 pre-existing warnings)

### CI (`.github/workflows/ci.yml`)
- **Runtime**: Python **3.12** pinned to the Vercel deploy runtime (Vercel defaults to 3.12; no `.python-version`/`pyproject.toml` overrides it) · Node 22 · postgres:16
- **Backend**: Ubuntu + postgres:16 service → `pip install -r requirements.txt requirements-dev.txt` → `pytest tests/ -q`
- **Frontend**: Node 22 + npm ci → oxlint → vitest run → vite build
- **Triggers**: push + PR to `main`

## 8. Environment Variables

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL (Supabase pooler) connection string |
| `TEST_DATABASE_URL` | No | embedded PG | Scratch DB for tests |
| `RAAS_SECRET` | Prod only | random per-process | App secret (sessions, cookies) |
| `PRODUCTION` | No | `0` | `1` → requires `RAAS_SECRET` |
| `HOST` / `PORT` | No | `127.0.0.1` / `5000` | Bind address/port |
| `FORCE_HTTPS` | No | `0` | `1` → HSTS + HTTPS redirect |
| `COOKIE_SECURE` | No | `1` | `0` disables Secure flag (dev only) |
| `DISABLE_SETUP` | No | unset | `true` disables `/setup` |
| `FLASK_DEBUG` | No | `0` | Never enable in production |
| `RAAS_LOG_LEVEL` | No | `INFO` | Python log level |
| `CORS_ALLOWED_ORIGINS` | No | `""` | Comma-separated origins (empty = same-origin only) |
| `RAAS_RATE_LIMITS` | No | `on` | `off` disables Flask-Limiter (tests) |
| `REDIS_URL` | **Prod (multi-process)** | unset → in-memory | Shared rate-limit storage (Flask-Limiter). **Required on serverless/multi-process** so limits are global, not per-process; optional for single-process dev |
| `TRUSTED_PROXY` | **Behind a proxy** | unset → header ignored | `1` trusts one `X-Forwarded-For` hop (`ProxyFix x_for`), making `remote_addr` the client IP. Unset means `remote_addr` is the socket address. `remote_addr` gates the API-key IP allowlist, login lockout-by-IP and the IP-keyed rate limits, so set this **only** when a proxy is the sole entry point |
| `MAX_CONTENT_LENGTH_MB` | No | `50` | Upload body cap in MB (float allowed, e.g. `4.5` on Vercel) |
| `RAAS_DATA_DIR` | No | repo root | Writable data dir (uploads, reports) |

## 9. Risks & Unknowns

| Risk | Mitigation |
|---|---|
| **Content-Security-Policy (Phase 4-B, closed)** | Shipped at the edge, hash-allowlisted — see §6.3.2. `script-src` is `'self'` plus the recomputed theme-bootstrap digest, with **no** `'unsafe-inline'`, so an injected inline script is now blocked at the edge as well as by React's escaping. `style-src` deliberately keeps `'unsafe-inline'` for `sonner`, which injects its toast stylesheet as a `<style>` element at runtime — *not* for the three `style={{…}}` props, which React writes through the CSSOM and which CSP does not govern. Moving sonner's stylesheet into the build is the remaining frontend work that would make a strict `style-src` shippable. Residual, recorded: there is no staging deployment, so enforcement is proven by digest against the source script Vite copies verbatim, rather than observed live |
| **Vercel 4.5MB body limit** | Platform limit is enforced at the edge before Flask; set `MAX_CONTENT_LENGTH_MB=4.5` on Vercel so the app cap matches. Off-Vercel (waitress) the cap is 50MB. Show size warning on upload page. |
| **Password leakage in chat** | Rotation guide (README); never put passwords in git/chat. |
| **Supabase project pause (free tier)** | Monitoring + alerts; recommend paid plan for production. |
| **Schema drift manual DROP** | Schema-sig fast path auto-detects; `_create_tables` idempotent. |
| **Legacy `.doc` support** | Windows-only `doc2docx` + `pywin32`; API rejects `.doc`; local CLI only. |
| **Frontend build output in git?** | `react_frontend/` gitignored; Vercel buildCommand builds → `public/`. |
| **Session cookie domain** | Same-site only; cross-origin use `CORS_ALLOWED_ORIGINS` + API key. |
| **Database clock vs app clock** | Use `clock_timestamp()` + `make_interval` (DB clock authoritative). |
| **SQL `to_char` timestamp format (P1-18, closed)** | Every datetime column is TEXT and every window is a **string comparison**, so the `to_char` pattern is load-bearing: SQL uses `'YYYY-MM-DD HH24:MI:SS'`, Python uses `strftime("%Y-%m-%d %H:%M:%S")`, and the two must produce identical output. Writing the strftime-looking `'YYYY-MM-DD HH:MM:SS'` in SQL is silently wrong — `to_char` reads `MM` as MONTH (so the minute slot held the month) and `HH` as 12-hour (so 14:00 sorted before 09:00). `tests/test_timestamp_format.py` fails the build if a malformed `to_char` literal reappears, if a Python `strftime` is given the SQL spelling, if any *live* column DEFAULT drifts, or if `_SCHEMA_VERSION` was not bumped for a DEFAULT change. A changed DEFAULT needs an explicit `ALTER TABLE ... SET DEFAULT` too: `CREATE TABLE IF NOT EXISTS` silently no-ops against an already-migrated table. |
| **`X-Forwarded-For` trust (P1-7, closed)** | `ProxyFix`'s `x_for` is gated on `TRUSTED_PROXY=1`; unset means the header is ignored and `remote_addr` is the socket address. `remote_addr` gates the API-key IP allowlist, login lockout-by-IP **and** the IP-keyed rate-limit buckets, so trusting the header unconditionally let a direct client forge its IP and bypass all three. `docker-compose.yml` now binds 5000 to loopback and expects a TLS proxy in front. Residual: setting `TRUSTED_PROXY=1` while any direct path exists re-opens it |
| **`audit_logs.ip_address` is not proof of client identity** | Since P1-6 it holds a real value everywhere (free-text reasons moved to `new_value`). P1-7 gates `X-Forwarded-For` on `TRUSTED_PROXY`: with the flag unset it is the socket address, with it set it is whatever the trusted proxy appended. Schema-safe (`TEXT`, rendered as escaped JSX); do not treat it as a validated IP field. |

---

*Generated from codebase at commit `c3400c6` (INV-REVISION schema v5, PBB booking gate) — October 2026*