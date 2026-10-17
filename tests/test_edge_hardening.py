"""Phase 4-B: the outermost boundary — Vercel response headers + the 415 handler.

Two gaps on the *unauthenticated* surface, both found by dumping live state rather
than reading the SPEC:

**4-B1 — `vercel.json` had no `headers` block at all.** Its whole content was
`env`, `buildCommand`, `rewrites`, `functions`, `crons`. Every SPA route is
rewritten to `/index.html` at the edge, and those responses shipped with no
`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
`Strict-Transport-Security` or `Permissions-Policy`. `TRUSTED_PROXY: "1"` was
already in `env`, so the app already assumed Vercel was the sole ingress; the
response hardening was the missing half of that assumption.

**4-B2 — 415 was unregistered and fell through to a 500.** Werkzeug raises
`UnsupportedMediaType` from `request.get_json()` when the body is not JSON, and no
handler was registered for that code, so Flask's lookup walked to the catch-all
`Exception` handler. A client sending `Content-Type: text/plain` was told
"internal server error, retry" for what is permanently its own bug.

Scope of these assertions — read before trusting them:

* The header tests read `vercel.json` and compile its `source` patterns **locally**.
  They prove the config is what this repo intends to ship; they cannot prove what
  the live edge does with a pattern (no staging deployment exists, decision 2 of
  Phase 4), and they do not assert any unmeasured edge behaviour.
* `_matcher` deliberately accepts only the group-with-inline-regex shape Vercel's
  `rewrites` already uses. A future edit to path-to-regexp parameter syntax
  (`:path*`) would make a naive `re.compile` silently match the literal string
  `:path*` and pass every test while matching nothing at runtime, so the shape is
  asserted instead of assumed.
* The `Content-Security-Policy` is copied verbatim from `flask_app.py`'s
  `after_request`, so the edge and the Flask layer enforce one policy. Three
  guards keep the static copy honest: the live Flask response is compared to it
  byte-for-byte, the inline theme-script digest is recomputed from the SPA's
  source `index.html` on every run, and the deliberate `style-src` exception is
  pinned with the reason it is still needed. An earlier version of this file
  asserted the CSP's *absence* on the premise that Vercel "cannot hash or nonce"
  an inline script; that premise was false — a `sha256-…` source expression is a
  compile-time constant, which is exactly what a static header can express — so
  the assertion is inverted and the hash now gates the copy.
"""
import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

import flask_app


_ROOT = Path(flask_app.__file__).resolve().parent  # the repo root (flask_app.py sits in it)
_VERCEL_PATH = _ROOT / "vercel.json"


def _vercel():
    """`vercel.json` parsed fresh, so a syntax error fails here and not in CI."""
    return json.loads(_VERCEL_PATH.read_text(encoding="utf-8"))


# ==================== 4-B1: Vercel security headers ====================

# `/((?!...).*)` — a literal `/` then a path-to-regexp group `(` whose body opens
# with its own `(` and closes with the group's `)`. Asserted rather than assumed:
# a future edit to parameter syntax (`:path*`) would still compile under
# `re.compile` and match the literal string `:path*`, passing every assertion
# below while matching nothing at runtime.
_GROUP_SHAPE = re.compile(r"^/\(\(.*\)\Z", re.S)


def _matcher(source: str):
    assert _GROUP_SHAPE.match(source), (
        f"header source {source!r} is not the /((?...)*) form this helper "
        "evaluates correctly. If the pattern language changed, fix the matcher "
        "in this test rather than loosening the assertion — a silent "
        "non-matching regex would pass every assertion below."
    )
    return re.compile("^" + source + r"\Z")


def _api_paths():
    """Every real `/api/...` path this app serves, from the live URL map.

    Read from `url_map` rather than a hand-written list so a new route is covered
    automatically — a route added after the header block was written cannot slip
    past a stale literal list.
    """
    out = set()
    for rule in flask_app.app.url_map.iter_rules():
        path = re.sub(r"<[^>]*>", "1", rule.rule)
        if path.startswith("/api/"):
            out.add(path)
    return out


def test_vercel_config_parses_and_declares_headers():
    cfg = _vercel()
    assert "headers" in cfg, (
        "vercel.json has no headers block: every response the edge serves — "
        "including every SPA route the rewrite sends to /index.html — ships "
        "with no security headers at all"
    )
    assert isinstance(cfg["headers"], list) and cfg["headers"]


def test_every_response_carrying_headers_declares_a_non_empty_header_list():
    for entry in _vercel()["headers"]:
        assert entry.get("headers"), f"empty header list on {entry.get('source')!r}"


@pytest.mark.parametrize("key, value", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    # `no-referrer`, not `strict-origin-when-cross-origin`: `ResetPassword.jsx`
    # reads `?token=` off the URL and then fetches `/api/auth/reset-password`
    # same-origin, so any less-strict value puts the full token URL into this
    # app's own access log. Nothing in `flask_app.py` or `src/` reads
    # `document.referrer`/`Referer`, and there is no analytics or external
    # origin, so the cross-origin guarantee bought nothing here.
    ("Referrer-Policy", "no-referrer"),
    # 0, not a legacy filter: the CSP is the XSS control now, and an enabled
    # filter has been the source of information-disclosure bugs in itself.
    # Matches `flask_app.py` so the two layers agree.
    ("X-XSS-Protection", "0"),
])
def test_required_security_header_is_present_with_that_exact_value(key, value):
    """Exact values, not just presence: `nosniff`/`SAMEORIGIN`/`origin` would all
    *pass* a membership test while meaning something weaker."""
    pairs = _header_pairs()
    assert pairs.get(key) == value, f"{key} is {pairs.get(key)!r}, expected {value!r}"


def _header_pairs():
    """key -> value across the whole block. One rule is asserted below, so a
    merge of several rules cannot hide a header behind a partial read."""
    out = {}
    for entry in _vercel().get("headers", []):
        for h in entry.get("headers", []):
            out[h["key"]] = h["value"]
    return out


def test_strict_transport_security_is_at_least_one_year_with_subdomains():
    value = _header_pairs().get("Strict-Transport-Security", "")
    m = re.match(r"^\s*max-age=(\d+)\s*(.*)$", value)
    assert m, f"Strict-Transport-Security is {value!r}; expected a max-age first"
    assert int(m.group(1)) >= 31536000, (
        f"max-age={m.group(1)} is under the OWASP one-year floor: HSTS is only "
        "protective once browsers have cached it"
    )
    assert "includesubdomains" in m.group(2).lower()
    # `preload` is a commitment to submit the domain to the browser preload
    # list. The app has not been submitted, and the header is scoped to this one
    # Vercel host, so shipping the token would only overstate the guarantee.
    assert "preload" not in m.group(2).lower()


_SPATIAL_SRC = Path(flask_app.__file__).resolve().parent / "raas-tracker-frontend" / "src"


@pytest.mark.parametrize("pattern", ["getUserMedia", "mediaDevices", "navigator.geolocation"])
def test_no_source_in_the_spa_uses_the_denied_device_apis(pattern):
    """Walks `src/` for the claim this header's rationale rests on, so the
    rationale cannot outlive the code. Denying camera/microphone/geolocation is
    only free while nothing calls them; the day a barcode-scanner or
    geofencing feature lands, this fails and the header value must be revisited
    rather than the header silently blocking the feature.

    Scoped to `src/` (the shipped SPA source), which is where any browser API
    call that could request these permissions would live.
    """
    assert _SPATIAL_SRC.is_dir(), f"expected the SPA source tree at {_SPATIAL_SRC}"
    offenders = sorted(
        str(f.relative_to(_SPATIAL_SRC))
        for f in _SPATIAL_SRC.rglob("*")
        if f.is_file() and pattern in f.read_text(encoding="utf-8", errors="replace")
    )
    assert not offenders, (
        f"{pattern} now appears in the SPA source ({offenders}); "
        "Permissions-Policy camera=(), microphone=(), geolocation=() would "
        "break it — re-scope the header, do not just drop the assertion"
    )


def test_permissions_policy_denies_camera_microphone_and_geolocation():
    """Each of the three is denied because no code path uses it — a property the
    test above now proves rather than this docstring claiming. Denying an unused
    capability removes a permission-prompt surface, and a prompt the operator
    could be socially engineered into granting, at no cost."""
    value = _header_pairs().get("Permissions-Policy", "")
    directives = {}
    # Permissions-Policy separates directives with commas, not semicolons
    # (semicolons appear inside e.g. `interest-cohort=()`).
    for part in value.split(","):
        if "=" in part:
            name, _, allow = part.partition("=")
            directives[name.strip().lower()] = allow.strip()
    for feature in ("camera", "microphone", "geolocation"):
        assert feature in directives, f"{feature} not named in Permissions-Policy: {value!r}"
        assert directives[feature] == "()", (
            f"{feature}={directives[feature]} still permits the feature; "
            "this app has no code path that uses it"
        )


def test_no_header_source_captures_an_api_route():
    """The load-bearing assertion. API responses are JSON and are already covered
    by the app's own controls; stamping SPA headers on them is at best noise and
    at worst a future `Content-Security-Policy` silently applying a document
    policy to an API response."""
    api_paths = _api_paths()
    assert len(api_paths) > 50, "URL map lookup failed to enumerate the API routes"
    for source in _sources():
        m = _matcher(source)
        captured = sorted(p for p in api_paths if m.match(p))
        assert not captured, (
            f"header source {source!r} captures {len(captured)} API path(s), "
            f"e.g. {captured[:5]}"
        )


def test_header_source_covers_the_spa_and_its_static_assets():
    """Positive half of the same invariant: a pattern that accidentally matched
    nothing would satisfy the test above while shipping an inert config."""
    sources = _sources()
    for path in ("/", "/login", "/reports", "/assets/index-DopUYxAQ.js",
                 "/assets/index-DXeKx-Gx.css", "/favicon.svg", "/icons.svg"):
        assert any(_matcher(s).match(path) for s in sources), (
            f"no header source covers {path!r} — the edge serves it with no "
            "security headers, which is the gap this block exists to close"
        )


def _sources():
    sources = [e["source"] for e in _vercel().get("headers", [])]
    # Non-vacuity guard for the negative tests below: an empty block would make
    # "matches no API path" and "agrees with the rewrite" true for free.
    assert sources, "vercel.json declares no header sources at all"
    return sources


def test_header_source_excludes_the_cron_path():
    """`vercel.json` schedules a Vercel cron at `/api/cron/maturity-check`, a
    route the URL map also owns. Named separately from the loop above so the
    config and the app are checked to agree on that path — the cron fires
    unauthenticated from the platform, so it must not pick up document headers.
    """
    scheduled = {c["path"] for c in _vercel()["crons"]}
    assert "/api/cron/maturity-check" in scheduled
    assert "/api/cron/maturity-check" in _api_paths()
    for source in _sources():
        assert not _matcher(source).match("/api/cron/maturity-check"), (
            f"header source {source!r} covers the scheduled cron route"
        )


def test_header_source_is_broader_than_the_rewrite_on_static_assets():
    """Why the `source` is NOT a copy of the rewrite's pattern.

    `rewrites` excludes dotted paths (`.*\\.`) because `/assets/*.js` must be
    served as a file, not rewritten to `/index.html`. That exclusion is correct
    for routing and wrong for headers: a hashed bundle and `index.html` are
    equally in need of `nosniff`. Copying the rewrite pattern wholesale would
    leave every static asset unprotected, so the header block deliberately drops
    the dotted-path exclusion and keys only on the `/api/` boundary.
    """
    rewrite = _vercel()["rewrites"][0]["source"]
    rw = _matcher(rewrite)
    header = _matcher(_sources()[0])
    assert rw.match("/assets/index-DopUYxAQ.js") is None, (
        "rewrite premise changed: assets are no longer excluded from the "
        "rewrite, so this test's justification needs rechecking"
    )
    assert header.match("/assets/index-DopUYxAQ.js")


# ---------- the CSP: shipped at the edge, gated against drift ----------

def _inline_script_hashes():
    """`sha256-…` for every inline `<script>` in the SPA's source `index.html`.

    Recomputed rather than hardcoded: Vite copies the inline theme bootstrap into
    the build verbatim, so a change to that script must change the hash in both
    CSP copies or browsers silently drop the script. That silence is not
    hypothetical — it is exactly how dark mode was broken in production before
    the Flask policy allowlisted the script by hash.
    """
    html = (_ROOT / "raas-tracker-frontend" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts, "expected an inline theme script in the SPA's source index.html"
    return [
        "sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode()
        for s in scripts
    ]


def _directive(csp: str, name: str) -> str:
    return next(
        (p.strip() for p in csp.split(";") if p.strip().startswith(name + " ")),
        "",
    )


def test_content_security_policy_is_shipped_with_the_theme_hash():
    """The policy Flask has always sent, now on the response that needs it.

    An earlier version of this file asserted the CSP's *absence*, on the premise
    that Vercel "cannot hash or nonce" an inline script because the header is
    static. That premise was false: a `sha256-…` source expression is a
    compile-time constant, which is precisely what a static header can express —
    a nonce is not involved and never was. `flask_app.py` already shipped this
    policy by hash. Because the edge serves the SPA document and `/assets/*`, the
    policy had simply never applied to them in production.
    """
    csp = _header_pairs().get("Content-Security-Policy")
    assert csp, (
        "no CSP at the edge: the SPA document and its bundles ship with no "
        "script-src restriction at all"
    )
    script_src = _directive(csp, "script-src")
    assert script_src, f"the CSP has no script-src directive: {csp!r}"
    assert "'unsafe-inline'" not in script_src, (
        f"script-src carries 'unsafe-inline' ({script_src!r}). It also disables "
        "the hash below, so this is strictly weaker than the policy it replaced"
    )
    for digest in _inline_script_hashes():
        assert f"'{digest}'" in script_src, (
            f"{digest} is missing from the edge's script-src ({script_src!r}): the "
            "theme bootstrap would be blocked and dark mode silently lost"
        )


def test_style_src_keeps_the_inline_exception_the_spa_still_needs():
    """`'unsafe-inline'` in `style-src` is deliberate, and not for the obvious reason.

    The tempting explanation is the three `style={{…}}` props in the SPA, but
    React 19 applies those through the CSSOM (`style.setProperty`), which CSP does
    not govern — they survive a strict `style-src` untouched. The real consumer is
    **`sonner`**, which injects a `<style>` element at module scope
    (`__insertCSS` -> `document.createElement("style")` -> `head.appendChild`),
    and an injected `<style>` *is* blocked by a strict `style-src`. Tightening
    this therefore silently strips the styling from every toast in the app, and
    the failure is invisible: the components that look responsible keep rendering
    correctly, and the guard that would catch it is the one being removed.
    Removing `'unsafe-inline'` needs sonner's stylesheet moved into the app's own
    build — not a Tailwind migration of those three props, which changes nothing.
    """
    style_src = _directive(_header_pairs()["Content-Security-Policy"], "style-src")
    assert "'unsafe-inline'" in style_src, (
        f"style-src lost 'unsafe-inline' ({style_src!r}) while the SPA still "
        "injects sonner's toast stylesheet at runtime; get that stylesheet into "
        "the build first, then tighten this"
    )


def test_the_edge_csp_is_byte_identical_to_the_one_flask_sends(client):
    """Two copies of one policy, so neither may drift from the other.

    `vercel.json` is static JSON and cannot import `flask_app.py`, so the policy
    is necessarily duplicated. This is the guard that the duplication is exact: a
    divergence is one policy meaning two different things depending on which
    layer answered, which is worse than either copy alone.
    """
    flask_csp = client.get("/api/auth/status").headers.get("Content-Security-Policy")
    assert flask_csp, (
        "Flask stopped sending a CSP; the edge copy is now the only one and "
        "nothing here would notice it going stale"
    )
    assert _header_pairs()["Content-Security-Policy"] == flask_csp, (
        "the edge and Flask CSPs have diverged; update both copies, not one"
    )


# ==================== 4-B2: the 415 handler ====================

def _handler_spec():
    """Flask 3.1 registry: `spec[None][<int code>]` -> `{exception class: func}`.

    A *by-code* registration lands under an **int** key; a *by-class* one lands
    under the `None` key of the same dict. Both are read explicitly here so the
    "no shadowing" claim below is checkable rather than assumed — and so the
    test fails loudly if Flask changes the shape instead of quietly passing on
    an empty dict.
    """
    return flask_app.app.error_handler_spec[None]


def _by_code():
    return {k: next(iter(v.values())) for k, v in _handler_spec().items()
            if isinstance(k, int)}


def _by_class():
    """Codes are ints; the only other entry is the `None` bucket that holds a
    by-class registration (`Exception`)."""
    return {k: next(iter(v.values())) for k, v in _handler_spec().items()
            if not isinstance(k, int)}


def test_415_is_registered_by_code_with_its_own_handler():
    spec = _handler_spec()
    assert 415 in spec, (
        f"415 is unregistered, so a non-JSON Content-Type falls through to the "
        f"catch-all Exception handler and answers 500. Codes present: "
        f"{sorted(k for k in spec if isinstance(k, int))}"
    )
    handler = _by_code()[415]
    assert handler.__name__ == "_unsupported_media_type"


def test_415_is_not_registered_by_class():
    """By code, for the same reason the 400 handler is: Flask consults the
    by-code bucket first and the by-class bucket second, matching the latter by
    walking the exception's MRO — and it consults it for *every* status with no
    code handler of its own. Registering `HTTPException` by class, say, would
    capture every unhandled 4xx into one response shape. This asserts the 415
    change added no by-class entry at all."""
    assert set(_by_class()) == {None}, (
        f"a new by-class handler appeared: {sorted(map(str, _by_class()))}"
    )
    assert _by_class()[None].__name__ == "_handle_exception"


def test_no_two_codes_share_one_handler():
    """A shadowing symptom: if two statuses resolved to the same function, one
    registration has quietly replaced the other."""
    by_code = _by_code()
    names = {}
    for code, handler in by_code.items():
        names.setdefault(handler.__name__, []).append(code)
    shared = {n: c for n, c in names.items() if len(c) > 1}
    assert not shared, f"one handler serving several statuses: {shared}"


@pytest.mark.parametrize("code, name", [
    (400, "_bad_request"),
    (404, "_not_found"),
    (405, "_method_not_allowed"),
    (413, "_too_large"),
    (429, "_rate_limit_exceeded"),
    (500, "_server_error"),
])
def test_the_pre_existing_handlers_still_resolve_to_their_own_function(code, name):
    by_code = _by_code()
    handler = by_code.get(code)
    assert handler is not None, f"{code} lost its handler entirely: {sorted(by_code)}"
    assert handler.__name__ == name, (
        f"{code} no longer resolves to {name}: adding the 415 handler moved it "
        f"to {handler.__name__}"
    )


# ---------- the behaviour that matters ----------

@pytest.mark.parametrize("content_type", ["text/plain", None])
def test_non_json_content_type_answers_415_not_500(admin_client, content_type):
    """The user-visible contract: a client that POSTs to a JSON route with the
    wrong (or no) Content-Type is told what to fix.

    This is *not* reachable only for an authenticated caller, contrary to what
    this docstring claimed before the gate. Three routes in `_PUBLIC_API` —
    `/api/auth/login`, `/api/auth/forgot-password`, `/api/auth/reset-password` —
    take `_gate_api`'s `None` path, so their bodies run `request.get_json()`
    before any credential exists. Those three unauthenticated calls used to reach
    the catch-all handler and answer 500; the honest status is the change, on
    exactly the surface this phase exists for.
    `test_a_public_route_answers_415_unauthenticated` pins that.

    `Content-Type: None` sends no type at all, the other half of "wrong/absent".
    """
    r = admin_client.post("/api/companies", data='{"name": "WrongType"}',
                          content_type=content_type)
    assert r.status_code == 415, r.get_json()
    assert r.is_json
    assert set(r.get_json()) == {"error"}, (
        f"body shape drifted from the other handlers: {r.get_json()}"
    )


@pytest.mark.parametrize("path", [
    "/api/auth/login",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
])
def test_a_public_route_answers_415_unauthenticated(client, path):
    """The three unauthenticated 500s this change removes, one test each.

    Each path is in `_PUBLIC_API`, so `_gate_api` returns `None` and the route
    body parses the request before authenticating anything. Before the 415
    handler these answered 500 — "server fault, retry" for a permanent client
    bug, on the surface a pre-auth caller reaches holding no credential at all.
    That improvement is the point of the phase, so it is asserted rather than
    described.
    """
    r = client.post(path, data='{"x": 1}', content_type="text/plain")
    assert r.status_code == 415, r.get_json()
    assert r.get_json() == {
        "error": "unsupported media type (expected application/json)"
    }


def test_non_json_content_type_is_415_for_an_operator_route_too(user_client):
    """Not route- or role-specific: the refusal comes from `request.get_json()`
    in the route body, so it is a property of the app's body parsing, not of one
    admin write."""
    r = user_client.post("/api/sales", data='{"sale": {}}',
                         content_type="text/plain")
    assert r.status_code == 415, r.get_json()


def test_415_writes_nothing(admin_client, db):
    """A rejected body must not have half-applied. `POST /api/companies` creates
    the row after the parse, so the count must be unchanged."""
    before = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    assert admin_client.post("/api/companies", data='{"name": "NeverMade"}',
                             content_type="text/plain").status_code == 415
    assert db.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == before
    assert db.execute(
        "SELECT COUNT(*) FROM companies WHERE name = 'NeverMade'").fetchone()[0] == 0


def test_a_json_body_that_fails_validation_is_still_400_with_details(admin_client):
    """The 415 must not swallow the ordinary validation path: a well-formed JSON
    body with the wrong shape is still bad data, and still 400 + `details[]`."""
    r = admin_client.post("/api/companies", json={"not_a_field": 1})
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    assert "details" in body and body["details"]


def test_undecodable_json_body_is_still_400(admin_client):
    r = admin_client.post("/api/companies", data=b"{not json",
                          content_type="application/json")
    assert r.status_code == 400
    assert r.get_json() == {"error": "Invalid request body"}


def test_a_missing_static_asset_is_still_404_json(client):
    """The 404 handler's reachable live path, plus a note on the one it is not.

    An unknown `/api/...` path never reaches this handler, but the reason is
    worth stating precisely because the obvious version of it is wrong. For an
    **unauthenticated** caller the answer is 401, not 200: `_gate_api`
    short-circuits in `before_request`, before routing runs, so no route is ever
    consulted and no 404 can be produced. For an **authenticated** caller it is
    200 carrying the SPA's `index.html`, because `/<path:path>` matches every
    path and the typo falls into `serve_static`.

    That is pre-existing behaviour, out of scope for this phase — which is why
    the assertion below uses `/static/`. Neither case leaks anything across
    tenants and neither bypasses a gate; the practical cost is that a typo'd API
    path from the SPA returns HTML, so `r.json()` raises a parse error where a
    404 would have been clearer.
    """
    r = client.get("/static/definitely-missing.js")
    assert r.status_code == 404, r.status_code
    assert r.get_json() == {"error": "not found"}


def test_wrong_method_on_an_api_route_is_still_405(admin_client):
    r = admin_client.post("/api/sales/no-such-sale/invoices", json={})
    assert r.status_code == 405
    assert r.get_json() == {"error": "method not allowed"}


def test_oversized_body_is_still_413(admin_client):
    original = flask_app.app.config["MAX_CONTENT_LENGTH"]
    flask_app.app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    try:
        r = admin_client.post("/api/companies", data=b"x" * (1024 * 1024 + 1024),
                              content_type="application/json")
        assert r.status_code == 413
        assert "max 1MB" in r.get_json()["error"]
    finally:
        flask_app.app.config["MAX_CONTENT_LENGTH"] = original


# 429 is asserted by registry (`test_the_pre_existing_handlers_still_resolve_to_their_own_function`)
# rather than live: proving it live costs a 16-request burst against a 15/min
# bucket keyed `user:1`, which would leave the counter spent for
# `tests/test_rate_limits.py::test_heavy_route_429_and_json`, which runs later
# in the same process. That test is the live 429 coverage and is re-run by the
# full suite; a second burst here would make it pass or fail on ordering.
