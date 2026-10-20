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
| M7.3 | **Recipe Item `required_qty_per_unit` Persistence** — Store computed `percentage * total_quantity / 100` on create/update | `recipes.py`, `flask_app.py` (recipe item PUT), `recipes.jsx` | Field persisted, editable, used in reports |
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

## 🧾 Phase INV-UNIFY: PI-faithful invoice editor (In progress)

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
| 5 | `InvoiceEditor` + rewritten `InvoiceLineComposer` | ✅ spec-first | 🟡 2026-10-21 — editor + hook + `code` on 404 landed (`f6c7fe4`, `23b8743`); composer rewrite rides with the Phase 6 swap so no dead composer is left behind |
| 6 | Swap call sites, delete the legacy files | ✅ | ⬜ |
| 7 | Docs close-out | — | ⬜ |

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
| `invoiced_quantity` | `SUM(invoice_items.quantity)` across **every** invoice of the sale, no status filter — identical to `check_invoice_tolerance` |
| `remaining` | `max(0, PI − invoiced)`; fully invoiced lines are still returned |
| `ceiling` | `PI × 1.05` quantized to 6 dp with the writer's own `Decimal(str(...))` expression |
| `invoice_ids` | `array_agg(DISTINCT …)` of the invoices holding lines for that PI line |
| `has_line_less_invoices` | `true` when a legacy invoice has header money no line backs; `remaining` overstates the budget in that case |

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
| The Phase 3 endpoint's 6 dp `ceiling` is accepted verbatim by the writer; `ceiling + 1e-6` is `ToleranceExceeded` | `test_endpoint_ceiling_is_exactly_what_the_writer_accepts` |
| One `INVOICE_CREATE` per invoice, one `INVOICE_ITEM_ADD` per line, header audited **last** | `test_create_invoice_audits_each_line_then_the_header` |
| Non-finite PI data → named `ValueError`, no invoice written (the `remaining_invoice_money` call that is otherwise value-unused) | `test_create_invoice_refuses_a_non_finite_pi_total_before_writing` |

Already covered, not duplicated: ceiling boundaries for all three writers
(`test_invoice_tolerance.py`), atomicity of a failed batch
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

**Hard stop, not advertised headroom.** The row shows PI qty, invoiced and
remaining. It does **not** advertise a ceiling. The ceiling is still enforced
from the payload's absolute `ceiling` (never recomputed as `pi * 1.05`: with
`quantity` stored as REAL the two disagree, e.g. PI `0.56` → server `0.588000`
vs recomputed `0.5880000025`):

- over-limit when `typed > 0 && invoiced_quantity + typed > ceiling + 1e-9`;
  the `typed > 0` term keeps a pre-existing over-ceiling row (only reachable via
  the Phase-1 tracked race) from poisoning the whole sale;
- Save stays enabled while every row is at or under the limit, including a
  quantity above `remaining` but within the ceiling — that is the
  shipment-advantage case;
- over-limit rows show the correctable number
  (`maximum you can still add is X`) and disable Save.

The `1e-9` allowance is one-directional: an input in that band is accepted by
the UI and refused by the writer, where the `409` resync is the backstop. It is
not a promise that UI and server agree bit-for-bit.

**409 / 400 handling (create).** Classification is **structural**, never by
parsing the server's prose: the server's only contract is the status code and
the reloaded budget. Create is atomic, so a refusal writes nothing:

1. `409` (`ToleranceExceeded`) → reload, keep number, date and every typed
   quantity, then mark whichever rows the *reloaded* figures put over the limit
   as `over-budget` (rose).
2. `400` (a PI line deleted between load and submit) → reload; the staged
   entries whose `sale_item_id` is gone are dropped and listed by product name
   in one form-level amber notice (`N PI line(s) are no longer on this PI —
   review and resubmit`). A dropped row cannot carry its own mark, so the
   notice is the mark.
3. anything else → generic rose error; staged state untouched.

**409 / 404 handling (add-line).** A `409` flags the row and **resyncs** (the
reloaded figures are what decide whether it is still over). A `404` is
genuinely ambiguous server-side — either the invoice is gone or the PI line is
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
4. a quantity above `ceiling - invoiced` shows the over-limit message and
   disables Save without POSTing.
5. a quantity exactly at `ceiling - invoiced` is accepted and POSTs.
6. a quantity above `remaining` but within the ceiling stays submittable.
7. the offered maximum comes from the payload, not `pi * 1.05` (fixture where
   `ceiling !== pi_quantity * 1.05`).
8. add-line POSTs `/api/invoices/<id>/items` with `{sale_item_id, quantity}`.
9. create 409 → budget reloaded, the *reloaded* invoiced/max-add figures are
   rendered, Save is now disabled, and number/date/quantities are preserved.
10. create 400 → vanished product name appears in the amber "no longer on this
    PI" notice and the header is preserved.
11. success → `onDone` receives the created invoice.

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
| 12 red-first tests | `src/components/sales/InvoiceEditor.test.jsx` |

Two corrections made while turning the spec into tests:

- The 409 test as first drafted typed a quantity already over the *initial*
  budget, so the hard stop (correctly) prevented the POST and no 409 could
  happen. It now types `6` (allowed against a stale `invoiced=2`, over once the
  server reports `invoiced=6` → max add `4.50`), which is the only shape a real
  409 can take from this UI.
- In add-line mode a `pi_line_gone` reloads and drops the row through the same
  superseded path as create; only `invoice_gone` disables Save, because the
  target itself is the thing that vanished.

`InvoiceLineComposer.jsx` is **not** rewritten yet: nothing renders the new
editor until Phase 6 swaps the four call sites, and rewriting the composer
first would leave two composers with no callers. Phase 6 owns that plus the
deletions listed in the table at the top of this section.

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

### Deployment reconciliation finding (open)

Production (`njanawhckyhyaduxcodr`, the same database as the app DSN) is **one
schema version behind `main`**:

- `app_settings.raas_schema_version` = **`5`**; `raas_tracker/db.py:141` is at **`6`**.
- `public.invoices` has **no `invoice_date` column** — the v6 migration
  (`710e36f`) has never run there.
- `raas_schema_sig` is present (7,642 chars), but `_version_is_current("5")` is
  `False` and `_schema_current()` requires it (`db.py:309`), so `_create_tables()`
  and `_run_migration()` execute on **every** connection. (Until Phase 2,
  `backfill_invoice_lines` ran there too.) Harmless today because the tables are
  empty: `invoices` 0, `invoice_items` 0, `sale_items` 1, `sales` 1,
  `recipe_items` 0, `production_runs` 0.
- Consequence: deploying `main` applies the v6 DDL on first boot. That is
  intended, but it must be **observed, not assumed** — after the deploy, re-read
  `raas_schema_version` (expect `6`) and confirm `invoices.invoice_date` exists.

### Phase 1 — writer lock discipline (no production code change)

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

## 📜 Public contract changes

Every entry here is an **intentional** change to a response a client can
observe. If you are debugging a status that "used to be 400 and is now 409",
this is where to look.

| Date | Change | Routes affected | Rationale |
|---|---|---|---|
| 2026-10-15 | **A 5% over-invoice tolerance breach answers `409`, not `400`.** `ToleranceExceeded` (`raas_tracker/_exceptions.py`) is a conflict with what the sale has already been invoiced for — retrying the identical body will never succeed — so it is RFC 9110 §15.5.10. `POST /api/invoices/<id>/items` already returned 409; `POST /api/sales/<id>/invoices` and `POST /api/sales/<id>/invoices/batch` **hardcoded 400** for the same refusal, so the status depended on which door the request came in by. Both now go through `status_for(e)`. **Not a bug** — `tests/test_typed_errors.py::test_tolerance_breach_is_409_on_every_invoice_write_route` pins all three at once | `POST /api/sales/<id>/invoices`, `POST /api/sales/<id>/invoices/batch` (400 → 409) | One condition must yield one status; the phase's whole purpose. `POST /api/invoices/<id>/items` is unchanged |
| 2026-10-17 | **A wrong or absent request `Content-Type` answers `415`, not `500`.** `request.get_json()` raises Werkzeug's `UnsupportedMediaType`, which had no handler, so Flask's lookup fell through to the catch-all `Exception` handler. A client that posted a JSON body as `text/plain` (or with no type at all) was told *"internal server error"* — a retry instruction for a fault that is permanently its own. `@app.errorhandler(415)` now answers `{"error": "unsupported media type (expected application/json)"}` | every JSON route (`request.get_json() or {}`) whose `Content-Type` is not JSON (500 → 415) | 415 is RFC 9110 §15.5.16 for exactly this, and it is the only honest answer: the body will never parse until the header changes. **Not authenticated-only** — an earlier draft of this row claimed it was. The three `_PUBLIC_API` routes `/api/auth/login`, `/api/auth/forgot-password` and `/api/auth/reset-password` take `_gate_api`'s `None` path and parse the body before any credential exists, so each went from an unauthenticated **500** to a 415: the change improves the unauthenticated surface rather than leaving it untouched. Pinned by `tests/test_edge_hardening.py::test_a_public_route_answers_415_unauthenticated` |

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

- **Current branch:** `main` (baseline taken at `8a35c2f`)
- **Local DB:** `DATABASE_URL` set via `setx` (Supabase pooler, encoded pw)
- **Vercel Preview:** `https://chemcalc-omega.vercel.app/` — auto-deploys on push
- **Test DB:** `pgserver` embedded (offline) or `TEST_DATABASE_URL`
- **Secrets:** `RAAS_SECRET` (local + Vercel), `DATABASE_URL` (local `setx` + Vercel), `PRODUCTION=1` (Vercel)
- **Password rotation:** Run `setx` locally + update Vercel env → redeploy
- **Do not push** without explicit user instruction

---

*Generated September 2026 — aligned with Vibe Coding Guidebook v2026*