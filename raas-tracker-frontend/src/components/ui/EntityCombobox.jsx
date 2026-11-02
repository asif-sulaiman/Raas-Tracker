// raas-tracker-frontend/src/components/ui/EntityCombobox.jsx
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

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
  const [searching, setSearching] = useState(false);
  const [open, setOpen] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [anchor, setAnchor] = useState(null);

  const selected = options.find((option) => String(option.value) === String(value));
  const filtered = useMemo(() => {
    // Focusing a field that already holds a pick lists every option; only typed
    // text narrows the list.
    const needle = normalize(searching ? query : '');
    if (!needle) return options;
    return options.filter((option) => normalize(option.label).includes(needle));
  }, [options, query, searching]);

  // A parent reset (the recipe cascade clearing a child) must not leave stale text.
  // Echoes of our own picks are skipped; any other value change wins over the query.
  useEffect(() => {
    if (String(emittedRef.current) === String(value)) {
      emittedRef.current = undefined;
      return;
    }
    setQuery('');
    setSearching(false);
    setOpen(false);
    setInvalid(false);
  }, [value]);

  // The list renders in a portal so a clipping ancestor (`overflow-hidden`)
  // cannot cut it off; measure the input to place it, flipping up when the
  // space below is too small.
  useLayoutEffect(() => {
    if (!open) {
      // Closing forgets the last geometry so a reopen never flashes a stale popup.
      // oxlint-disable-next-line react/set-state-in-effect
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
      const next = {
        left: rect.left,
        width: rect.width,
        top: up ? undefined : rect.bottom + gap,
        bottom: up ? window.innerHeight - rect.top + gap : undefined,
        maxHeight: Math.max(120, Math.min(240, (up ? above : below) - gap)),
      };
      // Scrolling inside the popup fires the capture listener too; skip the
      // render when nothing actually moved.
      setAnchor((prev) => {
        const unchanged = prev
          && prev.left === next.left
          && prev.width === next.width
          && prev.top === next.top
          && prev.bottom === next.bottom
          && prev.maxHeight === next.maxHeight;
        return unchanged ? prev : next;
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
    setSearching(false);
    setInvalid(false);
    setOpen(false);
    setActiveIndex(-1);
  }

  const firstEnabled = (list) => list.findIndex((option) => !option.disabled);

  function filteredFor(text) {
    const needle = normalize(text);
    if (!needle) return options;
    return options.filter((option) => normalize(option.label).includes(needle));
  }

  function handleChange(event) {
    const text = event.target.value;
    setQuery(text);
    setSearching(true);
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

  function handleFocus() {
    if (inputProps.disabled) return;
    focusValueRef.current = String(value);
    setQuery(selected ? selected.label : invalid ? query : '');
    setSearching(false);
    setOpen(true);
    setActiveIndex(-1);
    // Select the picked label so the first keystroke replaces it and searches.
    inputRef.current?.select();
  }

  function handleBlur() {
    const text = query;
    setSearching(false);
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
      setSearching(false);
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
        </ul>,
        document.body,
      )}
      {invalid && (
        <p id={hintId} role="status" className="mt-1 text-xs font-medium text-rose-600 dark:text-rose-400">
          {hint}
        </p>
      )}
    </div>
  );
}
