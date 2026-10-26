import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../context/AuthContext';

/**
 * The invoice figures of one sale: every PI line plus what has already been
 * invoiced against it, from `GET /api/sales/<id>/invoice-lines`.
 *
 * One request replaces the old N+1 (sale detail, then one items call per
 * invoice). The figures are informational: quantity is free relative to the
 * PI, so nothing here caps a write.
 *
 * Returns:
 *   lines: [{sale_item_id, product_name, unit, pi_quantity,
 *            invoiced_quantity, remaining, unit_price}]
 *   hasLineLessInvoices: a legacy invoice carries money no line backs, so
 *            `remaining` overstates what was billed; informational only.
 *   reload(): refetch and return the fresh `{lines, hasLineLessInvoices}`.
 */
export default function useInvoiceBudget(saleId) {
  const { apiFetch } = useAuth();
  const [lines, setLines] = useState([]);
  const [hasLineLessInvoices, setHasLineLessInvoices] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    if (!saleId) return { lines: [], hasLineLessInvoices: false };
    try {
      const res = await apiFetch(`/api/sales/${saleId}/invoice-lines`);
      const data = await res.json();
      const nextLines = Array.isArray(data?.lines) ? data.lines : [];
      const nextFlag = Boolean(data?.has_line_less_invoices);
      setLines(nextLines);
      setHasLineLessInvoices(nextFlag);
      setError(null);
      setLoading(false);
      return { lines: nextLines, hasLineLessInvoices: nextFlag };
    } catch (err) {
      setError(err?.message || 'Could not load the PI lines');
      setLoading(false);
      return { lines: [], hasLineLessInvoices: false };
    }
  }, [saleId, apiFetch]);

  useEffect(() => {
    reload();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [saleId]);

  return { lines, hasLineLessInvoices, loading, error, reload };
}
