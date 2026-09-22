/**
 * Single source of truth for chemical stock states.
 * out: exact zero. low: at/below the per-chemical reorder level (when set),
 * or below 20% of last month as a fallback heuristic. Otherwise matched.
 */
export function stockStatus(chem) {
  if (!chem || chem.qty === 0) return 'out';
  const level = chem.reorder_level || 0;
  if (level > 0 && chem.qty <= level) return 'low';
  if (chem.qty < (chem.balance_last_month || 0) * 0.2) return 'low';
  return 'matched';
}
