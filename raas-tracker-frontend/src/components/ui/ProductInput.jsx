import { useId } from 'react';
import { PRODUCT_NAMES } from '../../data/products';

/**
 * Product name field with the 2026 catalog as native suggestions.
 *
 * A plain <input list> (combobox) rather than a <select>: a parsed PI line or a
 * product released after the catalog was last updated can still be typed, while
 * the 70 known names appear as a dropdown. Keep it a drop-in replacement for a
 * text input — it forwards value/onChange/className/placeholder/aria-label.
 */
export default function ProductInput({ className = '', ...props }) {
  const listId = useId();
  return (
    <>
      <input list={listId} className={className} {...props} />
      <datalist id={listId}>
        {PRODUCT_NAMES.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
    </>
  );
}
