# Dropdowns as suggestion + search comboboxes — design

_2026-11-01 · approved approach 1 (two primitives, one behaviour)_

## Problem

Data-entry dropdowns are native `<select>`s. They offer no type-ahead, so a
company, LC, PI or chemical has to be hunted by eye in a growing list, and the
text-valued dropdowns (unit, adjustment reason) silently *forbid* values the
server would happily accept. The product-name fields already solved the second
half of this with an `<input list>` + `<datalist>` (suggestions, free text).

The request is to give every data-entry dropdown the same "type or pick"
behaviour. Two data shapes make that a two-part design, not one component:

- **Text-valued** fields store a string; anything typed is valid as far as the
  client is concerned (length caps excepted).
- **Record-valued** fields store a foreign key or a name the server resolves to
  one; free text has nothing to save against, so typing can only *narrow* the
  list and a pick must resolve to an existing option.

## Goals

1. Every field where a value is entered and saved becomes a type-or-pick
   control: suggestions plus free text for strings, search plus must-match for
   ids.
2. No new dependency, no backend change, no schema change.
3. Keep the referential-integrity check in one place rather than in each form.
4. Keep the product-name field exactly as shipped.

## Non-goals

- Report, audit-log and stock-history **filters** (explicitly out of scope).
- Fixed enums the API validates: role, payment terms, group-by, page size,
  month/year. Free text there is a guaranteed 400.
- Creating a company / LC / chemical by typing a new name. A missing record is
  never created from a picker.
- Any change to the product catalog or its field.

## Scope

| Kind | Field | Call sites |
|---|---|---|
| text | Product name | `MultiPICreateModal`, `ReviewModal`, `SaleDetailModal` — **already shipped, unchanged** |
| text | Unit | `MultiPICreateModal` row, `ReviewModal` row, `Chemicals` add form, `UnitMapping` target unit |
| text | Adjustment reason | `Chemicals` adjust form |
| id | Company | `MultiPICreateModal`, `ReviewModal`, `Recipes` create |
| id | LC | `LinkPIsModal` (existing LC), `Recipes` create |
| id | PI / Sale | `Recipes` create |
| id | Invoice | `Recipes` create, `SaleDetailModal` payment target invoice |
| id | Chemical | `Recipes` formulation card — this one stores the chemical **name** (the API resolves it), so `value` and `label` are both the name |

The recipe create modal keeps its cascade: company → LC → PI → invoice, each
child disabled until its parent is chosen and each reset when the parent
changes.

## Components

### `SuggestionInput` — `src/components/ui/SuggestionInput.jsx`

A generalisation of today's `ProductInput`.

```jsx
<SuggestionInput
  options={COMMON_UNITS}        // string[]
  value={unit}
  onChange={(e) => setUnit(e.target.value)}
  className={inputCls}
  aria-label="Unit"
/>
```

- Renders an `<input list={listId}>` plus a `<datalist>` built from `options`;
  one `useId()` per instance.
- Free text is always accepted; the component never coerces or trims the value
  (call sites that care pass `maxLength`, e.g. 120 for the reason).
- Forwards `value`, `onChange`, `className`, `placeholder`, `id`,
  `aria-label` and any other input prop, so it is a drop-in for a text input.
- `ProductInput` becomes:

```jsx
export default function ProductInput(props) {
  return <SuggestionInput options={PRODUCT_NAMES} {...props} />;
}
```

Its existing tests keep passing unchanged.

### `EntityCombobox` — `src/components/ui/EntityCombobox.jsx`

A custom ARIA combobox for record-valued fields, because `<datalist>` cannot show
disabled entries, separate a value from its label, or refuse unmatched text. The
selected value is opaque to the component — a numeric id at most call sites, a
chemical name in the recipe card — so it is compared and emitted unmodified.

```jsx
<EntityCombobox
  options={companies.map(c => ({ value: c.id, label: c.name }))}
  value={companyId}
  onChange={setCompanyId}
  aria-label="Company"
  placeholder="Search companies…"
/>
```

**State.** `value` (the selected option's value) is owned by the form. The component owns
`query` (the text being typed), `open` and `activeIndex`. When closed and a
resolvable option exists, the input shows that option's label; when open, it
shows the query. If `value` changes from the parent while the input is focused
(the cascade resetting a child), the query is cleared.

**Focusing an existing pick.** Focus lists every option (the current label does
not act as a filter) and selects the label text, so the first keystroke replaces
it and searches afresh instead of appending to the old name.

**Clipping.** The list is not a child of the field's card. It renders through a
portal at `document.body` with `position: fixed`, measured from the input on open
and on scroll/resize, and opens upwards when the space below is too small. Cards
that clip their contents (`overflow-hidden`) must never be able to hide it.

**Filtering.** Case-insensitive substring match on `label`; an empty query lists
all options. Disabled options are listed (greyed) and never skipped by the
filter, so the reason they are disabled stays visible.

**Commit rules.**

- Clicking an enabled option, or pressing Enter on the active enabled option,
  selects it.
- Typing text that exactly equals one option's label (case-insensitive,
  trimmed) selects it immediately. If two options share that label, no
  auto-commit happens — an explicit pick is required.
- `ArrowDown` / `ArrowUp` move the active option and skip disabled ones;
  `Home`/`End` are ignored (not needed for these list sizes).
- `Escape` closes the list and restores the selected label (or clears the query
  if nothing is selected).
- `blur` commits an exact single-label match; otherwise it sets the value to
  `''`, leaves the text in place so it can be corrected, and shows the hint.
- The component only ever emits a value present in `options`, or `''`. A bogus
  foreign key cannot reach the API from this control.

**Unmatched text.** While the text matches nothing, the field shows an inline
hint — `No matching company — pick one from the list` — wired through
`aria-describedby`, with `aria-invalid="true"`. The hint clears as soon as a
valid option is committed. The form's existing "required" handling blocks
submit while the value is `''`.

**Display of an unresolvable value.** Label when the value is in `options`; the
raw value while options are still loading; the raw value plus the hint when
options are loaded and the value is absent.

**Disabled options.** Rendered with muted classes, `aria-disabled="true"`, and
excluded from click/Enter/keyboard commits. Used for LCs with no invoices in
production and PIs whose invoices are all past production.

**Accessibility.** `role="combobox"`, `aria-expanded`, `aria-controls`,
`aria-autocomplete="list"`, `aria-activedescendant`; the panel is
`role="listbox"`; each row is `role="option"` with `aria-selected` and
`aria-disabled`. The panel is a scrollable (max ~240 px) fixed-position box
portalled to `document.body` and measured from the input, above modal content;
the hint is `aria-live="polite"`.

**Styling.** Same input classes as today's selects (passed in via
`className`), same dark-mode tokens; the panel reuses the app's border,
background and hover colours.

## Data flow and validation

Form state is unchanged: ids stay ids, text stays text. Therefore request
payloads, Pydantic models, SQL, audit rows and permissions are unchanged. This
is a frontend-only change with no migration and no new endpoint.

The only new failure mode is a picker left unmatched: it yields `''`, which the
existing required-field checks and disabled submit buttons already handle.

## Testing

New unit suites:

- `SuggestionInput.test.jsx` — options render as datalist values, free text
  fires `onChange`, two instances get distinct list ids, other input props are
  forwarded.
- `EntityCombobox.test.jsx` — filtering by substring; click commits; Enter
  commits the active option; exact-label typing commits; duplicate labels do
  not auto-commit; disabled options are skipped and unselectable; unmatched
  text clears the value and shows the hint; Escape restores the selected label;
  selected label renders when closed; a parent value reset clears the typed
  query.

Integration coverage (one new assertion per converted call site where it is
cheap, plus the migrated existing assertions):

- Recipe create still cascades company → LC → PI → invoice and still disables
  the child until its parent is picked.
- Link-PIs still lists existing LCs and refuses to submit without one.
- The sale's payment target still limits the invoice list to open invoices.
- Chemicals: typing an unlisted reason saves it; the unit accepts a typed unit.
- Multi-PI: company deselects to an unmatched value and blocks save; unit
  accepts a typed unit.

Existing tests that drive the converted fields must be migrated — roughly 23
assertions across `MultiPICreateModal`, `ReviewModal`, `LinkPIsModal`,
`SaleDetailModal`, `Chemicals` and `Recipes`. Several currently set a select by
id (`fireEvent.change(company, { target: { value: '7' } })`); they will pick by
visible label instead. `CommercialFilters` and `Reports` tests are untouched.

Gate: `npm test`, `npx oxlint`, `npx vite build`, then copy `dist/` →
`react_frontend/`.

## Risks

- **Keyboard and mobile regression.** A hand-rolled combobox is easier to get
  wrong than a native select. Mitigated by the explicit keyboard tests above and
  by keeping the native `<input>` as the focusable element.
- **Cascading state.** A child combobox must clear when its parent changes, and
  must not display a stale label. Mitigated by the parent-reset rule and a
  dedicated test.
- **Duplicate labels.** Two companies or two LCs sharing a label would make
  "type the exact name" ambiguous. Auto-commit only when the label match is
  unique; otherwise the user picks from the list.
- **Test churn.** The migrated assertions are part of the task; no behaviour is
  being removed from the app, only the control changes.

## Acceptance criteria

1. Every field in the scope table accepts typing and offers suggestions.
2. Text fields save any typed value (cap-respecting); record fields can only
   save a value from the list, and unmatched text blocks submit with a visible
   hint.
3. Disabled LC/PI entries remain visible and unselectable.
4. No request payload, API, schema or permission change.
5. The product-name field behaves exactly as before.
6. Full frontend gate is green and `react_frontend/` holds the new build.
