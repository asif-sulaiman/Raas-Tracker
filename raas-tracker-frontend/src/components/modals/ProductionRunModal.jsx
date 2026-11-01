import { useState, useEffect } from 'react';
import { AlertTriangle } from 'lucide-react';
import Modal from './Modal';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import { formatNumber } from '../../utils/format';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function today() {
  return new Date().toISOString().slice(0, 10);
}

const keyOf = (product) => String(product.product_name || '').toLowerCase();

const remainingOf = (product) => {
  const remaining = Number(product.remaining_qty);
  if (Number.isFinite(remaining)) return Math.max(remaining, 0);
  return Math.max(
    (Number(product.invoiced_qty) || 0) - (Number(product.produced_qty) || 0),
    0,
  );
};

/**
 * One run against the recipe's own invoice.
 *
 * The sale and invoice are fixed by the recipe, so this form cannot point a
 * run at another invoice. Every product on the invoice gets a row prefilled
 * with its remaining quantity; a row left at 0 is not sent, and a product
 * with no formulation is never silently produced.
 */
export default function ProductionRunModal({ isOpen, onClose, recipe, onSaved }) {
  const { apiFetch } = useAuth();

  const [products, setProducts] = useState([]);
  const [quantities, setQuantities] = useState({});
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  const [materialNumber, setMaterialNumber] = useState('');
  const [packing, setPacking] = useState('');
  const [batchNumber, setBatchNumber] = useState('');
  const [productionDate, setProductionDate] = useState(today());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const invoiceId = recipe?.invoice_id ?? null;

  useEffect(() => {
    if (!isOpen) return undefined;
    // Always mounted (isOpen toggles visibility) — reset the form and reload
    // the invoice's products on every open.
    // oxlint-disable-next-line react/set-state-in-effect
    setProducts([]);
    // oxlint-disable-next-line react/set-state-in-effect
    setQuantities({});
    // oxlint-disable-next-line react/set-state-in-effect
    setMaterialNumber('');
    // oxlint-disable-next-line react/set-state-in-effect
    setPacking('');
    // oxlint-disable-next-line react/set-state-in-effect
    setBatchNumber('');
    // oxlint-disable-next-line react/set-state-in-effect
    setProductionDate(today());
    // oxlint-disable-next-line react/set-state-in-effect
    setError(null);
    // oxlint-disable-next-line react/set-state-in-effect
    setLoadError(null);
    // oxlint-disable-next-line react/set-state-in-effect
    setSaving(false);
    if (invoiceId == null) {
      // Without its invoice the run cannot be linked at all; say so instead of
      // showing an empty product list that reads like "nothing to produce".
      setLoadError('This recipe is not linked to an invoice.');
      setLoading(false);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const res = await apiFetch(
          `/api/invoices/${encodeURIComponent(invoiceId)}/production-source`,
        );
        const data = await res.json();
        if (cancelled) return;
        const rows = Array.isArray(data?.products) ? data.products : [];
        setProducts(rows);
        setQuantities(Object.fromEntries(rows.map((p) => [
          keyOf(p), String(remainingOf(p)),
        ])));
      } catch (err) {
        if (cancelled) return;
        // An empty list is not evidence that everything is produced.
        setProducts([]);
        setLoadError(err?.message || 'Could not load invoice products');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // apiFetch is not referentially stable; re-keying on recipe identity is
    // what opens a fresh run.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, invoiceId, recipe?.id, attempt]);

  const rows = products
    .filter((p) => p.on_invoice !== false && remainingOf(p) > 0);
  const producedOut = products
    .filter((p) => p.on_invoice !== false && remainingOf(p) === 0)
    .map((p) => p.product_name);
  const submittableRows = rows.filter((p) => p.formulated);
  const hasFormulatedRow = submittableRows.length > 0;

  const setQuantity = (product, value) => {
    setQuantities((prev) => ({ ...prev, [keyOf(product)]: value }));
  };

  const handleStart = async () => {
    setError(null);
    if (invoiceId == null) {
      setError('This recipe is not linked to an invoice.');
      return;
    }
    const outputs = submittableRows
      .map((p) => ({
        product_name: p.product_name,
        quantity: Number(quantities[keyOf(p)]),
      }))
      .filter((o) => Number.isFinite(o.quantity) && o.quantity > 0);
    if (outputs.length === 0) {
      setError('Enter a quantity for at least one product');
      return;
    }
    setSaving(true);
    try {
      await apiFetch(`/api/recipes/by-id/${encodeURIComponent(recipe.id)}/produce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_id: Number(recipe.company_id),
          outputs,
          batch_number: batchNumber.trim() || null,
          production_date: productionDate || null,
          material_number: materialNumber.trim() || null,
          packing: packing.trim() || null,
        }),
      });
      toast.success('Production run recorded');
      onSaved?.();
      onClose?.();
    } catch (err) {
      setError(err.message || 'Could not start the production run');
      setSaving(false);
    }
  };

  const disabled = saving || loading || Boolean(loadError) || !hasFormulatedRow;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Go for Production"
      subtitle={recipe ? recipe.name : undefined}
      maxWidth="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleStart}
            loading={saving}
            disabled={disabled}
          >
            Start Production
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/40 px-3 py-2">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Invoice
            </p>
            <p className="text-sm font-semibold text-slate-900 dark:text-white">
              {recipe?.invoice_number || '—'}
            </p>
          </div>
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              PI / Client
            </p>
            <p className="text-sm font-semibold text-slate-900 dark:text-white">
              {recipe?.pi_number || '—'}
              {recipe?.client_name ? ` · ${recipe.client_name}` : ''}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Material number</label>
            <input
              aria-label="Material number"
              type="text"
              value={materialNumber}
              onChange={(e) => setMaterialNumber(e.target.value)}
              placeholder="Item number from the PI"
              className={inputCls}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Packing</label>
            <input
              aria-label="Packing"
              type="text"
              value={packing}
              onChange={(e) => setPacking(e.target.value)}
              placeholder="30 Kg Drum"
              className={inputCls}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Batch number</label>
            <input
              aria-label="Batch number"
              type="text"
              value={batchNumber}
              onChange={(e) => setBatchNumber(e.target.value)}
              placeholder="BATCH-001"
              className={inputCls}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Production date</label>
            <input
              aria-label="Production date"
              type="date"
              value={productionDate}
              onChange={(e) => setProductionDate(e.target.value)}
              className={inputCls}
            />
          </div>
        </div>

        <div>
          <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-2">
            Products on this invoice
          </h4>
          {loading ? (
            <p className="text-xs text-slate-400 dark:text-slate-500">Loading invoice products…</p>
          ) : loadError ? (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/40">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                Could not load invoice products
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">{loadError}</p>
              <Button variant="secondary" size="sm" onClick={() => setAttempt((n) => n + 1)}>
                Retry
              </Button>
            </div>
          ) : rows.length === 0 ? (
            <p className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-3 text-xs text-slate-500 dark:text-slate-400">
              Every product on {recipe?.invoice_number || 'this invoice'} has been fully produced.
            </p>
          ) : (
            <>
              {!hasFormulatedRow && (
                <p className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                  No formulated product is left to produce — add a formulation in the recipe first.
                </p>
              )}
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-slate-50 dark:bg-slate-800/60 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                      <th className="px-3 py-2 text-left">Product</th>
                      <th className="px-3 py-2 text-right">Invoiced</th>
                      <th className="px-3 py-2 text-right">Produced</th>
                      <th className="px-3 py-2 text-right">Remaining</th>
                      <th className="px-3 py-2 text-right">Quantity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((product) => {
                      const remaining = remainingOf(product);
                      const qty = Number(quantities[keyOf(product)]);
                      const exceeds = Number.isFinite(qty) && qty > remaining;
                      return (
                        <tr
                          key={keyOf(product)}
                          className="border-t border-slate-100 dark:border-slate-800 align-top"
                        >
                          <td className="px-3 py-2">
                            <p className="font-medium text-slate-900 dark:text-white">
                              {product.product_name}
                            </p>
                            {!product.formulated && (
                              <p className="text-[11px] text-rose-600 dark:text-rose-400">
                                no formulation yet — add chemicals in the recipe
                              </p>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-600 dark:text-slate-300">
                            {formatNumber(product.invoiced_qty)} {product.unit}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-600 dark:text-slate-300">
                            {formatNumber(product.produced_qty)} {product.unit}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-600 dark:text-slate-300">
                            {formatNumber(remaining)} {product.unit}
                          </td>
                          <td className="px-3 py-2">
                            <input
                              aria-label={`Quantity for ${product.product_name}`}
                              type="number"
                              min="0"
                              step="any"
                              value={quantities[keyOf(product)] ?? ''}
                              onChange={(e) => setQuantity(product, e.target.value)}
                              disabled={!product.formulated}
                              className={inputCls}
                            />
                            {exceeds && (
                              <p className="mt-1 flex items-start gap-1 text-[11px] text-amber-600 dark:text-amber-400">
                                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                                <span>
                                  Exceeds the invoice remainder ({formatNumber(remaining)} {product.unit}).
                                  Production is not capped — confirm this is intentional.
                                </span>
                              </p>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {producedOut.length > 0 && (
                <p className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">
                  Fully produced: {producedOut.join(', ')}
                </p>
              )}
            </>
          )}
        </div>

        {error && (
          <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
        )}
      </div>
    </Modal>
  );
}
