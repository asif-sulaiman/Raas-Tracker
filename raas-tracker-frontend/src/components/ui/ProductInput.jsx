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
