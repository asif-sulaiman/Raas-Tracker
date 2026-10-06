# RAAS Tracker — Chemical Inventory + Sales CRM

Warehouse chemical inventory with monthly reconciliation, recipe management,
file upload comparison (PDF/Excel), a sales pipeline (PI → LC → shipment →
payment → completed) with PDF/`.docx` PI parsing, and session + API-key
authentication. Flask backend serves a React 19 + Vite frontend.
PostgreSQL (Supabase) is the database in every environment.

## Quick start (local)

```bash
pip install -r requirements.txt
# Point at Postgres first (required - the app refuses to start without it):
#   $env:DATABASE_URL="postgresql://postgres.<ref>:<pw>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
python flask_app.py            # http://localhost:5000
```

Build the frontend into `react_frontend/` (served by Flask):

```bash
cd raas-tracker-frontend
npm install
npx vite build          # output is copied to ../react_frontend/
```

## First run / auth

1. Open `http://localhost:5000/setup`.
2. Read the single-use **setup token** printed to the server console (stderr).
3. Create the first admin. The setup route locks permanently afterwards
   (or set `DISABLE_SETUP=true` to kill it entirely).
4. Admins create users and API keys under **Users & Keys**.

Security model: bcrypt-13 over a SHA-256 pre-hash, SHA-256-hashed session
tokens (1 h TTL) and API keys, 5-fails-per-10-min login throttle (per
username and per IP), per-key rate limits, audited auth events.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | (none — **required**) | PostgreSQL connection string (Supabase session pooler). App refuses to start without it |
| `TEST_DATABASE_URL` | local `raas_test` | Scratch database for the test suite (wiped + rebuilt per test — never production) |
| `RAAS_SECRET` | random per-process | App secret. **Required** when `PRODUCTION=1` |
| `PRODUCTION` | `0` | `1` refuses to start without `RAAS_SECRET` |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Bind address and port |
| `FORCE_HTTPS` | `0` | `1` enables HSTS + HTTPS redirect (behind a TLS proxy) |
| `COOKIE_SECURE` | `1` | Session cookie `Secure` flag (`0` disables, dev only) |
| `DISABLE_SETUP` | unset | `true` disables the one-time setup endpoint |
| `FLASK_DEBUG` | `0` | Never enable in production |
| `RAAS_LOG_LEVEL` | `INFO` | Python log level for the `raas` logger |

See `.env.example` for a template.

## Database Connection Pooling

The app uses `psycopg_pool` for PostgreSQL connection pooling. The pool is configured with:
- **min connections**: 1
- **max connections**: 10 (configurable via `DB_POOL_MAX`)

For high-concurrency workloads, increase `DB_POOL_MAX` (e.g., `DB_POOL_MAX=20`). Each worker process maintains its own pool.

## Optional Redis for Rate Limiting

Flask-Limiter uses in-memory storage by default (suitable for single-worker deployments). For multi-worker or distributed deployments, set `REDIS_URL` to enable Redis-backed rate limiting:

```bash
REDIS_URL=redis://localhost:6379/0
```

Without Redis, rate limits are per-process only — not shared across workers.

## Production (online hosting)

The app is stateless (all state in Postgres) and PaaS-ready. Set these env
vars on your host: `DATABASE_URL` (Supabase session-pooler URI),
`RAAS_SECRET` (generate: `python -c "import secrets; print(secrets.token_urlsafe(64))"`),
`PRODUCTION=1`, `FORCE_HTTPS=1`, `COOKIE_SECURE=1`, `TRUSTED_PROXY=1`, and start
with:

```bash
pip install -r requirements.txt
waitress-serve --host=0.0.0.0 --port=$PORT --threads=4 wsgi:app
```

(`$PORT` is provided by the host; default `5000` locally.) Behind a TLS
proxy, HTTPS redirect and secure cookies are on by default (`COOKIE_SECURE=1`).

**`TRUSTED_PROXY=1` is required whenever a proxy sits in front of the app**
(Vercel, Cloudflare, nginx). It tells the app to read the client IP from
`X-Forwarded-For`; that IP is what gates the API-key IP allowlist, login
lockout-by-IP and the IP-keyed rate limits.

Leaving it **unset** behind a proxy is a real outage, not a silent degradation:
the header is ignored, so every request looks like it came from the same
address. `is_login_blocked` counts failures per username *or* per IP, so five
failed logins from anyone would lock every user out for 10 minutes, and
`forgot-password` would be capped at 5/min for the whole application. The app
prints its resolved setting at startup so you can confirm which mode it is in.

Conversely, do **not** set it when anything can reach the app directly: the
header is client-supplied, and trusting it lets a caller forge its own IP and
bypass all three controls. `docker-compose.yml` therefore binds
`127.0.0.1:5000` so only a co-located proxy can reach it.

Docker files (`Dockerfile`, `docker-compose.yml`) are retained for future
use; the current path is online hosting against Supabase (no DB container).

### Backups, restore, rotation, rollback

- **Backup**: Supabase dashboard → Backups, plus `pg_dump` any time:
  `pg_dump "$DATABASE_URL" -F custom -f raas-backup.dump` (needs a local
  `pg_dump`; the portable bundle in git history has one, or install Postgres
  tools). Back up before every deploy.
- **Restore** to a scratch project first to verify, then to production.
- **Password rotation**: reset the DB password in Supabase dashboard, then
  update `DATABASE_URL` everywhere it is set (local shell, hosting env).
  Never commit connection strings — they live in env only.
- **Rollback**: redeploy the previous release commit; data stays in
  Supabase. `scripts/migrate_sqlite_to_pg.py` remains available to
  re-import from a SQLite export you provide.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q        # backend: 90+ tests, isolated PG DBs
cd raas-tracker-frontend
npm install && npx vitest run     # frontend unit tests
npx oxlint && npx vite build      # lint + production build
```

Backend tests run against PostgreSQL: embedded zero-install PG locally
(`pgserver`, no `TEST_DATABASE_URL`), a `postgres:16` service in CI, or any
`TEST_DATABASE_URL` (e.g. Supabase). Each test truncates and reseeds, so the
target database must be scratch — never production.

CI (`.github/workflows/ci.yml`) runs pytest, oxlint, vitest and the build
on every push and pull request to `main`.

## Layout

```
flask_app.py          Flask API + gates + SPA hosting (requires DATABASE_URL)
wsgi.py               Waitress entry point
raas_tracker/          Data layer (PostgreSQL via psycopg)
  db.py               Connection, schema, migrations, settings (+fast-path probe)
  audit.py            Audit log + per-request actor
  auth.py             Users, sessions, API keys, throttle, setup tokens
  stock.py            Chemicals, units, reorder levels
  uploads.py          Uploads, comparison, approvals, reconciliation
  recipes.py          Recipes + production reports
  sales.py            Pipeline, items, payments, summaries
  cli.py              python chem_stock.py <command> entry
chem_stock.py         Compatibility shim (re-exports raas_tracker.*)
scripts/              One-off ops scripts (e.g. SQLite -> Postgres migration)
parse_sales.py        PI parser (PDF + .docx; legacy .doc via Word COM on Windows)
parse_stock.py        Stock file parsers (PDF/Excel)
raas-tracker-frontend/  React 19 + Vite + Tailwind 4 SPA
tests/                pytest suite (isolated PG database per test)
```

## Notes

- Money math is exact: integer cents in the UI (`utils/format.js`), cents
  rounding in SQL sums — invoices render like `$100.10` with no float drift.
- Stock states: `Out of stock` (zero), `Low stock` (at/below the
  per-chemical reorder level, default off), `Reconciled` otherwise.
- API errors use real HTTP statuses; validation failures return
  `{error, details:[{field, message}]}` (400).
