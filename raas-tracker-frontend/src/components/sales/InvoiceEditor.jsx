import { useMemo, useState } from 'react';
import { AlertTriangle, Lock } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../../context/AuthContext';
import Button from '../ui/Button';
import { formatNumber, lineTotal, todayLocalISO } from '../../utils/format';
import useInvoiceBudget from './useInvoiceBudget';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

const readOnlyCls =
  `${inputCls} w-28 text-right bg-slate-100 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 cursor-not-allowed`;

// One-directional slack so a quantity typed exactly at the boundary (already a
// float by the time it leaves the input) is not refused by a rounding hair.
const EPSILON = 1e-9;

/** How much this row may still add, or null when the typed quantity is fine. */
function overflow(line, typed) {
  if (!(typed > 0)) return null;
  const limit = Number(line.ceiling || 0) - Number(line.invoiced_quantity || 0);
  return typed > limit + EPSILON ? Math.max(0, limit) : null;
}

/**
 * What is left of the PI line once the quantity staged here is counted. The
 * server's remaining already nets out every prior invoice; this only subtracts
 * the open input, and a non-positive or non-finite entry counts as zero.
 */
function remainingAfter(line, typed) {
  const typedSafe = Number.isFinite(typed) && typed > 0 ? typed : 0;
  return Math.max(0, Number(line.remaining || 0) - typedSafe);
}

/**
 * The single invoice composer: create an invoice for a sale, or add one line to
 * an open one.
 *
 * The product and the unit price come from the PI line and are server-owned -
 * the price is shown read-only and never posted. The operator only decides
 * quantities. A quantity above the PI remaining is allowed (shipment
 * advantage) up to the server-provided ceiling, which is enforced here as a
 * hard stop rather than advertised as headroom.
 *
 * Each row carries one open quantity input. The live remaining (the server's
 * figure minus what is typed here) sits under the PI quantity, and the total
 * price is a read-only computed column restated by the server on save. Nothing
 * restates what is already invoiced: that column was noise beside the input.
 *
 * mode="create": invoice number + date + rows -> POST /api/sales/<id>/invoices
 * mode="add-line": rows only -> POST /api/invoices/<targetInvoiceId>/items
 *
 * A refusal is classified structurally, never by parsing the server's prose:
 * 409 means the budget moved, so reload and let the fresh figures mark the
 * rows; create-mode 400 means a PI line vanished, so reload and name the
 * product; add-line 404 carries code="invoice_gone" (the target is gone) or
 * "pi_line_gone" (the line is gone — drop it and say so, same as the 400).
 */
export default function InvoiceEditor({
  mode = 'create',
  saleId,
  invoiceLabel = '',
  targetInvoiceId,
  onDone,
  onCancel,
}) {
  const { apiFetch } = useAuth();
  const {
    lines, hasLineLessInvoices, loading, error: loadError, reload,
  } = useInvoiceBudget(saleId);

  const isCreate = mode === 'create';
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [invoiceDate, setInvoiceDate] = useState(todayLocalISO());
  const [qtys, setQtys] = useState({});
  const [error, setError] = useState(null);
  const [superseded, setSuperseded] = useState([]);
  const [targetGone, setTargetGone] = useState(false);
  const [saving, setSaving] = useState(false);

  const rows = useMemo(
    () => lines.map((l) => {
      const typed = Number(qtys[l.sale_item_id] || '');
      return {
        line: l,
        typed,
        over: overflow(l, typed),
        left: remainingAfter(l, typed),
      };
    }),
    [lines, qtys],
  );

  const staged = rows.filter((r) => r.typed > 0);
  const overRows = rows.filter((r) => r.over !== null);
  const ready = !saving
    && !targetGone
    && staged.length > 0
    && overRows.length === 0
    && (!isCreate || (invoiceNumber.trim() !== '' && invoiceDate !== ''));

  const setQty = (id, value) => {
    setQtys((prev) => ({ ...prev, [id]: value }));
    setError(null);
  };

  /** Reload after a refusal and drop staged rows whose PI line is gone. */
  const resyncDropping = async () => {
    const before = lines;
    const fresh = await reload();
    const alive = new Set(fresh.lines.map((l) => String(l.sale_item_id)));
    const dropped = before.filter(
      (l) => Number(qtys[l.sale_item_id] || '') > 0
        && !alive.has(String(l.sale_item_id)));
    if (dropped.length === 0) return false;
    setSuperseded(dropped.map((l) => l.product_name));
    setQtys((prev) => {
      const next = { ...prev };
      dropped.forEach((l) => { delete next[l.sale_item_id]; });
      return next;
    });
    return true;
  };

  const handleRefusal = async (err) => {
    if (err?.status === 409) {
      await reload();
      return;
    }
    if (err?.status === 400 && await resyncDropping()) return;
    if (err?.status === 404) {
      if (err?.code === 'invoice_gone') {
        await reload();
        setTargetGone(true);
        return;
      }
      // The PI line vanished mid-sequence: drop it and say so, rather than
      // echoing "sale item not found" over a row the operator can still see.
      if (err?.code === 'pi_line_gone' && await resyncDropping()) return;
    }
    await reload();
    setError(err?.message || 'Could not save the invoice');
  };

  const submit = async () => {
    if (!ready) return;
    setSaving(true);
    setError(null);
    setSuperseded([]);
    try {
      if (isCreate) {
        const res = await apiFetch(`/api/sales/${saleId}/invoices`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            invoice_number: invoiceNumber.trim(),
            invoice_date: invoiceDate,
            lines: staged.map((r) => ({
              sale_item_id: r.line.sale_item_id,
              quantity: r.typed,
            })),
          }),
        });
        const created = await res.json();
        // The amount is the server's, derived from the server-owned unit price.
        toast.success(
          typeof created?.amount === 'number'
            ? `Invoiced $${formatNumber(created.amount)}`
            : 'Invoice created',
        );
        onDone?.(created);
        setInvoiceNumber('');
        await reload();
      } else {
        // One request per staged row: the endpoint takes a single line, and a
        // partial success is real (each POST is its own transaction). Clearing
        // a row as it lands means a mid-sequence refusal leaves exactly the
        // unposted rows on screen, with the reloaded budget marking them.
        let landed = 0;
        let result = null;
        try {
          for (const row of staged) {
            const res = await apiFetch(`/api/invoices/${targetInvoiceId}/items`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                sale_item_id: row.line.sale_item_id,
                quantity: row.typed,
              }),
            });
            result = await res.json();
            landed += 1;
            setQtys((prev) => {
              const next = { ...prev };
              delete next[row.line.sale_item_id];
              return next;
            });
          }
        } finally {
          // Whatever landed is committed whether or not the loop finished, so
          // the parent is told even when the sequence stops part-way — its
          // totals would otherwise stay stale until an unrelated refresh.
          if (landed > 0) onDone?.(result);
        }
        await reload();
      }
    } catch (err) {
      await handleRefusal(err);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <p className="text-xs text-slate-400 dark:text-slate-500">Loading PI lines…</p>
    );
  }

  return (
    <div className="space-y-3">
      {isCreate ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
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
              className={inputCls}
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
              className={inputCls}
            />
          </div>
        </div>
      ) : (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Adding to <span className="font-mono font-medium">{invoiceLabel}</span>
        </p>
      )}

      {loadError && (
        <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{loadError}</p>
      )}

      {hasLineLessInvoices && (
        <p className="rounded-lg bg-amber-50 dark:bg-amber-900/30 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
          A legacy invoice on this PI carries money with no lines. The remaining
          figures below understate what has been billed — check before invoicing.
        </p>
      )}

      {lines.length === 0 ? (
        <p className="text-xs text-slate-400 dark:text-slate-500">
          This PI has no product lines to invoice.
        </p>
      ) : (
        <table className="w-full text-xs divide-y divide-slate-200 dark:divide-slate-700">
          <thead>
            <tr className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 uppercase tracking-wider">
              <th className="px-2 py-1 text-left">Product</th>
              <th className="px-2 py-1 text-right">PI qty</th>
              <th className="px-2 py-1 text-right">Invoice qty</th>
              <th className="px-2 py-1 text-right">Unit price</th>
              <th className="px-2 py-1 text-right">Total price</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ line, typed, over, left }) => {
              const unit = line.unit || 'KG';
              return (
                <tr key={line.sale_item_id} className="border-b border-slate-100 dark:border-slate-800">
                  <td className="px-2 py-1 text-left text-sm">{line.product_name}</td>
                  <td className="px-2 py-1 text-right text-slate-600 dark:text-slate-400">
                    {formatNumber(line.pi_quantity)} {unit}
                    <p
                      aria-live="polite"
                      className="mt-0.5 text-xs text-slate-500 dark:text-slate-400"
                    >
                      Remaining {formatNumber(left)} {unit}
                    </p>
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input
                      aria-label={`Invoice qty for ${line.product_name}`}
                      type="number"
                      step="any"
                      min="0"
                      value={qtys[line.sale_item_id] || ''}
                      onChange={(e) => setQty(line.sale_item_id, e.target.value)}
                      className={`${inputCls} w-24 text-right`}
                    />
                    {over !== null && (
                      <p className="mt-0.5 text-[10px] text-rose-600 dark:text-rose-400">
                        Over the invoicing limit — this line can add at most
                        {' '}{formatNumber(over)} {unit}
                      </p>
                    )}
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input
                      aria-label={`Unit price for ${line.product_name}`}
                      value={`$${formatNumber(line.unit_price)}`}
                      readOnly
                      tabIndex={-1}
                      title="Set by the PI line on the server — read-only"
                      className={readOnlyCls}
                    />
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input
                      aria-label={`Total price for ${line.product_name}`}
                      value={`$${formatNumber(typed > 0 ? lineTotal(typed, line.unit_price) : 0)}`}
                      readOnly
                      tabIndex={-1}
                      title="Invoiced quantity x unit price - restated by the server on save"
                      className={readOnlyCls}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {superseded.length > 0 && (
        <p className="rounded-lg bg-amber-50 dark:bg-amber-900/30 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          {superseded.length} PI line(s) are no longer on this PI — review and
          resubmit: {superseded.join(', ')}
        </p>
      )}

      {targetGone && (
        <p className="rounded-lg bg-amber-50 dark:bg-amber-900/30 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          This invoice is no longer available — reload the PI to continue.
        </p>
      )}

      {error && (
        <p role="alert" className="flex items-start gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          {error}
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        {onCancel && (
          <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        )}
        <Button
          variant="primary"
          size="sm"
          onClick={submit}
          loading={saving}
          disabled={!ready}
        >
          <span className="flex items-center gap-1.5">
            {isCreate ? 'Save invoice' : 'Add line'}
            {isCreate && <Lock className="h-3 w-3 opacity-60" aria-hidden="true" />}
          </span>
        </Button>
      </div>
    </div>
  );
}
