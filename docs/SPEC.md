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
├── vercel.json                    # Vercel build + SPA rewrite config
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
    amount NUMERIC(14,2),                    -- NULL = legacy/unknown; 0 → paid at creation
    approx_ship_date TEXT,
    actual_ship_date TEXT,
    notes TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE UNIQUE INDEX idx_invoices_sale_number ON invoices (sale_id, invoice_number);

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
- **Code-owned version gate (`_SCHEMA_VERSION`, primary):** `_schema_current()` reads `raas_schema_version` from `app_settings` in the same round trip as the signature. A missing, unparseable, or stale value means *not current* → the full DDL runs exactly once and re-stamps the version. **Bump `_SCHEMA_VERSION` whenever `_create_tables()` gains DDL.**
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
- A legacy invoice with no lines is reconciled by the schema migration (each line-less invoice is filled from its **own header amount**, so a 30% advance is not inflated into a full receivable); a legacy invoice with a NULL amount is deliberately left alone and logged, because inventing lines for an unknown value would fabricate money.
- The create path is **one transaction**: the `sales` row and the PI lines are locked `FOR UPDATE`, and nothing inside may commit before the end — a mid-seed commit would release those locks and let a concurrent create derive the same remainder, billing the sale twice. The seeded lines' audit rows are therefore written with `atomic=False`.
- When the derived remainder cannot cover a PI line's full quantity (only reachable when legacy line-less invoices already claimed part of the PI), the last line is filled **partially** so the money stays exact; a sub-gram quantity residue may remain on that PI line. Money is always exact to the cent; only quantity can carry the residue.

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
| GET | `/api/sales/<id>` | Session/Key | Detail: items (incl. `item_no`), `invoices[]`, `shipment_status`, invoice total/balance |
| POST | `/api/sales` | Session/Key | Create (header + items; company_id, unit, item_no) |
| PUT | `/api/sales/<id>` | Admin | Full update (header+items+removals) or patch header (incl. comments) |
| DELETE | `/api/sales/<id>` | Admin | Delete |
| POST | `/api/sales/<id>/move` | Admin | Advance stage (with notes) |
| PUT | `/api/sales/<id>/lc` | Admin | Legacy per-PI LC entry. **409 when the PI is already linked to an LC** — linked PIs use the `/api/lcs` endpoints instead (a half-linked PI has no UI to undo) |
| PUT | `/api/sales/<id>/payment` | Admin | Record payment |
| PUT | `/api/sales/<id>/payments/<pid>` | Admin | Edit payment |
| DELETE | `/api/sales/<id>/payments/<pid>` | Admin | Delete payment |
| POST | `/api/sales/<id>/items` | Admin | Add item (product, qty, price, unit) |
| PUT | `/api/sales/<id>/items/<iid>` | Admin | Update item (incl. unit) |
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
| POST | `/api/lcs/<id>/pis` | Admin (15/min) | Attach `{sale_ids:[...]}` — every PI must share the LC's company (400 otherwise); mirrors `lc_number`/`lc_date` onto the PIs |
| DELETE | `/api/lcs/<id>/pis/<sale_id>` | Admin (15/min) | Detach one PI (clears `lc_id` + mirrors) |
| POST | `/api/lcs/<id>/move` | Admin (15/min) | Move the LC stage; **two barriers** — crossing into `shipment_ongoing` requires **≥1 invoice** (recipes no longer required here); crossing into `payment_due` requires **every invoiced product to have a company recipe AND an invoice-linked production run**. Both barriers enforced under row locks; stage propagated to all child PIs in one transaction |
| GET | `/api/lcs/<id>/readiness` | Session/Key | Readiness snapshot — `invoices_ok`, `recipes_ok`, `produced_ok`, `detail` with per-PI remaining uninvoiced lines, `missing_recipes`, `missing_production` |

### Invoice Lines
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/invoices/<id>/items` | Session/Key | Invoice lines (product, quantity, unit_price, line_total) |
| POST | `/api/invoices/<id>/items` | Admin (15/min) | Add a line `{sale_item_id, quantity}` — **unit_price is resolved server-side from the PI line**, never accepted from the client; quantity must be finite, >0 and within the remaining uninvoiced quantity (**409** when it would exceed). Recomputes `invoices.amount` from its lines in the same transaction |

### Sales Invoices
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/sales/<id>/invoices` | Session/Key | List invoices with `amount` + `paid_amount` each |
| POST | `/api/sales/<id>/invoices` | Admin | Create (`invoice_number` only — **no `amount` is accepted**). Money is derived: `amount` = PI total − what the invoice lines already carry, one `invoice_items` line is seeded per remaining uninvoiced PI line, then the header is restated in SQL as `SUM(invoice_items.line_total)` so header == lines by construction. A stated `amount` in the body is ignored, never honoured (400 on duplicate number); sets sale `shipment_status='production_running'` (COALESCE, never downgrades) |
| POST | `/api/sales/<id>/invoices/batch` | Admin (15/min) | Create N invoices in ONE transaction: body `{invoices:[{invoice_number, lines:[{sale_item_id, quantity}]}]}` — each invoice gets ≥1 line; per `sale_item` the sum of quantities across the batch must not exceed the remaining uninvoiced quantity; headers restated as `SUM(line_total)`; atomic (one commit or full rollback) |
| POST | `/api/sales/<id>/invoices/<iid>/book` | Admin | Book (`approx_ship_date` required) → `booked`; sale → `ship_booked` (advance-only CASE) |
| POST | `/api/sales/<id>/invoices/<iid>/ship` | Admin | Ship (`actual_ship_date` required) → `shipped` + creates a `shipments` row |
| POST | `/api/sales/<id>/invoices/<iid>/pay` | Admin | Record payment (`payment_amount>0`, `payment_date`); flips invoice to `paid` when covered |
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
- **Auto-advances (all advance-only; `paid` is terminal — book/ship/produce can never downgrade it):**
  - invoice create → sale `shipment_status = 'production_running'` (`COALESCE`, never overwrites a later value)
  - production run linked to invoices → those invoices `'produced'` (guarded `status <> 'paid'`) + sale `'production_done'` (CASE: only from NULL/`production_running`)
  - book (requires `approx_ship_date`) → `'booked'`; sale → `'ship_booked'` (advance-only CASE)
  - ship (requires `actual_ship_date`) → `'shipped'` + `shipments` row
  - pay → `'paid'` when `paid_amount >= amount`; `amount = 0` → `paid` at creation; `amount IS NULL` (legacy) → any payment marks it paid
- **Sale `shipment_status` sub-steps** (surfaced as SaleCard badges in `shipment_ongoing`): `production_running` → `production_done` → `ship_booked`.
- **Stage hook:** `lc_received → shipment_ongoing` sets `shipment_status = 'production_running'`; the frontend shows a non-blocking warning toast when the sale has 0 invoices.
- **LC Stage Gates:**
  - **Barrier 1 (`lc_received → shipment_ongoing`):** requires **≥1 invoice** on the LC. Recipes are no longer required at this stage.
  - **Barrier 2 (`shipment_ongoing → payment_due`):** requires that **every distinct `invoice_items.product_name` across the LC's invoices** has (a) a company recipe, **and** (b) an invoice-linked production run (`production_run_links.invoice_id` points to one of that sale's invoices).
  - **Readiness endpoint:** `GET /api/lcs/<id>/readiness` returns `{invoices_ok, recipes_ok, produced_ok, detail{missing_recipes, missing_production, per-PI remaining lines}}` so the UI can render the correct form before the user attempts a move.
- **New invariant:** a sale cannot advance to `lc_received` (or beyond) while its `lc_id` is NULL — the move is refused until an LC is attached.

### Readiness Endpoint
- `GET /api/lcs/<id>/readiness` — Session/Key; returns `{invoices_ok, recipes_ok, produced_ok, detail:{missing_recipes, missing_production, per-PI remaining lines}}` so the UI can render the correct barrier form before the user attempts a move.

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

### Rate Limiting (Flask-Limiter)
- Default: 300/min per identity (user/key/IP)
- Tier 2: `/api/upload`, `/api/uploads/*/approve|apply|export`, `/api/reports/*`, `/api/sales/parse`, `/api/sales/export` → 15/min
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
- `POST /api/cron/maturity-check` — daily 09:00 UTC; checks sales with `maturity_date <= today` and unpaid; creates `maturity_due` (warning) and `maturity_escalated` (critical, at 7+ days overdue) notifications with dedupe; clears dedupe on full payment. Auth: `Authorization: Bearer <CRON_SECRET>`.

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
| **DB Errors** | `psycopg.IntegrityError` → `ValueError` → 400/409; `OperationalError` → 500 |
| **Auth** | Missing/invalid → 401/403 JSON; login throttle → 429 + `Retry-After` |
| **Rate Limit** | 429 + `Retry-After` header; JSON `{error: "rate limit exceeded, slow down"}` |
| **File Upload** | Max 50MB by default (`MAX_CONTENT_LENGTH`, override with `MAX_CONTENT_LENGTH_MB`); allowed ext {.pdf,.xlsx,.xls}; **one canonical filename**, computed once per request by `_safe_upload_filename` (`raas_tracker/uploads.py`) and reused for the extension gate, the stored `uploads.filename`, the `UPLOAD_CREATE` audit row and the JSON response — it strips control chars, path traversal, bidi controls (U+202E/U+200E/U+200F), leading dots and Windows reserved device names, and **preserves non-ASCII** (werkzeug's `secure_filename` is deliberately not used: it flattened a legitimate Arabic name to a bare `xlsx`, so the gate rejected a real file). The on-disk name is separate and stays `uuid4().hex + ext`; parse failure → 400 + file cleanup |
| **PI Parse** | Only `.pdf`/`.docx` accepted; legacy `.doc` rejected with guidance |
| **Global** | `@app.errorhandler(500)` + `@app.errorhandler(Exception)` → 500 `{"error":"internal server error"}` (log full trace) |
| **413** | File too large → 413 `{"error":"file too large (max N MB)"}` where N = `MAX_CONTENT_LENGTH_MB` (default 50). On Vercel the platform 4.5MB body limit fires at the edge first, so >4.5MB gets Vercel's own 413 page, not this JSON — set `MAX_CONTENT_LENGTH_MB=4.5` to align the app cap with the platform |

## 7. Testing Strategy

### Backend (pytest)
- **Isolation**: `conftest.py` — per-test `TRUNCATE ... RESTART IDENTITY CASCADE` (excludes `app_settings` for schema-sig fast path)
- **DB**: Embedded PG via `pgserver` (offline, zero-install) or `TEST_DATABASE_URL` (CI/Supabase)
- **Fixtures**: `db` (clean conn), `client` (Flask test client), `admin_client`, `user_client`
- **Bcrypt**: Rounds=4 in tests (vs 13 prod) for speed
- **Rate Limits**: `RAAS_RATE_LIMITS=off` globally; targeted tests re-enable
- **Coverage**: 367 passing / 8 skipped (auth, stock, upload, sales, invoices, production, rate limits, PI parse, notifications, schema migration, upload-limit config) — includes `tests/test_invoices_production.py` (12) + `tests/test_invoice_amount.py` (10)

### Frontend (Vitest)
- **Environment**: jsdom
- **Coverage**: 33 test files, 256 tests (utils, components, contexts, pages) — includes `ProductionRunModal.test.jsx` (7) + `InvoicePanel.test.jsx` (8)
- **Lint**: oxlint (0 errors, 0 warnings)

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

*Generated from codebase at commit 998b841 — September 2026*