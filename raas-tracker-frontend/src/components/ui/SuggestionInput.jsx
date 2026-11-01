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
