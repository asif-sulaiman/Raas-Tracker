# Recipe Water % in the View + Un-clipped Record Pickers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a per-product water percentage (and its grand-total calculation) to the recipe **view** only, and make `EntityCombobox`'s suggestion list visible when the field sits inside an `overflow-hidden` card.

**Architecture:** Water moves from the dropped recipe header to the formulation slot (`recipe_products.water_percentage`, schema v9). A NULL value means "whatever is not formulated" (`100 − ingredients`); a stored value overrides it. The detail read returns `water_percentage`, `water_effective` and `grand_total`; a single operator route sets or clears it. The create form stays untouched. Separately, `EntityCombobox` renders its listbox through a React portal with fixed positioning measured from the input, so no ancestor's `overflow: hidden` can clip it.

**Tech Stack:** Flask 3 · psycopg3 · PostgreSQL (Supabase) · Pydantic v2 · React 19 · Vite 8 · Tailwind 4 · Vitest + React Testing Library · oxlint.

**Spec:** `docs/superpowers/specs/2026-11-01-dropdown-combobox-design.md` (picker behaviour). The water decisions come from the approved conversation: **per product card**, **edited inline in the card**, **display only** (a warning, never a block). This plan records them under Global Constraints.

## Global Constraints

- **Scope of water:** water is edited **only in the recipe detail view**, one value per product card. The create form must keep having no product, quantity or water field (an existing test pins this).
- **Display only:** ingredients + water never block saving, producing or booking. The card shows the grand total and an amber warning when it is not 100%; the backend never rejects a non-100 total.
- **`raas_tracker/db.py` is off-limits without approval.** Task 1 edits exactly two things there: the `_SCHEMA_VERSION` constant (8 → 9) and one additive, idempotent `ALTER TABLE`. Approving this plan approves those two edits; nothing else in `db.py` changes.
- Never hardcode secrets, keys, passwords or connection strings. Env vars only. Never touch `.env*`.
- Never disable or delete an access-control check. The new route uses `@operator_or_admin_required` + `@limited()` exactly like the adjacent item routes.
- Always validate server-side with Pydantic and parameterized SQL (`%s`). No f-string SQL.
- Every mutating action is audited (`RECIPE_WATER_SET`, old → new, `atomic=False` inside the caller's transaction).
- TDD: the failing test is written and observed red before the implementation.
- One atomic commit per task, conventional prefix (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- **Never `git push`.** Commits only.
- **Never stage the pre-existing dirty files**: `.github/workflows/ci.yml`, `.opencode/opencode.json`, `AGENTS.md`, `README.md`, `opencode.json`, `raas_tracker/audit.py`, `raas_tracker/cli.py`, `raas_tracker/stock.py`, `raas_tracker/uploads.py`, `requirements-dev.txt`, or the untracked `ruff.toml`, `pyrightconfig.json`, `raas-tracker-frontend/src/components/sales/fetchSaleInvoiceLines.js`. Stage files by name.
- Do not modify anything under `.opencode/`.
- Frontend gate: `npx vitest run`, `npx oxlint`, `npx vite build`, then copy `dist/.` into `react_frontend/`.
- Backend gate: `python -m pytest tests/ -q`, `python -m ruff check .`, `python -m pyright`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `raas_tracker/db.py` | Modify (~line 150, ~line 559, ~line 955) | Schema v9: nullable `recipe_products.water_percentage` |
| `tests/test_recipe_schema_v8.py` | Modify | Pin v9 and the new column |
| `raas_tracker/recipes.py` | Modify | Read `water_percentage`; compute effective water/grand total; `set_product_water()` |
| `tests/test_recipe_invoice_model.py` | Modify | Service + HTTP tests for water |
| `chem_stock.py` | Modify | Re-export `set_product_water` |
| `flask_app.py` | Modify (~line 170, ~1432, ~1702) | `RecipeWaterIn`, `PUT .../products/<product>/water` |
| `raas-tracker-frontend/src/pages/Recipes.jsx` | Modify | Inline water control, grand-total warning, `handleSetWater` |
| `raas-tracker-frontend/src/pages/Recipes.test.jsx` | Modify | Water UI tests; picker regression inside the card |
| `raas-tracker-frontend/src/components/ui/EntityCombobox.jsx` | Modify | Portal + fixed positioning + flip-up |
| `raas-tracker-frontend/src/components/ui/EntityCombobox.test.jsx` | Modify | Clipping regression + flip-up test |
| `docs/PRD.md`, `docs/SPEC.md`, `docs/plan.md`, `docs/superpowers/specs/2026-11-01-dropdown-combobox-design.md` | Modify | Document v9, the route, the payload and the portal |

---

## Task 1: Schema v9 — `recipe_products.water_percentage`

**Files:**
- Modify: `raas_tracker/db.py:144-150` (version comment + constant), `raas_tracker/db.py:558-563` (fresh-install DDL), `raas_tracker/db.py:954` (existing-database ALTER)
- Test: `tests/test_recipe_schema_v8.py:26`, `:39`, `:107-115`

**Interfaces:**
- Produces: `recipe_products.water_percentage REAL NULL` — NULL means "auto remainder"; a stored value is an explicit water percentage.

- [ ] **Step 1: Write the failing schema test**

In `tests/test_recipe_schema_v8.py`, replace the version test and extend the column set:

```python
def test_schema_version_is_nine():
    assert dbmod._SCHEMA_VERSION == 9


def test_v9_recipe_shape_is_invoice_scoped(db):
    assert _columns(db, "recipes") == {
        "id", "name", "created_date", "company_id", "invoice_id"}
    assert _columns(db, "recipe_products") == {
        "id", "recipe_id", "product_name", "unit", "water_percentage"}
    assert _columns(db, "recipe_items") == {
        "id", "recipe_product_id", "chemical_id", "percentage"}
    assert _columns(db, "production_run_outputs") == {
        "id", "run_id", "recipe_product_id", "product_name", "unit",
        "qty_produced"}
```

(`test_v8_recipe_shape_is_invoice_scoped` is the old name — rename it, keep its body plus the new column.)

In `test_migration_is_idempotent_on_a_v8_database` (rename to `..._on_a_v9_database`), change the stamped-version assertion:

```python
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key = %s",
            (dbmod._SCHEMA_VERSION_KEY,)).fetchone()[0] == "9"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/test_recipe_schema_v8.py -q`
Expected: FAIL — `_SCHEMA_VERSION` is 8 and `recipe_products` has no `water_percentage`.

- [ ] **Step 3: Bump the version and add the column**

In `raas_tracker/db.py`, after the `# 8 = ...` comment block, add and bump:

```python
# 9 = RECIPE-WATER: recipe_products.water_percentage (nullable). NULL means the
#     unformulated remainder (100 - ingredients); a value is an explicit,
#     display-only water percentage per product. Additive only.
_SCHEMA_VERSION = 9
```

In the fresh-install DDL of `recipe_products` (inside `_create_tables`):

```sql
        CREATE TABLE IF NOT EXISTS recipe_products (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            product_name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'KG',
            water_percentage REAL
        );
```

In the v8 migration block of `_run_migration` (next to the other `recipe_products` statements), add the idempotent ALTER:

```python
    if "water_percentage" not in _table_columns(conn, "recipe_products"):
        conn.execute(
            "ALTER TABLE recipe_products ADD COLUMN water_percentage REAL")
```

- [ ] **Step 4: Run the schema tests**

Run: `python -m pytest tests/test_recipe_schema_v8.py tests/test_schema_migration.py tests/test_db_pg.py -q`
Expected: PASS. The v8 refuse-to-rewrite test still passes because the guard runs before/independently of this additive column.

- [ ] **Step 5: Commit**

```bash
git add raas_tracker/db.py tests/test_recipe_schema_v8.py
git commit -m "feat(schema): per-product water percentage (v9)"
```

---

## Task 2: Service — water on read and `set_product_water()`

**Files:**
- Modify: `raas_tracker/recipes.py:1-12` (docstring), `:227-231` (slot select), `:252-270` (`_product_row`), and add `set_product_water` after `add_recipe_item` (~line 493)
- Test: `tests/test_recipe_invoice_model.py` (append a `# water` section next to the other writes; imports at the top already include `pytest`, `recipesmod`, `stockmod`)

**Interfaces:**
- Consumes: `recipe_products.water_percentage` (Task 1).
- Produces:
  - `set_product_water(conn, recipe_id: int, product_name: str, percentage: Optional[float]) -> bool`
  - Each product dict from `list_recipe_products()` / `get_recipe_detail()` gains:
    - `water_percentage: Optional[float]` — the stored value, or `None`
    - `water_effective: Optional[float]` — stored value, else `100 − percentage` when the slot has ingredients, else `None`
    - `grand_total: Optional[float]` — `percentage + water_effective`, else `None`

- [ ] **Step 1: Write the failing service tests**

Append to `tests/test_recipe_invoice_model.py`:

```python
# --------------------------------------------------------------------------- #
# water (display only)
# --------------------------------------------------------------------------- #
def test_water_defaults_to_the_remainder_and_overrides_once_set(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 40)

    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_A)
    assert product["percentage"] == 40
    assert product["water_percentage"] is None
    assert product["water_effective"] == 60
    assert product["grand_total"] == 100

    assert recipesmod.set_product_water(
        db, recipe["id"], PRODUCT_A, 25) is True
    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_A)
    assert product["water_percentage"] == 25
    assert product["water_effective"] == 25
    assert product["grand_total"] == 65

    # Clearing (None) puts the remainder back.
    assert recipesmod.set_product_water(
        db, recipe["id"], PRODUCT_A, None) is True
    product = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_A)
    assert product["water_percentage"] is None
    assert product["water_effective"] == 60


def test_water_is_per_product_and_never_needs_an_ingredient(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)
    stockmod.add_chemical(db, CHEM, 500, "KG")
    recipesmod.add_recipe_item(db, recipe["id"], PRODUCT_A, CHEM, 40)

    # An empty product has no auto remainder, but an explicit value still shows.
    empty = _product(recipesmod.get_recipe_detail(db, recipe["id"]), PRODUCT_B)
    assert empty["water_effective"] is None
    assert empty["grand_total"] is None

    recipesmod.set_product_water(db, recipe["id"], PRODUCT_B, 15)
    detail = recipesmod.get_recipe_detail(db, recipe["id"])
    assert _product(detail, PRODUCT_B)["grand_total"] == 15
    assert _product(detail, PRODUCT_A)["water_effective"] == 60


def test_water_is_validated_and_audited(db):
    co, sale, inv = _scenario(db)
    recipe = _recipe(db, co, inv)

    with pytest.raises(ValueError):
        recipesmod.set_product_water(db, recipe["id"], PRODUCT_A, -1)
    with pytest.raises(ValueError):
        recipesmod.set_product_water(db, recipe["id"], PRODUCT_A, 100.5)
    with pytest.raises(ValueError):
        recipesmod.set_product_water(db, recipe["id"], "Not On Invoice", 10)

    recipesmod.set_product_water(db, recipe["id"], PRODUCT_A, 12.5)
    row = db.execute(
        "SELECT old_value, new_value FROM audit_logs"
        " WHERE entity_type = 'recipe_product'"
        "   AND action = 'RECIPE_WATER_SET' ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "auto"
    assert row[1] == "12.5"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_recipe_invoice_model.py -q -k water`
Expected: FAIL — `AttributeError: module ... has no attribute 'set_product_water'`, and the payload lacks the three keys.

- [ ] **Step 3: Read the stored value**

In `list_recipe_products`, extend the slot query and dict:

```python
    for row in conn.execute(
            "SELECT id, product_name, unit, water_percentage "
            "FROM recipe_products WHERE recipe_id = %s ORDER BY id",
            (recipe_id,)).fetchall():
        slots[(row[1] or "").strip().lower()] = {
            "id": row[0], "product_name": row[1], "unit": row[2],
            "water_percentage": row[3]}
```

In `_product_row`, compute and return the three keys:

```python
def _product_row(group, slot, items, produced, on_invoice=True) -> Dict[str, Any]:
    slot_items = items.get(slot["id"], []) if slot else []
    invoiced = float(group.get("invoiced_qty") or 0)
    built = float(produced.get(group["key"], 0))
    ingredient_pct = sum(i["percentage"] for i in slot_items)
    stored_water = slot.get("water_percentage") if slot else None
    if stored_water is not None:
        water_effective = float(stored_water)
    elif slot_items:
        water_effective = round(100.0 - ingredient_pct, 6)
    else:
        water_effective = None
    grand_total = (
        None if water_effective is None
        else round(ingredient_pct + water_effective, 6))
    return {
        "id": slot["id"] if slot else None,
        "on_invoice": on_invoice,
        "product_name": group["product_name"],
        "unit": (slot["unit"] if slot else group["unit"]) or "KG",
        "invoiced_qty": invoiced,
        "line_count": len(group.get("lines") or []),
        "lines": group.get("lines") or [],
        "unconverted": group.get("unconverted") or [],
        "produced_qty": built,
        "remaining_qty": max(invoiced - built, 0.0),
        "formulated": bool(slot_items),
        "percentage": ingredient_pct,
        "water_percentage": stored_water,
        "water_effective": water_effective,
        "grand_total": grand_total,
        "items": slot_items,
    }
```

Update the module docstring sentence to match the new rule:

```
Water/solvent is whatever is not formulated: a slot's percentages may sum to
anything from 0 to 100, and only the listed chemicals are deducted. An
optional per-slot `water_percentage` overrides that remainder for display; it
is never added to a deduction or used as a production quantity.
```

- [ ] **Step 4: Add the setter**

After `add_recipe_item`:

```python
def set_product_water(conn: psycopg.Connection, recipe_id: int,
                      product_name: str,
                      percentage: Optional[float]) -> bool:
    """Set (or clear, with ``None``) one product's water percentage.

    Display only: the detail read reports ``100 - ingredients`` when this is
    NULL and the stored value otherwise. Nothing is deducted and no cap uses
    it, so a total that is not 100 is a warning, never a refusal.
    """
    if percentage is not None:
        finite_values(percentage)
        if percentage < 0 or percentage > 100:
            raise ValueError("water percentage must be between 0 and 100")
    recipe = _require_recipe(conn, recipe_id)
    slot_id = _slot_for_product(conn, recipe, product_name)
    old = conn.execute(
        "SELECT water_percentage FROM recipe_products WHERE id = %s",
        (slot_id,)).fetchone()[0]
    conn.execute(
        "UPDATE recipe_products SET water_percentage = %s WHERE id = %s",
        (percentage, slot_id))
    log_audit_action(
        conn, "RECIPE_WATER_SET", "recipe_product", slot_id,
        old_value="auto" if old is None else str(old),
        new_value="auto" if percentage is None else str(percentage),
        atomic=False)
    conn.commit()
    return True
```

`_slot_for_product` is the right helper here: it writes only when the slot is missing (a product added to the invoice after the recipe) and validates that the product is on this recipe's invoice, so a foreign product is a 400 rather than a silent row.

- [ ] **Step 5: Run the service tests**

Run: `python -m pytest tests/test_recipe_invoice_model.py -q -k "water or formulate or product"`
Expected: PASS, including the existing per-product item tests.

- [ ] **Step 6: Commit**

```bash
git add raas_tracker/recipes.py tests/test_recipe_invoice_model.py
git commit -m "feat(recipes): report and set per-product water percentage"
```

---

## Task 3: API — `PUT /api/recipes/by-id/<id>/products/<product>/water`

**Files:**
- Modify: `chem_stock.py:112-118` (re-export), `flask_app.py:168-176` (import), `flask_app.py:1432-1436` (model), `flask_app.py` after the item DELETE route (~line 1701)
- Test: `tests/test_recipe_invoice_model.py` (HTTP section, after `test_api_formulate_by_product_path`)

**Interfaces:**
- Consumes: `set_product_water` (Task 2).
- Produces: `PUT /api/recipes/by-id/<id>/products/<path:product>/water`, body `{"percentage": 0–100 | null}`, `200 {"success": true}`; `400` validation; `404` unknown recipe; `401` unauthenticated.

- [ ] **Step 1: Write the failing HTTP test**

Append to the HTTP section of `tests/test_recipe_invoice_model.py`:

```python
def test_api_set_and_clear_water(admin_client, db):
    co, sale, inv = _scenario(db)
    rid = _api_recipe(admin_client, co, inv)
    items_path = f"/api/recipes/by-id/{rid}/products/{quote(PRODUCT_A)}/items"
    assert admin_client.post(
        items_path, json={"chemical": CHEM, "percentage": 40}).status_code == 200

    water_path = f"/api/recipes/by-id/{rid}/products/{quote(PRODUCT_A)}/water"
    assert admin_client.put(water_path, json={"percentage": 25}).status_code == 200

    detail = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()
    product = next(p for p in detail["products"]
                   if p["product_name"] == PRODUCT_A)
    assert product["water_percentage"] == 25
    assert product["water_effective"] == 25
    assert product["grand_total"] == 65

    assert admin_client.put(water_path, json={"percentage": None}).status_code == 200
    detail = admin_client.get(f"/api/recipes/by-id/{rid}").get_json()
    product = next(p for p in detail["products"]
                   if p["product_name"] == PRODUCT_A)
    assert product["water_percentage"] is None
    assert product["water_effective"] == 60
    assert product["grand_total"] == 100

    assert admin_client.put(water_path, json={"percentage": 100.5}).status_code == 400
    assert admin_client.put(
        f"/api/recipes/by-id/999999/products/{quote(PRODUCT_A)}/water",
        json={"percentage": 10}).status_code == 404


def test_api_set_water_requires_authentication(client):
    r = client.put(
        f"/api/recipes/by-id/1/products/{quote(PRODUCT_A)}/water",
        json={"percentage": 10})
    assert r.status_code == 401
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python -m pytest tests/test_recipe_invoice_model.py -q -k "api_set"`
Expected: FAIL with 404/405 — the route does not exist.

- [ ] **Step 3: Export the service function**

In `chem_stock.py`, inside the `from raas_tracker.recipes import (...)` block, add `set_product_water,` immediately after `get_recipe_detail,` (line ~124).

In `flask_app.py`, extend the same import block (line ~168):

```python
    list_recipe_items_by_id, update_recipe, update_recipe_item, delete_recipe_item,
    set_product_water,
```

- [ ] **Step 4: Add the payload model**

After `RecipePercentageIn`:

```python
class RecipeWaterIn(_FiniteModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, allow_inf_nan=False, extra="forbid")
    percentage: float | None = Field(default=None, ge=0, le=100)
```

`None` (or an omitted key) clears the value back to the remainder.

- [ ] **Step 5: Add the route**

After `api_delete_recipe_item_by_id` (before the name-alias routes):

```python
@app.route("/api/recipes/by-id/<int:recipe_id>/products/<path:product>/water",
           methods=["PUT"])
@operator_or_admin_required
@limited()
def api_set_recipe_water(recipe_id, product):
    try:
        payload = RecipeWaterIn.model_validate(request.get_json() or {})
    except ValidationError as e:
        return _validation_error_response(e)
    conn = get_db()
    try:
        set_product_water(conn, recipe_id, product, payload.percentage)
    except ValueError as e:
        _rollback_close(conn)
        return jsonify({"error": str(e)}), status_for(e)
    conn.close()
    return jsonify({"success": True})
```

- [ ] **Step 6: Run the HTTP tests**

Run: `python -m pytest tests/test_recipe_invoice_model.py -q -k "api_set or formulate"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add chem_stock.py flask_app.py tests/test_recipe_invoice_model.py
git commit -m "feat(api): set or clear a product's water percentage"
```

---

## Task 4: Recipe view — inline water control and grand total

**Files:**
- Modify: `raas-tracker-frontend/src/pages/Recipes.jsx:36-258` (`ProductFormulationCard`), `:632-653` (new handler), `:721-732` (prop)
- Test: `raas-tracker-frontend/src/pages/Recipes.test.jsx` (`installFetch`, `Per-product formulation`)

**Interfaces:**
- Consumes: product payload keys `water_percentage`, `water_effective`, `grand_total` (Task 2/3); route from Task 3.
- Produces: `ProductFormulationCard` prop `onSetWater(product, percentageOrNull) => Promise<void>`.

- [ ] **Step 1: Write the failing frontend tests**

In `installFetch`, add water state and the route. Replace the `state` initializer with:

```js
  const state = {
    recipes: [...recipes],
    items: {
      'Caustic Soda 50%': firstItem
        ? [{ id: 101, chemical_id: 1, chemical_name: firstItem.chemical_name,
          current_stock: 500, unit: 'KG', percentage: firstItem.percentage }]
        : [],
      'Soda Ash': [],
      'Sodium Silicate': [],
    },
    water: { 'Caustic Soda 50%': null, 'Soda Ash': null, 'Sodium Silicate': null },
    runs: false,
    nextId: 200,
  };
```

In `detailFor`, compute the three keys:

```js
    products: BASE_PRODUCTS.map((base) => {
      const items = state.items[base.product_name] || [];
      const invoiced = base.invoiced_qty;
      const produced = state.runs ? invoiced : base.produced_qty;
      const ingredientPct = items.reduce(
        (sum, i) => sum + Number(i.percentage || 0), 0);
      const storedWater = state.water[base.product_name];
      const waterEffective = storedWater != null
        ? storedWater
        : (items.length ? 100 - ingredientPct : null);
      return {
        ...base,
        id: 900 + BASE_PRODUCTS.findIndex(
          (p) => p.product_name === base.product_name),
        on_invoice: true,
        produced_qty: produced,
        remaining_qty: Math.max(invoiced - produced, 0),
        formulated: items.length > 0,
        percentage: ingredientPct,
        water_percentage: storedWater ?? null,
        water_effective: waterEffective,
        grand_total: waterEffective == null
          ? null
          : ingredientPct + waterEffective,
        items,
      };
    }),
```

Add the route next to the item routes in `fetchMock`:

```js
    const productWater = u.match(
      /\/api\/recipes\/by-id\/\d+\/products\/(.+)\/water$/);
    if (productWater && method === 'PUT') {
      const product = decodeURIComponent(productWater[1]);
      const body = JSON.parse(options.body);
      state.water[product] = body.percentage;
      return jsonResponse({ success: true });
    }
```

Add the tests to the `Per-product formulation` describe:

```jsx
  it('shows the remainder as water and flags a total that is not 100%', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    // 40% NaOH -> 60% water -> 100%.
    expect(within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%')).toHaveValue(null);
    expect(within(soda).getByText('Grand total 100.0%')).toBeTruthy();
    expect(within(soda).queryByText(/do not add up to 100/)).toBeNull();

    // Empty product: no remainder to show, so no invented grand total.
    expect(within(card('Soda Ash')).getByText('Grand total —')).toBeTruthy();
  });

  it('saves and clears water per product without blocking', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    const water = within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%');
    fireEvent.change(water, { target: { value: '25' } });
    // Preview is immediate; the warning is informational only.
    expect(within(soda).getByText('Grand total 65.0%')).toBeTruthy();
    expect(within(soda).getByText(/do not add up to 100/)).toBeTruthy();

    fireEvent.click(within(soda).getByRole('button', {
      name: 'Save water for Caustic Soda 50%',
    }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, options]) => options?.method === 'PUT'
          && decodeURIComponent(String(url))
            .endsWith('/api/recipes/by-id/3/products/Caustic Soda 50%/water'));
      expect(put).toBeTruthy();
      expect(JSON.parse(put[1].body)).toEqual({ percentage: 25 });
    });
    expect(await within(card('Caustic Soda 50%')).findByText('Grand total 65.0%'))
      .toBeTruthy();

    // Blank means "auto": the server stores NULL and the remainder returns.
    fireEvent.change(
      within(card('Caustic Soda 50%')).getByLabelText(
        'Water percentage for Caustic Soda 50%'),
      { target: { value: '' } });
    fireEvent.click(within(card('Caustic Soda 50%')).getByRole('button', {
      name: 'Save water for Caustic Soda 50%',
    }));

    await waitFor(() => {
      const puts = fetchMock.mock.calls.filter(
        ([url, options]) => options?.method === 'PUT'
          && decodeURIComponent(String(url)).endsWith('/water'));
      expect(JSON.parse(puts[puts.length - 1][1].body))
        .toEqual({ percentage: null });
    });
    expect(await within(card('Caustic Soda 50%')).findByText('Grand total 100.0%'))
      .toBeTruthy();
  });
```

Note: `toHaveValue(null)` is RTL's "empty input". Keep the create-form test at `Recipes.test.jsx:400` (`queryByLabelText(/^Water/)` is null there) unchanged — it now guards both behaviours.

- [ ] **Step 2: Run them and watch them fail**

Run: `cd raas-tracker-frontend && npx vitest run src/pages/Recipes.test.jsx -t "water"`
Expected: FAIL — no water input, no grand-total text.

- [ ] **Step 3: Implement the card**

Change the `ProductFormulationCard` signature:

```jsx
function ProductFormulationCard({
  product, chemicals, isAdmin, onAdd, onUpdate, onDelete, onSetWater,
}) {
```

Add state and derived values after the existing `saving` state:

```jsx
  const [waterDraft, setWaterDraft] = useState('');
  const [savingWater, setSavingWater] = useState(false);

  // Follow the server after every re-read (save, add/remove item, dialog open).
  useEffect(() => {
    setWaterDraft(
      product.water_percentage == null ? '' : String(product.water_percentage));
  }, [product.water_percentage, product.product_name]);

  const ingredientPct = product.percentage;
  const trimmedWater = waterDraft.trim();
  const draftWater = trimmedWater === '' ? null : Number(trimmedWater);
  const waterPct = draftWater
    ?? (product.items.length ? 100 - ingredientPct : null);
  const grandTotal = waterPct == null ? null : ingredientPct + waterPct;
  const offBalance = grandTotal != null && Math.abs(grandTotal - 100) > 1e-6;
```

Add the submit handler next to `submitEdit`:

```jsx
  const submitWater = async () => {
    const trimmed = waterDraft.trim();
    const value = trimmed === '' ? null : Number(trimmed);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 100)) {
      toast.error('Water must be between 0 and 100');
      return;
    }
    setSavingWater(true);
    try {
      await onSetWater(product, value);
    } finally {
      setSavingWater(false);
    }
  };
```

Insert the strip between the `<table>` and the add-ingredient `<div className="p-4 border-t ...">`:

```jsx
      <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 dark:border-slate-800 px-4 py-3">
        <label
          htmlFor={`water-${product.product_name}`}
          className="text-xs font-medium text-slate-500 dark:text-slate-400"
        >
          Water %
        </label>
        <input
          id={`water-${product.product_name}`}
          aria-label={`Water percentage for ${product.product_name}`}
          type="number"
          step="0.01"
          min="0"
          max="100"
          value={waterDraft}
          onChange={(e) => setWaterDraft(e.target.value)}
          placeholder="auto"
          className="w-24 px-2 py-1 text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white"
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={submitWater}
          loading={savingWater}
          aria-label={`Save water for ${product.product_name}`}
        >
          Save
        </Button>
        <span className="text-[11px] text-slate-400 dark:text-slate-500">
          {trimmedWater === '' ? 'blank = the unformulated remainder' : 'stored percentage'}
        </span>
        <span className="ml-auto text-sm font-semibold text-slate-700 dark:text-slate-200">
          {grandTotal == null
            ? 'Grand total —'
            : `Grand total ${grandTotal.toFixed(1)}%`}
        </span>
        {offBalance && (
          <span className="flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            ingredients + water do not add up to 100%
          </span>
        )}
      </div>
```

The summary line in the card header stays as-is (`x ingredient(s) · y% raw materials`): it describes the chemical formulation, while the strip owns water.

- [ ] **Step 4: Wire the handler in `Recipes`**

After `handleUpdateItem`:

```jsx
  const handleSetWater = async (product, percentage) => {
    if (!selectedRecipe) return false;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/products/' + encodeURIComponent(product.product_name) + '/water',
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ percentage }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || 'Could not save water percentage');
      }
      toast.success(
        percentage == null ? 'Water reset to the remainder' : 'Water saved');
      await fetchRecipeDetail(selectedRecipe);
      return true;
    } catch (err) {
      toast.error(err.message || 'Could not save water percentage');
      return false;
    }
  };
```

Pass it to the card:

```jsx
              onDelete={handleDeleteItem}
              onSetWater={handleSetWater}
```

- [ ] **Step 5: Run the recipe page tests**

Run: `cd raas-tracker-frontend && npx vitest run src/pages/Recipes.test.jsx`
Expected: PASS, including the untouched create-form test (no Water label there).

- [ ] **Step 6: Commit**

```bash
git add raas-tracker-frontend/src/pages/Recipes.jsx raas-tracker-frontend/src/pages/Recipes.test.jsx
git commit -m "feat(recipes): inline water percentage and grand total in the view"
```

---

## Task 5: `EntityCombobox` — portal the listbox out of clipped cards

**Files:**
- Modify: `raas-tracker-frontend/src/components/ui/EntityCombobox.jsx:1-2, 192-256`
- Test: `raas-tracker-frontend/src/components/ui/EntityCombobox.test.jsx`

**Interfaces:**
- Consumes: nothing new from earlier tasks.
- Produces: unchanged props. Internally the listbox renders into `document.body` with `position: fixed`; `onFocus`/typing/keyboard/commit behaviour is untouched.

- [ ] **Step 1: Write the failing tests**

Append to `EntityCombobox.test.jsx`:

```jsx
  it('escapes an overflow-hidden ancestor so the list is never clipped', () => {
    render(
      <div data-testid="clipped" style={{ overflow: 'hidden', height: 24 }}>
        <Controlled />
      </div>,
    );
    fireEvent.focus(screen.getByLabelText('Company'));
    const listbox = screen.getByRole('listbox');
    expect(screen.getByTestId('clipped').contains(listbox)).toBe(false);
    expect(document.body.contains(listbox)).toBe(true);
    expect(listbox).toHaveStyle({ position: 'fixed' });
  });

  it('opens upwards when there is no room below the field', () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 700, bottom: 740, left: 10, right: 210, width: 200, height: 40,
      x: 10, y: 700, toJSON: () => ({}),
    });
    render(<Controlled />);
    fireEvent.focus(screen.getByLabelText('Company'));
    const listbox = screen.getByRole('listbox');
    expect(listbox.style.top).toBe('');
    expect(listbox.style.bottom).not.toBe('');
  });
```

Add `vi` and `afterEach` to the import at the top: `import { describe, it, expect, vi, afterEach } from 'vitest';` and reset the prototype spy after every test (no global `restoreMocks` is configured):

```jsx
afterEach(() => {
  vi.restoreAllMocks();
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd raas-tracker-frontend && npx vitest run src/components/ui/EntityCombobox.test.jsx`
Expected: FAIL — the listbox is still inside the clipped div and is `position: absolute`.

- [ ] **Step 3: Implement the portal**

Change the imports:

```jsx
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
```

Add anchor state and the measurement effect after the existing state:

```jsx
  const [anchor, setAnchor] = useState(null);

  useLayoutEffect(() => {
    if (!open) {
      setAnchor(null);
      return undefined;
    }
    const place = () => {
      const el = inputRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const gap = 4;
      const below = window.innerHeight - rect.bottom;
      const above = rect.top;
      const up = below < 200 && above > below;
      setAnchor({
        left: rect.left,
        width: rect.width,
        top: up ? undefined : rect.bottom + gap,
        bottom: up ? window.innerHeight - rect.top + gap : undefined,
        maxHeight: Math.max(120, Math.min(240, (up ? above : below) - gap)),
      });
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open]);
```

Replace the inline `{open && (<ul …>)}` block with a portal. The surrounding `relative` div and the invalid hint stay where they are:

```jsx
      {open && anchor && createPortal(
        <ul
          id={listId}
          role="listbox"
          style={{
            position: 'fixed',
            left: anchor.left,
            width: anchor.width,
            top: anchor.top,
            bottom: anchor.bottom,
            maxHeight: anchor.maxHeight,
            zIndex: 1000,
          }}
          className="overflow-auto rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg dark:border-slate-700 dark:bg-slate-800"
        >
          {/* children unchanged */}
        </ul>,
        document.body,
      )}
```

Keep every child exactly as it is (`filtered.length === 0 ? … : filtered.map(…).`). The visual classes lose `absolute z-30 mt-1 w-full`; positioning is now inline so the popup can flip.

Why this works with the existing interaction model: the option rows already `preventDefault()` on `mousedown`, so the input never blurs before `onClick` commits; React portals keep synthetic events bubbling to this component, and `aria-controls` / `aria-activedescendant` resolve across the DOM.

- [ ] **Step 4: Run the combobox tests**

Run: `cd raas-tracker-frontend && npx vitest run src/components/ui/EntityCombobox.test.jsx`
Expected: PASS — all previous behaviours (focus listing every option, label selection, exact-label commit, Escape revert, unmatched hint) plus the two new tests.

- [ ] **Step 5: Commit**

```bash
git add raas-tracker-frontend/src/components/ui/EntityCombobox.jsx raas-tracker-frontend/src/components/ui/EntityCombobox.test.jsx
git commit -m "fix(ui): render combobox options outside clipped cards"
```

---

## Task 6: Recipe picker regression — the stock list appears

**Files:**
- Modify: `raas-tracker-frontend/src/pages/Recipes.jsx:215-223` (`EntityCombobox` props)
- Test: `raas-tracker-frontend/src/pages/Recipes.test.jsx` (`Per-product formulation`)

**Interfaces:**
- Consumes: Task 5's portal behaviour; `CHEMICALS` already in the test file.
- Produces: no API or signature change.

- [ ] **Step 1: Write the failing regression test**

Add to the `Per-product formulation` describe:

```jsx
  it('lists the stock chemicals when the chemical field is focused', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    fireEvent.focus(within(ash).getByLabelText('Chemical for Soda Ash'));

    expect((await screen.findAllByRole('option')).map((o) => o.textContent))
      .toEqual(['NaOH', 'Caustic Flakes', 'Soda Ash']);
    // The card is overflow-hidden; the list must live outside it.
    expect(ash.contains(screen.getByRole('listbox'))).toBe(false);

    // Typing still narrows the stock list.
    fireEvent.change(within(ash).getByLabelText('Chemical for Soda Ash'), {
      target: { value: 'caust' },
    });
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Caustic Flakes',
    ]);
  });

  it('explains a typed name that matches no stock chemical', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    const chemical = within(ash).getByLabelText('Chemical for Soda Ash');
    fireEvent.focus(chemical);
    fireEvent.change(chemical, { target: { value: 'Acclofol' } });

    expect(within(ash).getByText(/No matching chemical/)).toBeTruthy();
  });
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd raas-tracker-frontend && npx vitest run src/pages/Recipes.test.jsx -t "stock chemicals"`
Expected: the option list assertion passes (Task 5 did the portalling); the copy assertion FAILS because the empty message is still the generic `No matches`.

- [ ] **Step 3: Make the messages specific to stock**

In the card's `EntityCombobox`:

```jsx
          <EntityCombobox
            id={`chem-${product.product_name}`}
            aria-label={`Chemical for ${product.product_name}`}
            options={available.map((c) => ({ value: c.name, label: c.name }))}
            value={chemical}
            onChange={setChemical}
            placeholder="Search chemicals…"
            emptyMessage="No matching chemical — check the spelling or pick from the list"
            hint="No chemical by that name — pick one from the list"
            className={inputCls}
          />
```

- [ ] **Step 4: Run the recipe tests**

Run: `cd raas-tracker-frontend && npx vitest run src/pages/Recipes.test.jsx`
Expected: PASS. The existing typo test (`expect(within(ash).getByRole('status').textContent).toContain('No matching record')`) still matches because the new hint starts with `No chemical by that name`, not `No matching record` — so update that assertion to the new copy:

```js
    expect(within(ash).getByRole('status').textContent).toContain('No chemical by that name');
```

- [ ] **Step 5: Commit**

```bash
git add raas-tracker-frontend/src/pages/Recipes.jsx raas-tracker-frontend/src/pages/Recipes.test.jsx
git commit -m "fix(recipes): surface the stock chemical picker with clear empty copy"
```

---

## Task 7: Documentation and full gate

**Files:**
- Modify: `docs/PRD.md` (Flow 4, Data Model), `docs/SPEC.md` (DDL, version history, endpoint table, payload, coverage), `docs/plan.md` (new phase), `docs/superpowers/specs/2026-11-01-dropdown-combobox-design.md` (clipping note)

- [ ] **Step 1: PRD**

- Flow 4 step 1: keep "there is no product select, total quantity or water field" and append: "Water is set per formulation card once the recipe is open, never in this form."
- Flow 4 step 2: add a bullet: "Each card has a **Water %** control. Blank means the unformulated remainder (`100 − ingredients`); a saved value replaces it. The card shows `Grand total N%` and, when ingredients + water are not 100%, an amber note — informational only: nothing is blocked, deducted or capped by it."
- Data Model: `recipes` row keeps "no product, tonnage or water field"; `recipe_products` gains `water_percentage` (`REAL NULL`; NULL = auto remainder).

- [ ] **Step 2: SPEC**

- DDL block for `recipe_products`: add `water_percentage REAL` (nullable).
- Version history at line ~479: "Current value is **9** (RECIPE-WATER: `recipe_products.water_percentage`, nullable and additive; NULL = `100 − ingredients`)" and demote 8 to the history list with its own line.
- Detail payload (line ~549): append `water_percentage`, `water_effective`, `grand_total` to the `products[]` key list, with "the last two are derived and may be null for an empty formulation".
- Endpoint table after the item DELETE row: `PUT /api/recipes/by-id/<id>/products/<path:product>/water | Operator/Admin | Set or clear one product's water: {percentage: 0–100 | null}. null = remainder. Display only; never changes a cap or a deduction`.
- **Public contract changes** table (~line 874): add a row `PUT /api/recipes/by-id/<id>/products/<product>/water` beside the other recipe routes.
- Combobox section: add "The listbox is portalled to `document.body` with fixed positioning measured from the input and flips above the field when there is no room below, so an `overflow-hidden` card cannot clip it."
- Coverage: update the file/test counts to the numbers the gate prints in Step 5.

- [ ] **Step 3: Picker design spec**

Add a paragraph after the focus paragraph:

"**Clipping.** The list is not a child of the field's card. It renders through a portal at `document.body` with `position: fixed`, measured from the input on open and on scroll/resize, and opens upwards when the space below is too small. Cards that clip their contents (`overflow-hidden`) must never be able to hide it."

- [ ] **Step 4: plan.md**

Add a completed phase section at the top of the phase list with the commit SHAs and the gate numbers:

```markdown
## 💧 Phase RECIPE-WATER: water % in the view + un-clipped pickers (completed 2026-11-01)

- Schema v9: `recipe_products.water_percentage` (nullable; NULL = `100 − ingredients`).
- Detail payload adds `water_percentage`, `water_effective`, `grand_total`; `PUT /api/recipes/by-id/<id>/products/<product>/water` sets or clears it (operator, audited `RECIPE_WATER_SET`).
- The recipe view shows an inline Water % control per product card and an informational grand-total warning; the create form still has none.
- `EntityCombobox` portals its listbox to `document.body` (fixed, flips up), so `overflow-hidden` cards no longer hide suggestions.
```

- [ ] **Step 5: Run the full backend gate**

```bash
python -m pytest tests/ -q
python -m ruff check .
python -m pyright
```

Expected: all green, and the printed counts are what Step 2 records.

- [ ] **Step 6: Run the full frontend gate**

```bash
cd raas-tracker-frontend
npx vitest run
npx oxlint
npx vite build
rm -rf ../react_frontend/*
cp -r dist/. ../react_frontend/
```

Expected: every test file passes (the 50-card cap tests keep their 120 s timeout), oxlint 0 warnings / 0 errors, build green, `react_frontend/` refreshed.

- [ ] **Step 7: Manual browser check**

Start the backend with a valid `DATABASE_URL`, open Recipes → View on a recipe:

1. Focus the Chemical field in a card near the bottom of the page — the full stock list appears **over** the content below, not clipped.
2. Type part of a name — the list narrows; pick one, enter a percentage, Add.
3. Set Water %, watch `Grand total` update and the amber note appear; Save, reload — the saved value persists; blank + Save returns the remainder.
4. Open the Create modal — still no Water field.

- [ ] **Step 8: Commit**

```bash
git add docs/PRD.md docs/SPEC.md docs/plan.md \
  docs/superpowers/specs/2026-11-01-dropdown-combobox-design.md
git commit -m "docs: recipe water in the view and portal picker positioning"
```

---

## Verification

- `python -m pytest tests/test_recipe_schema_v8.py tests/test_recipe_invoice_model.py -q` — v9 column, water read/set/validate/audit, HTTP 200/400/404/401.
- `python -m pytest tests/ -q` — no regression in readiness, production or reports (`_product_row` is shared, so its new keys must be additive only).
- `cd raas-tracker-frontend && npx vitest run` — recipe water UI, picker portal, and every existing combobox behaviour.
- `npx oxlint` and `npx vite build` — clean.
- Manual pass from Task 7 Step 7.
- `git status --short` — only the named files committed; the ten pre-existing modifications and three untracked files remain untouched.
