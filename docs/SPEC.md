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
ChemCalc/
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
    upload_date TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
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
    reviewed_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
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
    timestamp TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
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
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
    updated_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
);

-- sale_items
CREATE TABLE sale_items (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    product_name TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 0,
    unit_price REAL NOT NULL DEFAULT 0
);

-- sale_payments
CREATE TABLE sale_payments (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    payment_date TEXT,
    payment_amount REAL NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
);

-- users
CREATE TABLE users (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
);

-- sessions
CREATE TABLE sessions (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

-- api_keys
CREATE TABLE api_keys (
    id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
    expires_at TEXT,
    allowed_ips TEXT DEFAULT '',
    revoked INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    last_used_ip TEXT
);

-- api_key_rate_limits
CREATE TABLE api_key_rate_limits (
    key_id INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    hit_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
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
    created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS'))
);
CREATE INDEX idx_notifications_dedupe ON notifications(dedupe_key);

-- notification_reads
CREATE TABLE notification_reads (
    user_id INTEGER NOT NULL,
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    read_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH:MM:SS')),
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
- FK indexes auto-created by PG

### Migration Strategy (`db.py:_create_tables`)
- Schema-signature fast path: `_schema_signature_live()` compares columns+indexes vs stored `raas_schema_sig` in `app_settings`
- If drift detected → full `_create_tables()` runs (idempotent `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` migrations)
- Seeds (`reason_codes`, `unit_conversions`) run on every connection if tables empty (test isolation friendly)

## 4. API Endpoints

### Auth
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/auth/status` | Public | Setup needed? + current identity |
| POST | `/api/auth/setup` | Public (token) | First-admin creation (single-use token) |
| POST | `/api/auth/login` | Public | Session cookie mint |
| POST | `/api/auth/logout` | Public | Revoke session cookie |
| GET | `/api/auth/me` | Session/Key | Current identity details |

### Users (admin only)
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/users` | Admin | List users |
| POST | `/api/users` | Admin | Create user |
| DELETE | `/api/users/<id>` | Admin | Delete user (not last admin) |
| POST | `/api/users/<id>/revoke` | Admin | Revoke all sessions |

### API Keys (admin only)
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/keys` | Admin | List keys (fingerprint only) |
| POST | `/api/keys` | Admin | Create key (returns raw once) |
| DELETE | `/api/keys/<id>` | Admin | Revoke key |

### Chemicals
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/chemicals` | Session/Key | List all |
| POST | `/api/chemicals` | Session/Key | Create (name, qty, unit) |
| POST | `/api/chemicals/update` | Session/Key | Delta stock update |
| PUT | `/api/chemicals/reorder` | Session/Key | Set reorder level |

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

### Audit & Notifications
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/audit-logs` | Admin | Recent logs (limit) |
| GET | `/api/notifications` | Session | User's notifications + unread count |
| POST | `/api/notifications/read` | Session | Mark read (ids or all) |

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
| GET | `/api/sales` | Session/Key | List (filter: stage, q) |
| GET | `/api/sales/export` | Session/Key (15/min) | CSV export |
| GET | `/api/sales/summary` | Session/Key | Stage-wise summary |
| GET | `/api/sales/<id>` | Session/Key | Detail |
| POST | `/api/sales` | Session/Key | Create (header + items) |
| PUT | `/api/sales/<id>` | Session/Key | Full update (header+items+removals) or patch header |
| DELETE | `/api/sales/<id>` | Admin | Delete |
| POST | `/api/sales/<id>/move` | Session/Key | Advance stage (with notes) |
| PUT | `/api/sales/<id>/lc` | Session/Key | LC details + move to `lc_received` |
| PUT | `/api/sales/<id>/payment` | Session/Key | Record payment |
| PUT | `/api/sales/<id>/payments/<pid>` | Session/Key | Edit payment |
| DELETE | `/api/sales/<id>/payments/<pid>` | Admin | Delete payment |
| POST | `/api/sales/<id>/items` | Session/Key | Add item |
| PUT | `/api/sales/<id>/items/<iid>` | Session/Key | Update item |
| DELETE | `/api/sales/<id>/items/<iid>` | Admin | Delete item (min 1 item) |

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

## 5. Authentication & Authorization

### Session (Humans)
- Cookie: `raas_session` (HttpOnly, SameSite=Lax, Secure if `COOKIE_SECURE!=0`)
- TTL: 1 hour (`SESSION_TTL_HOURS`), SHA-256 token hash in DB
- Auto-cleanup expired on login

### API Keys (Scripts)
- Format: `ck_live_<32-char>` (shown once at creation)
- Stored: SHA-256 hash (`key_hash`)
- Features: expiry, IP allowlist (CIDR), revocation, per-key rate limit (300/min), audit identity
- Header: `X-API-Key`

### Gates (`flask_app.py:_gate_api`)
- `/api/*` → identity required (except `_PUBLIC_API`: login, setup, logout, status)
- `OPTIONS` → pass through (CORS preflight)
- `X-API-Token` (legacy) → 401 + audit
- Admin paths (`/api/users`, `/api/keys`) → human admin only
- API keys → per-key rate limit (300/min, DB-backed)

### Rate Limiting (Flask-Limiter)
- Default: 300/min per identity (user/key/IP)
- Tier 2: `/api/upload`, `/api/uploads/*/approve|apply|export`, `/api/reports/*`, `/api/sales/parse`, `/api/sales/export` → 15/min
- Tier 1: Login throttle (5 fails/10 min per username+IP, DB-backed)
- Test bypass: `RAAS_RATE_LIMITS=off`

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
| **File Upload** | Max 50MB (`MAX_CONTENT_LENGTH`); allowed ext {.pdf,.xlsx,.xls}; secure filename; parse failure → 400 + file cleanup |
| **PI Parse** | Only `.pdf`/`.docx` accepted; legacy `.doc` rejected with guidance |
| **Global** | `@app.errorhandler(500)` + `@app.errorhandler(Exception)` → 500 `{"error":"internal server error"}` (log full trace) |
| **413** | File too large → 413 `{"error":"file too large (max 50MB)"}` |

## 7. Testing Strategy

### Backend (pytest)
- **Isolation**: `conftest.py` — per-test `TRUNCATE ... RESTART IDENTITY CASCADE` (excludes `app_settings` for schema-sig fast path)
- **DB**: Embedded PG via `pgserver` (offline, zero-install) or `TEST_DATABASE_URL` (CI/Supabase)
- **Fixtures**: `db` (clean conn), `client` (Flask test client), `admin_client`, `user_client`
- **Bcrypt**: Rounds=4 in tests (vs 13 prod) for speed
- **Rate Limits**: `RAAS_RATE_LIMITS=off` globally; targeted tests re-enable
- **Coverage**: 116 tests (auth, stock, upload, sales, rate limits, PI parse, notifications)

### Frontend (Vitest)
- **Environment**: jsdom
- **Coverage**: 13 test files, 73 tests (utils, components, contexts, pages)
- **Lint**: oxlint (0 errors, 39 baseline warnings)

### CI (`.github/workflows/ci.yml`)
- **Backend**: Ubuntu + postgres:16 service → `pip install -r requirements.txt requirements-dev.txt` → `pytest tests/ -q`
- **Frontend**: Node 22 + npm ci → oxlint → vitest run → vite build

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
| `RAAS_DATA_DIR` | No | repo root | Writable data dir (uploads, reports) |

## 9. Risks & Unknowns

| Risk | Mitigation |
|---|---|
| **Vercel 4.5MB body limit** | Preview-only; production host uses waitress (50MB). Show size warning on upload page. |
| **Password leakage in chat** | Rotation guide (README); never put passwords in git/chat. |
| **Supabase project pause (free tier)** | Monitoring + alerts; recommend paid plan for production. |
| **Schema drift manual DROP** | Schema-sig fast path auto-detects; `_create_tables` idempotent. |
| **Legacy `.doc` support** | Windows-only `doc2docx` + `pywin32`; API rejects `.doc`; local CLI only. |
| **Frontend build output in git?** | `react_frontend/` gitignored; Vercel buildCommand builds → `public/`. |
| **Session cookie domain** | Same-site only; cross-origin use `CORS_ALLOWED_ORIGINS` + API key. |
| **Database clock vs app clock** | Use `clock_timestamp()` + `make_interval` (DB clock authoritative). |

---

*Generated from codebase at commit 998b841 — September 2026*