import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import Button from '../ui/Button';
import InvoiceLineComposer from './InvoiceLineComposer';

/**
 * Add a line to one already-open invoice.
 *
 * The invoice is chosen explicitly (`invoiceNumber`) instead of being inferred
 * from the first non-paid row — with several invoices on a sale, silently
 * picking one makes it impossible to know where a line landed.
 *
 * Quantities may EXCEED the PI proposal (INV-REVISION shipment advantage), so
 * there is no ceiling here: the composer shows the proposal-vs-actual variance
 * as reference only. Unit price stays server-owned and is never posted.
 *
 * Props (wiring lane owns the POST):
 *   lines: [{sale_item_id, product_name, unit, pi_quantity, invoiced_quantity, unit_price}]
 *   invoiceNumber: the invoice this line is added to
 *   onAdd({sale_item_id, quantity}) — async, throws on failure
 *   saving, serverError
 */
export default function InvoiceLineEditor({
  lines = [],
  invoiceNumber,
  onAdd,
  saving = false,
  serverError = null,
}) {
  const firstLine = lines[0];
  const [value, setValue] = useState({
    sale_item_id: firstLine ? String(firstLine.sale_item_id) : '',
    quantity: '',
  });
  const [localError, setLocalError] = useState(null);
  const [adding, setAdding] = useState(false);

  const selectedId = value.sale_item_id;
  const qtyNum = Number(value.quantity);
  const invalid = !selectedId || !(qtyNum > 0);
  const busy = saving || adding;

  const pickLine = (next) => {
    setValue(next);
    setLocalError(null);
  };

  const handleAdd = async () => {
    setLocalError(null);
    if (!selectedId) {
      setLocalError('Pick a PI line first');
      return;
    }
    if (!(qtyNum > 0)) {
      setLocalError('Enter a quantity greater than 0');
      return;
    }
    setAdding(true);
    try {
      await onAdd?.({ sale_item_id: Number(selectedId), quantity: qtyNum });
      setValue((prev) => ({ ...prev, quantity: '' }));
    } catch {
      // The wiring lane surfaces the failure through `serverError`; keep the
      // typed quantity so it can be corrected.
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
        {invoiceNumber && (
          <span className="font-mono text-xs font-medium text-slate-600 dark:text-slate-300">
            to {invoiceNumber}
          </span>
        )}
      </div>

      {lines.length === 0 ? (
        <p className="text-xs text-slate-400 dark:text-slate-500">
          No PI lines available for this invoice yet.
        </p>
      ) : (
        <>
          <InvoiceLineComposer lines={lines} value={value} onChange={pickLine} />

          <div className="flex items-center justify-end gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={handleAdd}
              loading={busy}
              disabled={busy || invalid}
            >
              Add line
            </Button>
          </div>

          {/* Inline validation + server failure share one slot, same rose style */}
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