import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import Button from '../ui/Button';
import { formatNumber, todayLocalISO } from '../../utils/format';

const inputCls =
  'rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

/**
 * Create one invoice for a sale: number + date, then one quantity input per
 * PI line. Unit price stays server-owned (inherited from the PI line);
 * quantities are the operator's decision — they may exceed the PI remaining
 * up to the PI +5% ceiling, which the server enforces.
 *
 * Props:
 *   saleId, piLines: [{sale_item_id, product_name, unit, pi_quantity,
 *     invoiced_quantity, unit_price}]
 *   onSaved() — called after a successful create; the caller reloads.
 *   onCancel() — closes the form.
 */
export default function InvoiceCreateForm({ saleId, piLines = [], onSaved, onCancel }) {
  const { apiFetch } = useAuth();
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [invoiceDate, setInvoiceDate] = useState(todayLocalISO());
  const [qtys, setQtys] = useState({});
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);

  const submitLines = piLines
    .map((l) => {
      const q = Number(qtys[l.sale_item_id] || '');
      return q > 0 ? { sale_item_id: l.sale_item_id, quantity: q } : null;
    })
    .filter(Boolean);
  const ready = !!invoiceNumber.trim() && !!invoiceDate && submitLines.length > 0;

  const handleSubmit = async () => {
    setFormError(null);
    if (!ready) return;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/sales/${saleId}/invoices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invoice_number: invoiceNumber.trim(),
          invoice_date: invoiceDate,
          lines: submitLines,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error || 'Could not create invoice');
      }
      const created = await res.json();
      toast.success(
        typeof created?.amount === 'number'
          ? `Invoiced $${formatNumber(created.amount)}`
          : 'Invoice created',
      );
      onSaved?.();
    } catch (err) {
      setFormError(err.message || 'Could not create invoice');
    } finally {
      setSaving(false);
    }
  };

  const remainingOf = (l) => Math.max(0, Number(l.pi_quantity || 0) - Number(l.invoiced_quantity || 0));
  const ceilingOf = (l) => Number(l.pi_quantity || 0) * 1.05 - Number(l.invoiced_quantity || 0);

  return (
    <div className="space-y-3 px-2.5 py-2">
      <div>
        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
          Invoice number *
        </label>
        <input
          aria-label="Invoice number"
          type="text"
          value={invoiceNumber}
          onChange={(e) => setInvoiceNumber(e.target.value)}
          placeholder="INV-2026-001"
          className={`${inputCls} w-full`}
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
          Invoice date *
        </label>
        <input
          aria-label="Invoice date"
          type="date"
          value={invoiceDate}
          onChange={(e) => setInvoiceDate(e.target.value)}
          className={`${inputCls} w-full`}
        />
      </div>

      <div className="space-y-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/40 p-3">
        <p className="text-xs font-semibold text-slate-700 dark:text-slate-300">
          Lines — invoice only what ships this time; quantities may exceed the
          PI remaining up to the PI +5% ceiling (server-enforced).
        </p>
        {piLines.length === 0 ? (
          <p className="text-[11px] text-slate-400 dark:text-slate-500">
            This PI has no product lines to invoice.
          </p>
        ) : (
          <table className="w-full text-xs divide-y divide-slate-200 dark:divide-slate-700">
            <thead>
              <tr className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 uppercase tracking-wider">
                <th className="px-2 py-1 text-left">Product</th>
                <th className="px-2 py-1 text-right">PI qty</th>
                <th className="px-2 py-1 text-right">Invoiced</th>
                <th className="px-2 py-1 text-right">Remaining</th>
                <th className="px-2 py-1 text-right">Unit price</th>
                <th className="px-2 py-1 text-right">Invoice qty</th>
              </tr>
            </thead>
            <tbody>
              {piLines.map((l) => {
                const rem = remainingOf(l);
                const ceiling = ceilingOf(l);
                const q = Number(qtys[l.sale_item_id] || '');
                const overCeiling = q > 0 && q > ceiling;
                return (
                  <tr key={l.sale_item_id} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="px-2 py-1 text-left text-sm">
                      {l.product_name}
                    </td>
                    <td className="px-2 py-1 text-right text-slate-600 dark:text-slate-400">
                      {formatNumber(l.pi_quantity)} {l.unit || 'KG'}
                    </td>
                    <td className="px-2 py-1 text-right text-slate-600 dark:text-slate-400">
                      {formatNumber(l.invoiced_quantity)} {l.unit || 'KG'}
                    </td>
                    <td className="px-2 py-1 text-right text-sm font-medium">
                      {formatNumber(rem)} {l.unit || 'KG'} (ceiling {formatNumber(ceiling)} {l.unit || 'KG'})
                    </td>
                    <td className="px-2 py-1 text-right text-slate-600 dark:text-slate-400">
                      ${formatNumber(l.unit_price)}
                    </td>
                    <td className="px-2 py-1 text-right">
                      <input
                        type="number"
                        step="any"
                        min="0"
                        value={qtys[l.sale_item_id] || ''}
                        onChange={(e) => setQtys((prev) => ({ ...prev, [l.sale_item_id]: e.target.value }))}
                        placeholder="0"
                        className={`${inputCls} w-24 text-right`}
                        aria-label={`Invoice qty for ${l.product_name}`}
                      />
                      {overCeiling && (
                        <p className="mt-0.5 text-[10px] text-rose-600 dark:text-rose-400">
                          Above the PI +5% ceiling ({formatNumber(ceiling)} {l.unit || 'KG'} max)
                        </p>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {formError && (
        <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{formError}</p>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={handleSubmit}
          loading={saving}
          disabled={saving || !ready}
        >
          Save invoice
        </Button>
      </div>
    </div>
  );
}
