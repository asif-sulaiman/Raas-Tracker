# ChemCalc — Chemical Stock Tracker + Sales CRM

Warehouse chemical inventory with monthly reconciliation, recipe management,
file upload comparison (PDF/Excel), a sales pipeline (PI → LC → shipment →
payment → completed) with `.doc`/`.docx` PI parsing, and session + API-key
authentication. Flask backend serves a React 19 + Vite frontend.

## Quick start (local)

```bash
pip install -r requirements.txt
python flask_app.py            # http://localhost:5000
```

Build the frontend into `react_frontend/` (served by Flask):

```bash
cd chemcalc-frontend
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
| `CHEMCALC_SECRET` | random per-process | App secret. **Required** when `PRODUCTION=1` |
| `PRODUCTION` | `0` | `1` refuses to start without `CHEMCALC_SECRET` |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Bind address and port |
| `FORCE_HTTPS` | `0` | `1` enables HSTS + HTTPS redirect (behind a TLS proxy) |
| `COOKIE_SECURE` | `1` | Session cookie `Secure` flag (`0` disables, dev only) |
| `DISABLE_SETUP` | unset | `true` disables the one-time setup endpoint |
| `FLASK_DEBUG` | `0` | Never enable in production |
| `CHEMCALC_LOG_LEVEL` | `INFO` | Python log level for the `chemcalc` logger |

See `.env.example` for a template.

## Production (Waitress)

```bash
pip install -r requirements.txt
export CHEMCALC_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(64))")
export PRODUCTION=1 FORCE_HTTPS=1
waitress-serve --host=0.0.0.0 --port=5000 --threads=4 wsgi:app
```

Or with Docker (Linux images parse `.docx` only — Word COM `.doc`
conversion needs Windows):

```bash
docker compose up --build
```

SQLite, `uploads/` and `reports/` live in a named volume (`chemcalc-data`).
Compose defaults to plain HTTP (`COOKIE_SECURE=0`, `FORCE_HTTPS=0`); when
serving HTTPS (directly or when your proxy doesn't set `X-Forwarded-Proto`),
flip both to `1`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q        # backend: 40+ tests, isolated temp DBs
cd chemcalc-frontend
npm install && npx vitest run     # frontend unit tests
npx oxlint && npx vite build      # lint + production build
```

CI (`.github/workflows/ci.yml`) runs pytest, oxlint, vitest and the build
on every push and pull request to `main`.

## Layout

```
flask_app.py          Flask API + gates + SPA hosting
wsgi.py               Waitress entry point
chemcalc/             Data layer (split from legacy chem_stock.py)
  db.py               Connection, schema, migrations, settings
  audit.py            Audit log + per-request actor
  auth.py             Users, sessions, API keys, throttle, setup tokens
  stock.py            Chemicals, units, reorder levels
  uploads.py          Uploads, comparison, approvals, reconciliation
  recipes.py          Recipes + production reports
  sales.py            Pipeline, items, payments, summaries
  cli.py              python chem_stock.py <command> entry
chem_stock.py         Compatibility shim (re-exports chemcalc.*)
parse_sales.py        PI parser (.docx direct, .doc via Word COM on Windows)
parse_stock.py        Stock file parsers (PDF/Excel)
chemcalc-frontend/    React 19 + Vite + Tailwind 4 SPA
tests/                pytest suite (temp DB per test, real DB untouched)
```

## Notes

- Money math is exact: integer cents in the UI (`utils/format.js`), cents
  rounding in SQL sums — invoices render like `$100.10` with no float drift.
- Stock states: `Out of stock` (zero), `Low stock` (at/below the
  per-chemical reorder level, default off), `Reconciled` otherwise.
- API errors use real HTTP statuses; validation failures return
  `{error, details:[{field, message}]}` (400).
