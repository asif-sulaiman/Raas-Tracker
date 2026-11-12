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

## 🔒 Security hardening — Phase 1: authorization ✅ · Phase 2: numeric integrity 🔄

A read-only audit produced a 5-phase plan. **Phase 1 is complete**; Phase 2 is
authorised and in progress. Phases 3–5 are deferred. Each sub-phase is gated by the
full backend suite plus an Oracle review, and the working state lives in
`.slim/deepwork/phase1-authorization.md` and `.slim/deepwork/phase2-numeric-integrity.md`.

| Sub-phase | Scope | Gate |
|---|---|---|
| 0 | Pre-flight: scan all 20 PostgreSQL `REAL`/`double precision` columns for non-finite values before the Phase 2 numeric work | ✅ 0 non-finite rows |
| P1-A | Explicit role classification — `operator_or_admin_required` stamping `_raas_role`; every mutating route declares its class; `_ADMIN_PATHS` demoted to non-load-bearing defence in depth; `POST /api/sales/batch` demoted admin → operator to match `POST /api/sales` | Oracle ✅ PASS (commit `9e3d713`) |
| P1-B | Volume control — `limited()` stamps `_raas_limit` at 15/min on every mutating operator route, the invoice lifecycle, and the admin writes with row fan-out; `max_length` on 7 row-fanning list fields; report `recipes` capped inline with `{error, details[]}` | Oracle 🔴 blocked twice → all blockers remediated → ✅ PASS (commit `5afd27b`) |
| P2-A | Input boundary — app-wide `_FiniteJSONProvider` (`app.json`) rejecting the bare `NaN`/`Infinity`/`-Infinity` literals; shared `_FiniteModel` base (`allow_inf_nan=False`) across the 8 money/quantity write models; `@app.errorhandler(400)` so an undecodable body is a 400 and not a 500; `_req_float` on the two raw-dict numeric routes | Oracle ✅ APPROVED, no HIGH findings (2026-10-14) |
| P2-B | Output boundary — `math.isfinite()` in `record_sale_payment`, `mark_invoice_paid`, `add_sale_item`, `update_sale_item`; the layer that holds when the parser is bypassed | ⬜ planned |

### Phase 4 — unauthenticated surface (`vercel.json` headers + typed errors)

| Sub-phase | Scope | Gate |
|---|---|---|
| 3 | Gate integrity — stage barriers, invoice freeze, 5% tolerance | Oracle ✅ (commit `3307b22`) |
| 4-A | Typed exceptions (`raas_tracker/_exceptions.py`, `status_for`) replace message-wording status coupling; 5% tolerance breach standardised to 409 | Oracle ✅ (commit `6f158b2`) |
| 4-B | Edge response headers in `vercel.json` (nosniff / DENY / X-XSS-Protection / `no-referrer` / HSTS / Permissions-Policy / **hash-allowlisted CSP**) + `@app.errorhandler(415)` so a wrong Content-Type is 415, not 500 | ✅ committed `b645a3d` (Oracle 2/2, zero code defects) |
| 5 | IDOR + limits — bind payment records to their sale in URL (`api_edit_payment`/`api_delete_payment` verify `sale_payments.sale_id` → 404 on mismatch); fix maturity cron `HAVING` compare (join fan-out between `sale_payments` × `sale_items` inflates both sums → two independent correlated subqueries) | ✅ committed (TDD: 3 RED → 3 GREEN; full suite 1233/8) |
| 5-fu | Phase 5 follow-up (Oracle P1/P2/P3) — item PUT binds `sale_id` (3-valued return: `None` not-found → 404, `False` empty-patch → 200, `True` updated); cron paid side `ROUND(::numeric, 2)` so a REAL sum of non-binary-exact amounts (e.g. 8.2 → 8.199999809265137) cannot spuriously under-pay; plan.md TDD/call-site counts corrected | ✅ committed (TDD: 2 RED → 3 GREEN; full suite 1236/8) |

**4-B decisions worth not re-deriving**

| Decision | Choice | Why |
|---|---|---|
| Which responses carry the headers | One rule, `source: "/((?!api/).*)"` — every non-`/api` path | Deliberately **not** a copy of the rewrite's `/((?!api/|.*\.).*)`. The rewrite excludes dotted paths so `/assets/*.js` is served as a file; that exclusion is right for routing and wrong for headers, since a hashed bundle needs `nosniff` as much as `index.html` does. `/api/*` stays excluded — those responses are JSON and already carry the app's own controls. Pinned by `tests/test_edge_hardening.py` against the live `url_map`, so a route added later is covered automatically |
| HSTS | `max-age=31536000; includeSubDomains`, **no `preload`** | One year is the OWASP floor and HSTS only protects once cached. This is the **only** HSTS the production host receives — Flask's copy is gated on `FORCE_HTTPS == "1"` and `FORCE_HTTPS` is unset in `vercel.json`'s `env` — so it is the whole of the protection, not a duplicate. `includeSubDomains` is a no-op on the current host (previews get `*.vercel.app` names) and is kept for the custom-domain case, with the trap recorded in SPEC §6.3.1: on an apex domain it would force HTTPS on every sibling subdomain, reversibly only by waiting out the browser cache. `preload` would claim a preload-list commitment nobody has made |
| `Referrer-Policy` | `no-referrer` | Reset links carry a token in the URL (`/reset?token=…`) and `ResetPassword.jsx` then fetches `/api/auth/reset-password` same-origin, so any less-strict value put the **full** token URL into this app's own access log — while the cross-origin half of the guarantee had no consumer to protect. Nothing under `flask_app.py` or `src/` reads `document.referrer`/`Referer`, and there is no analytics, no cross-origin asset and no external origin at all, so `no-referrer` costs nothing measurable. Set in **both** layers so they agree; recorded in SPEC §6.3.1 |
| **`Content-Security-Policy`** | **Shipped at the edge, hash-allowlisted** | Copied verbatim from `flask_app.py`, which had shipped it all along. The deferral rested on a false premise: that Vercel "cannot hash or nonce" the inline theme script because the header is static. A `sha256-…` source expression is a **compile-time constant** — exactly what a static header can express — and no nonce is involved. The cost of the error was concrete, not theoretical: the edge serves `index.html` and `/assets/*`, so the policy had never once applied to the responses it exists for. `script-src` is `'self'` + the recomputed theme-bootstrap digest, **not** `'unsafe-inline'`, which would also disable the hash mechanism. `style-src` keeps `'unsafe-inline'` — but **not** because of the three `style={{…}}` props, which React writes through the CSSOM and which CSP does not govern. The actual consumer is `sonner`, which injects its toast stylesheet as a `<style>` element at runtime, and an injected `<style>` *is* blocked: tightening this silently strips every toast's styling. Getting that stylesheet into the build is the remaining frontend work. Three tests hold the edge and Flask copies identical, including byte-for-byte; SPEC §6.3.2 |
| 415 scope | Dedicated by-code handler beside 413; body `{error}` only, no `details`, no `code` field | A wrong Content-Type is a distinct client fault from an undecodable body (400), so it gets its own status. By code, not by class: Flask matches the by-class bucket by MRO and consults it for every status without a code handler, so a class entry is a far wider fallback than the status it was written for |


**P2-A gate findings, and what closed them**

| Finding | Problem | Resolution |
|---|---|---|
| MEDIUM-1 | `PUT /api/chemicals/reorder` has no Pydantic model, so neither wall applied: `float(data.get("reorder_level", 0) or 0)` accepted a quoted `"Infinity"`, and `stock.py:295` only rejects `level < 0` — `inf < 0` is False. It committed `inf` into a `REAL NOT NULL` column permanently, after which every `GET /api/chemicals` serialised invalid JSON | Routed through `_req_float`, which carries `math.isfinite`. Sweeping for the same shape found `PUT /api/recipes/<name>` (`total_quantity` had **no** guard; `water_percentage` had `0 ≤ v ≤ 100`, which `inf` trips but `nan` walks straight through) — same fix |
| MEDIUM-1b | Same sweep, found while fixing the above: all four recipe routes coerce a raw-dict `company_id` with `int(...)` under `except (TypeError, ValueError)`. `int(float('inf'))` raises **`OverflowError`**, which that tuple does not catch — so `{"company_id": 1e309}` reached the catch-all `Exception` handler as a 500, the exact shape the new 400 handler exists to prevent. No data was written (the route aborts before the UPDATE), so this is an error-shape defect, not a poisoning one | `OverflowError` added to all four `except` tuples |
| MEDIUM-2 | SPEC.md omitted the new 400 handler, the parser rejection and the `_FiniteModel` contract from its error-handler set; `plan.md` still said "Phase 1 only is authorised" | SPEC §6 gained the 400 handler and a new §6.1 "Numeric integrity"; this table |
| LOW-1 | `_bad_request`'s comment claimed it covered "a body whose content type is not JSON" — it does not (that is a 415 with no handler, pre-existing and out of scope) | Clause removed; the gap is now stated in the comment so it is not mistaken for coverage |
| LOW-2 | A test docstring cited `app.json_provider`, an attribute that does not exist in Flask 3.x | Corrected to `app.json` |
| Hygiene | `_FiniteFlask` subclass existed only to set `json_provider_class`; `app.json` is a plain attribute | Subclass dropped — `app.json = _FiniteJSONProvider(app)`, so `type(app) is Flask` is True again. The `loads()` override that installs `parse_constant` is load-bearing and now says so |

**P1-B blockers found by the first Oracle pass, and what closed them**

| Finding | Problem | Resolution |
|---|---|---|
| HIGH-1 | `POST /api/recipes/<name>/produce` unthrottled (300/min) and `sale_ids`/`invoice_ids` uncapped — the route had the *largest* fan-out (one round trip per id) while the `recipes` field beside it got the cap | `@limited()` + `max_length=200` on both id lists |
| HIGH-2 | `PUT /api/sales/<id>` was the only mutating route with an unbounded rate **and** two uncapped row-fanning fields, on the money-bearing tables | `@limited()` + `max_length=200` on `items` and `removedIds` |
| HIGH-3 | `POST /api/sales` — the route P1-A opened to operators — got a rate bound but no row bound; the operator backstop admits API keys | `items` capped at 200 |
| MED-1 | `POST .../invoices/batch` is N×M per request with both factors uncapped — the financial write path | `invoices` 50 / `lines` 200 |
| MED-2 | The admin half of `VOLUME_BOUNDED_ROUTES` had no drift guard, so the list could silently diverge from reality | `test_volume_bounded_list_matches_the_stamped_routes` mirrors P1-A's role guard |
| MED-3 | **21** routes used a bare `@limiter.limit("15 per minute")` — genuinely limited, but invisible to the stamp, while SPEC claimed they were declared | All converted to `@limited()`; SPEC is now true |
| MED-4 | `ProductionRunCreateIn.recipes` shipped with zero tests | Dedicated regression test |
| LOW-1 | `WRITE_LIMIT_PER_MINUTE` was defined twice; editing one copy would change nothing and be caught by nothing | Merged to a single definition |
| LOW-2 | The `_stamps()` docstring claimed `_raas_limit` lived "one `__wrapped__` hop down". Oracle proved `functools.wraps` copies both stamps onto the registered view, so the walk is currently a no-op — and a maintainer reading the wrong reason would delete it | Corrected; the walk is kept for either decorator order and non-`wraps` outer wrappers |
| LOW-3/4 | Report cap returned a bare `{error}` (AGENTS.md mandates `details[]`), and `len()` on a non-list body counted characters and then 500'd | Both fixed; cap still precedes the per-item shape loop so rejection is O(1) |
| LOW-6 | Only SPEC was updated; AGENTS.md rule 8 requires `plan.md` in the same task | This section |

**P1-B blockers found by the second Oracle pass, and what closed them**

| Finding | Problem | Resolution |
|---|---|---|
| HIGH-1 | `InvoiceCreateIn.lines` uncapped—the single-invoice twin of the capped `InvoiceBatchEntryIn.lines`, on a route this change newly stamped | `max_length=200`, added to `ROW_FANNING_CAPS` and the SPEC table |
| MED-1 | `_stamps()` used `out.setdefault(...)`, which writes on the outermost frame even when it has no stamp—latching `None` so the inner value could never surface | Changed to an explicit `None` check, and pinned by `test_stamps_survive_a_partial_functools_wraps`. Both false claims in the docstring were also corrected |
| MED-2 | `test_every_route_using_a_15_per_minute_limit_is_stamped` was a tautology (`sm—sg` by construction, and the drift guard already asserts `sm == declared`) | Deleted |
| MED-3 | `LcAttachIn.sale_ids` uncapped: ~5 round trips per id inside the LC lock, and each `pi_issued` sale advances to `lc_received` | `max_length=200` |
| MED-4 | The drift guard's docstring overclaimed—it is one-sided and cannot catch an admin write with no limit at all | Docstring now states the one-sidedness; the ~27 unthrottled admin routes are recorded as a tracked gap below |
| LOW-2 | `plan.md` said 14 conversions; the diff has 21 converted + 14 net-new | Corrected |

**Scope decisions taken (user-ratified)**

| Decision | Choice |
|---|---|
| Product cap on nested batch payloads | **Added `MAX_BATCH_ROWS = 2000`**—a `field_validator` on `SaleBatchIn.sales` and `InvoiceBatchCreateIn.invoices` capping total rows, rather than shrinking the per-factor caps (which would penalise single-sale entry). Raises through the existing `{error, details:[{field, message}]}` path |
| Extending the volume policy to all ~27 unthrottled admin routes | **Left for a later phase**—P1-B stays the operator + invoice-lifecycle policy. Recorded below as a tracked gap, not silently dropped |
| Client-side cap on `MultiPICreateModal` | **Added**—`MAX_DOCS = 50` disables "Add PI document" at the cap, and `validate()` rejects an over-cap set before the request, so the UI cannot build a request the server will refuse **on document count**. The per-PI item cap (200) and the total-row budget are NOT mirrored — an over-long PI still surfaces as a server 400 in the banner |

**Open scope decisions (not silently decided)**

1. ~~**Product cap on nested batch payloads.**~~ **CLOSED** — `MAX_BATCH_ROWS = 2000`
   added (see the ratified table above).
2. **~27 admin mutating routes remain on the 300/min default**, including the
   money-bearing ones (`PUT/DELETE /api/sales/<id>/items`, `PUT .../payment`,
   `PUT .../payments/<id>`, `POST .../shipments`, `PUT .../move`). SPEC is accurate— it claims only "every mutating operator route and the whole invoice lifecycle"— but P1-A's thesis was that opt-in-by-omission is the bug, so this is the same shape.
3. **`POST /api/uploads/<id>/approve|apply`** processes every parsed row of an uploaded
   file with no row cap (`chem_stock.py` has no `MAX_ROWS`). 15/min bounds the request
   rate, not the row set.

**Known gaps carried forward (tracked, not closed):**
- ~27 admin mutating routes remain on the 300/min default—see the scope table below.
- `POST /api/recipes/<name>/produce` — `recipes` is capped at 50 rows, but each row
  expands to a run **plus its whole BOM** (`update_stock` + insert per ingredient).
  ~1 000 ingredients in one transaction, and `MAX_BATCH_ROWS` deliberately does not
  cover this route.
- `MultiPICreateModal` mirrors only the 50-document cap; the per-PI item cap (200) and
  the total-row budget are not mirrored, so an over-long PI surfaces as a server 400.
- `DELETE /api/sales/<id>` — a money-bearing cascade delete — remains on the 300/min
  default.
- `POST /api/uploads/<id>/approve|apply` processes every parsed row of an uploaded file
  with no row cap (`chem_stock.py` has no `MAX_ROWS`); 15/min bounds the request rate,
  not the row set.

---

## ✅ Multi-PI under a single LC (2026-10)

Delivered in four phases, each gated by a full test run + an Oracle review.

| Phase | Scope | Gate |
|---|---|---|
| 1 | Schema foundation — `letters_of_credit` (UNIQUE company+number), `sales.lc_id`, `invoice_items`, idempotent legacy auto-group backfill, `_SCHEMA_VERSION` 1→2 | Oracle ✅ (1 re-review-free remediation pass) |
| 2 | Domain services + API — `lcs.py` (CRUD, attach/detach, stage propagation with `FOR UPDATE`), `POST /api/sales/batch`, invoice-line CRUD with server-owned price, `lc_received → shipment_ongoing` gate | Oracle ✅ (attempt 1 blockers → 1 residual → closed with evidence) |
| 3 | Frontend — LC board card with nested PIs, multi-PI create, LC linking, LC detail modal, partial-qty invoice line editor | Oracle ✅ PASS |
| 4 | Reporting shift — commercial report/summary/exports compute quantity, total and due from `invoice_items` (row grain and columns unchanged, `lc_id` filter); LC filter in Reports; docs updated | ✅ gate green |

**Business rules now in force**
- A proforma is an offer, not a receivable: `due = max(invoiced_total − received, 0)`.
- Invoice line quantity is independent of the PI — partial, split across invoices, or abandoned. Nothing records "abandoned"; an uninvoiced quantity never becomes due.
- Unit price is always inherited from the PI line and enforced server-side.
- An LC owns the journey for its PIs; all PIs under one LC progress together.
- PIs without an LC are transient — created in batches, linked later, deleted if the LC never arrives.

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
| M7.1 ✓ | **Upload Size Guard** — Env-configurable `MAX_CONTENT_LENGTH` (default 50MB via `MAX_CONTENT_LENGTH_MB`, Vercel 4.5MB override) + 413 JSON that reports the real cap | `flask_app.py`, `.env.example`, `docs/SPEC.md` | `MAX_CONTENT_LENGTH_MB` env respected (floats OK); 413 JSON message matches configured cap |
| M7.2 | **Frontend Error Boundary Logging** — Send React errors to `/api/notifications` (type `frontend_error`) | `ErrorBoundary.jsx`, `NotificationContext.jsx`, `flask_app.py` | Uncaught React errors appear in admin notifications |
| M7.3 ✓ | **~~Recipe Item `required_qty_per_unit` Persistence~~** — Obsolete 2026-11-01: v8 dropped the column with the product/quantity model. Items store `percentage` only and the per-unit requirement is derived at read time (`percentage / 100` scaled by the run quantity), so there is nothing to persist | — | Superseded by Phase INVOICE-SCOPED-RECIPES |
| M7.4 | **Sales Duplicate PI Warning → Configurable** — Add `ALLOW_DUPLICATE_PI` env (default true) to suppress warning | `flask_app.py:_duplicate_pi_warning`, `.env.example` | Env controls warning behavior |
| M7.5 ✓ | **Frontend Oxlint Cleanup** — Fix 39 baseline warnings (unused imports, exhaustive-deps, refs in render) | `raas-tracker-frontend/src/**/*.jsx`, `.oxlintrc.json` | `oxlint` → 0 warnings |
| M7.6 | **Vercel `public/` Assets Verify** — Ensure `favicon.svg`, `icons.svg` served with correct MIME | `vercel.json` (buildCommand), `public/` | `/favicon.svg` → `image/svg+xml` |
| M7.7 | **OpenAPI Spec Generation** — Add `flask-openapi3` or manual `openapi.json` for client SDKs | New file `openapi.json` (generated) | Valid OpenAPI 3.0 spec for all `/api/*` |
| M7.8 | **Add Chemical UI (admin-only)** — Add Chemical modal on `/chemicals` (name, qty, unit, reorder); Pydantic validation + 403 backstop on `POST /api/chemicals`; Adjust button + `update`/`reorder` endpoints admin-only | `flask_app.py`, `Chemicals.jsx`, `utils/units.js`, `tests/test_chemicals_api.py`, `Chemicals.test.jsx` | Admin adds/adjusts stock end-to-end; 400/403/409 covered; gate green |
| M7.9 | **Stock History (admin-only)** — Datewise inventory movements on `/chemicals` (range calendar + Day/Week/Month tabs, chart + feed with purpose); reason persistence, `ADD_CHEMICAL` birth audit, `GET /api/chemicals/history` | `stock.py`, `db.py`, `flask_app.py`, `StockHistory.jsx`, `utils/history.js`, `Chemicals.jsx` | Movements with purpose render grouped; filters work; gate green |
| M7.10 | **Low-stock alarm control** — Alarm editor inside Info modal (admin), reorder editing removed from Adjust (qty-only), "Low-stock alarm" relabels, buy alert on silent-add | `Chemicals.jsx`, `flask_app.py`, `tests/test_chemicals_api.py`, `Chemicals.test.jsx` | Alarm set per chemical; below-alarm notifies; gate green |

---

## 🔐 Phase P0: Audit Attribution Hardening (Completed 2026-10-06)

Fixes audit-trail defects found by a read-only security review.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| P0-1 ✓ | **Actor no longer leaks across requests** — `_gate_api` seeds `anonymous` at the *top* of every `/api` request (before the retired-token branch) and a `teardown_request` hook clears the thread-local. Pre-fix a public `forgot-password` recorded `user_id='admin'` (the last authenticated user on that waitress thread) | `flask_app.py`, `raas_tracker/audit.py` | Public/rejected writes record `anonymous`, never a prior user |
| P0-2 ✓ | **Audit `ip_address` populated** — defaults to the request's `remote_addr` for all 62 call sites; a caller-supplied value still wins (`ADJUST_STOCK` reason) | `raas_tracker/audit.py` | Mutating rows carry an IP; no per-call-site opt-in needed |
| P0-3 ✓ | **Distinct actor labels** — `username` / `api-key:<name>` / `anonymous` / `cron` / `system`; `_actor()` falls back to `anonymous`, never `system` | `flask_app.py`, `raas_tracker/audit.py` | A verified write can never be downgraded to `anonymous` |
| P0-4 ✓ | **Maturity cron audited** — `CRON_MATURITY_CHECK` row with `checked`/`notified`; was the one mutating route with no audit trail | `flask_app.py` | Every cron run leaves a row attributed to `cron` |
| P0-5 ✓ | **Conn-leak fix in the audited legacy-token path** — `conn.close()` in a `finally`; previously skipped whenever the INSERT failed | `flask_app.py` | No pooled connection lost per rejected request |

---

## 🔐 Phase P1: Audit Coverage & Value Bounds (Completed 2026-10-06)

Baseline established by enumeration: **64 mutating routes — 49 covered, 1 partial,
10 uncovered**, 4 read-only POSTs. Payloads classified across all 62 call sites:
**zero** secrets and effectively zero PII (every `auth.py` row logs a username;
`redeem_reset_token` logs `row[1]`=username, one column from the hash).

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| P1-1 ✓ | **Credential lifecycle audited** — `API_KEY_CREATE` / `API_KEY_REVOKE`; `create_api_key` gained `RETURNING id`. Raw key and hash never audited | `raas_tracker/auth.py`, `tests/test_api_key_audit.py` | Mint/revoke leave a row naming the admin and the key name |
| P1-2 ✓ | **Unit-conversion factors audited** — `UNIT_CONVERSION_UPSERT` with old→new factor. Route was unaudited *and* reachable by any session **or API key** | `raas_tracker/stock.py`, `tests/test_stock_audit.py` | Factor changes leave a before/after trail |
| P1-3 ✓ | **Central value bounds** — `_bounded()` caps every text column, strips control chars **including CR**, marks truncation. Also fixed: `LEGACY_TOKEN_USED` used the request path as `entity_type`; `SALE_UPDATE` leaked `pi_file_path` on **both** paths | `audit.py`, `flask_app.py`, `sales.py`, `tests/test_audit_limits.py` | No unbounded value; a NUL can no longer abort the INSERT |
| P1-6 ✓ | **Reason moved out of `ip_address`** into `new_value` as `"<qty> (<reason>)"`, restoring the real client IP. Stock-history feed taught to parse it, with a fallback so pre-P1-6 rows still render | `stock.py`, `uploads.py`, `tests/test_stock_audit.py` | Adjustments record reason **and** IP; history keeps delta + purpose |
| P1-4 ✓ | **Company field coverage** — `COMPANY_UPDATE` was gated on a *name* change, so moving `swift`/`lc_bank`/`address`/`contact_person`/`code`/`country` left no trace. Records the changed **field names** (values would push bank/contact PII into the audit trail), only for genuine changes. Patch fields derived from `_FIELDS` so a new column cannot skip auditing | `companies.py`, `tests/test_company_audit.py` | Any field edit leaves a row; no-op resubmits do not; no bank/contact value in `audit_logs` |
| P1-5 ✓ | **Admin deletes + kill switch audited** — `SALE_ITEM_DELETE`, `SHIPMENT_DELETE`, `UPLOAD_DELETE` (with cascaded row count), `SESSION_REVOKE` (with live-session count). Each captures an identifying summary read **before** the DELETE, since the delete destroys its own evidence; gated on `rowcount` so a refused or no-op delete leaves no row. Revoke audited at the admin route only, not in the shared `revoke_user_sessions`. Follow-ups: records `runs=N` — the production runs the cascade destroys, placed **first** so the 2000-char bound can never clip it — and names the PI; the delete is scoped by `sale_id`, so a line cannot be removed through another PI's URL, and 404s when nothing was deleted; the kill switch 404s an unknown user and carries `@admin_required` | `sales.py`, `flask_app.py`, `tests/test_delete_audit.py` | Every admin delete leaves a truthful row; `AuditLogs.jsx` renders `old_value` so the evidence is actually visible |
| P1-7 ✓ | **`X-Forwarded-For` trust gated** — `ProxyFix`'s `x_for` is now conditional on `TRUSTED_PROXY=1`; unset means the header is ignored and `remote_addr` is the socket address. Scope was **wider** than recorded: `remote_addr` gates the API-key IP allowlist (an authorisation bypass), login lockout-by-IP **and** Flask-Limiter's IP-keyed buckets. `docker-compose.yml` binds 5000 to loopback and now expects a TLS proxy in front. `x_proto`/`x_host`/`x_prefix` deliberately stay unconditional — they drive the HTTPS redirect and URL building, where over-trust is a correctness issue, not an authz bypass | `flask_app.py`, `docker-compose.yml`, `.env.example`, `tests/test_proxy_trust.py` | A forged header cannot satisfy the API-key IP allowlist; a declared proxy still yields real client IPs |

**Corrections made during P1** (recorded because both were stated earlier and were wrong):

- ~~P1-d: `api-key:<name>` is unreachable~~ — **wrong**. `POST /api/sales`,
  `/api/recipes`, `/api/recipes/<name>/items` have no admin gate, so API keys
  reach audited mutations. Now proven by a test that stamps a real key.
- ~~Flip the `atomic` default~~ — rejected; would silently lose audit rows.

- ~~P1-13 ~~ and ~~P1-16~~: **both closed** — see the corrected backlog rows
  below, which state exactly what is fixed and which two commit-before-audit
  sites are deliberately left (load-bearing route-level commits). The audit
  listing now orders by `id`, recorded in "P1-18 data migration" below.
- ~~P1-13 as originally written: "8 of 62 sites pass `atomic=False`; review the
  other ~54 for ghost rows that survive a rollback"~~ — **inverted.** With
  `if atomic: conn.commit()` (`audit.py:141-142`), `atomic=True` is the
  *committing* mode: it flushes the caller's entire open transaction. `False`
  leaves the INSERT inside the caller's transaction, where a rollback takes it
  along — the safe direction. Ghost rows and partial commits are therefore an
  `atomic=True` problem. Counts restated as **69** sites (50 `True` / 18
  literal `False` / 1 forwarded at `stock.py:278`). Worst site is
  `uploads.py:891`, inside the `adjust_stock_from_upload` row loop, whose
  `except` at `uploads.py:906` returns `False` without a rollback.
  The wording was corrected first, then P1-13 was **closed** for the enumerated
  sites — see the corrected row above.

**Tail batch completed** — P1-8, P1-10, P1-12, P1-14, P1-15 (see git log).

### P1-18 data migration (decided, implemented)

The DEFAULT change is **DDL**, and `CREATE TABLE IF NOT EXISTS` is a no-op
against an already-migrated table — correcting the format inside the CREATE
statements alone would have fixed only a *fresh* database while every live
deployment kept writing malformed timestamps. So `_SCHEMA_VERSION` went 3 → 4
and `_fix_timestamp_defaults()` re-asserts all 21 DEFAULTs with idempotent
`ALTER TABLE ... ALTER COLUMN ... SET DEFAULT`. Verified against a database
migrated by the pre-fix code: version 3 → 4 detected, all 21 live DEFAULTs
repaired, correct format on a new row. The test suite builds a fresh database
every run and could **not** have caught this — hence
`test_live_column_default_uses_the_correct_format` asserts against
`information_schema`.

| Table | Action | Why |
|---|---|---|
| `login_attempts` | **DELETE all rows** | Ephemeral, rebuilt on the next failure. A malformed row whose month-slot read as "in the future" made the lockout *under*-count, so reinterpreting it was not an option. |
| `api_key_rate_limits` | **DELETE all rows** | Same, and a leaked hit is a rate-limit bypass. |
| `audit_logs` | **untouched — no backfill, no rewrite** | Immutable ledger. Historical `timestamp` values keep their malformed shape **by decision**; the ledger stays byte-untouched. |
| All business `created_at` / `updated_at` / `hit_at` / `read_at` / `reviewed_at` / `changed_at` / `upload_date` | historical rows left as-is, **DEFAULT corrected going forward** | These are display/sort data with no control attached; rewriting history is not worth the risk. |
| `sessions.expires_at`, `api_keys.expires_at` | **no migration needed** | Written by Python `strftime("%Y-%m-%d %H:%M:%S")`, which was always correct. They became *correct in comparison* once the SQL floor was fixed. Confirmed, not assumed. |

**Resolved (the residual previously flagged here):** the audit-log listing now
orders by `id` — `raas_tracker/audit.py:180`, `ORDER BY id DESC`. Deciding
between backfilling the ledger and reordering was unnecessary: backfilling is
still rejected (the ledger is immutable), so the listing uses `id`, the
append-only IDENTITY that is both monotonic and the order rows were actually
written in. The `timestamp` column is still returned as evidence, just never
used as a sort key. `/api/audit-logs` therefore reads newest-first for rows
spanning the migration and for rows written across an afternoon (which the old
textual sort inverted outright, `HH` being a 12-hour clock). Pinned by
`test_audit_listing_orders_by_insertion_not_by_the_timestamp_text`, which
inserts two rows whose timestamps sort the opposite way to their ids.
Historical `audit_logs` rows remain untouched — no rewrite, ever.

**Remaining backlog (not started):**

| Task | Description |
|---|---|
| ~~P1-9~~ ✓ | **Concurrent regression test added** — `tests/test_concurrency.py` drives the per-thread actor store (`raas_tracker/audit._audit_state`) from 8 barrier-synchronised threads, one DB connection each: each thread reads back its own actor, a peer's `clear_audit_actor` never disturbs another thread mid-flight, an unset thread falls back to `system` rather than inheriting, and 8 concurrent `UPLOAD`-style audit writes stay attributed to their own writer. Exercises the resolver directly (not HTTP) because the Flask test client's cookie jar is shared mutable state and is not thread-safe | `tests/test_concurrency.py` | The `threading.local` bug class has concurrent coverage, not just sequential |
| ~~P1-11~~ ✓ | **CLOSED — one canonical name for the whole request.** `api_upload` computed `secure_filename` for the extension gate (then discarded it), let `save_upload` apply `_safe_upload_filename` for the stored column, and returned the **raw** client name in the JSON response — so the caller was told a different string from the one stored and audited. Two live defects followed, both now fixed: a legitimate non-Latin filename was **rejected 400** (werkzeug flattens Arabic/Hebrew to a bare `xlsx`, so the gate saw no extension at all), and U+202E RTL-override survived into the stored name and rendered in `UploadHistory` — a name-spoofing vector in an audit-visible list. `_safe_upload_filename` is now the single canonical sanitiser (unicode-preserving, plus bidi controls, dotfiles and Windows reserved names), computed once and reused for gate, stored column, audit and response. The on-disk name stays `uuid4().hex` and is deliberately NOT this value — there is no traversal sink. Verified non-vacuous: reverting only the source gave 19 failed / 23 passed | `raas_tracker/uploads.py`, `flask_app.py`, `tests/test_upload_filename.py` (42 tests) | Gate, stored column, audit and response always agree |
| ~~P1-13~~ ✓ | **CLOSED for the enumerated sites — mutation and audit now share one transaction.** 24 `atomic=True` call sites that committed *before* auditing now pass `atomic=False` and commit once, after the audit write. Highest severity first: `adjust_stock_from_upload` (`uploads.py:896`) no longer audits inside its row loop — the audit rows are **collected** and written after the loop with `atomic=False`, and the `except` now `conn.rollback()`s, so a failure on row N applies **nothing** (before: rows 1..N-1 durably flushed, audited, and reported as a failure the caller could not distinguish from success). Also fixed: the 6 delete paths (see P1-16) plus `create_user`, `create_company`, `update_company`, `add_recipe`, `add_recipe_item`, `update_recipe_item`, `update_recipe`, `add_shipment`, `add_sale_item`, `update_sale_item`, `create_invoice`, `ship_invoice` (2 rows), `void_invoice`, `save_upload`, `update_stock` (its own `atomic` param now forwarded to the audit instead of committing first), `set_reorder_level`, `add_chemical` (whose `except psycopg.IntegrityError` `rollback()` was dead code while the INSERT was already committed, and is now live). Class-3 swallowed rollbacks added in `approve_upload_row`, `reject_upload_row`, `approve_upload`, `lock_reconciliation_period`, `api_cron_maturity_check`. **Deliberately NOT changed** (each read in full first): `log_audit_action`'s global `atomic=True` default — changing it would silently alter ~50 sites (rejected in a prior decision); `update_sale_full` (`sales.py:814` commit → `:832` audit) and `book_invoice` (`update_invoice_status` commits at `:1835` → audit at `:1846`) still commit before auditing — both are **load-bearing commits that the route depends on for its own one-transaction guarantee** (`flask_app.py:2213-2230` books the sale badge and the invoice together via `book_invoice`'s commit; `test_production_atomicity.py:221` pins it), so moving them is a route-level redesign, not a call-site edit; `revoke_user_sessions` commits internally (`auth.py:527`), which is why the `SESSION_REVOKE` audit at `flask_app.py:731` commits separately — documented on P1-16. Test: `tests/test_audit_atomicity.py` (23 tests, TDD red-first) |
| ~~P1-17~~ ✓ | `tests/test_auth.py:16` assigned `auth_mod._DUMMY_HASH = None` as a bare module global instead of via `monkeypatch`, so it was never restored across tests (`conftest.py:88` does it correctly). Now set with `monkeypatch.setattr(auth_mod, "_DUMMY_HASH", None)` so it is restored at teardown; the anti-timing-oracle assertion is unchanged |
| ~~P1-18~~ ✓ | **CLOSED — a production timestamp defect, not a flaky test.** Root cause: `to_char(ts, 'YYYY-MM-DD HH:MM:SS')` is a *strftime* pattern, and `to_char` reads the codes differently — `MM` = MONTH (minutes are `MI`) and `HH` = 12-hour (24-hour is `HH24`). So every datetime this schema produced stored the **month** in the minute slot and lost both the real minutes and any AM/PM distinction: real `2026-10-07 07:06:58` → stored `2026-10-07 07:10:58`, real `2026-03-15 14:22:41` → `2026-03-15 02:03:41`. Because the columns are TEXT and every window is a **string comparison**, each time-window control silently degraded into a comparison of the **seconds field**: `is_login_blocked`'s 10-minute window became `second_row >= second_now`, so an attempt written seconds earlier could be judged outside the window the moment the clock ticked past its recorded second. That is exactly the `401×6` flake — it reproduces in `tests/test_proxy_trust.py` **alone** (~6% of runs, 3/48 measured), and captured failures showed all 6 rows present, all `ip=127.0.0.1`, `route_eval=4` vs 5. Fix: `'YYYY-MM-DD HH24:MI:SS'` in every SQL `to_char`, `db.py` docstring rewritten to name the `MM`/`MI` and `HH`/`HH24` traps, `_SCHEMA_VERSION` 3 → 4. Same defect also broke `check_api_key_rate_limit` (300/min shed hits every minute) and made session/API-key expiry wrong by ~9 min early / ~59 min late (a 1-hour TTL was really ~10 hours). See "P1-18 data migration" below |
| ~~P1-16~~ ✓ | **CLOSED — all 6 delete paths now commit AFTER the audit.** `api_delete_upload` (`flask_app.py:1397`), `delete_company`, `delete_recipe_item`, `delete_recipe`, `delete_shipment`, `delete_sale_item` each pass `atomic=False` and commit once, so a failing audit INSERT leaves the row **intact and unrecorded** rather than deleted and unrecorded. The rowcount gates are preserved, so a delete that matched no row still writes no audit row. The CTE idea in the original wording was not needed: each site already captured its evidence with a SELECT *before* the DELETE (P1-5), so capture + delete + audit is one transaction without rewriting the deletes. Not applicable to the session-revoke route, as originally noted: `revoke_user_sessions` commits internally (`auth.py:527`) — see P1-13 |

**P1 backlog (identified, not started):**

| Task | Description |
|---|---|
| ~~P1-b~~ | **Resolved as P1-6** — the operator reason was moved out of `ip_address` into `new_value` as `"<qty> (<reason>)"`, restoring the real client IP. A separate column was not needed; the overload was the bug, not the schema |
| ~~P1-c~~ | **Duplicate of P1-9** — the concurrent `Barrier` test for the `threading.local` bug class. Closed as P1-9 (`tests/test_concurrency.py`) |
| ~~P1-d~~ | **Wrong — retracted.** `POST /api/sales`, `/api/recipes` and `/api/recipes/<name>/items` carry no admin gate, so API keys *do* reach audited mutations and the `api-key:<name>` actor label is reachable in practice. Now proven by a test that stamps a real key |
| ~~P1-e~~ | **Duplicate of P1-13** — same `atomic=True` row, same inverted wording. Removed in favour of the corrected P1-13 above |

---

## 🚀 Phase M9: Sales–Production–Live Epic (Current)

Locked: USD only · admin-only financials/history/mutations · partial shipments real ·
fail-closed shortage handling (a short ingredient rejects the whole run) · manual master recipes (no auto-create) · frozen batch
snapshots · companies master · single shipment header per record.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| P0 ✅ | **Companies master** — `companies` table (name required, rest optional), `sales.company_id` FK RESTRICT, idempotent backfill merging case variants, CRUD UI + API (mutations admin) | `db.py`, `companies.py`, `chem_stock.py`, `flask_app.py`, `Companies.jsx`, `tests/test_companies.py` | CRUD + gates + backfill green; gate green |
| P1 ✅ | **Capture fields** — `sales.maturity_date/comments` (columns; maturity wired in P6), `sale_items.unit`, `shipments` table + record/delete UI, `production_runs` + `run_items` DDL; company select + unit inputs on PI entry; comments editor | `db.py`, `sales.py`, `flask_app.py`, `ReviewModal.jsx`, `ShipmentModal.jsx`, `SaleDetailModal.jsx` | New fields round-trip; gate green |
| P2 ✅ | **Master recipe from register** — company→product selects, auto-name, `UNIQUE(company_id, name)`, 409 → existing; `recipes.company_id/product_name` | `flask_app.py`, `Recipes.jsx`, register endpoints | One master per company×product; gate green |
| P3 ✅ | **Snapshot production** — run form (order/batch/date/qty/notes) → formula snapshot → atomic deduction → run record | `flask_app.py`, `recipes.py`, `stock.py`, Recipes UI | Deduction + snapshot + audit; gate green |
| P4 ✅ | **Live commercial report** — USD KPIs + 13-col table + export, admin-only Commercial tab | `flask_app.py`, `Reports.jsx` | KPIs/rows/export correct; gate green |
| P5 ✅ | **Consistency lock** — pipeline mutations admin-only; every write dated + actor | `flask_app.py`, `raas_tracker/sales.py`, sales UI | Non-admin blocked; gate green |
| P6 ✅ | **Maturity reminders** — maturity at payment entry + daily cron → bell (maturity + 7-day escalation, dedupe) | `flask_app.py`, `vercel.json`, `raas_tracker/sales.py`, `raas_tracker/notifications.py`, sales UI | Reminders fire, escalation fires, paid silent; gate green |

---

## 🔐 Phase M10: Password Reset & Forced Change (Current)

Backend only (frontend is a separate design lane).

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| M10 ✅ | **Password reset/change** — voluntary change (keeps current session), forgot-password with generic-200 anti-enumeration + delay floor, single-use redeem (`FOR UPDATE`), admin force-reset + admin token (one-time secrets, `must_change_password`), `_gate_api` 403 enforcement (API keys exempt), login/me/users surface flags | `raas_tracker/db.py`, `raas_tracker/auth.py`, `chem_stock.py`, `flask_app.py`, `tests/test_password_reset.py`, `docs/SPEC.md`, `AGENTS.md` | 27 new tests green; full gate green |

---

## 📊 Phase CR: Commercial Report Enhancement (Completed)

Vertical slices for filtering, grouping, calendar navigation, and export on the live commercial report.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| CR-1 ✅ | **Backend Filter Engine** — CTE-based query with date_anchor, date_from/to, customer_name (ILIKE), product_name (EXISTS), company_id, stage, payment_status, pagination | `sales.py`, `flask_app.py`, `tests/test_commercial_report.py` | `/api/reports/live/filtered` returns filtered detail rows |
| CR-2 ✅ | **Frontend URL-Synced Filter Bar** — Quick Search, Date Anchor, Preset Range, Custom From/To, Stage, Payment Status, Company, Group By, Clear; state in URL | `CommercialFilters.jsx`, `datePresets.js`, `Reports.jsx` | Filters persist on refresh, shareable links, group_by toggles view |
| CR-3 ✅ | **Expandable Period Table + Pagination** — Detail mode (19 cols) + Grouped mode (period headers with KPIs, expandable detail rows), skeleton loading | `CommercialTable.jsx`, `Reports.jsx` | Expand/collapse works, pagination, both modes render correctly |
| CR-4 ✅ | **Backend Group-By Summary + Detail Items** — Period aggregation (month/week/year) with KPIs; detail items for expandable rows | `sales.py`, `flask_app.py` | `/api/reports/live/summary` returns periods with items for CR-3 |
| CR-5 ✅ | **Frontend Calendar Navigator** — Prev/Next period, Date Anchor, Quick Presets (Month/Quarter/Year), Custom Dates, Clear; URL-synced | `CommercialCalendarNavigator.jsx`, `Reports.jsx` | Navigates periods, presets update dates, anchor changes refetch |
| CR-6 ✅ | **Filtered Export** — Export button sends current filters; GET/POST endpoint returns CSV of filtered data | `flask_app.py`, `Reports.jsx`, `test_commercial_report.py` | Exported CSV matches UI filters |
| CR-7 ✅ | **Full Gate + Docs** — pytest + vitest + oxlint + build pass; SPEC.md updated with new endpoints | `docs/SPEC.md`, `docs/plan.md` | All gates green, docs current |

---

## 🏭 Phase GP: Go for Production & Invoicing (Completed)

Vertical slices linking production runs to sale invoices: schema, lifecycle backend, Recipes
"Go for Production" modal, pipeline invoice panel, amount/auto-paid rules.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| S1 ✅ | **Production/invoice schema** — `invoices` (+ unique `(sale_id, invoice_number)`), `production_run_links`, `sales.shipment_status`, `sale_items.item_no`, `sale_payments.invoice_id`; `production_runs` gains `material_number/packing/invoice_number`, `sale_item_id` nullable; `_SCHEMA_VERSION` gate so additive DDL is never skipped by the schema fast path (replaced the unenforced `_REQUIRED_SIG_TOKENS` sentinel) | `raas_tracker/db.py` | Migrates existing DBs; gate green |
| S2–S3 ✅ | **Invoice lifecycle + run links backend** — invoice create/list, book/ship/pay, sale completion, `production-source`, produce links run→sales/invoices with advance-only statuses (`paid` terminal); sale payloads gain `shipment_status`/`invoices[]`/`item_no` | `flask_app.py`, `raas_tracker/sales.py`, `raas_tracker/recipes.py`, `tests/test_invoices_production.py` | 12 new tests green; gate green |
| S4 ✅ | **Go for Production modal** — company → PI/sale → invoice dropdown/new number → material number (auto from `item_no`) → packing → batch → date → qty → multi-recipe rows; Recipes detail calls now pass `company_id` (scoping fix) | `ProductionRunModal.jsx`, `Recipes.jsx` | Run created + linked; gate green |
| S5 ✅ | **Pipeline invoice panel + sub-step badges** — InvoicePanel in SaleDetailModal (`shipment_ongoing`) with per-invoice book/ship/pay, amount + balance, completion money header; SaleCard badges (Production running / Production done / Ship booked); non-blocking no-invoice toast on `lc_received → shipment_ongoing` | `InvoicePanel.jsx`, `SaleCard.jsx`, `SaleDetailModal.jsx`, `Sales.jsx` | Actions + badges render; gate green |
| S6 ✅ | **Invoice amount + auto-paid** — `invoices.amount` (NUMERIC(14,2), NULL = legacy), POST default = sale total, paid flip when `paid_amount >= amount` (0 → paid at creation, NULL → any payment), no-downgrade rules | `raas_tracker/db.py`, `flask_app.py`, `raas_tracker/sales.py`, `InvoicePanel.jsx`, `tests/test_invoice_amount.py` | 10 new tests green; backend 304 passed/8 skipped, frontend 234, oxlint 0, build OK |

---

## 🧾 Phase INV-500: LC invoice-barrier 500 (Completed 2026-10-08)

Creating an invoice from the `lc_received → shipment_ongoing` barrier modal
failed with `500 {"error": "internal server error"}`.

Root cause (reproduced locally with the test client): `Sales.jsx` always
passed `saleId={null}` to the modal, so it POSTed to
`/api/sales/null/invoices`. No POST route matches that shape, so Werkzeug
raised 405 — and with no 405 handler the catch-all `Exception` handler
masked it as a 500. Ruled out with evidence: no file writes on the invoice
path (read-only-FS theory), zero `—` placeholders (SQLite-syntax theory),
ProxyFix already correct (auth failures are 401/403, never this 500).

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| INV-1 ✅ | **405 handler** — `/api` wrong-method requests return 405 `{"error":"method not allowed"}` instead of falling into the catch-all 500 | `flask_app.py`, `tests/test_invoice_errors.py`, `docs/SPEC.md` | `POST /api/sales/null/invoices` → 405 JSON |
| INV-2 ✅ | **Non-finite money guards** — `remaining_invoice_money` / `_invoice_money_already_on_sale` raise `ValueError` (400) on NaN/Inf PI or invoiced totals instead of `InvalidOperation` (500); nothing persisted on the 400 path | `raas_tracker/sales.py`, `tests/test_invoice_errors.py` | NaN qty / Inf price / NaN legacy line → 400 + zero invoice rows |
| INV-3 ✅ | **Barrier modal takes the LC's PIs** — one invoice-number row per PI (single invoice or one per PI; same-PI splits stay in the PI detail line editor, since each create seeds the whole remainder), `apiFetch` instead of raw `fetch`, per-row errors, and auto-retry of the blocked `POST /api/lcs/<id>/move` on success | `InvoicesRequiredModal.jsx` (+ new `.test.jsx`, 5 tests), `Sales.jsx` (barrierPis/barrierMove, `handleBarrierInvoicesSuccess`) | Per-PI URLs asserted, never `null`; 299 frontend tests green, oxlint 0 errors, build OK |
| INV-4 ✅ | **Link advances PIs to `lc_received`** — linking IS entering LC: `attach_pis` advances `pi_issued` children in the same transaction (UPDATE + history + `SALE_MOVE`, inline, single commit; never downgrades; late attach to an advanced LC still lands `lc_received`). 2 pre-existing assertions updated to the new contract | `raas_tracker/lcs.py`, `tests/test_lcs.py` (4 new), `tests/test_lc_api.py` (1 updated) | `test_lcs.py` + `test_lc_api.py` 69 green |
| INV-5 ✅ | **Board buttons follow the LC's own stage** — `LcBoardCard` gets a move action only when its column matches `lc.stage`; early-column cards (split LC, lagging PI) show no move button, so the invoice modal is reachable only from a genuine 3rd-stage attempt | `Sales.jsx` (board render), new `Sales.test.jsx` (3 page tests) | 302 frontend tests green, oxlint 0 errors, build OK |

Business rule restated: one LC carries one **or many** invoices across its
PIs (gate counts invoices across ALL of the LC's sales). Linking a PI under
an LC moves it to `lc_received` — the LC-create/link form is the LC-details
demand for the 1st → 2nd stage hop.

---

## 🧾 Phase INV-REVISION: Flexible Invoices (Completed 2026-10-12)

The commercial model restated: **the invoice is the reference point of the
sale, the PI is a proposal.** An invoice line may carry a quantity **greater**
than its PI line (shipment advantage), one product may appear on several
invoices, and only PI products may be invoiced. A create **never** births
`paid` — only a payment record may, because `paid` asserts money was received.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| INV-R1 ✅ | **Schema v5 + flexible quantities** — `_SCHEMA_VERSION` 4 → 5; new `invoice_items.source_type`, `invoices.shipment_status`, `letters_of_credit.shipment_gate`; `create_invoice` gains an explicit-lines mode (no quantity ceiling) alongside the legacy PI-remainder auto-seed; header always restated as `SUM(invoice_items.line_total)` | `raas_tracker/db.py`, `raas_tracker/sales.py`, `tests/test_invoice_items.py`, `tests/test_schema_migration.py` | Migrates existing DBs; gate green |
| INV-R2 ✅ | **Flexible-invoice API** — `POST /api/sales/<id>/invoices` accepts optional `lines:[{sale_item_id, quantity}]`; `POST /api/sales/<id>/invoices/batch` creates N invoices atomically; `GET /api/invoices/<id>/items`; add-line recomputes `invoices.amount` in the same transaction | `flask_app.py` | gate green |
| INV-R3 ✅ | **InvoiceBuilderModal** — flexible invoice-creation UI (multi-line, per-line quantities) | `InvoiceBuilderModal.jsx`, `Sales.jsx` | build OK |
| INV-R4 ✅ | **Line-composer consolidation + no-born-paid** — shared `InvoiceLineComposer`, `useSaleInvoiceLines` hook, `varianceLabel`; lines required at creation; a zero-amount invoice is no longer born `paid`; the legacy **Record Shipment** button + `ShipmentModal` were deleted (shipment is now invoice-driven) | `InvoiceLineComposer.jsx`, `InvoiceLineEditor.jsx`, `InvoicePanel.jsx`, `SaleDetailModal.jsx`, `useSaleInvoiceLines.js`, `varianceLabel.js`, `ShipmentModal.jsx` (deleted), `raas_tracker/sales.py` | 305 frontend tests green; gate green |

---

## 🏭 Phase PBB: Production-Before-Booking Gate (Completed 2026-10-12)

Booking commits to a real shipment, so it is refused until the invoice's
products have actually been produced — a recipe run created from the **recipe
page** and linked to **that** invoice. The check is **fail-closed** and
**per-invoice**: it re-reads production content rather than trusting
`invoices.status`, because `record_production` flips a multi-product invoice to
`produced` on the **first** linked run, so on its own that flag reads satisfied
while later products are still unbuilt.

| Task | Description | Files Touched | Acceptance |
|---|---|---|---|
| PBB-1 ✅ | **Booking requires production** — `book_invoice` re-checks production readiness for every distinct product on the invoice, at both pre-booking states (`planned` and `produced`); a short invoice raises `InvoiceNotProduced(_production_blocked_message(...))` (`raas_tracker/_exceptions.py`, raised at `sales.py:2203`), reported as **409** by `status_for(e)` inside `_invoice_transition_error` (`flask_app.py:2469`). *(Corrected 2026-10-15: this row previously said it raised a `ValueError` literal mapped by `_state_error_status` — both halves were false after Phase 4-A deleted that mapper and typed the refusal.)* | `raas_tracker/sales.py`, `raas_tracker/_exceptions.py`, `flask_app.py`, `tests/test_invoices_production.py` | unauthorised booking 409; produced flow books; gate green |
| PBB-2 ✅ | **`invoice_production_readiness()`** — per-invoice gate core; requires, for each product on **this** invoice, a company recipe **and** a production run linked to this invoice (`production_run_links.invoice_id`); scoped to the invoice alone (siblings proceed independently) | `raas_tracker/sales.py` | gate green |
| PBB-3 ✅ | **Latent fixes surfaced by the gate** — import `production_readiness` in `flask_app.py` (a `NameError` crashed every `GET /api/invoices/<id>/readiness`); pass `model_dump()`ed lines in `api_create_invoice` (a raw Pydantic model has no `.get()`, so explicit-lines creates raised `AttributeError` → 500) | `flask_app.py`, `tests/test_production_atomicity.py`, `tests/test_invoice_amount.py`, `tests/test_sales.py` | both routes stabilised; gate green |

**Business rule now in force**
- `planned → booked` is **gated**: an invoice cannot be booked until every
  product on it has a company recipe and an invoice-linked production run.
- Production is mandatory **from the recipe page** when the goods are ready —
  there is no booking shortcut.
- The panel renders `Book` from the server's `can_book` / `book_blocked_reason`
  rather than from `status`, so a disabled button and a 409 always agree.

---

## 🚢 Phase SHIP-FIRST: Shipment-first invoicing (in progress, started 2026-10-26)

Invoices are created **in Shipment Ongoing**, not at LC Received. Each invoice
matures independently and strictly: `planned → produced → booked → shipped →
paid`. Booking is the per-invoice proof of production. (The Payment Due gate
first asked for **one booked invoice**; INVOICE-MATURITY Phase 2 replaced that
with an **actual ship date** — booking alone no longer opens Payment Due.)

| # | Phase | Scope | State |
|---|---|---|---|
| 1 | **Start shipment without an invoice** | `lc_received → shipment_ongoing` needs no invoice (sale + LC + readiness + Sales UI barrier/modal removed); Payment Due gate deliberately left as the old production gate | ✅ 2026-10-26 (`eb18d5e`) |
| 2 | **Strict Book → Ship → Pay** | `ship_invoice` requires `booked` (and stamps the invoice's `invoice_date` on the shipment); `mark_invoice_paid` requires `shipped`; typed `InvoiceNotBooked` / `InvoiceNotShipped` / `SaleNotReadyPastShipment`; `booked_invoice_readiness` powers both the sale and LC Payment Due gates; LC readiness gains `booked_ok`; `POST /api/sales/<id>/move` is rate-limited and returns typed 409s; invoice readiness `can_pay` only for `shipped` | ✅ 2026-10-26 — booking gate superseded 2026-10-27 by INVOICE-MATURITY Phase 2 (`shipped_invoice_readiness`) |
| 3 | **Completion on invoice totals** | Complete only when ≥ 1 invoice exists and every invoice is `paid`; amount compared is `SUM(invoice_items.line_total)`, never the PI (`get_sale_pi_total` keeps the offer); `move_sale_to_stage`, `mark_invoice_paid`, payment edits/deletes and voids funnel through `_sync_sale_completion`; a completed sale reopens when a correction leaves an invoice unpaid; LC auto-completes with its last completed child (`_sync_lc_completion`, `LC_MOVE`) and reverts on reopen; manual completion is gated by `SaleNotReadyForCompletion` / `LcNotReadyForCompletion` (**409**) | ✅ 2026-10-26 |
| 4 | **Report: live PI fallback + completed retention** | Shared `_commercial_report_base_sql` CTE: a line with invoice rows shows invoice quantity and final price, a line with none shows PI quantity and `is_provisional`; `sale_total` = sum of displayed line totals; `invoice_date` from `invoices`; no invoice inclusion gate, completed rows retained; summary/drill-down use the same base; `get_all_sales(include_completed=False)` archives completed from `/api/sales` while `/api/sales/export` and the report keep them; report CSV gains a `Provisional` column (20 columns) | ✅ 2026-10-27 |
| 5 | **Frontend board / panel / report editor** | 4-column board (`BOARD_STAGES`, completed archived); Shipment Ongoing action “Move to Payment Due”, Payment Due action `'detail'`; LC crossing gates on `booked_ok` (ProductionRequiredModal deleted); sale-level PaymentModal deleted — payment is invoice-level; `InvoicePanel` gains `readOnly` for completed sales and its step buttons follow status (`Book` planned/produced, `Ship` booked, `Pay` shipped); add-line targets restricted to `planned`/`produced`; report `Actions` column + Provisional badge + legend; row Edit opens `SaleDetailModal` and a save reloads the report/KPIs; skeleton/empty/pagination colspans 20 | ✅ 2026-10-27 — 5 test files / 91 tests green; full frontend 339/339, oxlint 0/0, build OK. The Shipment Ongoing “Move to Payment Due” action was removed 2026-10-27 by INVOICE-MATURITY Phase 2 |
| 6 | **Docs** | PRD/SPEC completion and report contract; handoff | ✅ 2026-10-27 — PRD Flow 3 (no invoice at LC Received, four live board columns, per-invoice strict order, invoice-defined completion, Payment Due opens invoices), Flow 7 (PI fallback, Provisional badge, completed retention, row editor); SPEC `/api/sales/<id>/move` gates + typed 409s, payment edit/delete completion re-sync, report money/columns/CSV contract, UI contract, `include_completed`, refreshed test coverage (1283/8 backend, 339 frontend); locked decisions recorded in this table; session handoff written |
| 7 | **Verification** | Full pytest/ruff/pyright/vitest/oxlint/build + isolated-Postgres E2E | ✅ 2026-10-27 — full backend 1283 passed / 8 skipped; ruff + pyright at baseline; frontend 339/339, oxlint 0/0, build OK; isolated pgserver E2E 61/61 checks (zero-invoice shipment → A1 80 KG → strict-order 409s → produce/book/ship/pay → invoice-defined completion → A2 completes sale/LC → report provisional→final, completed retention, 20-column CSV, payment-correction reopen) + Playwright UI pass (four columns, invoice composer read-only price + uncapped qty, report badge/legend/edit modal). The plan's “sale stays Payment Due while A2 is uninvoiced” step was superseded by the locked Q4 rule and is recorded as a deviation in `handoff.md` |

**Locked decisions:** stage is a rollup, invoice status is the source of truth;
**an actual ship date** opens Payment Due for a sale (booking alone does not);
unshipped siblings stay behind and advance individually; the LC advances only
when every invoice under it has shipped; completion is invoice-defined; report
is per PI line with PI fallback; report rows open the existing editor; strict
Book → Ship → Pay.

**Constraints:** no new tables/columns, no `_SCHEMA_VERSION` bump; parameterized
SQL; typed refusals are 409; every mutating route stays admin-only and
rate-limited; `chem_stock.py` compatibility imports kept in sync.

---

## 🧾 Phase INVOICE-MATURITY: LC payment terms + per-invoice maturity + board visibility (Phases 1–7 of 7, completed 2026-10-28)

Invoices are USD receivables that mature under the LC's payment terms. Phase 1
ships the data and the operator surface; Phase 2 makes the actual ship date the
Payment Due trigger and puts every invoice on its sale's board card; Phase 3
delivers the per-invoice payment reminders (bell + email to directors) from the
existing daily cron; Phase 4 lets a late invoice reopen a completed sale.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Schema v7** | `letters_of_credit.payment_terms`/`tenor_days` + `invoices.maturity_date`; `_SCHEMA_VERSION` 6 → 7; additive guarded `ALTER TABLE`s, no backfill (NULL legacy reads at sight) | ✅ |
| 2 | **LC terms service + API** | `create_lc` defaults `at_sight` and forces tenor NULL; `deferred` requires 1..720; `PUT /api/lcs/<id>` accepts terms, at-sight clears tenor; field-shaped **400** validation; list/detail rows expose terms | ✅ `tests/test_lc_terms.py` (11) |
| 3 | **Ship-time maturity** | `invoice_terms_for_sale` + `resolve_invoice_maturity`; at sight = actual ship date, deferred = actual + tenor (leap-safe); optional `maturity_date` override must be ISO and ≥ actual; resolution before the shipment INSERT; audit names the maturity; ship/list/readiness payloads expose it | ✅ `tests/test_invoice_maturity.py` (8) |
| 4 | **UI** | New-LC and LC-detail forms carry `Payment terms` + conditional `Tenor days`; the Ship form prefills an editable `Maturity date` from the row's terms and posts it; rows show `matures <date>` and an **Overdue** chip for past-due shipped invoices | ✅ LinkPIsModal / LcDetailModal / InvoicePanel tests (46 in the three files) |
| 5 | **Docs** | PRD Flow 6 item 10 + data model, SPEC schema/API/maturity contract, this phase table, handoff | ✅ |
| 6 | **Gate** | Full backend pytest, ruff, pyright, frontend vitest, oxlint, vite build | ✅ 2026-10-27 — backend **1302 passed / 8 skipped** (19 new tests in `test_lc_terms.py` + `test_invoice_maturity.py`); ruff 63 repo-wide findings, none in changed hunks; pyright 6 errors / 18 warnings = baseline; frontend **348/348** (39 files), oxlint 0/0, build OK |

**Locked decisions:** terms live on the LC (one tenor per LC, not per invoice);
maturity is computed at ship time, never at invoice creation; the operator
override is allowed but cannot precede the actual ship date and is audited;
legacy NULL terms are at sight with no backfill; all money stays USD; reminder
email recipients will be the company's directors only, never clients.

**Constraints:** parameterized SQL only; every mutating LC/invoice route stays
admin-only and rate-limited; no auth/gate edits; the maturity computation is
server-owned (the UI prefill is convenience, re-validated on POST).

### Phase 2 — per-invoice Payment Due + board visibility ✅ 2026-10-28

An invoice is a shipment: booking says production is planned, but only the
**actual ship date** starts the payment clock. Shipping therefore moves its own
sale, while the LC — a container — waits for all of its invoices.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Shipment gate** | `shipped_invoice_readiness` → `shipped_ok` (a sale needs one `shipped`/`paid` invoice) and `all_shipped_ok` (every non-void invoice under the LC shipped). Sale gate in `move_sale_to_stage` raises `SaleNotReadyPastShipment` ("at least one shipped invoice required"); `check_lc_shipment_ready` exposes both keys; `booked_ok`/`invoices_ok` become informational | ✅ `tests/test_gate_integrity.py`, `tests/test_typed_errors.py`, `tests/test_invoice_order.py` |
| 2 | **Auto-move on ship** | `ship_invoice` moves a `shipment_ongoing` sale to `payment_due` in the same transaction (`SALE_MOVE`, note names the invoice), then calls `_sync_lc_payment_due`: the LC advances (`LC_MOVE`) once every non-void invoice under it has shipped, carrying still-uninvoiced PIs along. An unshipped sibling stays behind | ✅ `tests/test_invoice_payment_due.py` (9 — including a sale that ships one of two invoices, a booked-only sale that refuses the move, and the LC rollup carrying an uninvoiced PI) |
| 3 | **List summaries** | `GET /api/sales` attaches `invoices[]` per sale (non-void: id, number, status, invoice/approx/actual dates, maturity, amount, `paid_amount`, LC `payment_terms`/`tenor_days`) and `has_uninvoiced_remainder` (a PI line with quantity but no invoice) | ✅ `tests/test_invoice_payment_due.py` |
| 4 | **Shared step form** | `InvoiceStepForm.jsx` extracted from `InvoicePanel`: book (approx. date), ship (actual date + prefilled editable maturity), pay (amount + date); inline validation and `error`/`localError` display. Used by the panel and the card so both post identical payloads | ✅ `InvoicePanel.test.jsx` unchanged in behaviour |
| 5 | **Board cards** | `SaleCard` renders an invoice section whenever `sale.invoices` is non-empty: status chip (`Payment due`/`Completed`), `$amount`, `paid $X`, `Matures <date>`, **Overdue** chip, and that invoice's next-action button; the active row opens `InvoiceStepForm` inline. Book is disabled with a reason until produced. `utils/invoices.js` holds the shared vocabulary/date/money helpers | ✅ `SaleCard.test.jsx` (7, new) |
| 6 | **Board wiring** | `utils/sales.js`: `shipment_ongoing` action `null` (manual “Move to Payment Due” gone). `Sales.jsx`: `handleInvoiceStep` posts book/ship/pay, toasts, refreshes; after a ship with `has_uninvoiced_remainder` asks “Create more invoices?” and opens the sale's editor on confirm. Grouping computed once globally so an LC card stays in its own column while an advanced PI renders its own card in the later column. `handleLcMove` probes no readiness (rollup is server-side) | ✅ `Sales.test.jsx` (lagging PI visible, no manual LC move, card ship + remainder prompt) |
| 7 | **Docs + gate** | PRD Flow 3/6, SPEC sales/LC/readiness/ship contracts + board visibility section, this table, handoff; full pytest/ruff/pyright/vitest/oxlint/vite build | ✅ 2026-10-28 — see gate row below |

**Gate run (2026-10-28):** backend **1311 passed / 8 skipped** in 1613.58s
(9 new tests in `test_invoice_payment_due.py`, adapted gate/invoice/LC suites);
ruff **63** repo-wide findings, unchanged from the Phase 1 baseline, none in the
changed hunks; pyright **6 errors / 18 warnings** = baseline; frontend
**355 passed / 40 files**, oxlint **0/0**, vite build OK.

**Deviation from the approved plan:** the plan said the LC gate and the derived
LC rollup needed "one shipped invoice" like the sale. Per the user's rule
(2026-10-27) a container cannot leave while any invoice under it is still
unshipped, so the LC uses `all_shipped_ok` instead. The user's second rule — an
uninvoiced PI must not be stranded — is why `_sync_lc_payment_due` carries
lagging children forward and why the board asks about more invoices.

**Locked decisions:** an actual ship date (never booking) opens Payment Due;
shipping moves its own sale; the LC waits for all invoices; unshipped siblings
advance individually; the manual ship-stage board action is gone but the API
move stays behind `shipped_ok`; board labels `Payment due`/`Completed` while the
data statuses stay `shipped`/`paid`.

**Constraints:** no schema change; one commit per slice; parameterised SQL;
invoice status is still the source of truth and the stage is its rollup.

### Phase 3 — per-invoice payment reminders (bell + email) ✅ 2026-10-28

The daily maturity cron now reminds **per invoice**, not per sale: at sight a
shipped invoice is reminded at `actual_ship_date + 7`; a deferred one at its
`maturity_date`; each escalates once, 7 days later. No new UI — the existing
bell, its 30 s poll and `entityRoute` (`sale → /sales`) already surface the
notifications.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Mailer** | New `raas_tracker/mailer.py`: env-only SMTP (`SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`/`SMTP_USE_TLS`), recipients **only** `NOTIFY_EMAILS` (internal directors), `EmailMessage`, 10 s timeout, starttls + optional login; unconfigured is a log-once no-op returning `False`; send failures are caught and logged, never raised | ✅ `tests/test_invoice_reminders.py` (FakeSMTP + no-config + failure) |
| 2 | **Reminder engine** | New `raas_tracker/reminders.py`: `_due_date` (deferred + maturity → maturity; else ship + 7), `AT_SIGHT_GRACE_DAYS = 7`, `ESCALATION_AFTER_DAYS = 7`; scans `status = 'shipped'` invoices, reads the invoice's own payments, builds a USD body naming invoice/PI/client and the outstanding balance; `notify()` keys `invoice:{id}:due` / `invoice:{id}:due:7`; returns one item per **newly inserted** notification | ✅ 16 tests: at-sight grace, dedupe + escalation, deferred on maturity, paid/void clears, siblings independent, USD payload, outstanding balance |
| 3 | **Clear on settle** | `notifications.clear_invoice_reminders` deletes both keys (no commit); called from `mark_invoice_paid` on a full payment and from `void_invoice` before the row disappears. `notify_maturity_initial`/`_escalation` now return the inserted id so counts are real | ✅ `test_paying_the_invoice_clears_its_reminder_keys`, `test_voiding_the_invoice_clears_its_reminder_keys` |
| 4 | **Cron wiring** | `POST /api/cron/maturity-check` keeps the Bearer check, restricts the legacy sale-level scan to sales **without** invoices, runs the invoice engine, mails one message per newly created reminder, returns `{success, checked, notified, sale_reminders, invoice_reminders, emails_sent}`, audits `CRON_MATURITY_CHECK` with the counts; unconfigured mailer still 200 | ✅ `test_cron_creates_reminders_and_emails_each_new_one`, `test_cron_second_run_creates_nothing_and_emails_nothing`, `test_sale_scan_skips_sales_that_have_invoices`, `test_unconfigured_mailer_*`. Upstream Vercel cron support shipped in SHIP-FIRST |
| 5 | **Config docs** | `.env.example` documents the SMTP keys and `NOTIFY_EMAILS` (no values), noting recipients are internal directors, never client addresses | ✅ |
| 6 | **Docs + gate** | PRD Flow 6 step 11, SPEC cron/notifications/env table, this table, handoff; full pytest/ruff/pyright/vitest/oxlint/vite build | ✅ 2026-10-28 — gate row below |

**Gate run (2026-10-28):** backend **1327 passed / 8 skipped** in 1231.46s
(16 new tests in `test_invoice_reminders.py`); ruff **63** repo-wide findings,
unchanged baseline, new files clean; pyright **6 errors / 18 warnings** =
baseline, none in the new modules; frontend **355 passed / 40 files**, oxlint
**0/0**, vite build OK.

**Locked decisions:** reminders are per invoice (a sibling neither waits for nor
suppresses another); at sight waits 7 days after the **actual ship date** and a
deferred invoice waits for **maturity**; escalation is one extra notification 7
days later, never repeats; the balance is `amount − payments` in USD; a partial
payment keeps the reminder; full payment and void clear both keys; email exists
only for a newly created notification and only to `NOTIFY_EMAILS`; a sale with
invoices never hits the legacy sale-level scan; an unconfigured mailer is a
no-op, not an error.

**Constraints:** no schema change, no new UI; env-only secrets; `clear_*` stays
commit-free so the caller keeps its single transaction and row lock; the cron
stays idempotent — a second run on the same day creates and mails nothing.

### Phase 4 — late invoicing reopens the sale ✅ 2026-10-28

A completed PI is not frozen: a shipment that was invoiced late is still
receivable. Creating an invoice on a completed sale now reopens it (and its LC)
to Payment Due, and the new invoice walks the ordinary lifecycle.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Single create** | `create_invoice` calls `_sync_sale_completion` after the `INVOICE_CREATE` audit and before its commit; `completed → payment_due` (`SALE_MOVE`, note "Invoice no longer settled — reopened"). Other stages are a no-op | ✅ `test_creating_an_invoice_on_a_completed_sale_reopens_it_to_payment_due`, `test_create_invoice_leaves_shipment_ongoing_and_payment_due_untouched` |
| 2 | **Batch create** | `create_invoices_batch` runs the same reconciliation **once** after the `shipment_status` UPDATE, so N late invoices reopen the sale exactly once | ✅ `test_batch_create_on_a_completed_sale_reopens_it_once` |
| 3 | **LC follows** | Reopening goes through `move_sale_to_stage`, whose `payment_due` crossing reconciles the parent LC (`_sync_lc_completion`), so a completed LC reverts with its child | ✅ `test_late_invoice_on_a_completed_lc_child_reopens_sale_and_lc` |
| 4 | **Lifecycle resumes** | The late invoice is `planned`; produce → book → ship (actual date) → pay completes the sale again; voiding it instead re-completes immediately | ✅ `test_late_invoice_books_ships_pays_and_recompletes`, `test_voiding_the_late_invoice_recompletes_the_sale` |
| 5 | **Report** | The late invoice line turns the PI line final in the report while the sale is reopened | ✅ `test_late_invoice_on_a_completed_sale_turns_its_line_final` |
| 6 | **Panel** | `InvoicePanel`: `canAddInvoice = isAdmin` so a completed sale keeps an **Add invoice** action; `canEdit` still freezes existing rows (no step, no void); badge reads `read-only · add an invoice to reopen` | ✅ `InvoicePanel.test.jsx` (admin add, non-admin absent, frozen rows) |
| 7 | **Docs + gate** | PRD Flow 6 item 9 + Flow 7, SPEC create/completion contracts, this table, handoff; full pytest/ruff/pyright/vitest/oxlint/vite build | ✅ gate row below |

**Gate run (2026-10-28):** backend **1334 passed / 8 skipped** in 1550.78s
(7 new tests across `test_sale_completion.py` and `test_commercial_report.py`);
ruff **63** repo-wide findings, baseline, none in the changed hunks; pyright
**6 errors / 18 warnings** = baseline; frontend **356 passed / 40 files**,
oxlint **0/0**, vite build OK.

**Locked decisions:** creation is the reopen trigger (a `planned` invoice is
unsettled by definition), single and batch share one reconciliation point,
only `completed` is affected, the LC reopens because it follows its children,
and adding is admin-only while advancing/voiding existing invoices stays
frozen on a completed sale.

**Constraints:** no schema change; the reconciliation runs before the caller's
commit so the invoice, its audits and the stage move land together.

### Phase 5 — sheet-like report rows, per-row money, recipe prod date ✅ 2026-10-28

The report now mirrors the operator's spreadsheet: every invoice line is a row,
an uninvoiced remainder is its own provisional row, and the money follows the
row instead of being repeated at sale level. `Prod. Date` is the latest
**recipe production** for that sale's product.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Row grain** | `_commercial_report_base_sql` rebuilt: `inv_lines` (one row per non-void `invoice_items` line) `UNION ALL` `remainders` (one row per PI line where `PI qty − invoiced qty > 0`, `is_provisional`); over-invoiced lines have no remainder; a legacy line with `sale_item_id IS NULL` is its own row and does not reduce a remainder | ✅ `test_split_pi_line_renders_invoice_row_plus_remainder`, `test_two_lines_on_one_invoice_render_as_two_rows`, `test_line_split_across_two_invoices_renders_both_invoice_rows`, `test_over_invoiced_line_has_no_remainder_row`, `test_legacy_invoice_line_without_sale_item_is_its_own_row` |
| 2 | **Per-row money** | Invoice payments allocate across that invoice's lines in `invoice_items.id` order (last line absorbs the overpayment); `invoice_id IS NULL` payments allocate across the sale's rows (`sale_item_id NULLS LAST, invoice_date NULLS LAST, item_id NULLS LAST, is_provisional, product_name`, last row absorbs the residue); `due_amount = GREATEST(total_price − received_amount, 0)` per row; `payment_status` is decided per row | ✅ `test_invoice_payments_allocate_to_its_lines`, `test_partial_invoice_payment_allocates_in_line_order_and_last_line_absorbs`, `test_sale_level_legacy_payment_allocates_across_rows`, `test_invoice_row_carries_its_own_money_and_allocation`, `test_due_clamped_at_zero_with_overpayment_on_invoice` |
| 3 | **Row dates** | Invoice rows: `latest_ship_date = COALESCE(actual, approx)`, invoice actual ship, `maturity = COALESCE(invoice maturity, actual ship, sale maturity)`; remainder rows: sale shipments/shipment date + sale maturity. **Superseded by Phase 8 (2026-10-29):** `latest_ship_date` is now the LC's latest date of shipment, and provisional rows carry no actual ship or maturity | ✅ `test_row_shape_contract_keys`, `test_report_row_maturity_prefers_the_invoice_maturity` (Phase 8 tests carry the current rule) |
| 4 | **`Prod. Date` from recipe production** | Subquery: `MAX(production_runs.production_date)` over `production_run_links`/`production_runs`/`recipes` for this **sale**, matching the run's `sale_item_id` or the recipe's `company_id`+`product_name`. `production_run_links.invoice_id` is ignored | ✅ `test_prod_date_comes_from_recipe_production_not_the_invoice`, `test_provisional_row_uses_the_sale_product_production_date`, `test_production_for_another_sale_does_not_leak_into_this_row` |
| 5 | **KPIs on row money** | Summary aggregates the same `base` rows: `SUM(total_price)`, `SUM(received_amount)`, `SUM(GREATEST(total − received, 0))`, overdue restricted to past-maturity owing rows, `COUNT(DISTINCT sale_id)`; drill-down rows equal the flat report | ✅ `test_summary_kpis_use_displayed_row_totals`, `test_report_kpi_received_equals_sum_of_row_payments`, `test_report_kpi_due_equals_row_totals_minus_received`, `test_mixed_sale_kpis_and_detail_use_displayed_line_totals` |
| 6 | **CSV** | 21 columns: `Prod. Date` after `Total Price ($)`, trailing `Provisional` | ✅ `test_export_marks_provisional_rows`, `test_export_carries_the_production_date` |

**Revision of the approved plan (user decision, 2026-10-28):** locked decision
#8 said `Prod. Date` came from "runs linked to the invoice matching the line
product, falling back to the sale's production." The user corrected this:
production is connected to **recipe production**, not to an invoice. The
implemented rule matches runs to the **sale** (by `sale_item_id` or by
`recipes.company_id` + `product_name`) and never reads
`production_run_links.invoice_id`, so an invoice-linked run for a different
product cannot set the date and one sale's production cannot leak into another.

**Locked decisions:** the row grain is the invoice line plus one remainder;
money is per row with invoice-first allocation and last-line residue
absorption; a payment never needs an invoice to be reported; `Prod. Date` is
recipe-produced for the sale's product.

**Constraints:** no schema change; parameterised SQL only; the report keeps its
filters/pagination/export contract, with the CSV at 21 columns.

**Gate run (2026-10-28):** backend **1347 passed / 8 skipped** in 1205.16s
(13 net-new report tests; the existing report suites were rewritten for the new
grain); ruff **63** repo-wide findings, the unchanged baseline, nothing in the
changed hunks; pyright **6 errors / 18 warnings** = baseline. Frontend is
untouched in this phase (Phase 6 is the report UI), so vitest/oxlint/build are
unchanged from Phase 4.

### Phase 6 — report UI: Prod. Date, per-row money, row captions ✅ 2026-10-28

The table now renders the Phase 5 rows as the operator sees them in the sheet:
a `Prod. Date` column and each row's own money, with KPI captions that talk
about rows instead of deduping them to sales.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Detail table** | `CommercialTable.jsx` gains `Prod. Date` after `Total ($)`; Received/Due/Maturity/Status are read from the row (no sale-level repetition); skeleton header and cells move 20 → 21; the grouped drill-down carries the same production-date cell | ✅ `CommercialTable.test.jsx` (headers, per-row money on two rows of one sale, skeleton 21, drill-down date) |
| 2 | **KPI fallback** | `Reports.jsx`: `dedupeSales` deleted; the page fallback sums `received_amount`/`due_amount` over **rows** and counts overdue rows, matching the whole-filter summary; captions read `report rows` (whole) / `report rows (this page)` (fallback) on gross/received/due, and `past-maturity rows (this page)` for overdue | ✅ `Reports.test.jsx` (same-sale rows sum 60/140, not 50; captions) |
| 3 | **Legend** | Legend explains the provisional **remainder** row and that an invoice row replaces that quantity; completed-sale retention sentence kept | ✅ `Reports.test.jsx` |

**Locked decisions:** the table renders the API's per-row money verbatim (no
client-side dedupe or re-allocation); the fallback exists only when the summary
is unavailable and says so in its caption; `Prod. Date` is display-only.

**Constraints:** no API change; no new columns beyond `Prod. Date`; the
skeleton must stay column-identical to the real table.

**Gate run (2026-10-28):** frontend **358 passed / 40 files** (was 356; 2 net
new assertions in `CommercialTable.test.jsx` and `Reports.test.jsx` rewritten
for row money); `npx oxlint` **0 errors / 0 warnings**; `npx vite build` OK
(chunk-size warning only). Backend untouched in this phase.

### Phase 7 — full gate, isolated-Postgres E2E, browser pass ✅ 2026-10-28

Release verification for the whole epic, plus the docs close-out. No product
code changed in this phase.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Docs audit** | SPEC's `get_sale_pi_total` note no longer claimed a report fallback; the Phase 5 handoff note is past tense and points at Phase 6; historical plan/handoff entries left as written | ✅ `docs/SPEC.md`, `docs/handoff.md` |
| 2 | **Backend gate** | `python -m pytest tests/ -q`, `python -m ruff check .`, `python -m pyright` | ✅ 1347 passed / 8 skipped; ruff 63; pyright 6 errors / 18 warnings |
| 3 | **Frontend gate** | `npx vitest run`, `npx oxlint`, `npx vite build`; fresh `dist/` copied to the gitignored `react_frontend/` | ✅ 358/358 in 40 files; oxlint 0/0; build OK |
| 4 | **Isolated-Postgres E2E** | Embedded pgserver in a throwaway temp dir (`DATABASE_URL` popped first and asserted to contain `raas_e2e`, never the production DSN); local SMTP sink so reminder mail is real-but-mocked | ✅ **64/64 checks**, exit 0 |
| 5 | **Browser pass** | Playwright against the same server: ship form, LC terms, card invoice list + maturity chip, report `Prod. Date`/per-row money, bell | ✅ no page errors (only the pre-login `/api/auth/me` 401) |

**E2E run (scenario from the approved plan).** Deferred-90 LC, PI A=100/B=100:
A invoice 80 → produce (2026-10-15) → book → actual ship 2026-10-26 → maturity
**2027-01-24** → sale and LC auto-move to Payment Due → pay → sale completes →
report shows A final 80 (received 400 / due 0, maturity 2027-01-24, `Prod. Date`
2026-10-15) and B provisional at the PI quantity 100 (0 / 700). A late B invoice
reopens the sale and LC to Payment Due, then walks book → ship (maturity
2027-01-26) → pay and completes again; CSV is 21 columns with `Prod. Date` after
`Total Price ($)`. A second at-sight LC shipped 8 days ago yields exactly one
cron reminder and one email to `NOTIFY_EMAILS` naming the invoice; a same-day
rerun creates and mails nothing; the bell shows the reminder.

**Browser pass highlights.** LC detail reads `Payment terms: Deferred · 90 days`;
the Ship form prefills the actual date and a `Maturity date` hinting
`Deferred 90 days`; saving moved the sale to Payment Due with no manual step and
recorded `shipped … / matures …` on the invoice row; a standalone card showed
its invoice list with `Matures Oct 20, 2026` + **Overdue**; the report rendered
the 21-column table with per-row `Prod. Date`, money and maturity plus the
provisional legend; the bell carried the `Invoice payment due` notification.

**Deviation (harness ordering, not app behaviour).** Recipes require the product
to be registered for the company, which the PI does; the E2E therefore creates
each PI before its recipe. Reminder mail hit a local SMTP sink rather than the
network.

**Constraints:** the E2E never reads or writes the production `DATABASE_URL`;
harness files live outside the repo; no schema, API, or gate changes.

### Phase 8 — latest date of shipment, LC deadline bell, report dates ✅ 2026-10-29

The LC's `expiry_date` is the **latest date of shipment** (shipment after it is
invalid). The field keeps its legacy column and wire key; only the label, the
report column and a new reminder changed. This section **supersedes** the
Phase 5 "Row dates" bullet above: `latest_ship_date` no longer comes from an
invoice.

| # | Slice | Scope | State |
|---|---|---|---|
| 1 | **Labels** | `LcDetailModal` edit + view call `expiry_date` "Latest Date of Shipment" (helper: shipment after this date is invalid, amend the LC); `LinkPIsModal` can set it when creating an LC | ✅ `LcDetailModal.test.jsx`, `LinkPIsModal.test.jsx` |
| 2 | **Report dates** | `latest_ship_date = letters_of_credit.expiry_date` on **every** row of the sale (NULL without an LC); `actual_ship_date`/`maturity_date` are invoice-only and **blank on provisional rows**, whose status is `Pending`; headers read `Latest Ship Date` / `Actual Ship Date` / `Maturity Date` | ✅ `test_latest_ship_is_the_lc_latest_shipment_date`, `test_latest_ship_is_null_without_an_lc_deadline`, `test_provisional_rows_have_no_actual_ship_or_maturity`, `CommercialTable.test.jsx` |
| 3 | **Bell warnings** | `reminders.run_lc_shipment_reminders`: 7 days out → `lc_shipment_deadline` (warning, `lc:{id}:ship:7`); after the deadline → `lc_shipment_deadline_passed` (critical, `lc:{id}:ship:0`). Bell only, **never email, never blocks shipping**. Silent without a parseable deadline (no `lc_date` fallback). Stops when every attached PI is completed; a zero-PI LC still warns; > 7 days out clears both keys | ✅ `tests/test_lc_shipment_reminders.py` (12) |
| 4 | **Cron GET** | Vercel Cron invokes GET; the route was POST-only (production 405). Both verbs registered, both in `_PUBLIC_API`, same Bearer gate. Response gains `lc_reminders`; the audit line records it | ✅ `test_cron_get_creates_lc_reminders`, `test_cron_get_still_requires_the_bearer_secret` |
| 5 | **Amend re-arms** | `PUT /api/lcs/<id>` validates `expiry_date` as ISO `YYYY-MM-DD` (400 otherwise, blank → NULL) and, when it changes, deletes both LC keys under `FOR UPDATE` in the same transaction as the update + `LC_UPDATE` audit | ✅ `test_lc_put_expiry_change_clears_lc_reminders`, `test_lc_put_unrelated_change_keeps_lc_reminders`, `test_lc_put_rejects_non_iso_expiry_date` |

**Locked decisions:** the latest ship date belongs to the credit, not an
invoice ("আর লেটেষ্ট শিপ ডেটের সাথে ইনভইসের কোন রিলেশন নাই"); a provisional row
has no actual ship and no maturity because it has no invoice; reminders are
bell-only so the operator can amend the LC and extend the deadline.

**Constraints:** no schema change (`expiry_date` kept); no ship blocking;
parameterised SQL only; frontend routes `entity_type='lc'` to `/sales`.

---

### Phase 9 — recipe sourcing from running LCs + revision ✅ 2026-10-29

The **Create New Recipe** dialog listed every LC of the company — including paid,
completed ones — labelled with raw stage codes, and every invoice of a PI,
including `booked`/`shipped`/`paid`. A recipe is a production formula for an
**invoice product**, so the only meaningful cascade is: LC still running →
PI with an invoice in production → invoice in `planned`/`produced` → product.
The name is user-typed (the PI and product are a hint; one recipe per product).
A recipe created by mistake is revisable afterwards.

| # | Slice | Scope | Commit |
|---|---|---|---|
| 1 | **LC + invoice sourcing** | `list_lcs(open_only)` keyed on **open child PIs** (never the LC's own stage — completion is derived and lags) + grouped `open_pi_count`/`producible_pi_count`; `get_lc(open_only)` with `client_name`/`company_id`/`producible_invoice_count`; `list_invoices(producible_only)` with public `BOOKABLE_STATUSES`; route params `open_only`/`producible` | `a8f0878` |
| 2 | **Readiness across recipes** | `production_readiness`/`invoice_production_readiness` select **all** recipe ids for the product and accept a run from any of them (the old `fetchone()` picked one recipe, so a sibling recipe could block booking forever). `missing_recipes` still means "none at all" | `61cc694` |
| 3 | **Create modal** | Company → LC → PI → Invoice → Product → name → quantity/water; zero-work options visible but disabled; `· recipe exists`; quantity prefills from the invoice line (batch basis, not a cap); LC/PI/invoice stay selected after create; `/sales` empty-state link | `c58863c` |
| 4 | **Revision backend** | `PUT /api/recipes/<name>` accepts `new_name`/`product_name`; resolve once, update by `id`; `list_recipe_items` by `recipe_id` (case-only rename used to lose ingredients); `lower(name)` uniqueness + `IntegrityError` backstop → `RecipeNameConflict` (409); product frozen after a run → `RecipeProductLocked` (409), checked before registration; audit records old → new name + changed fields; detail gains `run_count` | `71f49d5` |
| 5 | **Revision UI + name consumers** | Edit Recipe: name, product (disabled when `run_count > 0`), quantity, water; adopts the server-returned name in `selectedRecipe`, the list row, the follow-up detail fetch and the re-keyed `ProductionRunModal`; 409 inline with the form open; the modal no longer synthesises an option for a stale name | `38d6902` |
| 6 | **Docs + gate** | PRD/SPEC/plan; full pytest + ruff + pyright + vitest + oxlint + vite build → `react_frontend/` | this commit |

**Locked decisions:** `open_only` is child-based, because `_sync_lc_completion`
only runs on sale moves, `attach_pis` allows late attaches and `detach_pi` never
resyncs — an LC-stage filter would show paid LCs and hide live ones. `producible`
is `planned`/`produced`: `booked` is past production and `shipped`/`paid` are
already refused by the service. A rename does not rewrite history — runs link by
`recipe_id`, frozen items carry chemical names, audit/stock reasons are text. The
product is frozen after a run because readiness and the commercial report key off
`recipes.product_name`.

**Constraints:** no schema change; `production_runs.sale_item_id` stays NULL
(name-based matching remains a documented limitation); production quantity stays
free of `total_quantity`; water is metadata. Out of scope and flagged:
`ProductionRunModal` still offers all company recipes rather than product-matched
ones; `Reports.jsx` posts bare recipe names; `DELETE` of a produced recipe still
500s on FK RESTRICT; legacy `lc_number`-only PIs are not in the cascade.

> Superseded 2026-11-01 by **Phase INVOICE-SCOPED-RECIPES** below: one recipe now
> belongs to one invoice, so the product/quantity/water revision and the name-keyed
> report payloads described above no longer exist.

**Walkthrough (isolated Postgres, built SPA).** Two defects surfaced and were
fixed. `ProductionRunModal` read `GET /api/sales` as an array although it
answers `{sales, total}`, so the PI dropdown was always empty and no run could
be started from the UI (`f3c2910`). A run saved from that modal left the detail's
`run_count` stale, so the next Edit still offered a product change the server
refuses with 409 (`8b15da9`). Verified live: child-based `open_only` (LC-2026-003
listed with a `completed` LC stage and a planned child; paid-child LCs hidden),
`producible` excluding a paid invoice, disabled zero-work LC and PI, the
PI/product hint with an empty name, quantity prefill, selections retained after
create, rename propagation into the list/detail/production modal, product rebind
before a run, product lock after it, and set-based readiness (a run on a sibling
recipe made INV-014 bookable while the first recipe still had 0 runs).

**Detail lookup by row id (2026-10-29).** Live View reported "Recipe not found"
for a recipe the list was showing; the backend, the row and the list response
(`company_id` included) were all correct, so the miss was a client-supplied
`(company_id, name)` pair that did not match. Detail reads now key off
`GET /api/recipes/by-id/<id>` — the id the list already hands out — and the
`(company, name)` route stays as the fallback for payloads without one. A 404
now means the row is gone: the list refreshes. Mutations still address the
recipe by name (the detail carries the server's name), and runs link by
`recipe_id`, so renames stay surgical.

**Percent-encoded path parameters (2026-11-01).** Live Edit still answered
"Recipe not found" for the recipe the list and the detail were showing. The
cause was the host, not the data: Vercel's Python runtime hands the app a
`PATH_INFO` that is still percent-encoded, so
`PUT /api/recipes/Production%20KNIT%20432` captured the encoded name and every
lookup missed.
Proved read-only: `/api/auth/status` is 200 while `/api/auth/%73tatus` is 401,
and the Flask test client reproduces it with
`environ_overrides={"PATH_INFO": ...}`. The same defect caused the 2026-10-29
View miss — the by-id route fixed that read by sidestepping the name, not by
fixing the path. `DecodePathInfo` (`raas_tracker/wsgi_compat.py`) is installed
in the WSGI chain inside `_apply_proxy_fix` — inside ProxyFix, so it runs after
the proxy headers are applied and before routing — only when the
deployment declares `DECODE_PATH_INFO=1` (`vercel.json`), because a compliant
server has already decoded the path and a second pass would rewrite a name that
literally contains an escape (`Batch%20A` → `Batch A`); tests pin both host
behaviours. One shim
repairs every name-addressed route (detail fallback, update, delete, items with
a `<chem>` segment, produce, runs), and the gate now sees the path PEP 3333
promises.

---

## 🧾 Phase INVOICE-SCOPED-RECIPES: one recipe per invoice (completed 2026-11-01)

A recipe is now the **production plan for one invoice**, not a formula for one
product: every product on the invoice is formulated and produced under that one
recipe, quantities come from the invoice, and the old product/quantity/water
header fields are gone. Existing recipes were demo data and were deleted rather
than migrated (the v8 migration refuses to guess a product → invoice mapping).

| # | Phase | Scope | Commit |
|---|---|---|---|
| 0 | **Demo reset** | `scripts/reset_demo_recipes.py` + `raas_tracker/demo_reset.py`: dry run by default, apply needs `--yes` **and** an existing `--backup-file` dump written under an advisory lock; prints the per-chemical stock delta and restores it only with `--restore-stock`; resets affected invoices to `planned` and sale badges to `production_running`; never touches stages, payments, shipments or amounts | `1dd9ca4` |
| 1 | **Schema v8** | `recipes.invoice_id` NOT NULL + unique; `recipe_products` (slot per product, unique `(recipe_id, lower(product_name))`); `recipe_items.recipe_product_id` + percentage; `production_run_outputs`; drops the old product/quantity/water columns after asserting no recipe/run rows remain; `_SCHEMA_VERSION = 8` | `ae6b2c9` |
| 2 | **Service + API** | `add_recipe(name, company_id, invoice_id)` derives and syncs slots from invoice lines; detail returns `{recipe, products, items}` with invoiced/produced/remaining and formulation state; per-product item routes by item id; rename-only `PUT /api/recipes/by-id/<id>`; `POST /api/recipes/by-id/<id>/produce` takes `{outputs}` and derives sale/invoice links from the recipe; `/api/invoices/<id>/production-source` | `3dea6ac` |
| 3 | **Readiness, reports, units, stock** | Booking requires the invoice's recipe, a formulation per product and a linked run **output** per product; ship re-checks; report attribution per output and per invoice (provisional rows by sale); report request is `{recipes:[{recipe_id, outputs}]}` with one aggregated deduction per chemical and converted stock units; void guard when runs exist | `d04b6f5` |
| 4 | **Frontend** | Create modal keeps Company → LC → PI → Invoice and drops product/quantity/water; detail is one `ProductFormulationCard` per invoice product; rename-only edit; production modal fixed to the invoice with remainders prefilled; report picker by recipe id with per-product quantities and a per-product breakdown; list/KPI invoice wording | `0497c94` |
| 5 | **Docs + gate** | PRD Flow 4/6/7 + data model; SPEC schema/routes/readiness/report/version history; this section; full gate | this commit |

**Locked decisions.** `recipes.invoice_id` is `UNIQUE` because the invoice *is*
the recipe — two plans for one invoice would make readiness ambiguous. A product
on the invoice with no formulation blocks booking (an empty slot is not a plan);
a product added after the recipe was created gets that slot automatically and is
named in the block message. Production quantities are prefilled from the invoice
remainder but stay editable and are **not capped**, because over-production is a
real scenario; the UI warns instead. A run's links are derived from
`recipes.invoice_id`, so a request cannot fulfil an invoice it does not own.
Invoice lines removed after formulation mark the slot off-invoice instead of
deleting history. Reports aggregate one requirement per chemical across all
product outputs, and a missing unit conversion is a **400 naming both units**,
never a guessed KG.

**Risks and resolutions.** (1) *Lost demo formulations* — the reset script prints
the delta and requires a dump first; stock restore is opt-in and previewed. (2)
*Old clients posting product/quantity/water* — the Pydantic models are
`extra="forbid"`, so the request fails loudly with a 400 instead of silently
creating an invoice-less recipe. (3) *Late invoice lines silently unproduced* —
slots are synced on every detail read and readiness names the missing product.
(4) *Double deduction when two products share a chemical* — requirements are
aggregated per chemical id before conversion and one deduction is written. (5)
*Report totals that drift from the table* — export re-posts the exact selection
that generated the table, never `report.recipes` (server rows the route rejects).

**Cutover (live v7 → v8).** Stop writes, keep a dump, reset the demo data, then
deploy:

```bash
python scripts/reset_demo_recipes.py                     # dry run, review delta
pg_dump "$DATABASE_URL" -Fc -f demo_backup.dump          # keep this file
python scripts/reset_demo_recipes.py --yes \
    --backup-file demo_backup.dump --restore-stock
```

Then start the v8 app: the migration asserts no recipes/runs remain, adds the new
tables and drops the old columns in one transaction under
`pg_advisory_xact_lock`. Apply is **not** reversible without the dump.

**Verification.** Backend (Phase 5 gate, 2026-11-01): **1444 passed, 8 skipped**
(1225s on the embedded Postgres), `ruff check` at its 18 known baseline nits
(unused imports/redefinitions in `flask_app.py` and `sales.py`, none added by
this phase), `pyright` 0 errors / 15 baseline warnings. Frontend at Phase 4 and
re-run for Phase 5: 375 tests across 41 files, oxlint 0 warnings / 0 errors,
`vite build` green, `dist/` copied to `react_frontend/`. Isolated-Postgres and
browser walkthroughs (the plan's verification steps 1–5) remain to be re-run
against the deployed v8 before the demo data is rebuilt.

---

## 💧 Phase RECIPE-WATER: water % in the view + un-clipped pickers (completed 2026-11-02)

Water disappeared from the recipe flow with the invoice-scoped rewrite, and the
formulation card's stock-chemical picker rendered its options inside an
`overflow-hidden` card, so the suggestions were invisible. Water is back as a
per-product, **display-only** value in the recipe **view**; the picker's
listbox now renders outside the card.

- Schema v9: `recipe_products.water_percentage` (nullable; NULL = `100 − ingredients`).
- Detail payload adds `water_percentage`, `water_effective`, `grand_total`; `PUT /api/recipes/by-id/<id>/products/<product>/water` sets or clears it (operator, audited `RECIPE_WATER_SET`).
- The recipe view shows an inline Water % control per product card and an informational grand-total warning; the create form still has none.
- `EntityCombobox` portals its listbox to `document.body` (fixed, flips up), so `overflow-hidden` cards no longer hide suggestions.

**Commits** (`git log --oneline main..HEAD`; the docs commit for this phase follows them):

```
f398ada fix(recipes): surface the stock chemical picker with clear empty copy
f86b977 fix(ui): render combobox options outside clipped cards
7f065fe feat(recipes): inline water percentage and grand total in the view
bb36db3 feat(api): set or clear a product's water percentage
1dec1ec feat(recipes): report and set per-product water percentage
d913577 feat(schema): per-product water percentage (v9)
```

**Verification.** Backend gate: **1451 passed, 8 skipped** (1405s on the embedded
Postgres), `ruff check` at its 18 known baseline findings (unused imports,
redefinitions, unused locals and an ambiguous name in `flask_app.py` /
`sales.py`; none on a line this phase added), `pyright` 0 errors / 15 baseline
warnings. Frontend gate: **411 passed across 44 files**, `oxlint` 0 warnings /
0 errors, `vite build` green, `dist/` copied to `react_frontend/`.

---

## 🧾 Phase PRODUCT-CATALOG: product-name suggestions (completed 2026-11-01)

Operators typed product names freehand in three places, so the same SKU could
arrive with different spellings from a parsed PI and from manual entry. The 2026
product list (`List Of Product 2026.pdf`, 70 SKUs) is now a **suggestion list**
behind every editable product-name field. It is deliberately not a hard select:
parsed PIs and products not yet on the list must still be able to save.

| # | Phase | Scope | Commit |
|---|---|---|---|
| 1 | **Catalog + combobox** | `src/data/products.js` (`PRODUCT_NAMES`, the 70 names only — no category or HS code); `src/components/ui/ProductInput.jsx` — a native `<input list>` + `<datalist>` with a `useId()` list id per instance, drop-in for a text input | this commit |
| 2 | **Call sites** | `MultiPICreateModal` (per-document row, keeps its `Product name document N row R` label), `SaleDetailModal` edit rows (`Product name row N`), `ReviewModal` parsed-PI rows. No other editable product field exists: invoice, recipe and report product names are derived from invoice lines, and `CommercialFilters` carries unused `product_name` URL state with no rendered input | this commit |
| 3 | **Tests + docs** | `ProductInput.test.jsx` (70 unique option values, free text `onChange`, distinct ids for two instances), one dropdown-presence assertion each in `SaleDetailModal.test.jsx` and `ReviewModal.test.jsx`; SPEC/PRD notes | this commit |

**Locked decisions.** The catalog is code-owned, not a DB table: no admin CRUD
was requested, and shipping a table would mean a migration, seed and API for a
list that changes once a year. It is a UI affordance only — the API keeps
`product_name` as free text, so nothing downstream changes. Product names are
matched case-insensitively where the schema says so (`recipe_products`,
`production_run_outputs`), which means a near-miss spelling from the list is
still tolerated without a data migration.

**Verification.** Frontend gate: **382 passed across 42 files**, `oxlint` 0
warnings / 0 errors, `vite build` green, `dist/` copied to `react_frontend/`.
Backend untouched — no API, schema or test change.

---

## 🔎 Phase ENTRY-COMBOS: every entry dropdown is type-or-pick (completed 2026-11-01)

Product names gained a suggestion list, but the remaining entry dropdowns were
still hard `<select>`s: a unit outside `COMMON_UNITS`, an adjustment reason
outside the four presets, or a record that existed but was hard to find all
meant leaving the form. Every data-entry dropdown now follows one of two
shapes, and no request body changed.

| # | Phase | Scope | Commit |
|---|---|---|---|
| 1 | **`SuggestionInput`** | Extracted from `ProductInput`: `<input list>` + `<datalist>` over `options: string[]`, free text always accepted; `ProductInput` becomes a thin wrapper over the catalog | this branch |
| 2 | **`EntityCombobox`** | Hand-rolled ARIA combobox over `{ value, label, disabled }`: filters on type, commits on click/Enter/exact label, emits `''` and shows a hint for unmatched text, skips disabled rows, Escape restores the pick, focusing a field that already holds a pick lists every option and selects the label so the first keystroke searches afresh, a parent value change wins over stale text | this branch |
| 3 | **Free text** | Units in `MultiPICreateModal`, `ReviewModal`, `Chemicals` (add) and `UnitMapping` (target unit); the stock-adjustment reason (120-char cap kept) | this branch |
| 4 | **Record picks** | Company in `MultiPICreateModal` / `ReviewModal`; existing LC in `LinkPIsModal`; target invoice in `SaleDetailModal`; the recipe create cascade (company → LC → PI → invoice) and the chemical field (name-valued) in `Recipes` | this branch |

**Locked decisions.** Fixed enums and every filter (report, audit, stock board,
role, payment terms, page size, dates) stay native `<select>`s — they are not
record picks and a free-text value would be meaningless. Record pickers can
only emit an id (or, for chemicals, a name) that exists in their option list,
so referential integrity is preserved in the component rather than at the API.
No backend change: form state stays ids/strings and every payload is identical.

**Verification.** Frontend gate: **402 passed across 44 files**, `oxlint` 0
warnings / 0 errors, `vite build` green, `dist/` copied to `react_frontend/`.
Backend untouched — no API, schema or test change.

---

## 🧾 Phase INV-UNIFY: PI-faithful invoice editor (completed 2026-10-22)

One invoice-entry surface instead of two divergent ones (`InvoiceCreateForm`
vs `InvoiceLineEditor` + `InvoiceLineComposer`). The invoice mirrors the PI:
product and unit price are inherited from the PI line and are **server-owned**;
the operator types quantity only. Executed in gated phases, one at a time.

| # | Phase | Oracle gate | State |
|---|---|---|---|
| 0 | Baseline + deployment reconciliation | — | ✅ 2026-10-21 |
| 1 | Close the batch-vs-add-line race (`FOR UPDATE` on `sale_items`) | ✅ | ✅ 2026-10-21 (already closed; locked by tests) |
| 2 | Delete `backfill_invoice_lines` + its tests (auto-invoicing) | ✅ | ✅ 2026-10-21 |
| 3 | `GET /api/sales/<id>/invoice-lines` (kills the N+1 fetch) | — | ✅ 2026-10-21 |
| 4 | Pin the backend invoice contract with tests | — | ✅ 2026-10-21 |
| 5 | `InvoiceEditor` + rewritten `InvoiceLineComposer` | ✅ spec-first | ✅ 2026-10-21 — editor + hook + `code` on 404 landed (`f6c7fe4`, `23b8743`); the composer was deleted in Phase 6 instead of rewritten |
| 6 | Swap call sites, delete the legacy files | ✅ | ✅ 2026-10-21 (`d016093`, `96c2520`, `925a814`, review fixes `a1d272a`) |
| 7 | Docs close-out | ✅ | ✅ 2026-10-21 (`7db063e`, `bbde7e7`, `35a9426`, `0cacef3`) |
| 8 | Composer column model: open quantity, live Remaining, read-only Total | ✅ | ✅ 2026-10-22 (`10f9759`) |

### Phase 1 finding: the batch race was already closed (2026-10-21)

The plan said `create_invoices_batch` read `sale_items` at `sales.py:2007`
without `FOR UPDATE`, racing `create_invoice_item`. It does not:
`create_invoices_batch` has taken `SELECT id FROM sale_items WHERE sale_id = %s
ORDER BY id FOR UPDATE` (`sales.py:1933-1935`) since `7a7ae73` (2026-10-04),
before it evaluates tolerance. `create_invoice` locks each line inside
`validate_invoice_lines` (`sales.py:1714-1717`) and `create_invoice_item` locks
its own line (`sales.py:2518-2521`); all three hold the lock to commit
(`sales.py:1904`, `:2042`, `:2560`).

**No production code changed.** `TestWriterLockDiscipline`
(`tests/test_invoice_tolerance.py`) pins the behaviour instead. The batch lock
was mutation-checked by deleting `sales.py:1933-1935` and re-running:

| Test | Lock deleted | Observed failure |
|---|---|---|
| `test_batch_locks_every_pi_line_of_the_sale` | the `sale_items ... FOR UPDATE` at `sales.py:1933-1935` (**not** the `sales` row at `:1929`) | batch billed a line the lock still held |
| `test_add_line_and_batch_cannot_jointly_exceed_the_ceiling` | the batch's, then the add-line's `:2520` | `over-invoiced: 122.0 > 105` either way |

Only the first is a deterministic mutation detector. The joint test is an
*outcome* test: delete the add-line lock and whether it fails depends on which
request is queued first on the row, because the unlocked writer checks
tolerance before it blocks. Do not cite it as proof that the add-line lock is
pinned.

A naive "does it block?" probe cannot test this either: any writer that inserts
an `invoice_items` row waits on the foreign key's `FOR KEY SHARE` for the
referenced `sale_items` row, which conflicts with `FOR UPDATE` anyway. The first
test therefore holds the lock on a **different** PI line of the same sale, which
removes the confound.

**Precondition for the whole argument:** it holds under PostgreSQL READ
COMMITTED, which is what the app uses — nothing sets `SET TRANSACTION` or a
non-default `default_transaction_isolation`. A database or role default of
REPEATABLE READ would let the loser keep a pre-lock snapshot and miss the
winner's rows.

The remaining unlocked writer, `backfill_invoice_lines` (then `db.py:506`), was
deleted in Phase 2 — it evaluated quantities with no `sale_items` lock. Separately,
`delete_sale_item` (`sales.py:770-790`) counts `invoice_items` unlocked and then
deletes the parent, which `ON DELETE SET NULL` (`db.py:959`) turns into an
orphaned invoice line; that TOCTOU belongs with the other parent-mutation gaps
tracked at the end of this file, not with Phase 1.

### Phase 2: auto-invoicing removed (2026-10-21)

`backfill_invoice_lines` and its call inside `_run_migration` (invoked by
`_create_tables`, `db.py:347`) are gone, along with the `Decimal` /
`ROUND_HALF_UP` import and the `_D` alias that only it used. No invoice line can
now be written without an operator action.

Two tests went with it, not the one the plan named:
`test_backfill_invoice_lines_seeds_and_is_idempotent` **and**
`test_backfill_reachable_through_get_connection` — the second took the file's tail
plus its `salesmod_leftover_count` helper (`tests/test_lc_schema.py:353-457`).
Deleting the function before the tests turned both red (`AttributeError`, and
`backfill did not run on version bump`), which is the evidence they were really
exercising it rather than decoration. A guard replaces them:
`test_migration_never_invents_invoice_lines` forces a full migration pass on a
line-less invoice and asserts the line count is still 0. It was mutation-checked
by temporarily re-adding a seeding write, which turned it red.

`_SCHEMA_VERSION` was deliberately **not** bumped. The bump rule (`db.py:122-124`)
requires a bump when `_create_tables()` gains DDL, changes a column default, or
adds a one-shot data migration; this phase does none of those. Deleting a seed is
not a reason to bump — a bump would re-run every migration everywhere for no
benefit. (`_schema_signature_live`, `db.py:260-270`, hashes columns and indexes
only, and those are unchanged.)

Accepted consequence, stated precisely: a legacy line-less invoice stays
line-less, contributes nothing to `sale_total`, and makes a sale whose only
invoice it is report `gross_sales = 0`, `due = 0` and therefore
`payment_status = "Paid"` (`sales.py:1203-1206`, `:1240-1242`, `:1045-1046`).
That is the honest outcome versus inventing a receivable. Production has zero
invoices, so nothing is affected today; any other deployment must verify
line-less invoices = 0 before trusting its commercial report.

### Phase 3: PI-line budget endpoint (2026-10-21)

`GET /api/sales/<id>/invoice-lines` (Session/Key, 300/min) returns
`{lines: [...], has_line_less_invoices: bool}` in one request, replacing the N+1
(`/api/sales/<id>` then `/api/invoices/<id>/items` per invoice):

| Field | Rule |
|---|---|
| `pi_quantity`, `unit_price` | from `sale_items`; `unit` falls back to `'KG'` when blank |
| `invoiced_quantity` | `SUM(invoice_items.quantity)` across **every** invoice of the sale, no status filter |
| `remaining` | `max(0, PI − invoiced)`; informational — a fully invoiced line is still returned and still invoiceable |
| `invoice_ids` | `array_agg(DISTINCT …)` of the invoices holding lines for that PI line |
| `has_line_less_invoices` | `true` when a legacy invoice has header money no line backs; `remaining` overstates what was billed in that case |

**Updated 2026-10-25:** the `ceiling` field and the whole tolerance system were
removed. Quantity is free relative to the PI; `remaining` is informational. See
the 2026-10-25 row in "Public contract changes".

404 on an unknown sale; `{lines: [], has_line_less_invoices: false}` for a sale
with no PI lines. 8 tests in `tests/test_invoice_contract.py` §6, including a
directly inserted line-less invoice.

Why the flag exists: quantity is `PI − invoice_items`, so a line-less legacy
invoice contributes 0 and the whole PI reads as uninvoiced. The money side has a
helper that *does* count a header-only invoice (`_invoice_money_already_on_sale`,
`sales.py:1618-1643`), but its result is discarded at `sales.py:1837` (kept only
as a non-finite guard) and `pi_proposal_vs_invoiced` has no callers — nothing
reconciles the two. Synthesising quantity from a header amount would be the
deleted backfill again, so the endpoint reports the inconsistency instead.
Production has zero such rows; this is a contract guard, not a live bug.

### Phase 4: writer contract pinned (2026-10-21, tests only)

Most of the contract was already covered, so Phase 4 added only the gaps rather
than restating `test_invoice_tolerance.py` / `test_invoice_amount.py`:

| Property | Where |
|---|---|
| Forged `unit_price`/`line_total` in the POST body is inert: 201, stored price = PI price | `test_create_invoice_ignores_a_forged_unit_price` |
| Same at the service seam (`validate_invoice_lines` rebuilds the price from `sale_items`) | `test_validate_invoice_lines_ignores_a_forged_unit_price` |
| The Phase 3 endpoint's 6 dp `ceiling` was accepted verbatim by the writer; `ceiling + 1e-6` was `ToleranceExceeded` *(removed 2026-10-25 with the ceiling)* | `test_endpoint_ceiling_is_exactly_what_the_writer_accepts` |
| One `INVOICE_CREATE` per invoice, one `INVOICE_ITEM_ADD` per line, header audited **last** | `test_create_invoice_audits_each_line_then_the_header` |
| Non-finite PI data → named `ValueError`, no invoice written (the `remaining_invoice_money` call that is otherwise value-unused) | `test_create_invoice_refuses_a_non_finite_pi_total_before_writing` |

Already covered, not duplicated: ceiling boundaries for all three writers
(`test_invoice_tolerance.py`, deleted with the tolerance on 2026-10-25),
atomicity of a failed batch
(`test_batch_over_allocation_rejected_and_atomic`), create-vs-create race
(`test_concurrent_creates_cannot_over_invoice_the_sale`), the line-loop audit
never committing (`test_line_audit_never_commits_mid_create`), and Phase 1's
batch-vs-add-line lock test.

Mutation check: making `validate_invoice_lines` trust `line.get("unit_price")`
fails `test_validate_invoice_lines_ignores_a_forged_unit_price` (0.01 vs 7.5).
The API-level twin keeps passing under that mutation by design — Pydantic drops
the field before the service sees it, so the two tests pin different layers.

### Phase 5: `InvoiceEditor` (component + hook landed 2026-10-21; call sites in Phase 6)

**Why one component.** Five places write invoices; four of them collect lines
differently, and three still speak the pre-PHASE-1 contract:

| Call site | Today | State at HEAD |
|---|---|---|
| `InvoicePanel.jsx:502` | `InvoiceCreateForm` + `piLines` prop | works, N+1 fed |
| `SaleDetailModal.jsx:90` | `InvoiceLineEditor` + `useSaleInvoiceLines` | works, N+1 fed |
| `InvoicesRequiredModal.jsx:61` | `POST {invoice_number}` | **400** — `invoice_date` and `lines` are required (`flask_app.py:2374-2383`); `InvoicesRequiredModal.test.jsx:93` asserts the broken payload |
| `ProductionRunModal.jsx:222` | `POST {invoice_number}` | **400**, same cause; the comment at `:210-213` still describes the deleted auto-seed |
| `InvoiceBuilderModal.jsx:252` | `POST {invoice_number, lines}` | **400** — no `invoice_date`; dead code (imported by `Sales.jsx:14`, never rendered) → delete |

So Phase 6 is not a cosmetic swap: two user-facing flows are broken right now,
and only the LC-barrier modal is gated (it cannot create the invoice the
shipment barrier demands).

**Contract**

```
<InvoiceEditor
  mode="create" | "add-line"
  saleId={number}
  invoiceLabel?={string}      // add-line: the target invoice's number
  targetInvoiceId?={number}   // add-line: required in that mode
  onDone={(result) => void}   // after a successful write
  onCancel={() => void}
/>
```

- The editor owns `GET /api/sales/<saleId>/invoice-lines` through a new
  `useInvoiceBudget(saleId)` hook returning `{lines, hasLineLessInvoices,
  loading, error, reload}`. All four call sites then share one source of truth,
  and `useSaleInvoiceLines` / `fetchSaleInvoiceLines` are deleted.
- `mode="create"` renders number + date (default `todayLocalISO()`), then one
  row per PI line. `mode="add-line"` renders the same rows without the header
  fields, scoped to `targetInvoiceId`.
- Unit price is a **read-only `<input>`** (asserted, not incidental) sourced
  from the payload and **never** put in a request body.

**Row and figures (2026-10-25: no cap).** The row is
`Product | PI qty | Invoice qty | Unit price | Total price`: one open quantity
input, a live `Remaining` subline under the PI qty (`max(server remaining −
typed, 0)`; the server already nets out every prior invoice), and a read-only
`Total price` (`lineTotal(quantity, unit_price)`). Quantity is free relative to
the PI — equal, more, or less — and `Remaining` is informational. The old
`ceiling`-based hard stop and its `1e-9` boundary allowance were removed with
the tolerance system; see the 2026-10-25 contract row.

**400 handling (create).** Classification is **structural**, never by parsing
the server's prose. Create is atomic, so a refusal writes nothing:

1. `400` (a PI line deleted between load and submit) → reload; the staged
   entries whose `sale_item_id` is gone are dropped and listed by product name
   in one form-level amber notice (`N PI line(s) are no longer on this PI —
   review and resubmit`). A dropped row cannot carry its own mark, so the
   notice is the mark.
2. anything else → generic rose error; staged state untouched.

**404 handling (add-line).** A `404` is genuinely ambiguous server-side —
either the invoice is gone or the PI line is
(`sales.py:2512-2524`, both mapped to 404) — so the add-line response body
carries a `code` field (`invoice_gone` | `pi_line_gone`); the editor marks the
target `superseded` and disables Save only for `invoice_gone`. The backend half
is **done** (`flask_app.py:3927-3960`, pinned by
`tests/test_invoice_contract.py:test_api_add_line_404_carries_a_code`, and
`ApiError` now forwards `data.code` so the client can read it).

**Tests to write first (red).**

`InvoiceEditor.test.jsx`
1. create renders number, date (defaults to today), one row per PI line, a
   read-only unit price, and no "ceiling" copy.
2. Save disabled until a number and at least one quantity > 0 exist.
3. create POST body is exactly `{invoice_number, invoice_date,
   lines:[{sale_item_id, quantity}]}` — no `unit_price` key anywhere.
4. a quantity far above the PI (a fully invoiced row) stays submittable and
   POSTs; no over-limit copy is rendered.
5. no `ceiling` is read from the payload or advertised anywhere.
6. add-line POSTs `/api/invoices/<id>/items` with `{sale_item_id, quantity}`.
7. create 400 → vanished product name appears in the amber "no longer on this
   PI" notice and the header is preserved.
8. success → `onDone` receives the created invoice.

`InvoicesRequiredModal.test.jsx` (rewrite, not delete): each POST carries
`invoice_date` and a `lines` array covering that PI's remaining quantity; a PI
with no budget left is skipped rather than posted line-less, and a run that
creates nothing does **not** toast success or call `onSuccess` (the barrier
counts invoices across all the LC's sales, so a silent zero would just retry
into a 409).

`ProductionRunModal.test.jsx`: the invoice-create POST carries `invoice_date`
and `lines`.

**Landed (2026-10-21).**

| Piece | Where |
|---|---|
| `useInvoiceBudget(saleId)` | `src/components/sales/useInvoiceBudget.js` — one `GET /api/sales/<id>/invoice-lines`; returns the reloaded `{lines, hasLineLessInvoices}` so callers can resync |
| `InvoiceEditor` (create / add-line) | `src/components/sales/InvoiceEditor.jsx` |
| `code` on add-line 404 | `flask_app.py` + `ApiError.code` (`src/utils/api.js:3`, forwarded in `src/context/AuthContext.jsx:94-100`) |
| 15 tests (13 red-first + 2 partial-success from the Phase 6 review) | `src/components/sales/InvoiceEditor.test.jsx` |

Two corrections made while turning the spec into tests:

- The 409 test as first drafted typed a quantity already over the *initial*
  budget, so the hard stop (correctly) prevented the POST and no 409 could
  happen. It now types `6` (allowed against a stale `invoiced=2`, over once the
  server reports `invoiced=6` → max add `4.50`), which is the only shape a real
  409 can take from this UI.
- In add-line mode a `pi_line_gone` reloads and drops the row through the same
  superseded path as create; only `invoice_gone` disables Save, because the
  target itself is the thing that vanished.

`InvoiceLineComposer.jsx` was **deleted, not rewritten** (deviation from the
table at the top of this section, recorded in Phase 6): its one-line
select-and-quantity model has no place in the table the editor renders, and once
its only caller (`InvoiceCreateForm`) went, rewriting it would have produced a
second composer with no callers. Phase 6 also corrected this table's own premise:
`InvoiceBuilderModal` was dead code, not a live 400.

### Phase 6: call sites swapped, legacy files deleted (2026-10-21)

| Commit | Content |
|---|---|
| `d016093` | `InvoicePanel` and `SaleDetailModal` render `InvoiceEditor`; `InvoiceCreateForm`, `InvoiceLineEditor` (+ test), `InvoiceLineComposer`, `useSaleInvoiceLines` deleted |
| `96c2520` | `InvoicesRequiredModal` and `ProductionRunModal` state `invoice_date` + `lines` instead of posting a bare number |
| `925a814` | `InvoiceBuilderModal` deleted; dead `lcId` props dropped from `Sales.jsx` |
| `a1d272a` | Oracle review fixes: partial success is reported, `pi_line_gone` handled, header-only invoices refused |

**Both broken flows are fixed.** Each barrier now reads
`/api/sales/<id>/invoice-lines` and posts the rows that still have budget. A
filled row whose PI is exhausted is **refused with a reason** rather than being
dropped silently — the operator typed a number expecting an invoice, and the API
would reject quantity `0` anyway. A batch that creates nothing does not toast
success, call `onSuccess`, or close: the barrier would refuse again immediately.

**Partial success is reported, never assumed away.** Both of the first-cut
shapes were wrong in the same way — the happy path was wired, the half-failed
one was not:

- the barrier called `onSuccess` even when some rows failed. `onSuccess` closes
  the modal and retries the LC move, so the failed rows were unreadable and the
  LC could advance with a PI that still had no invoice (the gate only needs one
  invoice across the LC). It now stays open with the failures shown and does not
  advance.
- the add-line loop fired `onDone` only after the last row, so a mid-sequence
  refusal skipped it even though the earlier POSTs had committed. `onDone` now
  fires whenever at least one row landed.

**Add-line posts every staged row.** The first cut sent only the first row,
silently discarding the rest. Each row is now one `POST /api/invoices/<id>/items`;
rows are cleared as they land, so a mid-sequence refusal leaves exactly the
unposted rows on screen with the reloaded budget marking them.

**Header-only invoices are refused by both shortcuts.** `remaining` cannot see a
legacy invoice that carries money with no line rows, so auto-invoicing the
remainder would bill the same goods twice. The composer warns
(`hasLineLessInvoices`); the two shortcuts now stop with a reason instead.

**Test premises corrected while swapping** (none weakened a guarantee):

> **2026-10-25:** the ceiling was removed, so the over-ceiling/at-ceiling
> premises below are historical. `InvoicePanel` now types quantities far above
> the PI (100/250) and `InvoiceEditor`'s partial-success row uses `100`.

- `SaleDetailModal` F10 pinned one `GET /api/invoices/<id>/items` per invoice.
  The wiring is now a single `/api/sales/<id>/invoice-lines` read, so F10 asserts
  the new source and that its `404` surfaces inline instead of rendering as "no
  lines left".
- `InvoicePanel`'s over-ceiling case typed 250/100/500 to make the *server*
  refuse; the editor refuses first, so those became at-ceiling quantities
  (40/45/150). The 409 case remains the server-refusal test.
- `InvoiceEditor`'s two new partial-success tests had to stay inside the
  ceiling: a fully invoiced row has only its 5% slack left, so the staged
  quantity is `0.2`, not `1` — the hard stop, not the server, would otherwise
  block the sequence before the refusal this test is about.

**`api_list_invoices` deliberately does _not_ get `@limited()`.** It is a read,
it already runs under the 300/min default, and the editor reloads its figures
after a refusal — a 15/min stamp would break the recovery path rather than
protect it.

**Gates.** Backend: no Python file changed between `f6c7fe4` and `a1d272a`
(`git diff --name-only`), so the 1261-passed suite from Phase 5 still stands.
Frontend: `oxlint` 0 errors, 7 warnings; `vite build` ok; full `npm test`
**335/335**. The documented worker-starvation flake appeared once mid-phase
(4 failures in `MultiPICreateModal` and `Reports`, 13/13 when run together).


### Phase 8: Composer column model (2026-10-22)

The composer restated quantity twice — an `Invoiced`/`Remaining` column pair
beside an input that already carried the only quantity the operator chooses.
The row is now `Product | PI qty | Invoice qty | Unit price | Total price`:

- one open quantity input per line, empty on load, no placeholder, no pre-fill
  and no leftover figure beside the input; the live `Remaining` sits under the
  PI qty;
- `Remaining {X} {unit}` sits under the PI qty and updates on every keystroke:
  `max(server remaining − typed, 0)`, where the server's `remaining` already
  nets out every invoice of the sale. A fresh 100 KG PI reads
  `Remaining 100.00 KG`; typing 30 reads `Remaining 70.00 KG`. It is PI-based
  and informational: quantities are free relative to the PI (2026-10-25);
- `Total price` is read-only and computed in integer cents
  (`lineTotal(typed, unit_price)`), `$0.00` before input; the old
  "Line total $" helper is gone;
- `Invoiced` is not rendered in the row — no column and no already-invoiced
  figure (explicit operator request). The create-success toast still says
  `Invoiced $X`; that is the new invoice's amount, not this figure. `Remaining`
  still accounts for prior invoices, so a second invoice draws from the same
  figure and nothing is double-counted. `has_line_less_invoices` remains the
  warning for legacy header-only invoices, whose money the budget cannot see.

**2026-10-25:** the over-limit copy and the payload's `ceiling − invoiced`
figure were removed with the tolerance system. `Remaining` is the only quantity
figure beside the input.

**Validation.** `InvoiceEditor.test.jsx` 15 → 18 (live remaining, computed
total, floored remaining with slack still usable; the over-limit test now also
pins `Remaining 0.00 KG` and `$43.00`); `InvoicePanel.test.jsx` asserts the
subline updates as the operator types. Full suite **338/338** across 40 files;
`oxlint` 0 errors / 7 warnings; `vite build` ok.

### Phase 0 baseline (HEAD `8a35c2f`, clean tree)

| Gate | Result |
|---|---|
| `pytest tests/ -q` | 1246 passed, 8 skipped — 16:48 |
| `npm test` (vitest) | 317 passed / 317 — 1:41 |
| `npx oxlint` | 0 errors, 11 warnings |
| `npx vite build` | success, 2.08 s (1.07 MB chunk warning) |

Timings matter when judging whether a run has hung: the backend suite takes
~17 minutes and the frontend ~100 s. Never pipe the backend suite through
`| Select-Object` in PowerShell — the pipeline buffers the whole output, so a
healthy run looks identical to a hang.

An earlier, abandoned attempt at the unified UI was parked in `stash@{0}`
("wip: unified invoice editor (restore at Phase 6)"). It failed 9 frontend
tests and *removed* 2 net tests; it is reference material for Phase 5/6, not a
starting point.

**`npm test` flake under full-suite load (observed 2026-10-21, pre-existing).**
A full `vitest` run intermittently fails 1–4 tests in the slowest files
(`MultiPICreateModal`, `ProductionRunModal`, `Sales`, `Reports`), and the failing
set changes between runs — 1:41 elapsed on a clean baseline versus 66 s for
`MultiPICreateModal` alone when it fails. Every one of them passes in isolation
and the whole group passes when run together (`24 passed`), so this is worker
starvation, not behaviour. Judge the suite by whether the failures move and
reproduce alone, not by one red line on a loaded machine.

### Deployment reconciliation finding (closed 2026-10-21)

Production (`njanawhckyhyaduxcodr`, the same database as the app DSN) was **one
schema version behind `main`** at the start of the overhaul:

- `app_settings.raas_schema_version` was **`5`**; `raas_tracker/db.py:141` is at **`6`**.
- `public.invoices` had **no `invoice_date` column** — the v6 migration
  (`710e36f`) had never run there.
- `raas_schema_sig` was present (7,642 chars), but `_version_is_current("5")` was
  `False` and `_schema_current()` requires it (`db.py:309`), so `_create_tables()`
  and `_run_migration()` executed on **every** connection. (Until Phase 2,
  `backfill_invoice_lines` ran there too.)

**Observed after deploying `35a9426` (the completed overhaul):**

| Check | Result |
|---|---|
| `app_settings.raas_schema_version` | **`6`** |
| `invoices.invoice_date` exists | **yes** |
| Row counts | `sales` 1, `sale_items` 1, `invoices` 0 (unchanged) |

So the v6 DDL ran on first boot exactly as intended, and `_create_tables()` is
back on its fast path. The check was observed, not assumed.


### Phase 1 — writer lock discipline (no production code change)

> **Superseded 2026-10-25 — the tolerance system is gone.** The locks below
> still exist, but they no longer guard a quantity ceiling; they serialise an
> invoice's header, line, and audit writes. `tests/test_invoice_writer_locks.py`
> keeps the batch pre-lock pin; the joint-outcome test was deleted with the
> ceiling it measured.

**Finding: the race this phase was meant to close is already closed.** All three
invoice writers take the PI-line lock *before* they decide tolerance:

| Writer | Lock | Then tolerance |
|---|---|---|
| `create_invoice` | `validate_invoice_lines` locks each line (`sales.py:1714-1717`), under the sale-row lock (`:1829`) | `:1841` |
| `create_invoices_batch` | every PI line of the sale, up front (`:1933-1935`, in since `7a7ae73`) | `:1985` |
| `create_invoice_item` | its single line (`:2518-2521`, in since `10585061`) | `:2529` |

Under READ COMMITTED the loser blocks on `FOR UPDATE`, and its tolerance
aggregate re-runs against a statement snapshot taken *after* the winner
committed, so 60 + 60 against a 105 ceiling admits exactly one writer.

Deliverable is therefore evidence, not code: `TestWriterLockDiscipline` in
`tests/test_invoice_tolerance.py` pins all three locks plus the joint outcome.
Both tests are mutation-verified — deleting the batch pre-lock and the add-line
lock makes them fail, and the joint test then measured **122.0 units invoiced
against a 105 ceiling**.

**Tracked gap found while verifying (not closed here).** The two PI-rebase
writers check `invoice_items` and then write *without* holding the PI-line lock:
`update_sale_item` (`:707-710` / the count at `:713-718`) and
`update_sale_full` (`:941-958`). A concurrent create can insert a line between
the count and the `UPDATE`, leaving a PI quantity/price below what is already
invoiced. Closing it needs a lock-order decision (`update_*` would take the sale
row; `create_invoice_item` currently starts at the invoice row), so it is
recorded rather than folded in. This is the same defect class as Phase 2-B's
sign rule and matters most before Phase 5 swaps the UI onto these writers.

---

## ⚖️ Phase RECIPE-QUANTITIES: needed / remaining on the card + the 100% production gate (completed 2026-11-02)

The formulation card showed a percentage and the current stock but never how
much chemical the invoice still needed or what would be left, and water was
purely decorative: an unbalanced formula could still be produced. Now the
detail payload projects the quantities, the card shows them, and production
requires a balanced formula.

- Detail and production-source items add `required_qty` (percentage × the
  product's **unproduced remainder**, converted into the chemical's stock unit)
  and `stock_remaining` (current stock minus the whole invoice's need, summed
  once per chemical so shared chemicals show one remainder). Missing
  conversions or unconverted invoice lines yield null, never a guess.
- Products add `water_qty` (effective water × remainder, in product units);
  water is still never deducted.
- The card adds **Chemical needed** and **Stock remaining** columns, the water
  quantity, a `Quantities are for the X still to produce` line, and a
  `Ready` / `Not balanced` / `Empty formulation` badge. Negative remainders
  render in rose; unavailable quantities render `—`.
- Production refuses a product whose `ingredients + water` is not 100% within
  ±0.01 (`_BALANCE_TOLERANCE`, absorbing float4 noise), per product, before any
  write. The production modal disables only the unbalanced row and can still
  submit the rest of the invoice. A deduction shortage now names the chemical:
  `insufficient stock: NaOH needs 20 KG, 10 KG available`.
- Reports stay lax (`require_balanced=False`) and the Reports page only flags
  `formula totals 95.0% — production will be blocked until it's 100%`.
  Readiness keeps its formulation-only SQL; an unbalanced-but-formulated
  product still reads `missing_production`, and the produce 400 is the single
  authoritative explanation.

**Commits:**

```
0346101 feat(reports): flag unbalanced formulas without blocking the report
b795026 feat(recipes): block unbalanced products in the production modal
8a582cf feat(recipes): needed and remaining columns on the formulation card
757f99d feat(recipes): gate production on a balanced formulation and name shortages
079243d feat(recipes): report chemical needed and stock remaining per product
```

**Verification (full gate, 2026-11-02).** Backend: **1470 passed / 8 skipped**
in 1772.09s (`python -m pytest tests/ -q`); `tests/test_recipe_invoice_model.py`
66 passed and the recipe/production-adjacent suites 258 passed before the full
run; `ruff` stays at its 18 pre-existing baseline findings (12
`flask_app.py`, 6 `sales.py`), none in the touched files; `pyright` 0 errors /
15 baseline warnings. Frontend: **426/426 in 44 files**; Recipes 35,
ProductionRunModal 14, Reports 16; `oxlint` 0 warnings / 0 errors on 119 files;
`vite build` green and `dist/` copied to the gitignored `react_frontend/`.

---

## ⚡ Phase PERF-1: round trips, anonymous gate, asset caching (completed 2026-11-03)

**Measured problem.** The Vercel function ran in `iad1` while Supabase lives in
`ap-northeast-1`, so every database round trip cost ~130 ms. Each request made
~12–13 of them: `get_connection()` re-ran a schema probe and two seed `COUNT`s
on *every checkout*, and `_resolve_identity()` opened the database even for a
caller presenting no credential. `/api/auth/me` measured ~1.6 s warm. Static
assets were served with `Cache-Control: no-cache`, so repeat visits
re-downloaded the 1.06 MB bundle.

**Changes**

- `raas_tracker/db.py` — schema/seed probes moved into the psycopg pool's
  `configure` callback: once per physical connection, not once per checkout.
  `RAAS_SCHEMA_CHECK=always` keeps the per-checkout path and is set for the
  whole test suite (tests truncate/drop tables while reusing pooled
  connections). Pool `max_size` 10 → 4: the Supabase session pooler admits 15
  clients per project, one pool runs per instance, and waitress uses 4 threads.
  `RAAS_POOL_MAX_SIZE` overrides the cap; the suite raises it to 10 because the
  concurrency tests hold 8 connections live at once.
- `flask_app.py` — `_resolve_identity()` returns `None` before `get_db()` when
  neither the session cookie nor `X-API-Key` is present. Any *presented* value,
  including an invalid one, still goes through the database-backed checks.
- `flask_app.py` / `vercel.json` — `/assets/*` is `public, max-age=31536000,
  immutable` (Vite content hashes every file); `index.html` and the SPA
  fallbacks stay `no-cache`. Vercel gets the same asset policy in its `headers`
  block because it serves `public/` from its CDN without invoking Flask.
- `vercel.json` — `"regions": ["hnd1"]` (Tokyo, beside Supabase).

**Tests**

- `tests/test_perf_phase1.py` — a warm pool issues zero probes per checkout;
  forced mode still probes every checkout; the production pool cap defaults to
  4; a failed `configure` closes the abandoned connection instead of leaking
  it; anonymous `/api/*` answers 401 without opening a connection; a presented
  cookie/key still hits the database; region and edge cache rules.
- `tests/test_spa_fallback.py` — hashed asset is immutable, document is not.
- `tests/test_edge_hardening.py` — the header matcher accepts the
  `/assets/(.*)` source shape used by the cache rule.

**Deliberately out of scope.** Supabase's transaction pooler (port 6543) was
not switched on: the probe move makes session-pooler capacity comfortable, and
transaction mode needs the migration/seed path re-verified first. Bundle
splitting is Phase 2 (the build still emits one ~1.07 MB JS chunk).

**Verification (full gate, 2026-11-03).** Backend: **1484 passed / 8 skipped**
in 1440.05s (`python -m pytest tests/ -q`); `ruff` remains at its 18
pre-existing baseline findings (12 `flask_app.py`, 6 `sales.py`), none in the
touched modules or tests; `pyright` 0 errors / 15 baseline warnings. Frontend:
**426/426 in 44 files**; `oxlint` 0 warnings / 0 errors on 119 files; `vite
build` green (the pre-existing >500 kB chunk warning remains — splitting is
Phase 2).

---

## ⚡ Phase PERF-2: one dashboard request, split routes, cache-aware client (completed 2026-11-03)

**Measured problem.** Home issued seven API calls (chemicals, uploads, recipes,
sales page, sales summary, LCs, audit preview), each its own pool checkout. The
SPA shipped one ~1.07 MB JS chunk, so every first visit parsed Recharts before
painting the dashboard shell. Auth polled `/api/auth/me` every minute, and no
repeat dashboard read was cached anywhere.

**Changes**

- `flask_app.py` — `GET /api/dashboard` reads all seven datasets on **one**
  pooled connection and returns the exact shapes of the standalone routes:
  `{chemicals, uploads, recipes, sales:{sales,total}, sales_summary, lcs,
  activity}`. `activity` carries the five newest audit rows for a human
  `admin` and is `null` for everyone else, mirroring `/api/audit-logs`'
  admin-only rule without failing the rest of the dashboard. The response gets
  a strong ETag over its body, `Cache-Control: private, max-age=15` and
  `Vary: Cookie, X-API-Key`; a matching `If-None-Match` answers **304**.
- `raas-tracker-frontend/src/hooks/useCachedFetch.js` — keyed module cache with
  `staleTime`. Cached data renders immediately; stale entries refresh in the
  background without a skeleton; a failed refresh keeps the cached data. The
  `loading` flag only covers a cold cache.
- `raas-tracker-frontend/src/pages/Home.jsx` — one `/api/dashboard` call
  replaces seven, with chemicals mapped exactly as before. The two Recharts
  cards are `React.lazy` + `Suspense`, so the chart library leaves the first
  payload.
- `raas-tracker-frontend/src/App.jsx` — every page is `React.lazy` behind one
  `Suspense` boundary; errors still land in the existing `ErrorBoundary`.
- `raas-tracker-frontend/vite.config.js` — Rolldown `output.codeSplitting`
  groups for React/router and the full Recharts dependency set, so the chart
  chunk is shared by Home and Reports and fetched only with the lazy charts.
- `raas-tracker-frontend/src/context/AuthContext.jsx` — idle identity poll
  60 s → 5 min. A revoked session is still detected immediately by the next
  API 401, so the poll is only the idle safety net.

**Tests**

- `tests/test_dashboard.py` — auth required; every section equals its standalone
  endpoint with seeded data; non-admins get `activity: null` while
  `/api/audit-logs` still 403s; exactly one `get_db()` per dashboard request;
  `private` cache header; ETag 304 on a repeat and a **new** ETag after a write.
- `raas-tracker-frontend/src/pages/Home.test.jsx` — a single `/api/dashboard`
  request renders chemicals, sales and charts; admin activity shows three rows
  while non-admin payloads hide the panel; a remount inside `staleTime` makes no
  second request; a cold failure surfaces and Retry refetches.
- `raas-tracker-frontend/src/App.test.jsx` — `/login`, `/setup` and `/reset`
  render through the lazy Suspense boundary.

**Build budget (`vite build`).** Entry `index` 68.34 kB + `react` 259.17 kB +
`Home` 29.79 kB = **357.30 kB raw / 109.35 kB gzip**; the `charts` chunk
(388.19 kB) is not part of the entry payload and downloads with the chart
components. CSS is 74.16 kB (11.95 kB gzip). Before this phase the entry was a
single ~1.07 MB JS chunk.

**Verification (full gate, 2026-11-03).** Backend: **1489 passed / 8 skipped**
in 1378.26s (`python -m pytest tests/ -q`); `ruff` remains at its 18
pre-existing baseline findings, none in the touched files; `pyright` 0 errors /
15 baseline warnings. Frontend: **434/434 in 46 files**; `oxlint` 0 warnings /
0 errors on 122 files; `vite build` green and `dist/` refreshed into the
gitignored `react_frontend/`.

---

## ⚡ Phase PERF-3: hot-path FK indexes + reference-data ETags (completed 2026-11-03)

**Measured problem.** Ten per-key queries had no index and scanned their table:
the API-key rate-limit COUNT (once per API-key-authenticated request), the
recipes list's `EXISTS` on `production_runs` (once per row), the booking
readiness gate on `production_run_links.invoice_id`, and the sale-board /
upload-detail / run-detail lookups (`sales_stage_history`, `sale_payments`,
`shipments`, `upload_rows`, `production_run_items`, `approval_workflow` — all
by their foreign key). None of it is slow at today's data size; this is
hygiene for scale plus a live-database health check to confirm nothing else
is hiding. On the client side, the five reference lists re-sent their full
body on every repeat view.

**Changes**

- `raas_tracker/db.py` — ten `CREATE INDEX IF NOT EXISTS` statements close the
  idempotent-migration body of `_create_tables`, and `_SCHEMA_VERSION` 9 → 10
  so warm databases pick the DDL up. `idx_api_key_rate_limits_key_hit` is
  composite `(key_id, hit_at)` with `key_id` leading, because the hot COUNT
  filters by key first and a window second. Deliberately *not* added:
  `production_runs.sale_item_id` (always NULL today — see the known-limitation
  on the production-readiness join), `recipe_items.chemical_id` (second column
  of the existing unique index), `sessions.user_id` / `api_keys.created_by`
  (rare, tiny), and `pg_trgm` (row counts are tiny; revisit when search slows).
- `flask_app.py` — the dashboard's ETag block became `_revalidating_json()` and
  now also covers `GET /api/chemicals`, `/api/companies`, `/api/recipes`,
  `/api/unit-conversions`, `/api/reason-codes`. The reference lists get
  `private, no-cache` (store, revalidate every use) rather than the
  dashboard's `max-age=15`: each of their pages writes through the same URL
  and immediately rereads it, and `apiFetch` uses the browser's default cache
  mode — a freshness window would let that reread serve the pre-write body.

**Live-database health check (read-only, 2026-11-03).** `pg_stat_statements`
is already enabled. Top findings against production: no slow application query
exists (the session lookup, the hottest app query, averages 0.28 ms); the
dominant costs were the per-checkout schema/seed probes (16 145 calls, ~78 s
total) and the deploy-time `pg_advisory_xact_lock` migration queue (18 calls,
~4.9 s mean wait), both eliminated by PERF-1 in code but **still live because
nothing from PERF-1/2/3 is deployed yet** — the numbers double as the
before-picture for the eventual deploy verification. Catalog lookups
(`pg_timezone_names` 144 ms mean, type introspection ~40 ms) are the iad1→Tokyo
latency the `hnd1` region pin shrinks. The Supabase MCP `get_advisors` endpoint
is currently broken (every call errors -32603); the check ran over a direct
read-only connection instead.

**Tests**

- `tests/test_perf_phase3.py` — all ten indexes exist with the declared
  columns; the rate-limit composite leads with `key_id`; `_SCHEMA_VERSION` is
  10; each reference endpoint sends `private, no-cache` + `ETag` + `Vary` and
  answers `If-None-Match` with 304; a write changes the chemicals ETag; auth is
  still required on all five.
- `tests/test_recipe_schema_v8.py` — the version pin moved to 10, and the
  idempotence test now compares against `str(_SCHEMA_VERSION)` instead of a
  hardcoded literal so the next bump cannot rot it.

**Verification (full gate, 2026-11-03).** Backend: **1499 passed / 8 skipped**
in 2328.20s (`python -m pytest tests/ -q`) — Phase 2's 1489 plus the ten new
PERF-3 tests; `ruff` remains at its 18 pre-existing baseline findings, none in
the touched modules or tests; `pyright` 0 errors / 15 baseline warnings.
Frontend — no file touched this phase, re-run anyway: **434/434 in 46 files**,
`oxlint` 0 warnings / 0 errors on 122 files, `vite build` green with chunk
hashes identical to the browser-verified PERF-2 bundle.

---

## ⚡ Phase PERF-4: connection pool, statement timeouts, request deadline (completed 2026-11-03)

**Measured problem.** Verifying the authenticated routes against the live database
moved the diagnosis away from query cost: `list_recipes`, the dashboard aggregates
and the session lookup run **0.04–0.2 ms server-side**, with a ~122 ms
round-trip floor from the function region to the database. The three real risks
were capacity and unbounded waiting:

- The app sat on the Supabase **session** pooler, which pins one server backend per
  client. This project's `pool_size` is 15, so ~15 warm serverless instances
  exhaust it — the 2026-11-02 outage (`EMAXCONNSESSION … pool_size: 15`), where the
  fourth concurrent request could not get a connection.
- psycopg-pool's defaults worked against us: `min_size=1` held a connection per
  warm instance, `timeout=30s` made a caller wait half a minute, `max_idle=600s` /
  `max_lifetime=3600s` kept connections past the platform's instance recycling.
- The database's own `statement_timeout` is **2 min** — it outlives the function's
  60 s budget and the browser's patience, so a stalled query holds a pooled
  connection for minutes. Two 2-minute stalls were observed on the live database
  during this phase's measurements (transient contention, not attributable to a
  route); the same statements ran in 127–145 ms on retry.
- A pool failure surfaced as a bare 500, and on the client a hung request left
  `loading` true so `ProtectedRoute` rendered its spinner forever.

**Decisions taken with the user.** Target the **transaction pooler (`:6543`)**, with
`DATABASE_URL` swapped by the user in Vercel env and the local Windows user env (I do
not touch `.env`). Explicit approval to edit the off-limits files for this task:
`raas_tracker/db.py`, the `flask_app.py` error handlers, and `AuthContext.jsx`.

**Changes**

- `raas_tracker/db.py` — pool shape: `min_size=0`, `max_size` from `_max_pool_size()`
  (`RAAS_POOL_MAX_SIZE` → `DB_POOL_MAX` → 4, clamped 1..15), acquire `timeout` 10 s,
  `max_idle` 60 s, `max_lifetime` 600 s, and `_conn_kwargs()` on every physical
  connection (`prepare_threshold=None`, `connect_timeout=10`, keepalives 1/30/10/3 —
  Supavisor drops silent clients and a half-open connection otherwise surfaces on its
  *next* checkout). New `_timeout_statements()` applies
  `SET LOCAL statement_timeout / lock_timeout / idle_in_transaction_session_timeout`
  (25 s / 5 s / 30 s, env-overridable) as **one parameterless multi-statement execute**
  — PostgreSQL has no multi-assignment `SET`, and a bind parameter would force the
  extended protocol, which refuses multiple commands. `SET LOCAL`, never a session
  `SET`: a transaction-mode pooler gives the backend to another tenant at COMMIT.
  Connect-time `options` is not the vehicle — **verified on live** that both poolers
  ignore `-c statement_timeout=…` and the role default wins. The bundle is applied
  *last* in `get_connection()`, after the `RAAS_SCHEMA_CHECK=always` probe, because
  that probe commits in tests and would drop a `SET LOCAL` issued before it.
  `_create_tables()` widens its own transaction to 120 s after taking the advisory
  lock (the v10 DDL queue measured 4.9 s mean behind it; the request cap must not cut
  a deployment migration in half).
- `flask_app.py` — module-level `import psycopg` / `from psycopg_pool import
  PoolTimeout`, and `_database_unavailable()` registered on both exception classes:
  **503** `{"error": "database unavailable, retry shortly"}` + `Retry-After: 5`, cause
  logged and never sent. `errors.TooManyConnections` — the actual outage error —
  subclasses `OperationalError`, so it is covered. Flask dispatches by exception class
  before the status-code handlers, so an ordinary bug is still a 500.
- `raas-tracker-frontend/src/utils/api.js` — `DEFAULT_TIMEOUT_MS = 30000` and
  `withTimeout(options, {timeoutMs})` returning `{options, didTimeOut(), done()}`. It
  chains a caller-supplied `signal` (an already-aborted caller short-circuits with its
  own reason; the deadline timer is skipped) so a debounced search or unmount abort
  keeps `AbortError` semantics while a timeout reports distinctly.
- `AuthContext.jsx` — `refresh()`, `login()` and `apiFetch()` all run through
  `withTimeout` with `done()` in `finally`. A timeout maps to
  `ApiError(0, "Request timed out — the server did not answer")` **before** the
  existing `AbortError` passthrough; `login()` throws a retryable message instead of
  hanging the button.
- `components/auth/ProtectedRoute.jsx` — its `/api/auth/status` probe had the same
  undeadlined-fetch bug as `/api/auth/me`: the forever-spinner test found it.
- `README.md`, `.env.example`, `docs/SPEC.md` — pool/timeout tunables, the
  `SET LOCAL` rationale, the transaction-pooler requirement, and the §6 error row
  changed from `OperationalError → 500` to 503 + `Retry-After`. `SPEC.md` §3 also
  gained the "Connection Pool & Statement Timeouts" section and its stale
  `_SCHEMA_VERSION` note corrected to 10; `.env.example`'s header DSN example moved
  from `:5432` to `:6543` (it contradicted the pool notes below it).

**Tests**

- `tests/test_db_pool_timeouts.py` (22) — pool shape read off the live pool object
  (`min_size == 0`, `timeout == 10`, `max_idle <= 60`, `max_lifetime <= 600`,
  `connect_timeout <= 10`, `prepare_threshold is None`); the cap resolved from each
  env spelling, including junk/`0`/`500` → 4; the guard bundle's SQL shape (three
  `SET LOCAL`, no `%s`); env overrides reach the checkout; `RAAS_STATEMENT_TIMEOUT_MS=1000`
  + `SELECT pg_sleep(2)` really raises `QueryCanceled`; `SET LOCAL` does not leak onto
  a bare connection; an injection value falls back to the default; the migration
  widening asserted **behaviourally** through a recording connection proxy
  (`widened[0] >= 60000`), not by regex over source; 503 for `PoolTimeout`,
  `OperationalError` and `TooManyConnections`; the 503 body leaks no DSN or driver
  detail; an ordinary bug is still a 500; four threads grow the lazy pool as expected.
- `src/utils/api.test.js` (+8) — option passthrough, abort at the deadline, a caller
  abort is not reported as a timeout, an already-aborted caller, `done()` cancels the
  deadline and removes its listener, `timeoutMs: 0` disables, and the default sits
  between 25 s and 60 s.
- `src/App.test.jsx` (+1, the outage repro) — `/api/auth/me` never answers, the
  spinner shows, and after `DEFAULT_TIMEOUT_MS` it is gone and the login page renders.

**Deliberate limitation.** A route that wraps its own body in
`except Exception: return jsonify(...), 500` (e.g. `api_delete_lc_record`) still 500s
on an error raised *inside* the transaction, because the handler swallows it before
Flask's exception-class dispatch sees it. The gate's checkout path — the dominant one,
and the one that failed during the outage — now returns 503. Converting those bodies to
`with`-block/`__exit__` rollback is a separate, per-route change.

**Second, smaller limit.** The 30 s deadline covers `apiFetch`, `login`, `refresh` and
`ProtectedRoute`'s status probe — the paths that render the app. Six raw `fetch` calls
on the standalone public pages still have none (`Login` forgot-password, `Setup` status
+ submit, `ChangePassword`, `ResetPassword`, and `logout` in `AuthContext`);
`Setup.jsx:22` is the same forever-spinner shape as the one this phase fixed. Extending
`withTimeout` there is mechanical and was outside the approved file set.

**Deploy step (user-owned, not done here).** `DATABASE_URL` must move from port
`:5432` to `:6543` in Vercel project env and in the local Windows user variable, then a
redeploy releases the old session-mode backends. Verify after the swap:
`SELECT current_setting('statement_timeout')` still reads `2min` on a raw connection
(the pooler ignores conninfo options) while a request query is cancelled at 25 s, and
the 503 handler is what answers when the pool is saturated.

**Verification (full gate, 2026-11-03).** Backend: `python -m pytest tests/ -q`
reported **1520 passed / 1 failed / 8 skipped** in 1473.02 s. The one failure was
`tests/test_edge_hardening.py::test_415_is_not_registered_by_class` — the tripwire
that asserts nothing new was ever registered by exception class. The two 503
handlers are exactly such a deliberate addition, so the tripwire did its job; it now
asserts the by-class map **exactly** (`Exception` → `_handle_exception`, `PoolTimeout`
and `psycopg.OperationalError` → `_database_unavailable`) and still pins that
`HTTPException` is not captured, plus it reads the whole map instead of its first
entry, which is what made it order-dependent. Re-run with its neighbours: **149
passed**. Suite total after the fix: **1521 passed / 8 skipped** — no source file
changed between the full run and the re-run. `ruff` stays at its 18 pre-existing
baseline findings (12 `flask_app.py`, 6 `sales.py`), none in the touched files or
tests; `pyright` 0 errors / **17** warnings — the documented 15 plus two
`conn.execute` "no overloads match the provided arguments" entries on a non-literal
str argument, the same class already reported at `db.py:653`/`db.py:1027`; not
suppressed. Frontend: **443/443 in 46 files** (`api.test.js` +8, `App.test.jsx` +1),
`oxlint` 0 warnings / 0 errors on 122 files, `vite build` green and — first time
since the route split — no chunk over 500 kB (`charts` 387.85 kB is the largest);
`dist/` copied to `react_frontend/`.

---

## 📦 Phase PACKAGING: a counted container register inside Chemical Stock (completed 2026-11-04)

The user counts drums, bags and jars as physical objects — "drums are not chemical,
how much drum we have, which should be counted" — and nothing in the model could hold
that. `DRUM` existed only as a unit-conversion factor (1 DRUM = 200 L/KG) and as a
hand-typed string on production runs (`packing: "30 Kg Drum"`). No row anywhere said
how many containers were on site.

**Why a new table and not rows in `chemicals`:** four readers treat every `chemicals`
row as a formula ingredient or a reconciliation line (`uploads.py:75`,
`recipes.py:499`, `recipes.py:1109`, `flask_app.py:2499`). A `kind` column would have
to be filtered at all four; miss one and a 25 kg bag becomes a recipe ingredient. The
slice therefore adds `packaging_items` + `packaging_movements` (schema **v11**) and
changes **no** chemical code path. `tests/test_packaging.py::test_packaging_data_never_reaches_the_chemical_surface`
asserts `get_all_chemicals()` is byte-identical with packaging data present.

**Three chemical flaws deliberately not copied:**

| Chemical stock does | Packaging does | Test |
|---|---|---|
| stores a mutable `current_qty REAL` that can drift from its own history | no quantity column at all — pieces are `SUM(delta)`, integers, `capacity_kg NUMERIC(12,3)` | `test_no_mutable_quantity_column_exists`, `test_pieces_are_integers_not_real` |
| the adjust modal subtracts in the browser, so two people counting the same stack lose one edit (`Chemicals.jsx:77`) | a physical count arrives **absolute** and the service resolves the delta inside its own transaction under `SELECT … FOR UPDATE` | `test_physical_count_is_resolved_server_side` |
| chemical history is regex-parsed out of `audit_logs.new_value` (`stock.py:404-474`) | history reads real indexed columns (`before_qty`, `after_qty`, `kind`, `actor`) with sargable date bounds | `test_history_is_a_direct_read` |

**API** (`raas_tracker/packaging.py`, routes in the `API: PACKAGING` block): `GET
/api/packaging` (one `GROUP BY`, `_revalidating_json` ETag), `POST /api/packaging/items`
(admin), `POST /api/packaging/movements` (operator — same trust class as the monthly
upload), `PUT /api/packaging/reorder` (admin), `GET /api/packaging/history`. All three
writes ship with `@limited()` from the first commit, unlike the chemical write routes
still on the 300/min read default, and `tests/test_authorization_roles.py` — which
asserts **set equality**, not membership — was extended rather than bypassed.

**Alerts:** `notify_packaging_short()` reuses `notify()` with `entity_type="packaging"`
and mutually exclusive dedupe keys (`packaging:0:{id}` critical, `packaging:low:{id}`
warning), so the bell and the table badge cannot disagree; recovery clears the key. No
alert at creation — a brand-new type legitimately holds zero.

**UI:** `pages/Chemicals.jsx` gains a tab bar (`Chemicals` | `Packaging`, the
`Reports.jsx` `aria-pressed` idiom) and the chemical branch is untouched.
`components/packaging/PackagingPanel.jsx` + `PackagingMovementModal.jsx` and
`utils/packaging.js` — one status rule (0 = out, ≤ alarm = low), never the three
conflicting rules in `utils/stock.js`, and packable KG accumulated in integer
thousandths so a `3 × 0.1` total is exactly `0.3`.

**Out of scope, deliberately:** no auto-deduction from the production `packing` text,
sales or the monthly upload; no lot/expiry, no multi-warehouse; no repair of
`current_qty REAL`, the client-derived delta, or the chemical history parser.

**Verification (full gate, 2026-11-04).** Backend: `python -m pytest tests/ -q`
first reported **1558 passed / 2 failed / 8 skipped** in 3142.87 s. Both failures were
the same pre-existing tripwire, `test_schema_version_is_ten`, hard-pinned to
`_SCHEMA_VERSION == 10` in `tests/test_perf_phase3.py` and `tests/test_recipe_schema_v8.py`
— a bump of the version is exactly what that assertion forbids, so it fires on every
correct migration and had already been renamed twice. Both are now
`test_schema_version_at_least_ten` and assert `>= 10`: still a guard against lowering the
version, no longer a false alarm when new DDL raises it. The reachability of the packaging
DDL is not left unguarded — `test_packaging.py::test_migration_reapplies_packaging_tables`
stamps `_SCHEMA_VERSION - 1` and proves the tables and seeds come back through the fast
path, without pinning a number. Re-ran the two renamed files with `test_packaging.py`:
**51 passed**, so the suite stands at **1560 passed / 8 skipped**. `ruff check .`: all
checks passed. `pyright`: 0 errors / **17** warnings (the documented baseline, untouched).
Frontend: **468 tests in 49 files** all passed, `oxlint` 0 warnings / 0 errors on 128
files, `vite build` green (`charts` 387.85 kB still the largest chunk), `dist/` copied to
`react_frontend/`.

**Browser pass (2026-11-04, against an embedded Postgres — deliberately not the live
Supabase data).** Schema v11 reaches production when the deployed app first opens a
connection after this ships; a dev laptop counting drums must not migrate the customer
database ahead of a deploy. All seven planned checks held: the tab listed the 5 seeded
types at 0 pieces; `RECEIVED +40` on `200 kg drum` put 40 in the row, 40 in total pieces
and 8,000 kg in packable KG, with history `before 0 → after 40`; a physical count of 37
wrote a `COUNT` row of `delta -3` instead of overwriting anything; counting to 0 raised
the bell and a later `RECEIVED +10` cleared it (`packaging` notifications read back empty);
`-500` on a type holding 40 was refused and the count was unchanged after a refresh; an
operator session could Record but saw no Add Type / Alarm controls; and the Chemicals tab
still listed its own three rows (`120.5 KG`, `40 L`, `0 KG`) with KPIs `3 / 2 / 1 / 160.50`
while packaging data sat in the ledger — no packaging type appeared in the chemical list,
which is the live counterpart of the service-level separation test. Ledger after the pass: `RECEIVED 40 (0→40)`, `COUNT -3
(40→37)`, `COUNT -37 (37→0)`, `RECEIVED 10 (0→10)`, `RECEIVED 5 (0→5)` on `30 kg jar`, with
`SUM(delta)` equal to the last `after_qty` per type; audit held 4 × `PACKAGING_MOVEMENT`,
`SET_PACKAGING_REORDER_LEVEL 0→5` and `ADD_PACKAGING_ITEM '1000 kg IBC tote'`.

Two defects only a browser could show were found and fixed in this pass: the movement
modal printed the piece count with two decimals (`40.00 PCS` next to a table showing `40`),
and its live preview rendered `-463 PCS` for an over-issue the server would refuse — pieces
now format as integers and the preview reads `—` whenever the result would be negative.
The client-refused case was also confirmed in the network log to send no `POST` at all.

---

## 🚫 Phase WON'T-SHIP: a PI line can stop counting in the commercial figures (completed 2026-11-05)

The user looked at PI `12322332` in the Commercial Report and saw two rows — an
invoiced `400.00 KG / $576.00` and a **Provisional** `50.00 KG / $72.00` — with KPIs
`Gross $648.00 / Received $576.00 / Due $72.00`. "আসলে যে পরিমান প্রভিশনাল হিসেবে থাকবে,
সেটা শিপমেন্ট নাও হতে পারে… provisional ক্যাল্কুলেশন টা চাইলে কেটে দেওয়া যাবে" — that
unshipped balance may never ship, and while it sits in the report it holds a receivable
open forever. The ask was a switch that cuts it out so it stops counting in the figures.

**Why the mark lives on the PI line (`sale_items.wont_ship`, schema **v12**), not on the
report or in the viewer's session:** the decision that a balance will not ship belongs to
the document. A report-only toggle would be someone's private view, would reset on refresh,
and would leave the KPI cards, the table and Export CSV free to disagree. Storing it on the
line means one predicate moves all four surfaces at once, because
`_commercial_report_base_sql()` is the single place provisional rows are born and the flat
report, the KPI summary, the month drill-down and the CSV all embed it.

**Gone entirely, not zeroed or dimmed** (the user's choice): the row is not produced, so
there is nothing left to reconcile.

| Decision | Chosen | Why not the alternative |
|---|---|---|
| Where the state lives | `sale_items.wont_ship` (`INTEGER NOT NULL DEFAULT 0`, the same int-as-boolean spelling as `users.must_change_password`) | a `group_by`/`show` filter in the viewer's URL — per-user, lost on refresh, and the export would still carry it |
| What the report does with it | suppression **inside** the `remainders` CTE | an outer `WHERE` reads the same, but `row_alloc` re-derives `is_last_row`/`prev_sale_total` over the surviving rows: a legacy sale-level payment anchored to the cut row would be **dropped with it** instead of moving to the last real row. Pinned by `test_wont_ship_does_not_lose_a_payment_anchored_to_the_remainder` |
| Who may set it | `@admin_required` + `@limited()` (15/min), matching the sibling line-edit route's authority and the volume policy a *new* write must declare | operator-writable — the mark deletes money from a KPI the operator is measured on |
| Where it is set | the report row **and** the PI editor; because a marked row leaves the report, the editor is the only un-mark path | a report-only toggle would trap the decision: nothing left on screen to undo it |

**API:** `PUT /api/sales/<sale_id>/items/<item_id>/wont-ship` with `{wont_ship: bool}` —
required, no default, so an empty body is a 400 field error rather than an implicit clear.
The service returns three states and the route maps all three: `None` → 404 `Item not found`
(the `AND sale_id` binding Phase 5 established for this table), `False` → 200 with **no audit
row** (a repeat mark from a stale tab must not claim a change that did not happen), `True` →
200 + `SALE_ITEM_WONT_SHIP` old→new, committed together with the ledger row like
`update_sale_item`. `get_sale_by_id` now serialises `wont_ship` as a bool so the editor can
render the stored state, and the report rows gained `sale_item_id` — the SQL already selected
it, but the UI had never been handed the only id that can mark a provisional row.

**Deliberately unchanged:** `update_sale_full` neither sets nor clears the mark (it updates
`product_name/quantity/unit_price/unit` only, so a PI save cannot clobber a decision made
from the report, and a newly inserted line starts unmarked); the mark does **not** block
invoicing — a marked line can still be billed, and its invoiced row then shows normally and
brings its money back into the figures through the `LEAST` branch, with no un-marking
required. What a PI *says* it is worth stays the document's business: `get_sale_pi_total`
(invoice headroom, variance, the legacy maturity dedupe) and the editor's product totals keep
reading the full ordered quantity.

## 🚫 WON'T-SHIP, second half: the retired balance leaves every figure (same day)

The report-only cut left the rest of the app counting the money the admin had retired: the
pipeline board read `Value $648 / Balance $72` for the same PI whose report rows said $576,
Home's stage tiles and total pipeline carried that $72, the pipeline CSV exported it, and two
questions kept being asked about it — *"Create more invoices?"* after every shipment, and the
daily maturity bell, which no payment can ever answer because the money it tracks no longer
counts. The user's rule is why the exclusion reaches everywhere: *"PI কত টাকার তা হিসাবের
মুল না… যেটা un invoiced থাকবে, সেটা এখন শো করতেসে, সেরকমই শো করবে… যখন আমি শিপ করব না
un-invoiced পার্ট টুকু, তখন আমি সেটা সকল হিসাবে থেকে ডিলেট করে দিব… তা ডিলেট করলে তা সকল
হিসাব থেকে মুছে যাবে"*.

| Question asked | Answer | What it meant for the code |
|---|---|---|
| How far does the exclusion reach? | "Board, report, nowhere… everywhere" | One shared expression, interpolated by every query that computes a PI's value — the board, the CSV, the stage tiles and `/api/dashboard` all net the same amount |
| Should an un-invoiced balance that is **not** retired also go? | No — "সেটা এখন শো করতেসে, সেরকমই শো করবে", because the rest may still be invoiced or shipped | Only a marked line is netted out; an ordinary remainder keeps its Provisional row and its money |
| One click that moves the figures with no confirmation? | "it is not removing from PI, just removing from calculation" | **No confirm dialog anywhere** — not on the report's row action, not on the editor's checkbox |
| Show the retired balance anywhere (badge, editor note, "show excluded" filter)? | "no need to show it anywhere" | **No new UI**: no board badge, no report filter, no editor line — only the figures change |
| Durable mark, or really edit the PI line? | Keep the mark | The document still says 450 KG / $648 while the accounts say $576 — the accepted consequence, stated in `docs/PRD.md` |

**One definition of what a PI is worth** (`sales._SALE_VALUE_SUM` with its
`_INVOICED_QTY_JOIN`): the full ordered value, except on a marked line, where the invoiced
quantity counts — `LEAST(invoiced_qty, pi_qty)`, so a line billed above its PI can never turn
the mark into a raise, and void invoices never count, which is the same reading the report's
`invoiced` CTE and `_attach_invoice_summaries` already use. `get_all_sales` and
`get_sales_summary` each inlined `SUM(quantity * unit_price)` before and now interpolate it;
`balance` is `total_value − total_paid`, so the board column and the export follow with no
code of their own. `has_uninvoiced_remainder` gained the same predicate in the outer `WHERE`
of a query that already joins billed quantity per line, which drops the marked line from both
of its sums at once. The maturity scan is the one place the shared expression is deliberately
**not** reused: it reads only sales with no invoice at all, so its billed branch would always
be 0 and `CASE WHEN wont_ship = 1 THEN 0 …` is the honest, simpler form — commented in place.

**Accepted corner, pinned rather than clamped:** a legacy sale-level payment with no invoice,
on a sale whose only line is retired, now reads a **negative Balance** ($0 accounted value
against $576 held). `GREATEST(..., 0)` would hide it and would hide a genuine overpayment in
the same column too, so the number stays as computed and
`test_a_retired_line_under_a_legacy_payment_reads_negative` names it. A sale whose only line
is retired and never invoiced nets to $0.00 and keeps its `item_count`. No new index, no
migration, no `_SCHEMA_VERSION` bump — this half adds no DDL.

**Documented, not guarded:** a sale whose *only* rows are suppressed remainders leaves the
report entirely, taking its legacy sale-level payment out of `received`. That is the same
outcome as deleting an uninvoiced line today through `update_sale_full`'s removal path, and
`test_a_sale_whose_only_rows_are_suppressed_leaves_the_report` pins it as behaviour rather
than letting it read as a bug, and the decision stays reversible anyway: `SaleDetailModal`
is mounted on the Sales Pipeline board as well as the report, so a sale with no report rows
at all still has its checkbox. No new index: the predicate runs over rows the query already
visits, and `sale_items` is small.

**Frontend:** `CommercialTable`'s existing Actions cell gains a second, conditional button
(`Ban`, `Mark <PI> as won't ship`) rendered only when `row.is_provisional &&
row.sale_item_id != null` — a new column would have broken the 21-cell skeleton contract;
`pages/Reports.jsx` PUTs the mark, toasts, then calls the already-existing
`refreshCommercial()`, which reloads rows **and** the `group_by=none` KPI summary together.
`SaleDetailModal`'s Products table gains a **Won't ship** checkbox column, admin-only, in
both the read-only and the editing rows, and each toggle writes **immediately** instead of
riding on PI Save — a failed save must not silently lose the decision, and the save cannot
carry it. An unsaved new row shows the box disabled so the control does not vanish mid-edit.

**Verification (full gate, 2026-11-05).** Backend: `python -m pytest tests/ -q` →
**1587 passed / 8 skipped** in 1591.81 s, exit 0 — the 7 over the 1580 the first half ended
on are exactly the pipeline-figure tests. Before that, the focused set ran red first and then
green: `test_sale_wont_ship`, `test_commercial_report`, `test_commercial_report_kpis`,
`test_authorization_roles`, `test_dashboard`, `test_lcs`, `test_maturity_reminders`,
`test_invoice_reminders`, `test_sales`, `test_sales_capture` and `test_rate_limits` →
**321 passed** in 305.81 s. (The plan named `tests/test_sales_export.py`; no such file exists —
`/api/sales/export` is exercised by `test_commercial_report.py:1345`, `test_rate_limits.py:26`
and the new CSV test, which reads the exported columns against the board rather than living in
a module of its own.) `ruff check .`: all checks passed. `pyright`: 0 errors / **17** warnings —
the documented baseline, with none on a line this layer touched. Frontend: **476 tests in 49
files** all passed (131.64 s), `oxlint` 0 warnings / 0 errors on 128 files, `vite build` green
and `dist/` copied to `react_frontend/` (`assets/index-C9JJMaS5.js`, built after the last
source edit so the bundle the browser pass drove is the committed code).

**Browser pass (2026-11-05, against an embedded Postgres holding a seeded copy of the user's
own PI `12322332` — 450 KG @ 1.44, 400 invoiced, $576 paid — with `PI-SECOND` ($100) as the
untouched control).** A dev laptop must not migrate the customer database ahead of a deploy,
so the whole pass ran on the throwaway schema. Marking the remainder from the report row's
**Actions** moved every figure in one click, with no dialog: Home's "PI Issued" tile read
**2 sales / $748 → $676** and *Total pipeline value* **$748 → $676** while the tile still
counted 2 sales; the Sales board card dropped **$648.00 → $576.00** and its Balance **$72.00 →
$0.00**, with the column footer *"PI total (visible)"* and the board header *PIPELINE VALUE*
both **$748 → $676**; the Commercial Report went from 3 rows to 2, the `50.00 KG / $72.00`
Provisional row stopped existing, **Gross $748 → $676** and **Due $172 → $100** with **Received
$576** unchanged, and the month drill-down agreed with the KPI cards. The downloaded pipeline
CSV read `648.0,576.0,72.0` before and `576.0,576.0,0.0` after with `PI-SECOND`'s row
byte-identical in both — the export reuses `get_all_sales`, so it followed the shared
expression without a change of its own. In the editor the box survived a reload still checked
while the Products header stayed **"Products (1) — $648.00"**, which is the document/account
split shown above; Audit Logs recorded 4 × `SALE_ITEM_WONT_SHIP` on `sale_item #1` with actor
`admin`, one pair from each of the two controls. Un-marking restored every number listed here
exactly, including the `50.00 KG / $72.00` row and Due $172. The harness was left unmarked.

**Not exercised in the browser, proved over HTTP instead:** the ship-time *"Create more
invoices?"* prompt. The seeded invoice is `planned` and its Book button is disabled (*"Produce
this invoice before booking a shipment"*), and the throwaway database has no recipes or stock to
produce against — so forcing a shipment there would have tested something other than what is
claimed. What `Sales.jsx:212` actually gates on is `has_uninvoiced_remainder`, and that was read
from the live `/api/sales` payload across the same mark/un-mark cycle: **`True → False → True`**.
`tests/test_sale_wont_ship.py` pins the same three states server-side and `Sales.test.jsx` pins
the prompt to the flag.

---

## 🔧 Pipeline Value counts work in flight, not settled business (completed 2026-11-05)

**Measured problem.** The user pointed at the board header: **PIPELINE VALUE $50,174 / 3
sales**, with PI Issued, LC Received, Shipment Ongoing and Payment Due all reading **$0** and
Completed reading **3 / $50,174**. Running production's own `get_sales_summary` against the live
database reproduced it exactly — three sales, all `completed`, worth $648 + $4,600 + $45,432 —
so the "pipeline" was entirely closed business. Home was worse: the same money printed under
*"3 sales in flow"* and again under *"3 completed · $50,174 booked"*.

**Root cause, one line.** `total_value = sum(v["value"] for v in summary.values())` — and
`summary` is seeded from `SALE_STAGE_ORDER`, whose last entry is `completed`. `total_sales`
made the same mistake with the count. The board's per-column *"PI total (visible)"* had always
excluded completed (completed sales are archived off the board), which is why the header
contradicted the columns directly beneath it.

**The rule now:** `completed` is excluded from `total_pipeline_value` and `total_sales`, and
keeps its own per-stage entry, so the figure is moved rather than hidden. Written as
`stage != "completed"` rather than a whitelist of the four open stages, so a stage added later
counts as in flight instead of vanishing from the sum — `test_an_unknown_stage_still_counts_as_in_flight`
pins that intent, because the whitelist version would pass every other test in the file.

**Accepted consequence, stated before shipping:** on this database the card now reads
**$0 / 0 sales** until a PI is actually in flight. That is what an empty pipeline is worth, and
the user chose it when offered the alternative of a second sub-line.

**No frontend change.** Both consumers (`Sales.jsx`'s KpiCard and
`SalesPipelineOverview`'s "sales in flow") already read these two keys, and Home's wording
became true for the first time.

**Verification (full gate, 2026-11-05).** Red first: `tests/test_sales_summary.py` ran
**3 failed / 4 passed** against the pre-fix `get_sales_summary` — the pipeline-total, empty-board
and dashboard tests each still saw the completed money summed in — then green with the rule in
place. Backend: `python -m pytest tests/ -q` → **1594 passed / 8 skipped** in 1747.31 s, exit 0;
the 7 over the **1587** the won't-ship layer ended on are exactly the new file. Focused set
`test_sales_summary` + `test_sale_wont_ship` + `test_dashboard` + `test_commercial_report_kpis`
→ **48 passed** in 79.71 s, so the retired-balance figures and the report's own money model still
agree with the new total. `ruff check .`: all checks passed. `pyright`: 0 errors / **17** warnings
— the documented baseline, none on the changed lines. Frontend: **476 tests in 49 files** all
passed (141.69 s), `oxlint` 0 warnings / 0 errors, `vite build` green (2533 modules, 3.53 s). No
file under `raas-tracker-frontend/` changed, so `react_frontend/` needed no copy —
`git diff --stat -- react_frontend` is empty — and the served bundle hash stayed
`index-C9JJMaS5.js`, the same as the won't-ship release.

**Deployed, and what proves it.** PR #17: `backend` success in 220 s, `frontend` 62 s,
`semgrep` 264 s (`Supabase Preview` reports `skipped` by design), rebased onto `main` as
`7bd91cd`; Vercel production deployment `7574427801` → success. Because this fix is backend-only,
the unchanged bundle hash proves nothing about it, so the live evidence is a **read-only probe**
that ran production's `get_sales_summary` against the live database after the deploy:
sales by stage `{'completed': 3}`, per-stage values $648 + $4,600 + $45,432 all in the
`completed` row, `total_pipeline_value` **$0.00 / 0 sales** and `summary["completed"]`
**3 / $50,174.20**. No browser pass was run for this change — there was no UI to drive.

---

## 📜 Public contract changes

Every entry here is an **intentional** change to a response a client can
observe. If you are debugging a status that "used to be 400 and is now 409",
this is where to look.

| Date | Change | Routes affected | Rationale |
|---|---|---|---|
| 2026-11-10 | **`GET /api/chemicals/inventory` gained a forecast, and `coverage` stopped being a placeholder.** Every `materials[]` row now also carries `required_quantity` and `projected_quantity` (`null` when the demand is not knowable — never `0`), the body gains `coverage` (always an object: `state` `complete` \| `incomplete` \| `no_active_plans`, `open_pis_without_plan[]`, `incomplete_plans[]`) and `unstocked_requirements[]`, and the ETag is computed over that complete body. `GET /api/chemicals` is unchanged and stays lightweight. | `GET /api/chemicals/inventory` (additive keys; `coverage` **None → object**; new ETag semantics) | A buyer must not be shown a shelf table and a demand table that can disagree about what is on the shelf — so both are read from one REPEATABLE READ snapshot in one request. Slice 1's own test asserted `coverage is None`; that assertion described the *un-composed* endpoint and is now obsolete |
| 2026-11-10 | **Four new PI demo routes** (schema v15): `GET/POST /api/sales/<id>/demo-recipe`, `PUT …/demo-recipe/draft`, `POST …/demo-recipe/activate`. GET is any authenticated identity and **404s when nobody has planned the PI** — it never auto-creates; the three writes are operator-class + 15/min, answer **400** `{error, details:[{field,message}]}` for a gate refusal, **404** for a missing or already-bound PI, and **409** for a revision conflict or a second demo on one PI. No stock, production or recipe row is written by any of them. | `POST /api/sales/<id>/demo-recipe` (new, 201), `GET` (new, 404-vs-empty), `PUT …/draft`, `POST …/activate` | "What do we still have to buy for the PIs we have signed but not built?" is only askable before an LC or invoice exists, and answering it required a plan that is explicitly *not* a production order |
| 2026-11-05 | **`total_pipeline_value` and `total_sales` no longer include completed sales.** They summed every stage in `SALE_STAGE_ORDER`, `completed` included, so a database whose only sales are settled reported all of its closed business as the pipeline. `summary["completed"]` still carries its own count and value, so the Completed tile and Home's "N completed · $X booked" line are unchanged — the money moved out of the total, it was not hidden. | `GET /api/sales/summary`, `GET /api/dashboard` (`sales_summary.total_pipeline_value`, `.total_sales`) | The user's board read **$50,174 / 3 sales** as the pipeline beside four columns of **$0.00**, and Home printed the same $50,174 under *"3 sales in flow"* and again under *"3 completed"* — a completed sale is settled business, not work in flight |
| 2026-11-05 | **A sale's `total_value` / `balance` became the accounted value, not the document total.** A PI line marked won't ship now counts only its invoiced quantity (capped at what the PI ordered) in the pipeline list, the pipeline CSV and the stage sums; `has_uninvoiced_remainder` no longer reports a retired line, and the legacy maturity scan drops a sale whose only unpaid money is retired. Consequences: a retired, never-invoiced line reads `total_value 0`; a legacy sale-level payment against such a line answers a **negative** `balance`; a fully or over-invoiced marked line is unchanged. Only sales that carry the mark move — every other row of every response is identical. | `GET /api/sales`, `GET /api/sales/export`, `GET /api/sales/summary`, `GET /api/dashboard` (`sales` and `sales_summary`), `/api/cron/maturity-check` (`checked` can drop) | Netting the remainder in the Commercial Report alone left the board, Home and the CSV still counting a balance the user had retired — "everywhere, not just the report" |
| 2026-11-05 | **A commercial report row now names the PI line behind it, and an admin can retire a remainder.** Report rows gain the additive key `sale_item_id` (previously selected by the SQL but never serialised), and `PUT /api/sales/<id>/items/<iid>/wont-ship` (Admin, 15/min) stores `sale_items.wont_ship` (schema v12). A marked line yields **no** provisional remainder row in `/api/reports/live`, `/live/filtered`, `/live/summary` or the CSV, so its unshipped balance leaves Gross Sales/Due/Overdue. Invoiced rows and their money are untouched, and the mark never blocks invoicing. Responses are otherwise unchanged. | `GET /api/reports/live`, `/live/filtered`, `/live/summary`, `/live/export`, `GET /api/sales/<id>` (`items[].wont_ship`), `PUT /api/sales/<id>/items/<iid>/wont-ship` (new) | The user's PI `12322332` held $72 Due on a 50 KG balance that may never ship; a decision that belongs to the document, not to a viewer's filter |
| 2026-11-03 | **A saturated or unreachable database answers `503`, not `500`.** `psycopg_pool.PoolTimeout` (the 10 s acquire deadline) and `psycopg.OperationalError` — which covers `errors.TooManyConnections`, the error that produced the 2026-11-02 outage — now yield **503** `{"error": "database unavailable, retry shortly"}` with `Retry-After: 5`. The driver message is logged, never sent. Separately, every authenticated client request carries a 30 s deadline: a hung request now produces `ApiError(0, "Request timed out — the server did not answer")` instead of an endless route spinner. | every `/api/*` route reached through `_gate_api`'s checkout and any query that raises while holding a pooled connection (500 → 503) | RFC 9110 §15.6.4 is the honest answer for transient capacity — the client's request was fine and retrying may work; 500 asserts a server bug and tells the caller nothing about retrying. The deadline exists because `loading` stayed true forever when `/api/auth/me` never answered |
| 2026-11-02 | **Production refuses an unbalanced formulation, and a shortage names the chemical.** `POST /api/recipes/by-id/<id>/produce` (and its name alias) now answers **400** when an output product's `ingredients + water` is not 100% within ±0.01 (`'<product>' formulation totals 95% - ingredients + water must equal 100% before production`), before any write. A deduction that would go below zero answers 400 `insufficient stock: <chemical> needs X <unit>, Y <unit> available` instead of the bare `insufficient stock`. Detail/production-source payloads additionally carry `water_qty`, `required_qty`, `stock_remaining` (additive). Reports, the CSV and booking readiness are unchanged. | `POST /api/recipes/by-id/<id>/produce`, `POST /api/recipes/<name>/produce` (new 400 condition; additive keys on `GET /api/recipes/by-id/<id>` and `GET /api/invoices/<id>/production-source`) | The operator asked for production to deduct exactly what the recipe needs; a formula that does not total 100% cannot say what that is, and a bare "insufficient stock" did not say which chemical was short |
| 2026-10-15 | **Superseded 2026-10-25 — the tolerance was removed; no quantity answers 409 any more.** Originally: **A 5% over-invoice tolerance breach answers `409`, not `400`.** `ToleranceExceeded` (`raas_tracker/_exceptions.py`) is a conflict with what the sale has already been invoiced for — retrying the identical body will never succeed — so it is RFC 9110 §15.5.10. `POST /api/invoices/<id>/items` already returned 409; `POST /api/sales/<id>/invoices` and `POST /api/sales/<id>/invoices/batch` **hardcoded 400** for the same refusal, so the status depended on which door the request came in by. Both now go through `status_for(e)`. **Not a bug** — `tests/test_typed_errors.py::test_tolerance_breach_is_409_on_every_invoice_write_route` pins all three at once | `POST /api/sales/<id>/invoices`, `POST /api/sales/<id>/invoices/batch` (400 → 409) | One condition must yield one status; the phase's whole purpose. `POST /api/invoices/<id>/items` is unchanged |
| 2026-10-17 | **A wrong or absent request `Content-Type` answers `415`, not `500`.** `request.get_json()` raises Werkzeug's `UnsupportedMediaType`, which had no handler, so Flask's lookup fell through to the catch-all `Exception` handler. A client that posted a JSON body as `text/plain` (or with no type at all) was told *"internal server error"* — a retry instruction for a fault that is permanently its own. `@app.errorhandler(415)` now answers `{"error": "unsupported media type (expected application/json)"}` | every JSON route (`request.get_json() or {}`) whose `Content-Type` is not JSON (500 → 415) | 415 is RFC 9110 §15.5.16 for exactly this, and it is the only honest answer: the body will never parse until the header changes. **Not authenticated-only** — an earlier draft of this row claimed it was. The three `_PUBLIC_API` routes `/api/auth/login`, `/api/auth/forgot-password` and `/api/auth/reset-password` take `_gate_api`'s `None` path and parse the body before any credential exists, so each went from an unauthenticated **500** to a 415: the change improves the unauthenticated surface rather than leaving it untouched. Pinned by `tests/test_edge_hardening.py::test_a_public_route_answers_415_unauthenticated` |
| 2026-10-25 | **Invoice quantities are uncapped; the 5% tolerance and its `ceiling` field are gone.** `OVER_INVOICE_TOLERANCE`, `check_invoice_tolerance`, `ToleranceExceeded` and the `GET /api/sales/<id>/invoice-lines` `ceiling` field were removed. A quantity may equal, exceed, or fall below the PI line; writes accept anything finite and > 0. No invoice-write route returns 409 for a quantity. | `POST /api/invoices/<id>/items`, `POST /api/sales/<id>/invoices`, `POST /api/sales/<id>/invoices/batch` (no quantity 409; responses otherwise unchanged), `GET /api/sales/<id>/invoice-lines` (`ceiling` removed) | The operator manages over-invoicing manually (operator decision, 2026-10-25). A hard cap that refused a fully invoiced line was a UI blocker, not a commercial control; the writer still derives price and money server-side |

Also documented in `docs/SPEC.md` §4 ("Sales Invoices", "Invoice Items"), §6 and §6.3.

---

## ⚠️ Known limitations (deliberate, tracked)

| # | Limitation | Why it stands | What closing it needs |
|---|---|---|---|
| 1 | **The gate matches products by name**, not by a stable id: `lower(recipes.product_name) = lower(invoice_items.product_name)`, and `invoice_items.product_name` is a denormalised copy taken at line-creation time. Rename a product after invoicing and the gate looks for a recipe under the old name, refusing booking indefinitely. | `production_runs.sale_item_id` exists but is **always NULL** — the only INSERT (`recipes.py:637`) never sets it and its `NOT NULL` was dropped at `db.py:860`. There is no other stable link. | Populate `production_runs.sale_item_id` in `create_production_run`, then join on it in `invoice_production_readiness` with a name fallback for historical rows. |
| 2 | **The gate asserts production *presence*, not *quantity*.** Producing 1 KG against a 3,000 KG invoice line unblocks booking. | Decided deliberately: booking is a commercial act, and the production department owns whether a batch was sufficient. | Compare `SUM(qty_produced)` per invoice line against `invoice_items.quantity`. Blocked on a unit: neither `recipes` nor `production_runs` stores the product unit, so the two numbers cannot be compared soundly without one. |
| 3 | **A line-less legacy invoice is invisible to quantity checks** but its header money is still real. `remaining` derives from `invoice_items` only, so the PI quantity reads as fully uninvoiced. | Phase 2 removed the seed that hid this; production has zero such rows. | `GET /api/sales/<id>/invoice-lines` now surfaces `has_line_less_invoices` (Phase 3) rather than repairing the data. |

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
| Mobile PWA (partly shipped) | Installability shipped 2026-11-12 (manifest, icons, dynamic viewport, safe area). **Remaining:** service worker, offline queue for uploads, install prompt | 2–3 |
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

- **Current branch:** `main` (baseline taken at `8a35c2f`)
- **Local DB:** `DATABASE_URL` set via `setx` (Supabase pooler, encoded pw)
- **Vercel (live app):** `https://raastracker-omega.vercel.app` — the project's production
  alias (Vercel-provided `*.vercel.app`, not a DNS name you own). Pushes to `main`
  auto-deploy to it; other branches get a preview deployment. The older alias
  `chemcalc-omega.vercel.app` answers `404 DEPLOYMENT_NOT_FOUND`; the deployment and
  branch URLs sit behind Vercel Authentication, the production alias does not.
- **Test DB:** `pgserver` embedded (offline) or `TEST_DATABASE_URL`
- **Secrets:** `RAAS_SECRET` (local + Vercel), `DATABASE_URL` (local `setx` + Vercel), `PRODUCTION=1` (Vercel)
- **Password rotation:** Run `setx` locally + update Vercel env → redeploy
- **Do not push** without explicit user instruction

---

## 🔒 Phase CHEMICAL-PURCHASES: Lock ordering (Slice 1, audited 2026-11-08)

Canonical acquisition chain (top → bottom, never back-acquire):

```
LC → sale → invoice/recipe → purchase headers → chemicals
```

Concrete sequences for intersecting writers:

| Writer | Sequence | Sort key |
|---|---|---|
| `create_production_run` | invoice(FU) → sale(FKS/UPD) → chemicals(FU) | **chemical_id ascending** (changed from name) |
| `purchase.create` (new) | purchase_header(INS) → chemicals(FU/INS) | chemical_id ascending |
| `purchase.correct/cancel` (new) | purchase_header(FU) → chemicals(FU) | chemical_id ascending |
| `stock.update_stock` | chemicals(FU by name) | caller determines order |
| `uploads.adjust_stock_from_upload` | chemicals(UPD, upload-row order) | unsorted (pre-existing, not fixed) |
| `stock.import_from_json` | chemicals(UPD/INS, file order) | unsorted (pre-existing, not fixed) |
| `lcs.attach_pis/detach_pi/move_lc_stage` | LC(FU) → sales(FU) | sale id ascending |
| `sales.create_invoice/batch` | sale(FU) → sale_items(FU) → invoices(INS) | sale_item id ascending |
| `sales.record_invoice_payment/ship_invoice` | invoice(FU) → sale(UPD) | single row |

**Decision:** Production `_aggregate_requirements` (`recipes.py:803`) sorts by
`chemical_id` ascending. Purchases acquire chemical locks in the same order.
This is rename-stable and uniform. No existing test asserts name-order of
production_run_items.

**Pre-existing inversions NOT fixed in Slice 1:**
- Sale↔invoice: production/payment/ship lock invoice-first; create_invoice
  locks sale-first. Purchases never lock sale/invoice, so they cannot
  participate in that cycle.
- Reconciliation/import: unsorted chemical acquisition. Self-contained
  transactions; no purchase intersection.

**New-material race:** `INSERT … ON CONFLICT (lower(name)) DO NOTHING` +
re-SELECT + `FOR UPDATE`. Unique index `idx_chemicals_name_lower` enforces
identity; no assumed existing-row lock.

**Concurrency tests (Slice 1):**
- A: purchase correction vs production on overlapping chemicals (barrier)
- B: two purchases with reversed row order (both must acquire id-ascending)
- C: concurrent new-material registration (identity race)
- D: repeated cancellation (idempotent guard under FU)

---

## 🧪 Phase PI-DEMOS: plan a PI before it exists, and forecast the buying (Slice 3, built 2026-11-10)

Implements the third slice of the Chemical Stock redesign (slices 1 and 2 are
recorded above and in `docs/SPEC.md` §"Where a recipe's material comes from").
Schema **v15**; nothing existing changed shape.

**What shipped:** `raas_tracker/pi_demos.py` (create / get / save_draft /
activate_demo / project_purchasing / arm_snapshot), four routes in `flask_app.py`
(`GET|POST /api/sales/<id>/demo-recipe`, `PUT …/draft`, `POST …/activate`), and the
inventory route rewired so the forecast and the shelf are read from **one**
REPEATABLE READ snapshot. Frontend: `PiDemoEditor.jsx`, the demo entry in
`SaleDetailModal.jsx`, the Projected Quantity column + coverage alerts in
`Chemicals.jsx`.

**Lock order (extends the chain above, never inverts it):**
`sales(FU) → pi_demos(FU OF d) → pi_demo_products/items(DELETE+INSERT)`, chemical
ids ascending where a formulation names several. `create_demo` inserts on
`pi_demos.sale_id` and maps `UniqueViolation → 409`, `ForeignKeyViolation → 404`,
so a concurrent create and a deleted PI both answer with their own fact instead
of a 500. Purchases never take a sale lock and demos never take a chemical lock,
so neither new writer can join the pre-existing sale↔invoice inversion.

**Rulings taken while building (the plan was silent or self-contradictory):**

- **`coverage` is always an object.** Slice 1's `test_chemical_purchases._material`
  asserted `coverage is None` — a statement about an endpoint that had no
  projection. Once Slice 3 composes the forecast into that same response, the
  placeholder is gone and the test now pins the real shape. The behaviour change
  is the point of the slice, not a regression.
- **Demo writes are operator-class, and operator-class means *any authenticated
  identity*.** The RED half wrote `test_a_plain_user_cannot_write_a_demo` expecting
  **403**, but `users.role` has exactly two values (`admin`, `user`) and
  `operator_or_admin_required` deliberately admits both — `POST /api/sales`,
  `POST /api/upload` and the packaging movements are already reachable by a plain
  `user`. Refusing one here would be a role-model change smuggled into a planning
  feature. The test became
  `test_a_demo_write_needs_a_session_but_not_an_admin`: no session → 401 and
  nothing stored; a signed-in `user` → the write works. The three routes also
  joined `OPERATOR_ROUTES`, which the declared-vs-stamped cross-check requires.
- **The projection does not end a transaction it does not own.** `_begin_snapshot`
  sets `SET LOCAL transaction_isolation` only when no transaction is open; when
  one is already open it reads the isolation and **logs** a warning rather than
  committing away someone else's work. The inventory route — a pure read — ends
  and re-arms its own transaction (`commit → _timeout_statements() → arm_snapshot`)
  before calling, so the forecast always gets the guarantee it is allowed to
  assume. Exactly one statement is issued per call either way, which is what makes
  `2 × baseline` a meaningful assertion.
- **`GET` never creates, and unknown never becomes zero.** Both are the same
  invariant seen from two ends: "nobody has planned this PI" must stay a fact the
  coverage report can surface, so a miss is a 404 and an unresolvable figure is
  `null`.

**Verification (task #10, closed 2026-11-12).** Focused set (8 files, 250 cases) green;
`ruff check raas_tracker/tests/` clean. The full-suite gate ran green afterwards:
**1760 passed / 8 skipped** against a real PostgreSQL, `pyright` **0 errors**, `ruff`
clean.

The browser flow ran against a local stack - Flask on an embedded PostgreSQL, so no
production data was touched. A PI was opened from the sales board, its demo recipe drafted
one product per PI line with the PI's own quantity and unit, an ingredient picked from the
chemical catalog (the automatic water resolved the rest to 100%), the draft saved
(revision 2, then 3) and the demo activated. The Chemical Stock forecast then carried the
demand that activation created: Caustic Soda 50% required 125 KG against 100 KG in stock,
so 25 KG projected to buy; NaOH required 400 KG against 500 KG, so nothing to buy. The
coverage report named the *other* PI as the one without a plan, which is the distinction
the whole feature exists to keep.

---

## 📱 Phase MOBILE-UX: phone usability pass (M-0..M-6, completed 2026-11-12)

**What shipped.** The mobile fix plan (`MOBILE_FIX_PLAN.md`, revised after a source-verified
audit) ran as seven slices, one commit each, each starting from a failing test:

| Slice | Commit | What it fixed |
|---|---|---|
| M-0 | `09e6b6e`, `2a392da` | the plan committed; the dead opencode config removed from the tree |
| M-1 | `76d5d42`, `c74b0bc` | one `collapsed` boolean drove both the desktop rail and the mobile drawer, so the drawer opened expanded over the page and its only toggle slid off-screen. `Layout` now owns `desktopCollapsed` + `mobileOpen`, the header carries a hamburger (`aria-expanded`, `aria-controls="app-sidebar"`), Escape closes the drawer and focus returns to the hamburger |
| M-2 | `c7d7328` | 26 `type="number"` inputs rendered at 14 px, so iOS Safari auto-zoomed on focus. Now `text-base sm:text-xs` (16 px on phones) plus `inputMode="decimal"` / `"numeric"` |
| M-3 | `6b55764` | `role="dialog"` appeared nowhere in the repo. `useDialogLayer` gives `Modal` and the drawer the same stack-aware Escape, focus trap, focus restore and body-scroll lock; below `md` a modal is a bottom sheet |
| M-4 | `7eb3960` | 4 wide tables had no scroll wrapper (Production Run, Reports stock availability, 2x Recipes) and the invoice editor had no phone view. Every wide table now scrolls and `InvoiceEditor` renders stacked cards below `md` |
| M-5 | `7a6537a` | icon-only controls were 26-36 px. One shared centred 44x44 pseudo-element gives each of them a 44 px target without changing the visual box |
| M-6 | `c01ad08` | `h-screen` at 9 sites meant the address bar cut the app; `index.html` had no `viewport-fit=cover`, no theme colour and no manifest. Now `h-dvh` / `min-h-dvh`, the safe-area inset is reserved inside the sheet and under the main area, and the app is installable (manifest + 192/512/180 icons, `scripts/make_pwa_icons.py`) |

**Verification.** `vitest` 599/599 (57 files) - `oxlint` 0 errors - `vite build` clean -
`ruff` clean - `pyright` 0 errors. Chromium at 360x640 and 1280x800: no page-level
horizontal scroll on any page, 44 px hit areas measured by hit-testing (a tap 21.5 px from
an icon's centre lands on it, 23 px does not), the dynamic viewport equals the visible
height, and the modal sheet sits flush with the bottom edge. Flask serves `/manifest.json`
and the three icons with 200; Chrome reports no installability errors; Lighthouse mobile:
accessibility 0.92, best-practices 1.00.

**How it landed.** PR #21 (branch `feat/mobile-ux-pass`), rebase-merged into `main` as
`d6e1075`; PR #23 (`fix/ci-async-budget`) followed as `2a14b09` to close the load-induced
test flakes; PR #24 (`fix/auth-a11y-and-recipes-touch-targets`) closed the two remaining
defects. `main` is a protected branch: changes go through a pull request and both
required checks - the CI workflow's `backend` and `frontend` jobs - must be green. Both
were green before each merge, and the CI run on `main` was green after it.

**Deliberately not done.** B-1 (`CURRENT_DATE` -> UTC: the DB is already UTC) and B-2
(`auth.py` `to_char` -> UTC: 8 mixed read/write sites on TEXT columns plus DDL defaults in
off-limits `db.py`). M-7 was folded into M-4. `DataTable` needed no card view - it already
scrolls.

**Follow-ups, and where they stand.**
1. The load-induced test flakes are closed: PR #23 raised Testing Library's async
   assertion budget to 5 s and vitest's per-test timeout to 15 s, and
   `src/testBudget.test.js` pins them. The assertions themselves are unchanged - a state
   change that never arrives still fails.
2. The Recipes dense-table icon buttons are closed: PR #24 gave them the shared 44x44
   target and widened the recipe row actions from 6 px to 12 px, so the destructive
   control cannot overlap the button beside it. Measured in Chromium at 390 px: the target
   stops 9 px short of the next row and the View button keeps its full width. The 12 px
   gap itself still belongs to the destructive button - a tap in the exact middle opens
   its confirmation dialog.
3. The login page's missing labels, `main` landmark and meta description are closed:
   PR #24 associated every control on all four auth pages with its label, made each page
   a `main` landmark and declared the document description. Lighthouse mobile now scores
   accessibility 1.00, best-practices 1.00 and SEO 1.00 with no failing audit.

**Still open.**
1. `react_frontend/` is gitignored, so a local Flask serve is stale until the
   build is published. `python scripts/build_frontend.py` now builds and publishes in one
   step, replacing the previous hashed assets; Vercel's build command does the same copy
   in production.

**Small findings from the review, and where they stand.**
- The dialog layer keeps its open-layer stack in module scope, so the app must mount one
  React root. `src/rootInvariant.test.js` pins that now, instead of leaving it as an
  assumption in a comment.
- `Button` treats a child that renders nothing as no child, but a rendered "0" is text: a
  button showing "0" is not an icon-only control and does not get the 44 px target.
- The drawer is named with `aria-label="Navigation"` rather than `aria-labelledby`: it
  carries no heading element to point at, and a descriptive aria-label is a valid
  accessible name.
- The viewport invariant test assembles the legacy class name from parts on purpose, so
  Tailwind's content scanner does not re-emit that dead utility into the production
  stylesheet.

---

*Generated September 2026 — aligned with Vibe Coding Guidebook v2026*