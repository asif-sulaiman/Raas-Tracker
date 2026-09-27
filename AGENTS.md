# RAAS Tracker — Agent Rules (opencode)

> Living document. Updated whenever we discuss or implement a new plan.
> Full product context: `docs/PRD.md`, `docs/SPEC.md`, `docs/plan.md`.

## Project
Chemical inventory + monthly reconciliation + recipe/production reports + sales
pipeline (PI → LC → shipment → payment) with PI parsing (PDF/`.docx`).
Flask 3 + psycopg3 backend, React 19/Vite/Tailwind 4 frontend, PostgreSQL (Supabase).

## Commands

- **Install:** `pip install -r requirements.txt` · `pip install -r requirements-dev.txt` · `cd raas-tracker-frontend && npm ci`
- **Dev backend:** `python flask_app.py` (requires `DATABASE_URL`; serves SPA on :5000)
- **Dev frontend:** `cd raas-tracker-frontend && npm run dev` (proxies `/api` to :5000)
- **Backend tests:** `python -m pytest tests/ -q`
- **Frontend tests:** `cd raas-tracker-frontend && npm test`
- **Lint:** `cd raas-tracker-frontend && npx oxlint`
- **Build:** `cd raas-tracker-frontend && npx vite build` (then copy `dist/` → `react_frontend/`)
- **Full gate:** pytest + vitest + oxlint + vite build
- **Production:** `waitress-serve --host=0.0.0.0 --port=$PORT --threads=4 wsgi:app`
- **Launch (Windows):** `start_flask.bat` — refuses to start without `DATABASE_URL`

## Workflow

1. **One task at a time.** Finish the full loop before starting another.
2. **Plan mode first.** Show files, approach, and tests. Wait for my approval.
3. **TDD:** failing test first (red), then implementation (green).
4. **Tests + lint must pass** before anything is called done.
5. **Atomic commits:** `feat:` · `fix:` · `chore:` · `refactor:` · `test:`
6. **Ask, don't guess** — unclear API, schema, or requirement → use the question tool.
7. **Follow existing patterns.** Read the analogous file before inventing a new way.
8. **Keep docs current:** when a plan changes scope or architecture, update
   `docs/PRD.md`, `docs/SPEC.md`, and `docs/plan.md` in the same task.
9. **Check `docs/plan.md` first** for pending work before proposing something new.

## Code Standards

- **Backend:** Python 3.10+, Pydantic v2 for API schemas, psycopg3 in
  transactional mode (`prepare_threshold=None`, pooler-friendly), parameterized
  queries only (`%s`), structured logging via the `raas` logger.
- **Frontend:** React 19 function components + hooks, Tailwind 4, oxlint clean
  of errors, Vitest + React Testing Library.
- **Money math is exact** — integer cents in `utils/format.js`, `::numeric`
  rounding in SQL. No float drift.
- **API errors:** real HTTP statuses; validation → 400 `{error, details:[{field, message}]}`;
  auth → 401/403; rate limit → 429 + `Retry-After`.
- **Schema/migrations:** idempotent `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` in
  `raas_tracker/db.py:_create_tables()`. Schema-signature fast path skips DDL
  (`raas_schema_sig` in `app_settings`).
- **Seeds** run per-connection when tables are empty, so tests can truncate safely.

## Security — non-negotiable

- **Never** hardcode secrets, keys, passwords, or connection strings. Env vars only.
- **Never** commit `.env*` (only `.env.example`).
- **Never** disable or delete an access-control check to make an error go away.
- **Always** validate input server-side (Pydantic). Never trust the client.
- **Always** use parameterized SQL. Never f-string SQL.
- **Always** enforce auth on protected routes (`_gate_api`, `@admin_required`).
- **Rate limits** on mutating/heavy routes (15/min; 300/min default).
- **Cookies:** HttpOnly, SameSite=Lax, `Secure` in production.
- **Audit** every mutating action with actor + old/new values.
- **Secrets live in env only** — never in git, never in chat.

## Auth & Password Security Rules

- **bcrypt-only hashing** — passwords via `_hash_password` (bcrypt-13 over
  SHA256 pre-hash). Never use werkzeug or any other hasher.
- **Constant-time + generic-200 anti-enumeration** — unknown-username logins
  burn `_dummy_hash` bcrypt work; forgot-password returns an identical generic
  200 for known and unknown accounts with a ~200ms delay floor (no dummy-hash
  there — single-sided bcrypt would invert the oracle). Reset tokens are
  256-bit unguessable; only hashes are stored.
- **Admin resets audit + must_change** — force-resets audit
  `ADMIN_FORCE_PASSWORD_RESET`, set `must_change_password=1`, and return the
  temp secret / token exactly once (hash stored, raw never persisted).
- **TTL ≤ 60 min** — reset tokens expire (`reset_token_expires_at`, UTC text);
  redeem uses `SELECT ... FOR UPDATE` so double-redeem loses the race.
- **Sessions revoked** — every password write revokes sessions (voluntary
  change keeps the current session via `except_token_hash`; resets and admin
  force-resets revoke all).
- **`_gate_api` enforcement** — `must_change_password==1` human sessions get
  403 on all `/api/*` except change-password, me, and logout; API keys exempt.

## Off-limits without explicit approval

- `.env` / `.env.*`
- `raas_tracker/auth.py` · `raas_tracker/db.py` (auth + schema core)
- Security gates in `flask_app.py` (`_gate_api`, `_enforce_https`, error handlers)
- Existing migrations · `scripts/migrate_sqlite_to_pg.py` (one-off, complete)

## Git

- **Never `git push` without my explicit instruction.** You commit; I decide when to push.
- Branch: `main`. Clean tree before committing (`git status --short`).

## Context Management

- New task → new session (`/clear`). Context rot degrades quality.
- `/compact <hint>` when context gets heavy: `/compact keep auth decisions, drop test debug`.
- Before switching tasks, write `docs/handoff.md` (or run `/handoff`).
- Broad code search → `@explore` subagent.

## Conventions Observed

- Shell is Windows PowerShell: use `npm.cmd` (not `npm`), no `&&` chaining,
  read the DSN from `[Environment]::GetEnvironmentVariable('DATABASE_URL','User')`
  when verifying saved credentials (this shell's inherited env can be stale).
- Passwords with URL-special characters must be percent-encoded
  (`?` → `%3F`, `@` → `%40`, `&` → `%26`, `+` → `%2B`) in `DATABASE_URL`.

## Custom Commands

- `/start-task <id>` — plan a task before building
- `/security-review` — read-only security review of the last change
- `/review` — explain recent changes and flag risks
- `/handoff` — write session handoff notes

## Skills

- `secure-api-route` — checklist for any new or modified endpoint
- `vertical-slice-task` — each task ships UI + API + DB together
