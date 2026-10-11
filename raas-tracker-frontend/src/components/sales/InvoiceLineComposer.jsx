import { useMemo } from 'react';
import { Lock } from 'lucide-react';
import { formatNumber, lineTotal } from '../../utils/format';
import varianceLabel from './varianceLabel';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

/**
 * One controlled invoice line row: PI line + quantity, with the server-owned
 * unit price shown read-only and the exact line total.
 *
 * Purely presentational — the caller owns `value`, staging/submitting, and any
 * error surface. Shared by the "add invoice" form (stages lines before the
 * invoice exists) and InvoiceLineEditor (posts straight to an open invoice),
 * so both collect a line the same way.
 *
 * value: { sale_item_id: string, quantity: string }
 */
export default function InvoiceLineComposer({ lines = [], value, onChange, qtyAriaLabel = 'Line quantity', lineAriaLabel = 'PI line' }) {
  const selected = useMemo(
    () => lines.find((l) => String(l.sale_item_id) === String(value?.sale_item_id)) || null,
    [lines, value?.sale_item_id]
  );

  const piQty = selected ? Number(selected.pi_quantity) || 0 : 0;
  const invoiced = selected ? Number(selected.invoiced_quantity) || 0 : 0;
  const unitPrice = selected ? Number(selected.unit_price) || 0 : 0;
  const unit = selected ? selected.unit || 'KG' : 'KG';
  const qtyNum = Number(value?.quantity);
  const liveTotal = lineTotal(qtyNum > 0 ? qtyNum : 0, unitPrice);

  return (
    <>
      <div>
        <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
          PI line
        </label>
        <select
          aria-label={lineAriaLabel}
          value={value?.sale_item_id ?? ''}
          onChange={(e) => onChange({ ...value, sale_item_id: e.target.value })}
          className={inputCls}
        >
          <option value="">Select a PI line…</option>
          {lines.map((l) => (
            <option key={l.sale_item_id} value={l.sale_item_id}>
              {l.product_name} — PI {formatNumber(l.pi_quantity)} {l.unit || 'KG'}
              {' · '}
              {formatNumber(l.invoiced_quantity)} invoiced
            </option>
          ))}
        </select>
      </div>

      <dl className="grid grid-cols-3 gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-center">
        <div>
          <dt className="text-[11px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
            PI proposed
          </dt>
          <dd className="text-sm font-semibold text-slate-900 dark:text-white">
            {formatNumber(piQty)}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
            Invoiced
          </dt>
          <dd className="text-sm font-semibold text-slate-900 dark:text-white">
            {formatNumber(invoiced)}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
            Variance
          </dt>
          <dd className="text-xs font-semibold text-slate-600 dark:text-slate-300">
            {varianceLabel(piQty, invoiced, unit)}
          </dd>
        </div>
      </dl>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
            Quantity * <span className="font-normal text-slate-400">— may exceed the PI quantity</span>
          </label>
          <input
            aria-label={qtyAriaLabel}
            type="number"
            min="0"
            step="any"
            value={value?.quantity ?? ''}
            onChange={(e) => onChange({ ...value, quantity: e.target.value })}
            placeholder="0.00"
            className={inputCls}
          />
        </div>
        <div>
          <label className="flex items-center gap-1 block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
            Unit price <Lock className="h-3 w-3 text-slate-400" />
          </label>
          <input
            aria-label="Unit price"
            value={selected ? `$${formatNumber(unitPrice)}` : '—'}
            readOnly
            tabIndex={-1}
            title="Set by the PI line on the server — read-only"
            className={`${inputCls} bg-slate-100 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 cursor-not-allowed`}
          />
          <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
            Server-owned — inherited from the PI line, never typed.
          </p>
        </div>
      </div>

      <p className="text-xs text-slate-500 dark:text-slate-400">
        Line total{' '}
        <span className="text-sm font-bold text-emerald-600 dark:text-emerald-400">
          ${formatNumber(liveTotal)}
        </span>
      </p>
    </>
  );
}