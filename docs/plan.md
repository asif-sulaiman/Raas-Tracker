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
| M7.1 | **Upload Size Guard** — Env-configurable `MAX_CONTENT_LENGTH` (default 50MB, Vercel 4.5MB override) + clean 413 JSON | `flask_app.py`, `wsgi.py`, `.env.example` | `MAX_CONTENT_LENGTH_Vercel` env respected; 413 JSON on Vercel |
| M7.2 | **Frontend Error Boundary Logging** — Send React errors to `/api/notifications` (type `frontend_error`) | `ErrorBoundary.jsx`, `NotificationContext.jsx`, `flask_app.py` | Uncaught React errors appear in admin notifications |
| M7.3 | **Recipe Item `required_qty_per_unit` Persistence** — Store computed `percentage * total_quantity / 100` on create/update | `recipes.py`, `flask_app.py` (recipe item PUT), `recipes.jsx` | Field persisted, editable, used in reports |
| M7.4 | **Sales Duplicate PI Warning → Configurable** — Add `ALLOW_DUPLICATE_PI` env (default true) to suppress warning | `flask_app.py:_duplicate_pi_warning`, `.env.example` | Env controls warning behavior |
| M7.5 | **Frontend Oxlint Cleanup** — Fix 39 baseline warnings (unused imports, exhaustive-deps, refs in render) | `raas-tracker-frontend/src/**/*.jsx` | `oxlint` → 0 warnings |
| M7.6 | **Vercel `public/` Assets Verify** — Ensure `favicon.svg`, `icons.svg` served with correct MIME | `vercel.json` (buildCommand), `public/` | `/favicon.svg` → `image/svg+xml` |
| M7.7 | **OpenAPI Spec Generation** — Add `flask-openapi3` or manual `openapi.json` for client SDKs | New file `openapi.json` (generated) | Valid OpenAPI 3.0 spec for all `/api/*` |
| M7.8 | **Add Chemical UI (admin-only)** — Add Chemical modal on `/chemicals` (name, qty, unit, reorder); Pydantic validation + 403 backstop on `POST /api/chemicals`; Adjust button + `update`/`reorder` endpoints admin-only | `flask_app.py`, `Chemicals.jsx`, `utils/units.js`, `tests/test_chemicals_api.py`, `Chemicals.test.jsx` | Admin adds/adjusts stock end-to-end; 400/403/409 covered; gate green |

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