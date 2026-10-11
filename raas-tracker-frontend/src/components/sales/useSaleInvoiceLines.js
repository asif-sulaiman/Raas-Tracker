import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../context/AuthContext';

/**
 * Per-PI-line invoiced quantities for one sale.
 *
 * Each invoice's posted lines are read from `GET /api/invoices/<id>/items` and
 * summed per `sale_item_id`, so the figure reflects what has actually been
 * invoiced across EVERY invoice of the sale — not just the open one. That total
 * is what makes proposal-vs-actual variance meaningful.
 *
 * Returns `lines` shaped for InvoiceLineComposer / InvoiceLineEditor:
 *   [{sale_item_id, product_name, unit, pi_quantity, invoiced_quantity, unit_price}]
 *
 * A 404 on one invoice means it was voided elsewhere; the hook drops it and
 * asks the caller to refresh. Any other failure degrades invoiced to zero for
 * the affected invoice (the server re-checks on write) and sets `error`.
 */
export default function useSaleInvoiceLines(sale, onVoided) {
  const { apiFetch } = useAuth();
  const [itemsByInvoice, setItemsByInvoice] = useState({});
  const [error, setError] = useState(null);

  const invoices = Array.isArray(sale?.invoices) ? sale.invoices : [];
  const invoiceIdsKey = invoices.map((i) => i.invoice_id).join(',');

  // Keyed on the id list, not the array identity: `sale.invoices` is a fresh
  // array on every sale refetch, so depending on it would rebuild this
  // callback (and re-fire the effect) on unrelated renders.
  const load = useCallback(async () => {
    const map = {};
    let saw404 = false;
    const ids = invoiceIdsKey ? invoiceIdsKey.split(',') : [];
    await Promise.all(
      ids.map(async (id) => {
        try {
          const res = await apiFetch(`/api/invoices/${id}/items`);
          const data = await res.json();
          if (Array.isArray(data)) map[id] = data;
        } catch (err) {
          if (err?.status === 404) saw404 = true;
        }
      })
    );
    // Replace the whole map so entries for gone ids disappear.
    setItemsByInvoice(map);
    setError(null);
    if (saw404) onVoided?.();
  }, [invoiceIdsKey, apiFetch, onVoided]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await load();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
    // Refetch when the invoice set changes (add/void), not just on sale id.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [sale?.id, invoiceIdsKey]);

  const invoicedBySaleItem = {};
  for (const items of Object.values(itemsByInvoice)) {
    for (const it of items || []) {
      invoicedBySaleItem[it.sale_item_id] =
        (invoicedBySaleItem[it.sale_item_id] || 0) + (Number(it.quantity) || 0);
    }
  }

  const lines = (sale?.items || []).map((si) => ({
    sale_item_id: si.id,
    product_name: si.product_name,
    unit: si.unit || 'KG',
    pi_quantity: Number(si.quantity) || 0,
    invoiced_quantity: invoicedBySaleItem[si.id] || 0,
    unit_price: Number(si.unit_price) || 0,
  }));

  return { lines, reload: load, error };
}