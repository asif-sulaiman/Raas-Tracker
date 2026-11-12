# Mobile UX Fix Plan — REVISED (final)

Supersedes `O:\Projects\Raas-Tracker\MOBILE_FIX_PLAN.md` (untracked draft).
Revised 2026-11-11 after verifying every claim against the working tree, the
CI workflow, and the live database.

Repo: `O:\Projects\Raas-Tracker` · branch `main` · frontend root `raas-tracker-frontend`

## Verification basis (measured, not assumed)

| Fact | Evidence |
|---|---|
| DB timezone is **UTC** | `current_setting('TimeZone') = UTC`; `now() = +00`; `CURRENT_DATE = 2026-11-11` |
| `sessions.expires_at`, `api_keys.expires_at`, `login_attempts.attempted_at`, `api_key_rate_limits.hit_at` are **TEXT** | `raas_tracker/db.py:946,954,967,976` — lexicographic comparison, `DEFAULT (to_char(NOW(), …))` |
| `sales.maturity_date`, `invoices.maturity_date` are **TEXT**, not DATE | `db.py:1061,1265` |
| `auth.py` has **8** `to_char` sites, reads **and** writes | lines 294, 413, 521, 528, 556, 572, 585, 595 |
| `role="dialog"` occurrences repo-wide | **0** |
| `type="number"` / `inputMode` | 26 / 1 |
| `const inputCls` files | **15** |
| Components with `<table` **and** no `overflow-x-auto` | `ProductionRunModal`, `InvoiceEditor`, `Recipes` (×2), `Reports` |
| Components with `<table>` that already scroll | 12 others, incl. `DataTable` (173, 212), `CommercialTable`, `PurchaseHistory`, `Users`, `Home` |
| `h-screen` sites | 9, in `App.jsx`, `main.jsx`, `Layout.jsx`, `Login.jsx`, `Setup.jsx` (×2), `ResetPassword.jsx`, `ChangePassword.jsx`, `ProtectedRoute.jsx` |
| `Button.sm` height | `px-2.5 py-1.5 text-xs` ≈ 30 px |
| Existing test files | 42; **no** `Modal.test.jsx`, `Sidebar.test.jsx`, `DataTable.test.jsx`; `Header.test.jsx` and `ProductionRunModal.test.jsx` exist |
| CI gates | backend: ruff + pyright + pytest · frontend: oxlint + `vitest run` + `vite build` |
| Production SPA source | `react_frontend/` (built `dist/` is copied there; CI does **not** copy) |

## Approved decisions

1. Scope = **M-0 … M-6**. No backend, no `auth.py` edit → off-limits approval not needed.
2. **B-1 dropped** (no-op: DB already UTC). Re-add only as an optional defensive change.
3. **B-2 dropped** (unsafe: mixed TEXT read/write sites + DDL defaults in off-limits `db.py`).
4. **M-7 merged into M-4** as explicit acceptance criteria.
5. **M-0** reverts `opencode.json` and `.opencode/opencode.json` (dead V2-schema ollama experiment).

## Execution order

`M-0 → M-1 → M-2 → M-3 → M-4 → M-5 → M-6`, one commit each, full gate before commit.
One task per session; `/clear` between slices.

---

### M-0 — Clean tree (10 min, `chore:`)

- `git checkout -- opencode.json .opencode/opencode.json` (revert the ollama experiment).
- Commit this plan file into the repo as `docs: add revised mobile fix plan`
  (move from the plan dir to repo root or `docs/`; keep the original untracked draft deleted).
- Gate: `git status --short` empty.

### M-1 — Mobile navigation (1.5 h, `fix:`)

**Verified problem.** `Sidebar` holds a single `collapsed` boolean doing two jobs:
desktop icon rail (`w-16`, `justify-center`, tooltips) and mobile drawer
(`-translate-x-full`, overlay rendered when `!collapsed`). On a phone it opens
*expanded*, covers the content, and its only toggle slides off-screen with it —
`Header` has no hamburger. Navigation is unreachable without a reload.

**Files.** `src/components/layout/Layout.jsx`, `Header.jsx`, `Sidebar.jsx`,
new `src/components/layout/Sidebar.test.jsx`, extend `Header.test.jsx`.

**Approach.**
- `Layout` owns `desktopCollapsed` (default `false`) and `mobileOpen` (default `false`).
- `useLocation` → close `mobileOpen` on pathname change.
- `Sidebar` takes `open`, `collapsed`, `onClose`; overlay keyed to `mobileOpen` only.
- `Header` hamburger: `lg:hidden`, `aria-expanded`, `aria-controls="app-sidebar"`,
  `aria-label="Open navigation"`.
- Escape closes the drawer; focus returns to the hamburger.
- Desktop rail behaviour byte-identical: `w-16`, `justify-center`, `title` tooltips.

**Tests (red first).** drawer closed by default; hamburger opens; backdrop closes;
nav-item click closes; route change closes; `aria-expanded` mirrors state;
desktop rail still collapses to `w-16`.

**Gate.** `npx oxlint` + `npx vitest run`. Manual: 360×640 — drawer reachable, content not covered on load.

### M-2 — Input sizing + `inputMode` (45 min, `fix:`)

**Verified problem.** 26 `type="number"`, exactly 1 with `inputMode`. Inputs use
`text-sm` (14 px) → iOS Safari auto-zooms on focus.

**Files.** The 15 files with `const inputCls`: `ProductionRunModal`, `InvoiceEditor`,
`InvoiceStepForm`, `LcDetailModal`, `LinkPIsModal`, `MultiPICreateModal`,
`ReviewModal`, `SaleDetailModal`, `ChangePassword`, `Companies`, `Login`,
`Recipes`, `ResetPassword`, `Setup`, `Users` — plus inline `<input>`s found by
grepping `type="number"`.

**Approach.**
- `text-sm` → `text-base sm:text-sm` in each `inputCls` constant.
- `inputMode="decimal"` for money/quantity inputs; `inputMode="numeric"` for integers.
- No shared component introduced; keep the local-constant pattern (minimal diff).

**Gate.** `npx oxlint` + `npx vitest run`. Manual: focus an input at 390 px, page must not zoom.

### M-3 — Modal: dialog semantics + sheet (2 h, `fix:`)

**Verified problem.** No `role="dialog"` anywhere (0 repo-wide), no `aria-modal`,
no focus trap, close button ≈28 px, card centred with `my-8`.

**Files.** `src/components/modals/Modal.jsx`, new `src/components/modals/Modal.test.jsx`,
new `src/hooks/useDialogLayer.js` (shared by `Modal` and the mobile drawer),
`src/components/layout/Sidebar.jsx`, `src/components/layout/Layout.test.jsx`.

**Approach.**
- Below `md`: bottom sheet (anchored to the viewport bottom, rounded top,
  `max-h-[85dvh]`, scrollable body, safe-area bottom padding).
- `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing at the title id.
- Close button ≥44 px hit area (pseudo-element, so layout doesn't shift), `aria-label="Close"`.
- Focus trap: Tab/Shift+Tab cycle inside; focus restored to the trigger on close.
- **Stacking:** modals nest (`ConfirmContext` over detail modals). Track open layers
  in a module-level stack in `useDialogLayer`; only the topmost handles Escape and
  traps focus. Body `overflow` restored only when the stack empties. The M-1 mobile
  drawer uses the same hook, closing known issue #1.

**Tests (red first).** role/aria attributes; Escape closes; Escape on a covered
modal does nothing; Tab wraps from last to first; focus restored to trigger; drawer
traps Tab and restores focus.

**Gate.** `npx oxlint` + `npx vitest run`. Manual: open a modal at 390×640 — sheet
scrolls, close is tappable, background doesn't scroll.

### M-4 — Un-clip the four remaining tables (2 h, `fix:`)

**Verified problem.** Only these lack a scroll wrapper; the other 12 table
components already have `overflow-x-auto`.

| Site | Fix |
|---|---|
| `ProductionRunModal.jsx:328` (5-col) | wrap table in `overflow-x-auto` |
| `Reports.jsx:913` (7-col Stock Availability) | wrap table in `overflow-x-auto` |
| `Recipes.jsx:348`, `1479` | wrap tables in `overflow-x-auto` |
| `InvoiceEditor.jsx:254` (5-col, `text-xs`) | card view below `md`: stacked rows with product header + qty/price/total 2-col grid |

**Also in scope (absorbed M-7).** `Recipes.jsx:378` `grid-cols-3 text-[11px]` →
`grid-cols-1 sm:grid-cols-3` and `text-xs` minimum. `InvoicePanel` / `CommercialTable`
need no structural change (already scroll) — verify only. The real-browser pass found
one more 360 px offender: the recipe detail action row (`Edit` / `Delete` / `Go for
Production`) pushed the page 73 px wide; it now wraps (`flex-wrap` + `min-w-0`).

**Tests.** Extend `ProductionRunModal.test.jsx`, `InvoiceEditor.test.jsx`,
`Recipes.test.jsx`, `Reports.test.jsx`: stacked view renders at small breakpoint
(assert on the DOM branch, not on CSS), table headers still present at `md`, and the
recipe detail action row declares `flex-wrap`.

**Gate.** `npx oxlint` + `npx vitest run` + `npx vite build`. Manual at 360 px:
no horizontal page scroll on Recipes / Reports / InvoiceEditor / Production Run.

### M-5 — Touch targets for icon-only controls (30 min, `fix:`)

**Verified problem.** `Button.sm` ≈30 px; Modal close ≈28 px; Header/Sidebar icon
buttons ≈36 px.

**Approach.** Do **not** enlarge `sm` text buttons (dense tables degrade). Add a
44 px hit area via `after:absolute after:-inset-*` to icon-only controls:
`Modal` close, `Header` (change-password, logout), `Sidebar` toggle, hamburger.
Keep visual size unchanged.

**Files.** `src/components/ui/Button.jsx` (icon-only variant), `Modal.jsx`, `Header.jsx`, `Sidebar.jsx`.

**Gate.** `npx oxlint` + `npx vitest run`. Manual: tap targets ≥44 px measured in DevTools.

### M-6 — Viewport, safe area, installability (1.5 h, `feat:`)

**Verified problem.** `h-screen` in 9 places (not just Layout) → content cut by the
mobile address bar. `index.html` lacks `viewport-fit=cover`, `theme-color`,
`manifest`, `apple-touch-icon`. No manifest → not installable.

**Files.** `index.html`, `Layout.jsx`, `App.jsx`, `main.jsx`, `Login.jsx`,
`Setup.jsx`, `ResetPassword.jsx`, `ChangePassword.jsx`, `ProtectedRoute.jsx`,
new `public/manifest.json`, new `public/icons/icon-192.png`, `icon-512.png`, `apple-touch-icon.png`.

**Approach.**
- `h-screen` → `h-dvh` at all 9 sites.
- `index.html`: `viewport` → `width=device-width, initial-scale=1, viewport-fit=cover`;
  add `theme-color`, `link rel="manifest"`, `link rel="apple-touch-icon"`.
- `Layout` main area: `pb-[env(safe-area-inset-bottom)]`.
- `manifest.json`: name/short_name/`start_url:"/"`, `display:"standalone"`,
  `theme_color`/`background_color`, icons 192/512.
- Icons: downscale `public/raas-logo.png` (already PNG, 32 KB) to 192/512/180.
  No SVG conversion needed.
- **Deploy step:** `npx vite build` then copy `dist/` → `react_frontend/`
  (AGENTS.md build step; CI does not do this). Without it production keeps serving
  the old shell and the manifest 404s.

**Gate.** `npx vite build` · `npx oxlint` · serve Flask and confirm
`/manifest.json` + `/icons/icon-192.png` return 200 · Lighthouse mobile
(Accessibility ≥90, Best Practices ≥90, installable).

---

## Dropped, with reasons

| Item | Reason |
|---|---|
| **B-1** `sales.py` CURRENT_DATE → UTC | DB is already UTC; `CURRENT_DATE` is correct today. Optional 20-min defensive change only |
| **B-2** `auth.py` `to_char` → UTC | 8 mixed read/write sites on TEXT columns + `DEFAULT` expressions in off-limits `db.py`. Converting reads only would corrupt expiry semantics under a non-UTC DB. Zero current benefit |
| **M-7** separate polish slice | Merged into M-4 with acceptance criteria; a criteria-free "polish" slice absorbs time |
| DataTable card view | Already has `overflow-x-auto` (173, 212) |

## Risks / watch-list

- **Nested modals** are the sharp edge of M-3 — `ConfirmContext` and
  `NotificationContext` open dialogs over other dialogs. Escape and body-scroll
  must be stack-aware or confirmations will close the wrong layer.
- **Tailwind tests are not layout tests.** jsdom asserts behaviour only; every
  breakpoint claim needs a 360×640 manual pass. Do not mark a slice done on green tests alone.
- **`react_frontend/` drift.** Slices verified against `npm run dev` show the new UI;
  the Flask-served SPA stays old until `dist/` is copied. Copy once at the end of M-6.
- **`opencode.json` experiment** must be reverted before the first commit, or it
  rides along in an unrelated `fix:` commit.

## Per-slice template

```
1. /clear, /start-task M-x
2. failing test(s)          (red)
3. implement                (green)
4. npx oxlint && npx vitest run && npx vite build
5. manual 360x640 check     (screenshot)
6. git commit -m "fix: M-x <desc>"
7. update docs/plan.md + /handoff if switching
```

Estimated total: **1.5–2 days** (down from 2.5–3).
