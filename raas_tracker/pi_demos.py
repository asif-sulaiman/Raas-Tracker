"""PI demo recipes — Slice 3 service: per-PI planning formulations + net purchasing.

A demo recipe is a *plan*, not a production order. It belongs to one proforma
invoice, it can be drafted the moment the PI exists (no LC, no invoice, no
recipe), and it touches nothing: not stock, not production, not the recipe
register. Its whole purpose is the question "how much of each chemical do I
still have to buy across all unfinished PIs", which is only answerable while
the PI is still open.

Three invariants hold this module together:

* **Two working sets, one counter each.** ``scope='draft'`` rows are the editor's
  scratchpad and move ``draft_revision``; ``scope='active'`` rows are the sole
  operational snapshot and are replaced only by a successful activation, which
  stamps ``active_revision`` with the draft revision it came from. There is no
  JSONB formula column: the active child rows ARE the snapshot, and every
  activation audits old and new inside the same transaction.
* **Activation is a gate over the PI, not over the shelf.** A positive-target PI
  product must have a balanced formulation with reachable units. Stock shortage
  is deliberately NOT a failure — forecasting shortages is the feature. A
  refused gate writes nothing, so the previous active snapshot survives.
* **The projection is read-only and bounded.** One snapshot, a fixed handful of
  bulk queries (never one per PI), and every figure either exact or ``None`` —
  an unknown is never published as a zero.

Demand semantics (shared with the gate so the two can never disagree):

    line target  = ordered quantity, or min(ordered, non-void invoiced) when the
                   line is marked won't-ship (a retired remainder is still owed)
    remaining    = max(target - produced, 0)          floored per PI and product
    required     = SUM(remaining x percentage/100)     converted to chemical unit
    projected    = max(SUM(required) - current_qty, 0) stock spent ONCE per chemical

Water is never purchased. ``sales.stage = 'completed'`` is the only exclusion
predicate; ``paid`` is an invoice status and shipment proves nothing.
"""
import json
import math
from typing import Any, Optional

import psycopg
from psycopg import pq

from .db import logger, _timeout_statements
from .audit import log_audit_action
from ._numeric import finite_values
from ._exceptions import LifecycleConflict, ResourceMissing, ServiceError

# Mirrors ``recipes._BALANCE_TOLERANCE``: ingredients + water must reach 100
# within a hundredth of a percentage point. The ``+1e-9`` is the same binary
# float guard recipes.py uses (100 - 99.99 is 0.010000000000005).
_BALANCE_TOLERANCE = 0.01
_BALANCE_FLOAT_GUARD = 1e-9

_COMPLETED_STAGE = "completed"
_OPEN_SALE = "COALESCE(s.stage, '') <> 'completed'"

_DEMO_COLS = ("d.id, d.sale_id, d.draft_revision, d.active_revision, "
              "d.activated_at, d.activated_by")

# The per-line target rule, in ONE bulk read: the won't-ship remainder needs the
# non-void invoiced quantity, and the voided-invoice semantics are the same
# ``status <> 'void'`` predicate sales.py uses for the commercial report.
_PROGRESS_SQL = (
    "SELECT si.sale_id, s.pi_number, si.id, si.product_name, si.unit, "
    "COALESCE(si.quantity, 0), COALESCE(si.wont_ship, 0), inv.invoiced_qty "
    "FROM sale_items si "
    "JOIN sales s ON s.id = si.sale_id "
    "LEFT JOIN (SELECT ii.sale_item_id, SUM(ii.quantity) AS invoiced_qty "
    "FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id "
    "WHERE i.status <> 'void' GROUP BY ii.sale_item_id) inv "
    "ON inv.sale_item_id = si.id ")

# Activated demo rows of OPEN PIs. Completed PIs are excluded here rather than
# filtered later, so the snapshot never carries demand that must not be bought.
_PLAN_PRODUCTS_SQL = (
    "SELECT d.sale_id, p.id, p.product_name, p.unit, p.water_percentage "
    "FROM pi_demos d "
    "JOIN pi_demo_products p ON p.demo_id = d.id AND p.scope = 'active' "
    "JOIN sales s ON s.id = d.sale_id AND " + _OPEN_SALE + " "
    "WHERE d.active_revision IS NOT NULL ORDER BY p.id")

_PLAN_ITEMS_SQL = (
    "SELECT p.id, i.chemical_id, c.name, c.unit, i.percentage "
    "FROM pi_demo_items i "
    "JOIN pi_demo_products p ON p.id = i.demo_product_id AND p.scope = 'active' "
    "JOIN pi_demos d ON d.id = p.demo_id AND d.active_revision IS NOT NULL "
    "JOIN sales s ON s.id = d.sale_id AND " + _OPEN_SALE + " "
    "JOIN chemicals c ON c.id = i.chemical_id ORDER BY i.id")

# Provenance: (sale, run) pairs from the links, both the direct sale_id and the
# invoice route, deduplicated FIRST — a run reachable through two invoices of
# one PI is still one output, and joining outputs through the link rows would
# fan the quantity out. An output reachable from two different sales cannot be
# attributed to either and is reported unknown instead of credited.
_PRODUCED_SQL = (
    "SELECT l.sale_id, o.id, o.run_id, o.product_name, o.unit, o.qty_produced "
    "FROM (SELECT DISTINCT x.run_id, x.sale_id FROM ("
    "SELECT l.run_id, l.sale_id FROM production_run_links l "
    "WHERE l.sale_id IS NOT NULL "
    "UNION ALL "
    "SELECT l.run_id, i.sale_id FROM production_run_links l "
    "JOIN invoices i ON i.id = l.invoice_id WHERE i.sale_id IS NOT NULL"
    ") x) l "
    "JOIN production_run_outputs o ON o.run_id = l.run_id "
    "JOIN sales s ON s.id = l.sale_id AND " + _OPEN_SALE + " ")


class DemoGateFailure(ServiceError):
    """400 — the plan is well-formed JSON but cannot be activated.

    Carries ``details`` in the API's ``[{field, message}]`` shape, with the
    offending product name in the field, because "which product is broken" is
    the only part of a rejected formulation the editor can act on.
    """

    status_code = 400

    def __init__(self, message: str, details: list):
        super().__init__(message)
        self.details = [{"field": field, "message": text} for field, text in details]


# --------------------------------------------------------------------------- #
# primitives
# --------------------------------------------------------------------------- #
def _iso(value: Any) -> Any:
    """date/datetime -> ISO string for JSON; anything else passes through."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else value


def _identity(name: Any) -> str:
    """The trimmed, case-insensitive product identity used everywhere."""
    return (name or "").strip().lower()


def _unit_key(value: Any) -> str:
    return (value or "").strip().upper()


def _num(value: Any) -> Optional[float]:
    """A JSON-safe float, or None. Never NaN, never Infinity.

    NUMERIC columns arrive as Decimal and REAL as float; either can be absent.
    A non-finite value would poison every SUM downstream and be unserializable,
    so it leaves here as ``None`` = unknown rather than as a plausible zero.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _label(name: Any) -> str:
    """A product name as it should appear in an error field."""
    return json.dumps((name or "").strip())


def _field(name: Any, *parts: str) -> str:
    return "products[%s]%s" % (_label(name), "".join("." + p for p in parts))


def _factor(graph: dict, src: Any, dst: Any) -> Optional[float]:
    """The conversion from one unit to another, or None when unreachable.

    Searched over an undirected graph (every seeded row is read with its
    reciprocal) and resolved breadth-first, so ``KG -> DRUM`` works from the
    seeded ``DRUM -> KG`` and a two-hop path is found when the register only
    stores one. Returning ``None`` is the whole point: the caller turns an
    unreachable figure into ``None``, never into zero.
    """
    a, b = _unit_key(src), _unit_key(dst)
    if not a or not b:
        return None
    if a == b:
        return 1.0
    if a not in graph or b not in graph:
        return None
    seen = {a}
    queue = [(a, 1.0)]
    while queue:
        node, acc = queue.pop(0)
        edges = graph.get(node) or {}
        for nxt in sorted(edges):
            if nxt in seen:
                continue
            total = acc * edges[nxt]
            if nxt == b:
                return total
            seen.add(nxt)
            queue.append((nxt, total))
    return None


def _conversion_graph(conn) -> dict:
    """All unit conversions as one undirected graph, in one query."""
    graph: dict = {}
    for src, dst, raw in conn.execute(
            "SELECT from_unit, to_unit, factor FROM unit_conversions").fetchall():
        factor = _num(raw)
        a, b = _unit_key(src), _unit_key(dst)
        if not factor or factor <= 0 or not a or not b:
            continue
        graph.setdefault(a, {})[b] = factor
        graph.setdefault(b, {})[a] = 1.0 / factor
    return graph


def _demo_dict(row) -> dict:
    return {"id": row[0], "sale_id": row[1], "draft_revision": row[2],
            "active_revision": row[3], "activated_at": _iso(row[4]),
            "activated_by": row[5]}


def _formulation(conn, demo_id: int, scope: str) -> list:
    """Product rows of one scope, each with its ingredient rows. Read-only."""
    products = [{"id": r[0], "product_name": r[1], "unit": r[2],
                 "water_percentage": _num(r[3]), "items": []}
                for r in conn.execute(
                    "SELECT id, product_name, unit, water_percentage "
                    "FROM pi_demo_products WHERE demo_id = %s AND scope = %s "
                    "ORDER BY id", (demo_id, scope)).fetchall()]
    if products:
        by_id = {p["id"]: p for p in products}
        for row in conn.execute(
                "SELECT i.demo_product_id, i.chemical_id, c.name, c.unit, "
                "i.percentage FROM pi_demo_items i "
                "JOIN chemicals c ON c.id = i.chemical_id "
                "WHERE i.demo_product_id = ANY(%s) ORDER BY i.id",
                (list(by_id),)).fetchall():
            by_id[row[0]]["items"].append(
                {"chemical_id": row[1], "chemical": row[2], "unit": row[3],
                 "percentage": _num(row[4])})
    return products


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #
def _demo_progress(conn, sale_id: int) -> list:
    """What this PI still owes, product by product, for the editor's header.

    One row per grouped product identity, in the canonical unit of its first
    PI line: ordered, retained target, produced, remaining. Any figure whose
    arithmetic is not available — an unreachable conversion, an output that
    cannot be attributed to this PI alone — is ``None``, so the editor never
    shows a number it cannot stand behind.
    """
    graph = _conversion_graph(conn)
    targets = _pi_progress(conn, graph, sale_id=sale_id).get(sale_id, {})
    products = targets.get("products", {})
    produced_by = _produced(conn, sale_id=sale_id)
    rows: list = []
    for ident in sorted(products):
        product = products[ident]
        known = not product["unknown"]
        produced, broken = _output_credit(
            produced_by.get((sale_id, ident), ()), graph, product["unit"])
        remaining = (max(product["target"] - produced, 0.0)
                     if known and not broken else None)
        rows.append({
            "product_name": product["name"], "unit": product["unit"],
            "ordered_quantity": product["ordered"],
            "target_quantity": (round(product["target"], 6) if known else None),
            "produced_quantity": (round(produced, 6) if not broken else None),
            "remaining_quantity": (round(remaining, 6) if remaining is not None
                                   else None)})
    return rows


def get_demo(conn, sale_id: int) -> Optional[dict]:
    """The demo of one PI with both working sets, or None.

    Strictly a read: no demo, no formulation row, no revision is ever created
    here. An auto-created empty demo would silently convert "nobody has planned
    this PI" into "this PI is planned with nothing in it", which is the one
    state the coverage report must keep surfacing.
    """
    row = conn.execute("SELECT " + _DEMO_COLS + " FROM pi_demos d "
                       "WHERE d.sale_id = %s", (sale_id,)).fetchone()
    if row is None:
        return None
    demo = _demo_dict(row)
    demo["products"] = _formulation(conn, demo["id"], "draft")
    demo["active_products"] = _formulation(conn, demo["id"], "active")
    header = conn.execute("SELECT s.pi_number, COALESCE(c.name, s.client_name) "
                          "FROM sales s LEFT JOIN companies c ON c.id = s.company_id "
                          "WHERE s.id = %s", (sale_id,)).fetchone()
    demo["pi_number"] = header[0] if header else None
    demo["client_name"] = header[1] if header else None
    demo["progress"] = _demo_progress(conn, sale_id)
    return demo


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #
def create_demo(conn, sale_id: int, *, actor: str) -> Optional[dict]:
    """Open a demo for a PI that has none. Works the moment a PI does.

    No LC and no invoice is required, and none is looked up: planning a purchase
    before the letter of credit exists is the reason this feature is here. The
    draft seeds one product per PI line (trimmed case-insensitive identity),
    with that line's unit, no ingredients and auto water — the editor opens on
    the PI's own shape rather than an empty grid.
    """
    sale = conn.execute("SELECT id, pi_number FROM sales WHERE id = %s",
                        (sale_id,)).fetchone()
    if sale is None:
        raise ResourceMissing("PI not found")
    try:
        row = conn.execute("INSERT INTO pi_demos (sale_id) VALUES (%s) "
                           "RETURNING " + _DEMO_COLS.replace("d.", ""),
                           (sale_id,)).fetchone()
        demo_id = row[0]
        seeded = conn.execute(
            "SELECT DISTINCT ON (lower(trim(product_name))) product_name, unit "
            "FROM sale_items WHERE sale_id = %s "
            "ORDER BY lower(trim(product_name)), id", (sale_id,)).fetchall()
        for name, unit in seeded:
            conn.execute("INSERT INTO pi_demo_products (demo_id, scope, "
                         "product_name, unit) VALUES (%s, 'draft', %s, %s)",
                         (demo_id, (name or "").strip(), unit))
        new_value = json.dumps({"sale_id": sale_id, "pi_number": sale[1],
                                "products": [[r[0], r[1]] for r in seeded]})
        log_audit_action(conn, "PI_DEMO_CREATE", "pi_demo", demo_id, user_id=actor,
                         new_value=new_value, atomic=False)
        conn.commit()
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()
        raise ResourceMissing("PI not found")
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise LifecycleConflict("this PI already has a demo recipe")
    except Exception:
        # Nothing partial may survive: a demo without its seeded draft rows, or
        # a draft without its audit, would both read as a planned PI.
        conn.rollback()
        raise
    logger.info("pi demo created id=%s sale=%s by=%s", row[0], sale_id, actor)
    return get_demo(conn, sale_id)


# --------------------------------------------------------------------------- #
# draft
# --------------------------------------------------------------------------- #
def _validate_products(conn, products: list) -> list:
    """Draft-shape validation: resolvable, non-contradictory, in range.

    Returns ``[{product_name, unit, water_percentage, items:[{chemical_id,...}]}]``.
    Deliberately laxer than the activation gate: a zero percentage and an empty
    ingredient list are legal draft state ("not decided yet"), while an
    unresolvable chemical, a duplicate chemical, a name that cannot be a
    primary key, or an out-of-range number would break a CHECK or the unique
    expression index mid-write, so they are refused here as field errors (400).
    """
    known: dict = {}
    for row in conn.execute("SELECT id, name, unit FROM chemicals").fetchall():
        known.setdefault(_identity(row[1]), {"chemical_id": row[0],
                                             "name": row[1], "unit": row[2]})
    cleaned: list = []
    details: list = []
    seen: set = set()
    for entry in products:
        name = (entry.get("product_name") or "").strip()
        unit = (entry.get("unit") or "").strip()
        if not _identity(name):
            details.append(("products[].product_name",
                            "must not be empty once trimmed"))
            continue
        ident = _identity(name)
        if ident in seen:
            details.append((_field(name),
                            "is listed twice; one formulation per product"))
            continue
        seen.add(ident)
        if not unit:
            details.append((_field(name, "unit"), "is required"))
            continue
        water = _num(entry.get("water_percentage"))
        if entry.get("water_percentage") is not None:
            if water is None or not 0.0 <= water <= 100.0:
                details.append((_field(name, "water_percentage"),
                                "must be a finite percentage between 0 and 100"))
                continue
        items: list = []
        chemicals_seen: set = set()
        for item in entry.get("items") or []:
            chem = known.get(_identity(item.get("chemical")))
            if chem is None:
                details.append((_field(name, "items[%s]" % _label(item.get("chemical"))),
                                "is not a known chemical"))
                continue
            percentage = _num(item.get("percentage"))
            if percentage is None or not 0.0 <= percentage <= 100.0:
                details.append((_field(name, "items[%s]" % _label(chem["name"]),
                                       "percentage"),
                                "must be a finite percentage between 0 and 100"))
                continue
            if chem["chemical_id"] in chemicals_seen:
                details.append((_field(name, "items[%s]" % _label(chem["name"])),
                                "is included twice in this product"))
                continue
            chemicals_seen.add(chem["chemical_id"])
            items.append({"chemical_id": chem["chemical_id"],
                          "chemical": chem["name"], "unit": chem["unit"],
                          "percentage": percentage})
        cleaned.append({"product_name": name, "unit": unit,
                        "water_percentage": water, "items": items})
    if details:
        raise DemoGateFailure("the demo draft was rejected", details)
    return cleaned


def save_draft(conn, sale_id: int, *, expected_revision: int, products: list,
               actor: str) -> Optional[dict]:
    """Replace the draft scope wholesale and move ``draft_revision``.

    The active scope is never touched: an unfinished edit must not suspend the
    snapshot the plant is buying against. The revision is optimistic locking in
    the only sense that is honest here — the caller states the revision it read,
    and a loser is told rather than silently overwriting the winner.
    """
    finite_values(*(p.get("water_percentage") for p in products),
                  *(i.get("percentage") for p in products for i in (p.get("items") or [])))
    cleaned = _validate_products(conn, products)
    try:
        row = conn.execute("SELECT " + _DEMO_COLS + " FROM pi_demos d "
                           "WHERE d.sale_id = %s FOR UPDATE OF d",
                           (sale_id,)).fetchone()
        if row is None:
            raise ResourceMissing("demo recipe not found")
        demo = _demo_dict(row)
        if demo["draft_revision"] != expected_revision:
            raise LifecycleConflict(
                "draft revision conflict: this draft is now at revision %s, "
                "you saved against %s" % (demo["draft_revision"], expected_revision))
        old_value = json.dumps(_formulation(conn, demo["id"], "draft"))
        conn.execute("DELETE FROM pi_demo_products WHERE demo_id = %s "
                     "AND scope = 'draft'", (demo["id"],))
        for entry in cleaned:
            product_id = conn.execute(
                "INSERT INTO pi_demo_products (demo_id, scope, product_name, "
                "unit, water_percentage) VALUES (%s, 'draft', %s, %s, %s) "
                "RETURNING id",
                (demo["id"], entry["product_name"], entry["unit"],
                 entry["water_percentage"])).fetchone()[0]
            if entry["items"]:
                # psycopg3 connections run in transactional mode here, which
                # has no conn.executemany — the cursor does.
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO pi_demo_items (demo_product_id, "
                        "chemical_id, percentage) VALUES (%s, %s, %s)",
                        [(product_id, item["chemical_id"], item["percentage"])
                         for item in entry["items"]])
        updated = conn.execute(
            "UPDATE pi_demos SET draft_revision = draft_revision + 1, "
            "updated_at = now() WHERE id = %s RETURNING " + _DEMO_COLS.replace("d.", ""),
            (demo["id"],)).fetchone()
        log_audit_action(conn, "PI_DEMO_DRAFT_SAVE", "pi_demo", demo["id"],
                         user_id=actor, old_value=old_value,
                         new_value=json.dumps(cleaned), atomic=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("pi demo draft saved id=%s revision=%s by=%s",
                demo["id"], updated[2], actor)
    return get_demo(conn, sale_id)


# --------------------------------------------------------------------------- #
# activation gate
# --------------------------------------------------------------------------- #
def _pi_progress(conn, graph: dict, *, sale_id: Optional[int] = None,
                 open_only: bool = False) -> dict:
    """Every PI's per-product effective target, from the same rule the gate and
    the projection share.

    ``{sale_id: {"pi_number", "products": {identity: {"name", "unit", "target",
    "ordered", "unknown"}}}}``. Lines are computed BEFORE grouping (a won't-ship
    line keeps ``min(ordered, non-void invoiced)``, which is not zero) and then
    grouped by trimmed case-insensitive identity, converted into that identity's
    own first line unit so two lines of one product in different units are one
    demand. ``ordered`` is the raw PI quantity in the same canonical unit and is
    only ever shown to a human; ``target`` is what the demand arithmetic uses.

    A unit that cannot be reached makes the product ``unknown`` rather than
    wrong: the gate treats unknown as "needs a formulation" (fail closed) and
    the projection publishes it as ``None``.
    """
    sql = _PROGRESS_SQL
    params: tuple = ()
    if sale_id is not None:
        sql += "WHERE si.sale_id = %s "
        params = (sale_id,)
    elif open_only:
        sql += "WHERE " + _OPEN_SALE + " "
    sql += "ORDER BY si.sale_id, si.id"
    out: dict = {}
    for row in conn.execute(sql, params).fetchall():
        sid, pi_number, _item_id, name, unit = row[0], row[1], row[2], row[3], row[4]
        quantity = _num(row[5]) or 0.0
        wont_ship = int(row[6] or 0) == 1
        invoiced = _num(row[7])
        if wont_ship:
            # The retained remainder: billed-then-retired work is still owed,
            # but only as far as it was ever invoiced.
            target = min(quantity, invoiced or 0.0)
        else:
            target = quantity
        entry = out.setdefault(sid, {"pi_number": pi_number, "products": {}})
        ident = _identity(name)
        product = entry["products"].get(ident)
        if product is None:
            product = {"name": (name or "").strip(),
                       "unit": (unit or "").strip(), "target": 0.0,
                       "ordered": 0.0, "unknown": False}
            entry["products"][ident] = product
        # Lines group under the first line's unit, so every line's quantity is
        # read in the canonical unit of the identity it belongs to.
        factor = _factor(graph, unit, product["unit"])
        product["ordered"] += quantity * (factor or 0.0)
        if factor is None:
            product["unknown"] = True
        else:
            product["target"] = max(product["target"] + target * factor, 0.0)
    for entry in out.values():
        for product in entry["products"].values():
            product["ordered"] = (None if product["unknown"]
                                  else round(product["ordered"], 6))
    return out


def _check_product(product: dict, graph: dict, details: list) -> Optional[float]:
    """Validate one formulation row for activation; return its effective water.

    Rules, all of them about the FORMULA: at least one ingredient (no
    ingredients means unresolved water, and auto-filling 100% water would
    activate a plan that buys nothing), positive finite percentages, one row per
    chemical, ingredients + effective water = 100 within tolerance, and a
    reachable conversion from the product unit to every chemical unit.
    """
    name = product["product_name"]
    items = product["items"]
    if not items:
        details.append((_field(name, "items"),
                        "needs at least one ingredient; water would be unresolved"))
        return None
    percentages = [item["percentage"] for item in items]
    if any(p is None or p <= 0 or p > 100 for p in percentages):
        details.append((_field(name, "items[].percentage"),
                        "every included ingredient must be greater than 0 and at "
                        "most 100"))
        return None
    total = sum(p for p in percentages if p is not None)
    water = product["water_percentage"]
    if water is None:
        # Auto water resolves at activation and is then STORED, because the
        # active scope is the operational formula.
        water = round(100.0 - total, 6)
    if not 0.0 <= water <= 100.0:
        details.append((_field(name, "water_percentage"),
                        "resolves to %s%%, outside 0..100" % (round(water, 6))))
        return None
    if abs(total + water - 100.0) > _BALANCE_TOLERANCE + _BALANCE_FLOAT_GUARD:
        details.append((_field(name, "water_percentage"),
                        "ingredients total %s%% and water %s%%, which is not 100%%"
                        % (round(total, 6), round(water, 6))))
        return None
    for item in items:
        if _factor(graph, product["unit"], item["unit"]) is None:
            details.append((_field(name, "unit"),
                            "cannot be converted to %s (%s): no unit conversion "
                            "from %s to %s" % (item["chemical"], item["unit"],
                                               product["unit"], item["unit"])))
            return None
    return water


def activate_demo(conn, sale_id: int, *, expected_revision: int,
                  actor: str) -> Optional[dict]:
    """Replace the active snapshot with the validated draft, or change nothing.

    The gate runs entirely on reads, so every failure path rolls back a
    transaction that never wrote: the previous active rows, the activation
    stamp and ``active_revision`` survive a refusal untouched. Stock is never
    consulted — a forecast shortage is the output of this feature, not a reason
    to refuse it.
    """
    try:
        # Lock the PI first, then the demo: the only order that cannot deadlock
        # against a sibling PI write, which takes the same pair in the same way.
        if conn.execute("SELECT id FROM sales WHERE id = %s FOR UPDATE",
                        (sale_id,)).fetchone() is None:
            raise ResourceMissing("PI not found")
        row = conn.execute("SELECT " + _DEMO_COLS + " FROM pi_demos d "
                           "WHERE d.sale_id = %s FOR UPDATE OF d",
                           (sale_id,)).fetchone()
        if row is None:
            raise ResourceMissing("demo recipe not found")
        demo = _demo_dict(row)
        if demo["draft_revision"] != expected_revision:
            # Checked BEFORE the gate: a stale tab must lose with a 409 even if
            # its content happens to describe a currently valid formula.
            raise LifecycleConflict(
                "draft revision conflict: this draft is now at revision %s, "
                "you activated against %s" % (demo["draft_revision"], expected_revision))

        graph = _conversion_graph(conn)
        # The gate reads the PI as it is NOW, at any stage: completion removes a
        # PI from demand, it does not forbid editing or re-activating its plan.
        progress = _pi_progress(conn, graph, sale_id=sale_id)
        targets = progress.get(sale_id, {}).get("products", {})
        draft = _formulation(conn, demo["id"], "draft")
        by_ident = {_identity(p["product_name"]): p for p in draft}

        details: list = []
        waters: dict = {}
        for ident, target in targets.items():
            if not target["unknown"] and target["target"] <= 0:
                continue          # wholly retired: no demand, no formulation owed
            product = by_ident.get(ident)
            if product is None:
                details.append((_field(target["name"]),
                                "has no demo formulation, but this PI still owes "
                                "%s %s of it" % (round(target["target"], 6),
                                                 target["unit"] or "units")))
                continue
            water = _check_product(product, graph, details)
            if water is not None:
                waters[product["id"]] = water
        for product in draft:
            ident = _identity(product["product_name"])
            if ident not in targets:
                details.append((_field(product["product_name"]),
                                "is not a product of this PI; refusing to invent "
                                "a mapping onto a similar name"))
            elif product["id"] not in waters and (
                    targets[ident]["unknown"] or targets[ident]["target"] > 0):
                pass              # already reported as an invalid formulation
        if details:
            raise DemoGateFailure("the demo recipe cannot be activated", details)

        old_value = json.dumps(_formulation(conn, demo["id"], "active"))
        conn.execute("DELETE FROM pi_demo_products WHERE demo_id = %s "
                     "AND scope = 'active'", (demo["id"],))
        for product in draft:
            target = targets.get(_identity(product["product_name"]))
            if target is None or (not target["unknown"]
                                  and target["target"] <= 0):
                # Not this PI's product, or wholly retired work: neither belongs
                # in the operational formula, and snapshotting it would put a
                # retired product back on the buying list.
                continue
            # `waters` covers every product the gate above required a formulation
            # for, and an invalid one already raised DemoGateFailure.
            new_id = conn.execute(
                "INSERT INTO pi_demo_products (demo_id, scope, product_name, "
                "unit, water_percentage) VALUES (%s, 'active', %s, %s, %s) "
                "RETURNING id",
                (demo["id"], product["product_name"], product["unit"],
                 waters[product["id"]])).fetchone()[0]
            if product["items"]:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO pi_demo_items (demo_product_id, "
                        "chemical_id, percentage) VALUES (%s, %s, %s)",
                        [(new_id, item["chemical_id"], item["percentage"])
                         for item in product["items"]])
        updated = conn.execute(
            "UPDATE pi_demos SET active_revision = draft_revision, "
            "activated_at = now(), activated_by = %s, updated_at = now() "
            "WHERE id = %s RETURNING " + _DEMO_COLS.replace("d.", ""),
            (actor, demo["id"])).fetchone()
        log_audit_action(conn, "PI_DEMO_ACTIVATE", "pi_demo", demo["id"],
                         user_id=actor, old_value=old_value,
                         new_value=json.dumps(_formulation(conn, demo["id"], "active")),
                         atomic=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("pi demo activated id=%s revision=%s by=%s",
                demo["id"], updated[3], actor)
    return get_demo(conn, sale_id)


# --------------------------------------------------------------------------- #
# the net purchasing projection
# --------------------------------------------------------------------------- #
def arm_snapshot(conn) -> None:
    """Ask Postgres for the one snapshot a forecast must be read from.

    Only legal before the transaction's first data query, so a caller that owns
    its transaction and needs the guarantee — the inventory route, which reads
    and writes nothing — re-arms its timeouts and calls this before calling the
    projection. The literal lives here because the isolation rule is this
    module's, not the HTTP layer's.
    """
    conn.execute("SET LOCAL transaction_isolation = 'repeatable read'")


def begin_read_snapshot(conn) -> None:
    """Put a pure-read request into the transaction a forecast may be computed in.

    A pooled checkout has already spent its statement guards on a READ COMMITTED
    transaction that has queried nothing, and isolation can only be chosen before
    the first data query — so this ends that transaction (which publishes and
    discards nothing, because a forecast writes no rows), re-arms the guards that
    COMMIT handed to the next client, and only then asks for the snapshot.
    """
    conn.commit()
    conn.execute(_timeout_statements())
    arm_snapshot(conn)


def _begin_snapshot(conn) -> None:
    """One REPEATABLE READ snapshot for the whole forecast — when one is still available.

    The projection reads stock, receipt metadata, formulas, PI progress,
    production history and coverage. Under the default READ COMMITTED those are
    eight separate snapshots, and a production plus stock commit landing
    mid-read weaves a forecast that describes an instant that never existed.

    Isolation can only be set BEFORE a transaction's first query, and Postgres
    raises 25001 afterwards, so the branch is decided by the connection's state:
    no transaction open yet gets ``SET LOCAL`` (never a session ``SET`` — this
    database sits behind a TRANSACTION pooler that hands the backend to another
    client on COMMIT); a transaction that is already open is read as it stands,
    because ending it here would publish, or discard, work this call does not
    own. A caller that needs the snapshot and already holds a READ COMMITTED
    transaction — the inventory route, which is a pure read — ends and re-arms
    its own transaction before calling, so the projection always finds the
    isolation it is allowed to assume.

    Either way exactly one statement is issued, so a second projection inside
    the same transaction reads the same snapshot for twice the same cost, and
    the number of statements stays a property of the query shape.
    """
    try:
        status = conn.info.transaction_status
    except AttributeError:
        status = None
    if status == pq.TransactionStatus.INTRANS:
        isolation = str(conn.execute(
            "SELECT current_setting('transaction_isolation')").fetchone()[0])
        if isolation.upper() not in ("REPEATABLE READ", "SERIALIZABLE"):
            # Not a failure: the caller's snapshot is the caller's decision. It
            # is loud, because the forecast below is then not one instant.
            logger.warning("purchasing projection on caller's %s transaction",
                           isolation)
        return
    arm_snapshot(conn)


def _active_plan(conn) -> dict:
    """Activated demo rows of every open PI: {sale_id: {identity: product}}."""
    plan: dict = {}
    for row in conn.execute(_PLAN_PRODUCTS_SQL).fetchall():
        entry = {"id": row[1], "name": (row[2] or "").strip(),
                 "unit": (row[3] or "").strip(),
                 "water_percentage": _num(row[4]), "items": []}
        plan.setdefault(row[0], {})[_identity(row[2])] = entry
    by_product: dict = {}
    for row in conn.execute(_PLAN_ITEMS_SQL).fetchall():
        by_product.setdefault(row[0], []).append(
            {"chemical_id": row[1], "name": row[2], "unit": row[3],
             "percentage": _num(row[4])})
    for products in plan.values():
        for entry in products.values():
            entry["items"] = by_product.get(entry["id"], [])
    return plan


def _produced(conn, sale_id: Optional[int] = None) -> dict:
    """Production outputs attributed to open PIs, grouped by PI and product.

    ``{(sale_id, identity): [output, …]}`` where an output carries the set of
    PIs that can reach it, so the caller can tell one attributable run from an
    output two PIs both claim. Read in ``o.id`` order, which keeps every sum
    below reproducible.
    """
    sql = _PRODUCED_SQL
    params: tuple = ()
    if sale_id is not None:
        sql += "WHERE s.id = %s "
        params = (sale_id,)
    sql += "ORDER BY o.id"
    outputs: dict = {}
    by_sale: dict = {}
    for row in conn.execute(sql, params).fetchall():
        sid, output_id, _run_id, name, unit, qty = row[:6]
        out = outputs.get(output_id)
        if out is None:
            out = {"output_id": output_id, "name": (name or "").strip(),
                   "unit": unit, "qty": _num(qty) or 0.0, "sales": set()}
            outputs[output_id] = out
        out["sales"].add(sid)
        by_sale.setdefault((sid, _identity(name)), []).append(out)
    return by_sale


def _output_credit(outputs, graph: dict, unit: Any) -> tuple:
    """Attributed production of one PI product, in that product's own unit.

    Returns ``(qty, broken)``. ``broken`` means the credit cannot be stated:
    an output reachable from two PIs would pay for whichever one we picked, and
    an output unit that cannot be converted is not a quantity we can subtract.
    """
    produced = 0.0
    broken = False
    for output in outputs:
        if len(output["sales"]) != 1:
            broken = True
            continue
        factor = _factor(graph, output["unit"], unit)
        if factor is None:
            broken = True
            continue
        produced += output["qty"] * factor
    return produced, broken


def project_purchasing(conn) -> dict:
    """Net additional purchasing across every activated demo of every open PI.

    ``projected = max(total remaining requirement - in-house stock, 0)`` — the
    quantity still to BUY, with stock subtracted once per chemical after the
    plans are summed, so two PIs cannot each spend the same drum. Nothing is
    cached, stored or committed; the caller's transaction is left exactly as it
    was, only read from.

    Statement count is a property of the query shape, not of how many PIs are
    open, so one more PI never means one more query.
    """
    _begin_snapshot(conn)
    graph = _conversion_graph(conn)

    chemicals: dict = {}
    for row in conn.execute(
            "SELECT id, name, unit, COALESCE(current_qty, 0), "
            "COALESCE(in_house_registered, TRUE) FROM chemicals "
            "ORDER BY id").fetchall():
        chemicals[row[0]] = {"name": row[1], "unit": row[2],
                             "qty": _num(row[3]) or 0.0, "in_house": bool(row[4])}

    # An activated demo anywhere is the signal that somebody is planning. With
    # none, there is no basis for a number at all, and publishing 0 would tell
    # a buyer "nothing to order" about work nobody has looked at.
    activated = conn.execute("SELECT 1 FROM pi_demos "
                             "WHERE active_revision IS NOT NULL LIMIT 1").fetchone()
    activated = activated is not None
    progress = _pi_progress(conn, graph, open_only=True)
    plan = _active_plan(conn)
    produced_by = _produced(conn)

    required: dict = {}
    unknown: set = set()
    incomplete: dict = {}
    unplanned: dict = {}

    for sid in sorted(progress):
        targets = progress[sid]["products"]
        positives = sorted(ident for ident, t in targets.items()
                           if t["unknown"] or t["target"] > 0)
        products = plan.get(sid)
        if not products:
            if positives:
                unplanned[sid] = [targets[ident]["name"] for ident in positives]
            continue
        for ident in sorted(targets):
            target = targets[ident]
            product = products.get(ident)
            if product is None:
                if target["unknown"] or target["target"] > 0:
                    incomplete.setdefault(sid, set()).add(target["name"])
                continue
            if not target["unknown"] and target["target"] <= 0:
                # Retired work: a leftover formulation must not leak back onto
                # the buying list.
                continue
            produced, broken = _output_credit(
                produced_by.get((sid, ident), ()), graph, target["unit"])
            broken = broken or bool(target["unknown"])
            if broken:
                incomplete.setdefault(sid, set()).add(target["name"])
                for item in product["items"]:
                    unknown.add(item["chemical_id"])
                continue
            remaining = max(target["target"] - produced, 0.0)
            for item in product["items"]:
                factor = _factor(graph, product["unit"], item["unit"])
                percentage = item["percentage"]
                if factor is None or percentage is None:
                    incomplete.setdefault(sid, set()).add(target["name"])
                    unknown.add(item["chemical_id"])
                    continue
                required[item["chemical_id"]] = (
                    required.get(item["chemical_id"], 0.0)
                    + remaining * percentage / 100.0 * factor)

    # A produced name that is no longer one of this PI's lines is an
    # unattributable historical identity: it credits nothing, and saying so is
    # the only honest answer. One pass over the outputs, not one per PI.
    for (out_sid, ident), outputs in produced_by.items():
        if out_sid in progress and ident not in progress[out_sid]["products"]:
            incomplete.setdefault(out_sid, set()).add(outputs[0]["name"])

    materials: list = []
    unstocked: list = []
    for cid in sorted(chemicals):
        chem = chemicals[cid]
        if not activated or cid in unknown:
            need: Optional[float] = None
            buy: Optional[float] = None
        else:
            need = _num(required.get(cid, 0.0))
            buy = _num(max((need or 0.0) - chem["qty"], 0.0))
        row = {"chemical_id": cid, "name": chem["name"], "unit": chem["unit"],
               "current_qty": chem["qty"], "required_quantity": need,
               "projected_quantity": buy}
        if chem["in_house"]:
            materials.append(row)
        elif activated and (cid in unknown or (need or 0.0) > 0):
            # An outward-only identity is not part of Chemical Stock by design,
            # but its demand must survive the filter.
            unstocked.append(row)

    def _rows_by(mapping):
        return [{"sale_id": sid, "pi_number": progress.get(sid, {}).get("pi_number"),
                 "products": sorted(names)} for sid, names in
                sorted(mapping.items())]

    open_unplanned = _rows_by(unplanned)
    incomplete_plans = _rows_by(incomplete)
    # "complete" describes the arithmetic, not the world: it says every demand
    # this forecast claims to cover is covered. Unplanned PIs are listed beside
    # it, and only a forecast with no activated plan behind it at all is
    # declared to be nothing but a list of open questions.
    coverage = {
        "state": ("no_active_plans" if open_unplanned and not activated
                  else "incomplete" if incomplete_plans else "complete"),
        "open_pis_without_plan": open_unplanned,
        "incomplete_plans": incomplete_plans}

    return {"materials": materials, "coverage": coverage,
            "unstocked_requirements": unstocked}
