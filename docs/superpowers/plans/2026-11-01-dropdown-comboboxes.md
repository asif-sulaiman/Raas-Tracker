# Dropdown Comboboxes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every data-entry dropdown becomes type-or-pick: datalist suggestions with free text for stored strings, a must-match searchable combobox for record picks.

**Architecture:** Two primitives. `SuggestionInput` wraps the existing `<input list>` + `<datalist>` and takes an `options: string[]` prop; `ProductInput` becomes a thin wrapper over it. `EntityCombobox` is a hand-rolled ARIA combobox that filters `{ value, label, disabled }` options and only ever emits a value present in the list, or `''`. Call sites swap the control, never the form state, so no request payload, API, schema or permission changes.

**Tech Stack:** React 19 function components, plain JSX, Tailwind 4, Vitest + React Testing Library, oxlint, Vite.

**Spec:** `docs/superpowers/specs/2026-11-01-dropdown-combobox-design.md`

## Global Constraints

- Frontend only. No change to `flask_app.py`, `raas_tracker/**`, SQL, migrations, Pydantic models or permissions.
- No new dependency. No TypeScript. Plain `.jsx` with hooks.
- Text fields accept any typed value; record fields emit only a value from `options`, or `''`.
- Keep every existing label, `aria-label`, placeholder intent, disabled state and dark-mode class.
- Do not stage the pre-existing unrelated modified files (`.github/workflows/ci.yml`, `.opencode/opencode.json`, `AGENTS.md`, `README.md`, `opencode.json`, `raas_tracker/audit.py`, `raas_tracker/cli.py`, `raas_tracker/stock.py`, `raas_tracker/uploads.py`, `requirements-dev.txt`, untracked `ruff.toml`, `pyrightconfig.json`, `fetchSaleInvoiceLines.js`). Stage only the files named in each task.
- Run frontend commands from `raas-tracker-frontend/`.
- One atomic commit per task, conventional prefix.

---

### Task 1: `SuggestionInput` extracted from `ProductInput`

**Files:**
- Create: `raas-tracker-frontend/src/components/ui/SuggestionInput.jsx`
- Create: `raas-tracker-frontend/src/components/ui/SuggestionInput.test.jsx`
- Modify: `raas-tracker-frontend/src/components/ui/ProductInput.jsx`

**Interfaces:**
- Produces: `SuggestionInput({ options = [], className = '', ...inputProps })` — renders `<input list>` + `<datalist>`; free text always allowed.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing test**

```jsx
// raas-tracker-frontend/src/components/ui/SuggestionInput.test.jsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import SuggestionInput from './SuggestionInput';

describe('SuggestionInput', () => {
  it('renders every option as a datalist suggestion', () => {
    render(<SuggestionInput options={['KG', 'L', 'DRUM']} value="" onChange={() => {}} aria-label="Unit" />);
    const input = screen.getByLabelText('Unit');
    const listId = input.getAttribute('list');
    const values = Array.from(document.querySelectorAll(`#${CSS.escape(listId)} option`)).map(
      (o) => o.getAttribute('value'),
    );
    expect(values).toEqual(['KG', 'L', 'DRUM']);
  });

  it('accepts text that is not in the list', () => {
    const onChange = vi.fn();
    render(<SuggestionInput options={['KG']} value="" onChange={onChange} aria-label="Unit" />);
    fireEvent.change(screen.getByLabelText('Unit'), { target: { value: 'PALLET' } });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('gives each instance its own datalist', () => {
    render(
      <>
        <SuggestionInput options={['KG']} value="" onChange={() => {}} aria-label="First" />
        <SuggestionInput options={['KG']} value="" onChange={() => {}} aria-label="Second" />
      </>,
    );
    const first = screen.getByLabelText('First').getAttribute('list');
    const second = screen.getByLabelText('Second').getAttribute('list');
    expect(first).not.toBe(second);
  });

  it('forwards input props untouched', () => {
    render(
      <SuggestionInput
        options={['KG']}
        value="KG"
        onChange={() => {}}
        className="row-input"
        placeholder="Unit"
        maxLength={120}
        aria-label="Unit"
      />,
    );
    const input = screen.getByLabelText('Unit');
    expect(input).toHaveValue('KG');
    expect(input).toHaveClass('row-input');
    expect(input).toHaveAttribute('placeholder', 'Unit');
    expect(input).toHaveAttribute('maxlength', '120');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/components/ui/SuggestionInput.test.jsx`
Expected: FAIL — `Failed to resolve import "./SuggestionInput"`.

- [ ] **Step 3: Write the implementation**

```jsx
// raas-tracker-frontend/src/components/ui/SuggestionInput.jsx
import { useId } from 'react';

/**
 * Text field with native suggestions.
 *
 * A plain <input list> (combobox) rather than a <select>: the suggestions are
 * only hints, and any typed value is accepted. Keep it a drop-in replacement
 * for a text input — it forwards value/onChange/className/placeholder/aria-label
 * and every other input prop.
 */
export default function SuggestionInput({ options = [], className = '', ...inputProps }) {
  const listId = useId();
  return (
    <>
      <input list={listId} className={className} {...inputProps} />
      <datalist id={listId}>
        {options.map((option) => (
          <option key={option} value={option} />
        ))}
      </datalist>
    </>
  );
}
```

Replace `ProductInput.jsx` with a wrapper (its existing tests must keep passing unchanged):

```jsx
// raas-tracker-frontend/src/components/ui/ProductInput.jsx
import SuggestionInput from './SuggestionInput';
import { PRODUCT_NAMES } from '../../data/products';

/**
 * Product name field backed by the 2026 catalog.
 *
 * Suggestions only: a parsed PI line or a product released after the catalog
 * was last updated can still be typed.
 */
export default function ProductInput(props) {
  return <SuggestionInput options={PRODUCT_NAMES} {...props} />;
}
```

- [ ] **Step 4: Run both suites**

Run: `npx vitest run src/components/ui/SuggestionInput.test.jsx src/components/ui/ProductInput.test.jsx`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add raas-tracker-frontend/src/components/ui/SuggestionInput.jsx \
        raas-tracker-frontend/src/components/ui/SuggestionInput.test.jsx \
        raas-tracker-frontend/src/components/ui/ProductInput.jsx
git commit -m "refactor(ui): extract SuggestionInput from ProductInput"
```

---

### Task 2: `EntityCombobox`

**Files:**
- Create: `raas-tracker-frontend/src/components/ui/EntityCombobox.jsx`
- Create: `raas-tracker-frontend/src/components/ui/EntityCombobox.test.jsx`

**Interfaces:**
- Produces: `EntityCombobox({ options, value, onChange, className, placeholder, emptyMessage, hint, id, ...inputProps })` where `options: Array<{ value: string|number, label: string, disabled?: boolean }>`. Emits `String(option.value)` on a pick and `''` on an unmatched pick.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing tests**

```jsx
// raas-tracker-frontend/src/components/ui/EntityCombobox.test.jsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import EntityCombobox from './EntityCombobox';

const OPTIONS = [
  { value: '1', label: 'Acme' },
  { value: '2', label: 'Beta' },
  { value: '3', label: 'Gamma', disabled: true },
];

function Controlled({ onChange, initial = '' }) {
  const [value, setValue] = useState(initial);
  return (
    <EntityCombobox
      aria-label="Company"
      options={OPTIONS}
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

describe('EntityCombobox', () => {
  it('filters options by substring and lists all on focus', () => {
    render(<Controlled />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Acme', 'Beta', 'Gamma',
    ]);
    fireEvent.change(input, { target: { value: 'bet' } });
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Beta']);
  });

  it('commits a value when an option is clicked', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.click(screen.getByRole('option', { name: 'Beta' }));
    expect(onChange).toHaveBeenCalledWith('2');
    expect(input).toHaveValue('Beta');
  });

  it('commits an exact label typed in full', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Beta' } });
    expect(onChange).toHaveBeenCalledWith('2');
  });

  it('does not auto-commit when two options share the label', () => {
    const onChange = vi.fn();
    render(
      <EntityCombobox
        aria-label="Company"
        options={[{ value: '1', label: 'Acme' }, { value: '2', label: 'Acme' }]}
        value=""
        onChange={onChange}
      />,
    );
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Acme' } });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('skips disabled options and refuses to commit them', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.click(screen.getByRole('option', { name: 'Gamma' }));
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.focus(input);
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenLastCalledWith('2');
  });

  it('commits the active option on Enter', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Bet' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('2');
  });

  it('clears the value and shows a hint for unmatched text', () => {
    const onChange = vi.fn();
    render(<Controlled onChange={onChange} initial="1" />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Zzz' } });
    expect(onChange).toHaveBeenLastCalledWith('');
    fireEvent.blur(input);
    expect(screen.getByRole('status').textContent).toContain('No matching record');
    expect(input).toHaveValue('Zzz');
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });

  it('reverts to the selected label on Escape', () => {
    render(<Controlled initial="1" />);
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Be' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(input).toHaveValue('Acme');
  });

  it('shows the selected label while closed', () => {
    render(<EntityCombobox aria-label="Company" options={OPTIONS} value="2" onChange={() => {}} />);
    expect(screen.getByLabelText('Company')).toHaveValue('Beta');
  });

  it('clears the typed query when the parent resets the value', () => {
    const { rerender } = render(
      <EntityCombobox aria-label="Company" options={OPTIONS} value="1" onChange={() => {}} />,
    );
    const input = screen.getByLabelText('Company');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Be' } });
    rerender(<EntityCombobox aria-label="Company" options={OPTIONS} value="" onChange={() => {}} />);
    expect(input).toHaveValue('');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/components/ui/EntityCombobox.test.jsx`
Expected: FAIL — cannot resolve `./EntityCombobox`.

- [ ] **Step 3: Write the implementation**

```jsx
// raas-tracker-frontend/src/components/ui/EntityCombobox.jsx
import { useEffect, useId, useMemo, useRef, useState } from 'react';

const normalize = (text) => String(text ?? '').toLowerCase().trim();

/**
 * Searchable picker for a record-valued field.
 *
 * The typed text filters `options`; only a pick from the list is emitted, so an
 * unknown name can never reach the API as a foreign key. `options` entries are
 * `{ value, label, disabled }`; `value` is compared and emitted as a string.
 */
export default function EntityCombobox({
  options = [],
  value = '',
  onChange,
  className = '',
  placeholder = 'Search…',
  emptyMessage = 'No matches',
  hint = 'No matching record — pick one from the list',
  id,
  ...inputProps
}) {
  const listId = useId();
  const hintId = `${listId}-hint`;
  const inputRef = useRef(null);
  const emittedRef = useRef(undefined);
  const focusValueRef = useRef('');
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const selected = options.find((option) => String(option.value) === String(value));
  const filtered = useMemo(() => {
    const needle = normalize(query);
    if (!needle) return options;
    return options.filter((option) => normalize(option.label).includes(needle));
  }, [options, query]);

  // A parent reset (the recipe cascade clearing a child) must not leave stale text.
  useEffect(() => {
    if (String(emittedRef.current) === String(value)) {
      emittedRef.current = undefined;
      return;
    }
    if (document.activeElement === inputRef.current) {
      setQuery('');
      setOpen(false);
      setInvalid(false);
    }
  }, [value]);

  const display = open
    ? query
    : invalid
      ? query
      : selected
        ? selected.label
        : value === '' || value == null
          ? ''
          : String(value);

  function emit(next) {
    emittedRef.current = String(next);
    onChange?.(String(next));
  }

  function commit(option) {
    if (!option || option.disabled) return;
    emit(option.value);
    setQuery('');
    setInvalid(false);
    setOpen(false);
    setActiveIndex(-1);
  }

  const firstEnabled = (list) => list.findIndex((option) => !option.disabled);

  function handleChange(event) {
    const text = event.target.value;
    setQuery(text);
    setOpen(true);
    setInvalid(false);

    const exact = options.filter(
      (option) => !option.disabled && normalize(option.label) === normalize(text),
    );
    if (exact.length === 1) {
      commit(exact[0]);
      return;
    }

    setActiveIndex(firstEnabled(filteredFor(text)));

    if (value !== '' && normalize(text) !== normalize(selected?.label)) {
      emit('');
    }
  }

  function filteredFor(text) {
    const needle = normalize(text);
    if (!needle) return options;
    return options.filter((option) => normalize(option.label).includes(needle));
  }

  function handleFocus() {
    if (inputProps.disabled) return;
    focusValueRef.current = String(value);
    setQuery(selected ? selected.label : invalid ? query : '');
    setOpen(true);
    setActiveIndex(-1);
  }

  function handleBlur() {
    const text = query;
    const exact = options.filter(
      (option) => !option.disabled && normalize(option.label) === normalize(text),
    );
    if (exact.length === 1) {
      commit(exact[0]);
      return;
    }
    setOpen(false);
    if (!text.trim() || normalize(text) === normalize(selected?.label)) {
      setInvalid(false);
      setQuery('');
      return;
    }
    setInvalid(true);
    setQuery(text);
  }

  function handleKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      if (filtered.length === 0) return;
      for (let step = 1; step <= filtered.length; step += 1) {
        const candidate = (Math.max(activeIndex, -1) + step) % filtered.length;
        if (!filtered[candidate].disabled) {
          setActiveIndex(candidate);
          return;
        }
      }
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      if (filtered.length === 0) return;
      for (let step = 1; step <= filtered.length; step += 1) {
        const base = activeIndex <= 0 ? filtered.length : activeIndex;
        const candidate = (base - step + filtered.length) % filtered.length;
        if (!filtered[candidate].disabled) {
          setActiveIndex(candidate);
          return;
        }
      }
      return;
    }
    if (event.key === 'Enter' && open) {
      const option = filtered[activeIndex];
      if (option) {
        event.preventDefault();
        commit(option);
      }
      return;
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
      setInvalid(false);
      setQuery('');
      // Typing cleared the value; Escape puts the focus-time pick back.
      if (focusValueRef.current !== '' && String(value) === '') {
        emit(focusValueRef.current);
      }
    }
  }

  return (
    <div className="relative">
      <input
        {...inputProps}
        ref={inputRef}
        id={id}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={
          open && activeIndex >= 0 ? `${listId}-opt-${activeIndex}` : undefined
        }
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? hintId : inputProps['aria-describedby']}
        value={display}
        placeholder={placeholder}
        onChange={handleChange}
        onFocus={handleFocus}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        className={className}
      />
      {open && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-30 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg dark:border-slate-700 dark:bg-slate-800"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-xs text-slate-400 dark:text-slate-500">{emptyMessage}</li>
          ) : (
            filtered.map((option, index) => (
              <li
                key={`${option.value}-${index}`}
                id={`${listId}-opt-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                aria-disabled={option.disabled || undefined}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => commit(option)}
                className={
                  option.disabled
                    ? 'cursor-not-allowed px-3 py-2 text-slate-400 dark:text-slate-500'
                    : index === activeIndex
                      ? 'cursor-pointer bg-blue-50 px-3 py-2 text-slate-900 dark:bg-slate-700 dark:text-white'
                      : 'cursor-pointer px-3 py-2 text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-700'
                }
              >
                {option.label}
              </li>
            ))
          )}
        </ul>
      )}
      {invalid && (
        <p id={hintId} role="status" className="mt-1 text-xs font-medium text-rose-600 dark:text-rose-400">
          {hint}
        </p>
      )}
    </div>
  );
}
```

Note: `handleChange` calls `filteredFor(text)` because the `filtered` memo still reflects the previous query on that render.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/components/ui/EntityCombobox.test.jsx`
Expected: PASS, 10 tests. If the disabled-skip test lands on the wrong option, assert on `input.getAttribute('aria-activedescendant')` before Enter rather than changing the component.

- [ ] **Step 5: Commit**

```bash
git add raas-tracker-frontend/src/components/ui/EntityCombobox.jsx \
        raas-tracker-frontend/src/components/ui/EntityCombobox.test.jsx
git commit -m "feat(ui): add a must-match EntityCombobox"
```

---

### Task 3: Text fields — units and adjustment reason

**Files:**
- Modify: `raas-tracker-frontend/src/components/sales/MultiPICreateModal.jsx`
- Modify: `raas-tracker-frontend/src/components/sales/ReviewModal.jsx`
- Modify: `raas-tracker-frontend/src/pages/Chemicals.jsx`
- Modify: `raas-tracker-frontend/src/components/forms/UnitMapping.jsx`
- Test: `raas-tracker-frontend/src/components/sales/MultiPICreateModal.test.jsx`
- Test: `raas-tracker-frontend/src/pages/Chemicals.test.jsx`

**Interfaces:**
- Consumes: `SuggestionInput` from Task 1.
- Produces: no new interface; these fields keep their current state (`item.unit`, `addUnit`, `adjustReason`, `f.target`).

- [ ] **Step 1: Write the failing tests**

Add to `MultiPICreateModal.test.jsx`:

```jsx
  it('accepts a unit that is not in the common list', async () => {
    const posts = [];
    const onSaved = vi.fn();
    renderModal({ onSaved, postsOpts: { posts } });

    const company = await screen.findByLabelText('Company');
    fireEvent.change(company, { target: { value: 'Acme' } });

    await fillDoc(1, 'PI-UNIT-TEST');
    fireEvent.change(screen.getByLabelText('Unit document 1 row 1'), {
      target: { value: 'PALLET' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Save 1 PI to PI Issued/ }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].sales[0].items[0].unit).toBe('PALLET');
  });
```

Add to `Chemicals.test.jsx` inside the add/adjust describes:

```jsx
  it('saves a typed reason and a typed unit', async () => {
    const posts = [];
    renderChemicals({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    fireEvent.change(screen.getByLabelText('New quantity'), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Spill cleanup' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Adjustment' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ reason: 'Spill cleanup' });
  });
```

Run both:

`npx vitest run src/components/sales/MultiPICreateModal.test.jsx src/pages/Chemicals.test.jsx`

Expected: the new unit test FAILS (native `<select>` ignores `PALLET` and keeps `KG`); the reason test FAILS (no such `<option>`).

- [ ] **Step 2: Replace the four unit controls**

`MultiPICreateModal.jsx` — add `import SuggestionInput from '../ui/SuggestionInput';`, then:

```jsx
<select
  aria-label={`Unit document ${docIdx + 1} row ${rowIdx + 1}`}
  value={item.unit || 'KG'}
  onChange={(e) => updateItem(doc.key, item.key, 'unit', e.target.value)}
  className={rowInputCls}
>
  {COMMON_UNITS.map((u) => (
    <option key={u} value={u}>{u}</option>
  ))}
</select>
```

becomes

```jsx
<SuggestionInput
  aria-label={`Unit document ${docIdx + 1} row ${rowIdx + 1}`}
  options={COMMON_UNITS}
  value={item.unit || 'KG'}
  onChange={(e) => updateItem(doc.key, item.key, 'unit', e.target.value)}
  className={rowInputCls}
/>
```

`ReviewModal.jsx` — same import, then:

```jsx
<SuggestionInput
  aria-label="Unit"
  options={COMMON_UNITS}
  value={item.unit || 'KG'}
  onChange={(e) => updateItem(item.key, 'unit', e.target.value)}
  className={inputCls}
/>
```

`Chemicals.jsx` — add `import SuggestionInput from '../ui/SuggestionInput';`, a module constant, and replace the add-unit select:

```jsx
const REASON_SUGGESTIONS = [
  'Physical count correction',
  'Transfer from another warehouse',
  'Disposal / expiry',
  'Supplier delivery',
];
```

```jsx
<SuggestionInput
  aria-label="Unit"
  options={COMMON_UNITS}
  value={addUnit}
  onChange={(e) => setAddUnit(e.target.value)}
  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
/>
```

Then replace the adjust-reason select:

```jsx
<SuggestionInput
  aria-label="Reason"
  options={REASON_SUGGESTIONS}
  value={adjustReason}
  onChange={(e) => setAdjustReason(e.target.value)}
  maxLength={120}
  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
/>
```

`UnitMapping.jsx` — add the import and replace the target-unit select (it sits inside its `<label>`, so no `aria-label`):

```jsx
<SuggestionInput
  options={[...new Set([...(knownUnits.length ? knownUnits : COMMON_UNITS), ...g.dbUnits])]}
  value={f.target}
  onChange={(e) => setForm(g.unit, { target: e.target.value })}
  className="ml-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm text-slate-900 dark:text-white"
/>
```

- [ ] **Step 3: Run the suites**

Run: `npx vitest run src/components/sales/MultiPICreateModal.test.jsx src/components/sales/ReviewModal.test.jsx src/pages/Chemicals.test.jsx src/components/forms/UnitMapping.test.jsx`
Expected: PASS. The old `fireEvent.change(..., { value: 'L' })` / `'DRUM'` tests keep working because the control is now a text input.

- [ ] **Step 4: Commit**

```bash
git add raas-tracker-frontend/src/components/sales/MultiPICreateModal.jsx \
        raas-tracker-frontend/src/components/sales/ReviewModal.jsx \
        raas-tracker-frontend/src/pages/Chemicals.jsx \
        raas-tracker-frontend/src/components/forms/UnitMapping.jsx \
        raas-tracker-frontend/src/components/sales/MultiPICreateModal.test.jsx \
        raas-tracker-frontend/src/pages/Chemicals.test.jsx
git commit -m "feat(ui): free-text units and adjustment reasons"
```

---

### Task 4: Company pickers

**Files:**
- Modify: `raas-tracker-frontend/src/components/sales/MultiPICreateModal.jsx`
- Modify: `raas-tracker-frontend/src/components/sales/ReviewModal.jsx`
- Modify: `raas-tracker-frontend/src/pages/Recipes.jsx`
- Test: `raas-tracker-frontend/src/components/sales/MultiPICreateModal.test.jsx`
- Test: `raas-tracker-frontend/src/components/sales/ReviewModal.test.jsx`
- Test: `raas-tracker-frontend/src/pages/Recipes.test.jsx`

**Interfaces:**
- Consumes: `EntityCombobox` from Task 2.
- Produces: company state stays a string id; `handleCompanyChange` / `setCompanyId` unchanged.

- [ ] **Step 1: Migrate the tests first (they must fail)**

`MultiPICreateModal.test.jsx:87,108,136` — replace `{ target: { value: '7' } }` with `{ target: { value: 'Acme' } }` (exact label auto-commits).

`ReviewModal.test.jsx` — every company assertion switches from a select's id to the input's label:

| Line | Old | New |
|---|---|---|
| 79-80 | `expect(select.value).toBe('7')` | `expect(input).toHaveValue('Acme')` |
| 96-97 | same | same |
| 122-123 | same | same |
| 141-142 | `toBe('1')` | `toHaveValue('ACME Corp')` |
| 147-148 | `toBe('2')` | `toHaveValue('COLOR CITY LTD.')` |
| 153-154 | `toBe('3')` | `toHaveValue('ABC')` |
| 159-160 | `toBe('4')` | `toHaveValue('HUEWASH LTW Soaping')` |
| 165-166 | `toBe('')` | `toHaveValue('')` |
| 173-174 | `toBe('')` | `toHaveValue('')` |

Rename the local `select` to `input` in those blocks (e.g. `const input = await screen.findByLabelText('Company');`).

`Recipes.test.jsx` — change every `{ target: { value: '7' } }` on `/^Company/` to `{ target: { value: 'Acme' } }` (lines 295, 318, 332, 349, 423).

Add one new test to `ReviewModal.test.jsx`:

```jsx
  it('rejects a company name that is not in the register', async () => {
    const saved = vi.fn(async () => {});
    renderReview(saved);
    const input = await screen.findByLabelText('Company');
    await waitFor(() => expect(input).toHaveValue('Acme'));
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'Ghost Co' } });
    fireEvent.blur(input);
    expect(screen.getByRole('status').textContent).toContain('No matching record');
    fireEvent.click(screen.getByRole('button', { name: 'Save to PI Issued' }));
    expect(await screen.findByText(/Select a company/i)).toBeTruthy();
    expect(saved).not.toHaveBeenCalled();
  });
```

Run: `npx vitest run src/components/sales/MultiPICreateModal.test.jsx src/components/sales/ReviewModal.test.jsx src/pages/Recipes.test.jsx`
Expected: FAIL — the fields are still `<select>`s, so typing a name yields no id and the save payload/company id is missing.

- [ ] **Step 2: Replace the three company selects**

`MultiPICreateModal.jsx` (import `EntityCombobox`):

```jsx
<EntityCombobox
  aria-label="Company"
  options={companies.map((c) => ({ value: c.id, label: c.name }))}
  value={companyId}
  onChange={(next) => {
    setCompanyId(next);
    const picked = companies.find((c) => String(c.id) === next);
    setClientName(picked ? picked.name : '');
  }}
  placeholder="Search companies…"
  className={inputCls}
/>
```

`ReviewModal.jsx`:

```jsx
<EntityCombobox
  aria-label="Company"
  options={companies.map((c) => ({ value: c.id, label: c.name }))}
  value={companyId}
  onChange={(next) => {
    setCompanyId(next);
    const picked = companies.find((c) => String(c.id) === next);
    setClientName(picked ? picked.name : '');
  }}
  placeholder="Search companies…"
  className={inputCls}
/>
```

`Recipes.jsx` (the `<label htmlFor="recipe-company">` stays; the input receives the id):

```jsx
<EntityCombobox
  id="recipe-company"
  options={companies.map((c) => ({ value: c.id, label: c.name }))}
  value={companyId}
  onChange={handleCompanyChange}
  placeholder="Search companies…"
  className={inputCls}
/>
```

- [ ] **Step 3: Run the suites**

Run: `npx vitest run src/components/sales/MultiPICreateModal.test.jsx src/components/sales/ReviewModal.test.jsx src/pages/Recipes.test.jsx`
Expected: PASS. Note `Recipes.test.jsx` also selects LC/PI/invoice by id — those tests fail here and are fixed in Task 5; if they block the run, run the two sales suites now and re-run Recipes after Task 5. Do not "fix" the cascade by making the company picker accept ids.

- [ ] **Step 4: Commit**

```bash
git add raas-tracker-frontend/src/components/sales/MultiPICreateModal.jsx \
        raas-tracker-frontend/src/components/sales/ReviewModal.jsx \
        raas-tracker-frontend/src/pages/Recipes.jsx \
        raas-tracker-frontend/src/components/sales/MultiPICreateModal.test.jsx \
        raas-tracker-frontend/src/components/sales/ReviewModal.test.jsx \
        raas-tracker-frontend/src/pages/Recipes.test.jsx
git commit -m "feat(sales): searchable company pickers"
```

---

### Task 5: LC, PI and invoice pickers

**Files:**
- Modify: `raas-tracker-frontend/src/components/sales/LinkPIsModal.jsx`
- Modify: `raas-tracker-frontend/src/components/sales/SaleDetailModal.jsx`
- Modify: `raas-tracker-frontend/src/pages/Recipes.jsx`
- Test: `raas-tracker-frontend/src/components/sales/LinkPIsModal.test.jsx`
- Test: `raas-tracker-frontend/src/components/sales/SaleDetailModal.test.jsx`
- Test: `raas-tracker-frontend/src/pages/Recipes.test.jsx`

**Interfaces:**
- Consumes: `EntityCombobox` from Task 2.
- Produces: `lcId`, `saleId`, `invoiceId`, `targetId`, `lcId` (LinkPIs) stay string ids; the cascade handlers are untouched.

- [ ] **Step 1: Migrate the tests first**

`LinkPIsModal.test.jsx:91`:

```jsx
fireEvent.change(screen.getByLabelText('Existing LC'), { target: { value: 'LC-100' } });
fireEvent.keyDown(screen.getByLabelText('Existing LC'), { key: 'Enter' });
```

`SaleDetailModal.test.jsx:287-294`:

```jsx
const input = await screen.findByLabelText('Target invoice');
fireEvent.focus(input);
await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(0));
const options = screen.getAllByRole('option');
// A booked invoice is frozen: lines may not be added behind its back.
expect(options).toHaveLength(1);
expect(options[0].textContent).toContain('INV-PLAN');
expect(options[0].textContent).not.toContain('INV-BOOK');
```

`Recipes.test.jsx` — replace the `pickInvoice` helper:

```jsx
async function pickInvoice() {
  fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: 'Acme' } });

  const lc = await screen.findByLabelText(/^LC/);
  fireEvent.focus(lc);
  fireEvent.change(lc, { target: { value: 'LC-2026-001' } });
  fireEvent.keyDown(lc, { key: 'Enter' });

  const pi = await screen.findByLabelText(/^PI/);
  await waitFor(() => expect(pi.disabled).toBe(false));
  fireEvent.focus(pi);
  fireEvent.change(pi, { target: { value: 'PI-2026-014' } });
  fireEvent.keyDown(pi, { key: 'Enter' });

  const invoice = await screen.findByLabelText(/^Invoice/);
  await waitFor(() => expect(invoice.disabled).toBe(false));
  fireEvent.focus(invoice);
  fireEvent.change(invoice, { target: { value: 'INV-014' } });
  fireEvent.keyDown(invoice, { key: 'Enter' });

  await screen.findByText('3 products on this invoice');
}
```

Then rewrite the option-inspection tests so the list is open before querying it, and disabled rows are asserted with `aria-disabled`:

```jsx
  it('labels running LCs by their producible PIs and disables empty ones', async () => {
    renderRecipes();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: 'Acme' } });

    const lc = screen.getByLabelText(/^LC/);
    fireEvent.focus(lc);

    const withWork = await screen.findByRole('option', { name: /LC-2026-001/ });
    expect(withWork.textContent).toContain('1 PI in production');
    expect(withWork.textContent).not.toContain('lc_received');

    const noWork = screen.getByRole('option', { name: /LC-2026-002/ });
    expect(noWork).toHaveAttribute('aria-disabled', 'true');
    expect(noWork.textContent).toContain('no invoices in production');
  });
```

```jsx
  it('keeps a PI with nothing left to produce visible but disabled', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: 'Acme' } });

    const lc = screen.getByLabelText(/^LC/);
    fireEvent.focus(lc);
    fireEvent.click(await screen.findByRole('option', { name: /LC-2026-001/ }));
    await waitFor(() => expect(called(fetchMock, '/api/lcs/1?open_only=true')).toBe(true));

    const pi = screen.getByLabelText(/^PI/);
    fireEvent.focus(pi);
    const openPi = await screen.findByRole('option', { name: /PI-2026-014/ });
    expect(openPi).not.toHaveAttribute('aria-disabled', 'true');
    expect(openPi.textContent).toContain('1 invoice in production');

    const donePi = screen.getByRole('option', { name: /PI-2026-015/ });
    expect(donePi).toHaveAttribute('aria-disabled', 'true');
    expect(donePi.textContent).toContain('no invoices in production');
  });
```

```jsx
  it('offers only invoices still in production', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: 'Acme' } });

    const lc = screen.getByLabelText(/^LC/);
    fireEvent.focus(lc);
    fireEvent.click(await screen.findByRole('option', { name: /LC-2026-001/ }));

    const pi = await screen.findByLabelText(/^PI/);
    await waitFor(() => expect(pi.disabled).toBe(false));
    fireEvent.focus(pi);
    fireEvent.click(await screen.findByRole('option', { name: /PI-2026-014/ }));

    await waitFor(() => expect(called(fetchMock, '/api/sales/5/invoices?producible=true')).toBe(true));
    fireEvent.focus(screen.getByLabelText(/^Invoice/));
    const option = await screen.findByRole('option', { name: /INV-014/ });
    expect(option).toBeTruthy();
  });
```

Run: `npx vitest run src/components/sales/LinkPIsModal.test.jsx src/components/sales/SaleDetailModal.test.jsx src/pages/Recipes.test.jsx`
Expected: FAIL — the controls are still selects.

- [ ] **Step 2: Replace the five controls**

`LinkPIsModal.jsx`:

```jsx
<EntityCombobox
  aria-label="Existing LC"
  options={existingLcs.map((l) => ({
    value: l.id,
    label: `${l.lc_number} — ${l.company_name || l.client_name || ''} (${l.pi_count ?? 0} PIs)`,
  }))}
  value={lcId}
  onChange={setLcId}
  placeholder="Search LCs…"
  className={inputCls}
/>
```

`SaleDetailModal.jsx`:

```jsx
<EntityCombobox
  aria-label="Target invoice"
  options={openInvoices.map((inv) => ({
    value: inv.invoice_id,
    label: `${inv.invoice_number} — ${inv.status}`,
  }))}
  value={targetId}
  onChange={setTargetId}
  className={inputCls}
/>
```

`Recipes.jsx` — LC:

```jsx
<EntityCombobox
  id="recipe-lc"
  options={companyLcs.map((l) => {
    const count = l.producible_pi_count ?? 0;
    return {
      value: l.id,
      label: `${l.lc_number} · ${count === 0
        ? 'no invoices in production'
        : `${count} PI${count === 1 ? '' : 's'} in production`}`,
      disabled: count === 0,
    };
  })}
  value={lcId}
  onChange={handleLcChange}
  disabled={!companyId}
  aria-describedby="recipe-lc-help"
  placeholder={companyId ? 'Search LCs…' : 'Select a company first'}
  className={clsx(inputCls, 'disabled:opacity-60')}
/>
```

PI:

```jsx
<EntityCombobox
  id="recipe-pi"
  options={lcPis.map((s) => {
    const count = s.producible_invoice_count ?? 0;
    return {
      value: s.id,
      label: `${s.pi_number || `PI #${s.id}`}${s.client_name ? ` — ${s.client_name}` : ''} · ${count === 0
        ? 'no invoices in production'
        : `${count} invoice${count === 1 ? '' : 's'} in production`}`,
      disabled: count === 0,
    };
  })}
  value={saleId}
  onChange={handleSaleChange}
  disabled={!lcId || loadingPis}
  placeholder={!lcId ? 'Select an LC first' : loadingPis ? 'Loading PIs…' : 'Search PIs…'}
  className={clsx(inputCls, 'disabled:opacity-60')}
/>
```

Invoice:

```jsx
<EntityCombobox
  id="recipe-invoice"
  options={invoices.map((inv) => {
    const id = inv.invoice_id ?? inv.id;
    return {
      value: id,
      label: `${inv.invoice_number || `Invoice #${id}`} · ${INVOICE_STATUS_LABELS[inv.status] || inv.status}`,
    };
  })}
  value={invoiceId}
  onChange={handleInvoiceChange}
  disabled={!saleId}
  placeholder={saleId ? 'Search invoices…' : 'Select a PI first'}
  className={clsx(inputCls, 'disabled:opacity-60')}
/>
```

- [ ] **Step 3: Run the suites**

Run: `npx vitest run src/components/sales/LinkPIsModal.test.jsx src/components/sales/SaleDetailModal.test.jsx src/pages/Recipes.test.jsx`
Expected: PASS. If a test queries an option before its list is open, add `fireEvent.focus(...)` on that field rather than removing the assertion.

- [ ] **Step 4: Commit**

```bash
git add raas-tracker-frontend/src/components/sales/LinkPIsModal.jsx \
        raas-tracker-frontend/src/components/sales/SaleDetailModal.jsx \
        raas-tracker-frontend/src/pages/Recipes.jsx \
        raas-tracker-frontend/src/components/sales/LinkPIsModal.test.jsx \
        raas-tracker-frontend/src/components/sales/SaleDetailModal.test.jsx \
        raas-tracker-frontend/src/pages/Recipes.test.jsx
git commit -m "feat(sales): searchable LC, PI and invoice pickers"
```

---

### Task 6: Recipe chemical picker

**Files:**
- Modify: `raas-tracker-frontend/src/pages/Recipes.jsx`
- Test: `raas-tracker-frontend/src/pages/Recipes.test.jsx`

**Interfaces:**
- Consumes: `EntityCombobox` from Task 2.
- Produces: the formulation card's `chemical` state stays the chemical **name**; the POST body is unchanged (`{ chemical, percentage }`).

- [ ] **Step 1: Add the failing test**

In `Recipes.test.jsx`, after "adds an ingredient to a specific product", add:

```jsx
  it('refuses a chemical that is not registered', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    const chemical = within(ash).getByLabelText('Chemical for Soda Ash');
    fireEvent.focus(chemical);
    fireEvent.change(chemical, { target: { value: 'Unobtainium' } });
    fireEvent.blur(chemical);

    expect(within(ash).getByRole('status').textContent).toContain('No matching record');
    fireEvent.change(within(ash).getByLabelText('Percentage for Soda Ash'), { target: { value: '10' } });
    fireEvent.click(within(ash).getByRole('button', { name: 'Add ingredient to Soda Ash' }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.some(
        ([url, options]) => String(url).includes('/api/recipes/by-id/') &&
          String(url).includes('/products/') && options?.method === 'POST',
      );
      expect(posted).toBe(false);
    });
  });
```

Run: `npx vitest run src/pages/Recipes.test.jsx -t "refuses a chemical"`
Expected: FAIL — no `role="status"` in the card.

- [ ] **Step 2: Replace the select**

```jsx
<EntityCombobox
  id={`chem-${product.product_name}`}
  options={available.map((c) => ({ value: c.name, label: c.name }))}
  value={chemical}
  onChange={setChemical}
  placeholder="Search chemicals…"
  className={inputCls}
/>
```

The `<label htmlFor={...}>Chemical</label>` stays; `id` on the combobox input labels it. The old `aria-label` stays on the component so `getByLabelText('Chemical for …')` keeps working:

```jsx
<EntityCombobox
  id={`chem-${product.product_name}`}
  aria-label={`Chemical for ${product.product_name}`}
  options={available.map((c) => ({ value: c.name, label: c.name }))}
  value={chemical}
  onChange={setChemical}
  placeholder="Search chemicals…"
  className={inputCls}
/>
```

- [ ] **Step 3: Run the suite**

Run: `npx vitest run src/pages/Recipes.test.jsx`
Expected: PASS. The existing "adds an ingredient" test still types `NaOH`, which is an exact label and commits.

- [ ] **Step 4: Commit**

```bash
git add raas-tracker-frontend/src/pages/Recipes.jsx \
        raas-tracker-frontend/src/pages/Recipes.test.jsx
git commit -m "feat(recipes): searchable chemical picker"
```

---

### Task 7: Docs, full gate and production build

**Files:**
- Modify: `docs/SPEC.md`
- Modify: `docs/PRD.md`
- Modify: `docs/plan.md`

**Interfaces:**
- Consumes: all earlier tasks.
- Produces: no code interface.

- [ ] **Step 1: Update the docs**

`docs/SPEC.md`, Frontend (Vitest) section: add to the coverage parenthetical

```
`EntityCombobox.test.jsx` / `SuggestionInput.test.jsx` (record pickers filter and must match an option, disabled rows are unselectable, unmatched text clears the value with a hint; text fields accept unlisted values), `ProductInput.test.jsx` (…unchanged…)
```

and replace the leading counts with the numbers from the gate run. Under **Lint**, keep `0 errors, 0 warnings`.

`docs/PRD.md`, Flow 2, extend the product-name note with one sentence:

> The same type-or-pick behaviour covers the other entry dropdowns: company, LC, PI, invoice and chemical must resolve to an existing record, while unit and adjustment reason accept a typed value the list does not contain.

`docs/plan.md`, after the PRODUCT-CATALOG section, add:

```markdown
## 🔎 Phase PRODUCT-COMBOBOXES: type-or-pick entry fields (completed 2026-11-01)

Every data-entry dropdown now accepts typing. Text-valued fields (unit,
adjustment reason) use a native `<input list>` (`SuggestionInput`) and accept
values outside the list; record-valued fields (company, LC, PI, invoice,
chemical) use `EntityCombobox`, which filters a must-match list, skips disabled
rows and emits `''` for unmatched text so a bogus foreign key cannot be saved.
Report/audit/stock filters and fixed enums are deliberately untouched. No API,
schema or permission change. Spec:
`docs/superpowers/specs/2026-11-01-dropdown-combobox-design.md`.

**Verification.** Frontend gate green (write the exact `Tests … passed` and
`Test Files … passed` totals printed by Step 2 here), oxlint 0/0, `vite build`
green, `dist/` copied to `react_frontend/`.
```

Record those two totals from `/tmp/vitest_combo_gate.log`; the section must not
ship with the earlier phase's counts.

- [ ] **Step 2: Run the full frontend gate**

```bash
npx vitest run > /tmp/vitest_combo_gate.log 2>&1; echo "EXIT:$?"; grep -E "Test Files|Tests " /tmp/vitest_combo_gate.log
npx oxlint
npx vite build
```

Expected: 0 failures, oxlint 0/0, build green. If a suite fails, fix the call site or the test — never weaken an assertion to pass.

- [ ] **Step 3: Copy the build and commit docs**

```bash
cd ..
rm -rf react_frontend/assets && cp -r raas-tracker-frontend/dist/. react_frontend/
git add docs/SPEC.md docs/PRD.md docs/plan.md
git commit -m "docs: record type-or-pick entry fields"
```

`react_frontend/` is gitignored; copying it is for the local server only.

---

## Self-Review Notes

- Spec coverage: two primitives (Tasks 1–2), text fields (3), company (4), LC/PI/invoice (5), chemical (6), gate/docs (7). Filters and enums are out of scope in the spec and untouched here.
- The recipe chemical is name-valued, not id-valued; Task 6 says so explicitly.
- `SuggestionInput` is a drop-in text input, so the unit/reason tests keep working; only the new unlisted-value tests are added.
- `EntityCombobox` never renders options while closed — every test that inspects a list must focus or type first. This is stated per task.
- State shapes do not change, so no payload/API task exists.
