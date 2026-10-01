import { useState, useMemo } from 'react';
import { Lock, AlertTriangle } from 'lucide-react';
import Button from '../ui/Button';
import Badge from '../ui/Badge';
import { formatNumber, lineTotal } from '../../utils/format';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

/**
 * Phase 3 — partial-quantity invoice line editor.
 *
 * Lives inside the invoice flow (next to InvoicePanel): pick a PI line, enter
 * a quantity up to the remaining amount. Unit price is server-owned —
 * displayed read-only and never posted (POST /api/invoices/<id>/items takes
 * only {sale_item_id, quantity}). Over-quantity is blocked inline before
 * submit; a server 409 arrives via `serverError` and renders in the same
 * slot. Money display uses the app's exact integer-cents lineTotal.
 *
 * Props (wiring lane owns the POST):
 *   lines: [{sale_item_id, product_name, unit, pi_quantity, invoiced_quantity, unit_price}]
 *   onAdd({sale_item_id, quantity}) — async, throws on failure
 *   saving, serverError
 */
export default function InvoiceLineEditor({
  lines = [],
  onAdd,
  saving = false,
  serverError = null,
}) {
  const firstAvailable = lines.find(
    (l) => (l.pi_quantity || 0) - (l.invoiced_quantity || 0) > 0
  );
  const [saleItemId, setSaleItemId] = useState(
    firstAvailable ? String(firstAvailable.sale_item_id) : ''
  );
  const [quantity, setQuantity] = useState('');
  const [localError, setLocalError] = useState(null);
  const [adding, setAdding] = useState(false);

  const selected = useMemo(
    () => lines.find((l) => String(l.sale_item_id) === String(saleItemId)) || null,
    [lines, saleItemId]
  );

  const piQty = selected ? Number(selected.pi_quantity) || 0 : 0;
  const invoiced = selected ? Number(selected.invoiced_quantity) || 0 : 0;
  const remaining = Math.max(0, piQty - invoiced);
  const unitPrice = selected ? Number(selected.unit_price) || 0 : 0;
  const qtyNum = quantity === '' ? 0 : Number(quantity);
  const overQty = quantity !== '' && qtyNum > remaining;
  const invalid = quantity === '' || !(qtyNum > 0) || overQty;
  const liveTotal = lineTotal(qtyNum > 0 && !overQty ? qtyNum : 0, unitPrice);
  const busy = saving || adding;

  const pickLine = (value) => {
    setSaleItemId(value);
    setQuantity('');
    setLocalError(null);
  };

  const handleAdd = async () => {
    setLocalError(null);
    if (!selected) {
      setLocalError('Pick a PI line first');
      return;
    }
    if (!(qtyNum > 0)) {
      setLocalError('Enter a quantity greater than 0');
      return;
    }
    if (qtyNum > remaining) {
      // Blocked inline — the request never leaves the browser.
      setLocalError(
        `Quantity exceeds the remaining ${formatNumber(remaining)} ${selected.unit || ''} on this line`
      );
      return;
    }
    setAdding(true);
    try {
      await onAdd?.({ sale_item_id: selected.sale_item_id, quantity: qtyNum });
      setQuantity('');
    } catch {
      // The wiring lane surfaces the failure through `serverError` (409
      // over-qty included); keep the typed quantity so it can be trimmed.
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/40 p-3.5 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-800 dark:text-white">
          Add invoice line
        </h4>
        {selected && (
          <Badge variant={remaining > 0 ? 'info' : 'default'} size="xs">
            {formatNumber(remaining)} {selected.unit || 'KG'} remaining
          </Badge>
        )}
      </div>

      {lines.length === 0 ? (
        <p className="text-xs text-slate-400 dark:text-slate-500">
          No PI lines available for this invoice yet.
        </p>
      ) : (
        <>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
              PI line
            </label>
            <select
              aria-label="PI line"
              value={saleItemId}
              onChange={(e) => pickLine(e.target.value)}
              className={inputCls}
            >
              <option value="">Select a PI line…</option>
              {lines.map((l) => {
                const rem = Math.max(0, (Number(l.pi_quantity) || 0) - (Number(l.invoiced_quantity) || 0));
                return (
                  <option key={l.sale_item_id} value={l.sale_item_id} disabled={rem <= 0}>
                    {l.product_name} — remaining {formatNumber(rem)} {l.unit || 'KG'}
                    {rem <= 0 ? ' (fully invoiced)' : ''}
                  </option>
                );
              })}
            </select>
          </div>

          {selected && (
            <dl className="grid grid-cols-3 gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-center">
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  PI qty
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
                  Remaining
                </dt>
                <dd className="text-sm font-bold text-indigo-600 dark:text-indigo-400">
                  {formatNumber(remaining)}
                </dd>
              </div>
            </dl>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
                Quantity * <span className="font-normal text-slate-400">— max {formatNumber(remaining)}</span>
              </label>
              <input
                aria-label="Invoice quantity"
                type="number"
                min="0"
                step="any"
                value={quantity}
                onChange={(e) => {
                  setQuantity(e.target.value);
                  setLocalError(null);
                }}
                placeholder="0.00"
                className={`${inputCls} ${overQty ? '!border-rose-400 focus:!ring-rose-500/40 focus:!border-rose-500' : ''}`}
              />
            </div>
            <div>
              <label className="flex items-center gap-1 text-xs font-medium text-slate-600 dark:text-slate-300">
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

          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Line total{' '}
              <span className="text-sm font-bold text-emerald-600 dark:text-emerald-400">
                ${formatNumber(liveTotal)}
              </span>
            </p>
            <Button
              variant="primary"
              size="sm"
              onClick={handleAdd}
              loading={busy}
              disabled={busy || !selected || invalid}
            >
              Add line
            </Button>
          </div>

          {/* Inline validation + server 409 share one slot, same rose style */}
          {(localError || serverError) && (
            <p
              role="alert"
              className="flex items-start gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              {localError ||
                (typeof serverError === 'string' ? serverError : serverError?.message)}
            </p>
          )}
        </>
      )}
    </div>
  );
}
