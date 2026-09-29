import { useState, useEffect } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import Modal from './Modal';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import { formatNumber, toCents, fromCents } from '../../utils/format';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function today() {
  return new Date().toISOString().slice(0, 10);
}

export default function ProductionRunModal({ isOpen, onClose, recipe, recipeItems = [], onSaved }) {
  const { apiFetch } = useAuth();

  const [companies, setCompanies] = useState([]);
  const [recipes, setRecipes] = useState([]);
  const [allSales, setAllSales] = useState([]);
  const [loadingRefs, setLoadingRefs] = useState(false);

  const [companyId, setCompanyId] = useState('');
  const [saleId, setSaleId] = useState('');
  const [products, setProducts] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [loadingSource, setLoadingSource] = useState(false);

  const [invoiceChoice, setInvoiceChoice] = useState('');
  const [newInvoiceNumber, setNewInvoiceNumber] = useState('');
  const [newInvoiceAmount, setNewInvoiceAmount] = useState('');
  const [materialNumber, setMaterialNumber] = useState('');
  const [packing, setPacking] = useState('');
  const [batchNumber, setBatchNumber] = useState('');
  const [productionDate, setProductionDate] = useState(today());
  const [sourceError, setSourceError] = useState(null);

  const [recipeRows, setRecipeRows] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  // The dropdown sources (companies / recipes / PIs) load as one unit. A failure
  // leaves every list empty, which is indistinguishable from a genuinely empty
  // master — so the failure gets its own state and never renders as "no data".
  const [refsError, setRefsError] = useState(null);
  const [refsAttempt, setRefsAttempt] = useState(0);

  useEffect(() => {
    if (!isOpen) return;
    const presetCompany = recipe?.company_id != null ? String(recipe.company_id) : '';
    const initialQty = recipe?.total_quantity != null ? String(recipe.total_quantity) : '';
    // Always mounted (isOpen toggles visibility) — reset the form and reload
    // the dropdown sources on every open.
    // oxlint-disable-next-line react/set-state-in-effect
    setCompanies([]);
    setRecipes([]);
    setAllSales([]);
    setCompanyId(presetCompany);
    setSaleId('');
    setProducts([]);
    setInvoices([]);
    setLoadingSource(false);
    setInvoiceChoice('');
    setNewInvoiceNumber('');
    setNewInvoiceAmount('');
    setMaterialNumber('');
    setPacking('');
    setBatchNumber('');
    setProductionDate(today());
    setRecipeRows([{ key: 'row-0', recipe_name: recipe?.name || '', qty: initialQty }]);
    setError(null);
    setRefsError(null);
    setSaving(false);
    setLoadingRefs(true);
    (async () => {
      try {
        const [coRes, recRes, saleRes] = await Promise.all([
          apiFetch('/api/companies'),
          apiFetch('/api/recipes'),
          apiFetch('/api/sales'),
        ]);
        const [cos, recs, sales] = await Promise.all([coRes.json(), recRes.json(), saleRes.json()]);
        setCompanies(Array.isArray(cos) ? cos : []);
        setRecipes(Array.isArray(recs) ? recs : []);
        setAllSales(Array.isArray(sales) ? sales : []);
      } catch (err) {
        // Empty lists are indistinguishable from an empty master, so record the
        // failure — the empty-state messages below stay reserved for a real
        // successful-but-empty response.
        setCompanies([]);
        setRecipes([]);
        setAllSales([]);
        setRefsError(err?.message || 'Could not load companies, recipes, and PIs');
      } finally {
        setLoadingRefs(false);
      }
    })();
    // apiFetch is not referentially stable (its useCallback chain bottoms out in
    // react-router's navigate) — adding it would refetch on every parent render.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, recipe, refsAttempt]);

  const retryRefs = () => setRefsAttempt((n) => n + 1);

  const defaultQty = () => (recipe?.total_quantity != null ? String(recipe.total_quantity) : '');

  const setRowField = (index, field, value) => {
    setRecipeRows((prev) => prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  };

  const resetSaleFields = () => {
    setSaleId('');
    setProducts([]);
    setInvoices([]);
    setLoadingSource(false);
    setSourceError(null);
    setInvoiceChoice('');
    setNewInvoiceNumber('');
    setNewInvoiceAmount('');
    setRowField(0, 'qty', defaultQty());
  };

  const handleCompanyChange = (value) => {
    setCompanyId(value);
    resetSaleFields();
  };

  const handleSaleChange = async (value) => {
    setSaleId(value);
    setProducts([]);
    setInvoices([]);
    setInvoiceChoice('');
    setNewInvoiceNumber('');
    setNewInvoiceAmount('');
    setSourceError(null);
    if (!value) {
      setRowField(0, 'qty', defaultQty());
      setLoadingSource(false);
      return;
    }
    setLoadingSource(true);
    try {
      const [srcRes, invRes] = await Promise.all([
        apiFetch(`/api/production-source?sale_id=${encodeURIComponent(value)}`),
        apiFetch(`/api/sales/${encodeURIComponent(value)}/invoices`),
      ]);
      const [src, invs] = await Promise.all([srcRes.json(), invRes.json()]);
      const rows = Array.isArray(src?.products) ? src.products : [];
      setProducts(rows);
      setInvoices(Array.isArray(invs) ? invs : []);
      if (rows.length > 0) {
        const total = rows.reduce((sum, p) => sum + (Number(p.quantity) || 0), 0);
        setRowField(0, 'qty', String(total));
        const itemNo = rows.find((p) => p.item_no)?.item_no;
        if (itemNo) setMaterialNumber(String(itemNo));
      }
    } catch (err) {
      setSourceError(err.message || 'Could not load PI details');
      setError(err.message || 'Could not load PI details');
    } finally {
      setLoadingSource(false);
    }
  };

  // Sale remaining in integer cents: sale total minus the amounts already
  // invoiced, clamped at zero (never float subtraction). Null when the sale
  // total is unknown, in which case no prefill is possible.
  const selectedSale = allSales.find((s) => String(s.id) === String(saleId));
  const invoicedCents = invoices.reduce((sum, inv) => sum + toCents(inv.amount), 0);
  const saleTotalCents =
    selectedSale?.total_value === null || selectedSale?.total_value === undefined
      ? null
      : toCents(selectedSale.total_value);
  const remainingCents = saleTotalCents === null ? null : Math.max(0, saleTotalCents - invoicedCents);
  const saleHasInvoices = invoices.length > 0;

  const handleInvoiceChoiceChange = (value) => {
    setInvoiceChoice(value);
    if (value === 'new') {
      // Prefill the unbilled remainder so a follow-up invoice defaults to
      // exactly what's left. Blank-optional for a sale's first invoice.
      if (saleHasInvoices && remainingCents !== null) {
        setNewInvoiceAmount(String(fromCents(remainingCents)));
      } else {
        setNewInvoiceAmount('');
      }
    } else {
      setNewInvoiceAmount('');
    }
  };

  const addRecipeRow = () => {
    setRecipeRows((prev) => [
      ...prev,
      { key: `row-${Date.now()}-${prev.length}`, recipe_name: '', qty: '' },
    ]);
  };

  const removeRecipeRow = (index) => {
    setRecipeRows((prev) => prev.filter((_, i) => i !== index));
  };

  const handleStart = async () => {
    setError(null);
    if (!companyId) {
      setError('Company is required');
      return;
    }
    if (!saleId) {
      setError('PI / Sale is required');
      return;
    }
    const rows = recipeRows.map((row) => ({
      recipe_name: row.recipe_name,
      qty: parseFloat(row.qty),
    }));
    if (rows.some((row) => !row.recipe_name)) {
      setError('Choose a recipe for every row');
      return;
    }
    if (!rows.some((row) => row.qty > 0)) {
      setError('Add at least one recipe with a quantity greater than 0');
      return;
    }
    if (rows.some((row) => !(row.qty > 0))) {
      setError('Each recipe needs a quantity greater than 0');
      return;
    }

    const isNewInvoice = invoiceChoice === 'new';
    const trimmedNewNumber = newInvoiceNumber.trim();
    if (isNewInvoice && !trimmedNewNumber) {
      setError('New invoice number is required');
      return;
    }
    // The server requires an amount for every invoice after the first, so
    // validate before any POST. Blank stays omitted for a first invoice and
    // the server defaults it to the sale total.
    let newInvoiceBodyAmount;
    if (isNewInvoice) {
      const rawAmount = newInvoiceAmount.trim();
      if (saleHasInvoices && !rawAmount) {
        setError('Amount is required when the sale already has invoices');
        return;
      }
      if (rawAmount) {
        const value = parseFloat(rawAmount.replace(/,/g, ''));
        if (Number.isNaN(value) || value < 0) {
          setError('Enter an amount of 0 or more');
          return;
        }
        newInvoiceBodyAmount = fromCents(toCents(value));
      }
    }

    setSaving(true);
    // A typed invoice number is free text until it exists as a row: create it
    // first so produce can link the run via invoice_ids. A failed create (400
    // over total, 409 duplicate) blocks the run — the error shows inline.
    let createdInvoiceId = null;
    let invoiceNumber;
    if (isNewInvoice) {
      const invBody = { invoice_number: trimmedNewNumber };
      if (newInvoiceBodyAmount !== undefined) invBody.amount = newInvoiceBodyAmount;
      try {
        const invRes = await apiFetch(`/api/sales/${encodeURIComponent(saleId)}/invoices`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(invBody),
        });
        const created = await invRes.json();
        createdInvoiceId = created?.invoice_id ?? created?.id ?? null;
        invoiceNumber = created?.invoice_number || trimmedNewNumber;
      } catch (err) {
        setError(err.message || 'Could not create invoice');
        setSaving(false);
        return;
      }
    } else {
      const chosen = invoices.find((inv) => String(inv.invoice_id) === invoiceChoice);
      invoiceNumber = chosen?.invoice_number || '';
    }

    const body = {
      company_id: Number(companyId),
      production_qty: rows[0].qty,
      batch_number: batchNumber.trim() || null,
      production_date: productionDate || null,
      material_number: materialNumber.trim() || null,
      packing: packing.trim() || null,
      invoice_number: invoiceNumber || null,
      sale_ids: [Number(saleId)],
      recipes: rows,
    };
    if (createdInvoiceId !== null) {
      body.invoice_ids = [createdInvoiceId];
    } else if (invoiceChoice && invoiceChoice !== 'new') {
      body.invoice_ids = [Number(invoiceChoice)];
    }

    try {
      await apiFetch(`/api/recipes/${encodeURIComponent(rows[0].recipe_name)}/produce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      toast.success('Production run recorded');
      onSaved?.();
      onClose?.();
    } catch (err) {
      setError(err.message || 'Could not start the production run');
      setSaving(false);
    }
  };

  const companySales = allSales.filter((sale) => String(sale.company_id) === companyId);
  const companyRecipes = recipes.filter((rec) => String(rec.company_id) === companyId);
  const totalProductQty = products.reduce((sum, p) => sum + (Number(p.quantity) || 0), 0);

  // Keep the row's current value selectable even when it belongs to another company.
  const rowOptions = (row) => {
    const opts = companyRecipes.slice();
    if (row.recipe_name && !opts.some((rec) => rec.name === row.recipe_name)) {
      opts.unshift({ name: row.recipe_name });
    }
    return opts;
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Go for Production"
      subtitle={
        recipe
          ? `${recipe.name} · ${recipeItems.length} ingredient${recipeItems.length === 1 ? '' : 's'}`
          : undefined
      }
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
            disabled={saving || loadingRefs}
          >
            Start Production
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Company *</label>
          {loadingRefs ? (
            <p className="text-xs text-slate-400 dark:text-slate-500">Loading…</p>
          ) : refsError ? (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-xs font-medium text-rose-600 dark:text-rose-400">
                Could not load companies
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">{refsError}</p>
              <Button variant="secondary" size="sm" onClick={retryRefs}>
                Retry
              </Button>
            </div>
          ) : companies.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400 italic">
              No companies registered yet.
            </p>
          ) : (
            <select
              aria-label="Company"
              value={companyId}
              onChange={(e) => handleCompanyChange(e.target.value)}
              className={inputCls}
            >
              <option value="">Select company…</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">PI / Sale *</label>
          {!companyId ? (
            <select aria-label="PI / Sale" value="" disabled className={inputCls}>
              <option value="">Select a company first</option>
            </select>
          ) : loadingRefs ? (
            <p className="text-xs text-slate-400 dark:text-slate-500">Loading…</p>
          ) : refsError ? (
            <p className="text-xs font-medium text-rose-600 dark:text-rose-400">
              Could not load PIs for this company
            </p>
          ) : companySales.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400 italic">
              No PIs found for this company yet.
            </p>
          ) : (
            <select
              aria-label="PI / Sale"
              value={saleId}
              onChange={(e) => handleSaleChange(e.target.value)}
              className={inputCls}
            >
              <option value="">Select PI…</option>
              {companySales.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.pi_number || `PI #${s.id}`}{s.client_name ? ` — ${s.client_name}` : ''}
                </option>
              ))}
            </select>
          )}
          {saleId && (
            <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
              {loadingSource
                ? 'Loading PI details…'
                : sourceError
                  ? <span className="font-medium text-rose-600 dark:text-rose-400">{sourceError}</span>
                  : products.length === 0
                    ? 'No items found on this PI.'
                    : `${products.length} item${products.length === 1 ? '' : 's'} · total quantity ${formatNumber(totalProductQty)}`}
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Invoice number</label>
            <select
              aria-label="Invoice number"
              value={invoiceChoice}
              onChange={(e) => handleInvoiceChoiceChange(e.target.value)}
              disabled={!saleId || loadingSource}
              className={inputCls}
            >
              <option value="">
                {!saleId ? 'Select a PI first' : loadingSource ? 'Loading…' : 'No invoice'}
              </option>
              {invoices.map((inv) => (
                <option key={inv.invoice_id} value={inv.invoice_id}>
                  {inv.invoice_number}
                </option>
              ))}
              {saleId && !loadingSource && <option value="new">New invoice number…</option>}
            </select>
          </div>
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
          {invoiceChoice === 'new' && (
            <div className="col-span-2 grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">New invoice number</label>
                <input
                  aria-label="New invoice number"
                  type="text"
                  value={newInvoiceNumber}
                  onChange={(e) => setNewInvoiceNumber(e.target.value)}
                  placeholder="INV-2026-001"
                  className={inputCls}
                />
              </div>
              <div>
                <label
                  htmlFor="new-invoice-amount"
                  className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5"
                >
                  Amount
                </label>
                <input
                  id="new-invoice-amount"
                  type="number"
                  min="0"
                  step="any"
                  value={newInvoiceAmount}
                  onChange={(e) => setNewInvoiceAmount(e.target.value)}
                  placeholder="0.00"
                  aria-required={saleHasInvoices}
                  className={inputCls}
                />
              </div>
              <p className="col-span-2 text-[11px] text-slate-400 dark:text-slate-500">
                {saleHasInvoices
                  ? `Required — this sale already has invoices${remainingCents !== null ? ` (remaining $${formatNumber(fromCents(remainingCents))})` : ''}.`
                  : 'Optional — blank uses the sale total.'}
              </p>
            </div>
          )}
        </div>

        <div className="grid grid-cols-3 gap-3">
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
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Recipes *</h4>
            <Button variant="secondary" size="sm" icon={Plus} onClick={addRecipeRow}>
              Add recipe
            </Button>
          </div>
          <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
            <div className="grid grid-cols-[1fr_9rem_2.25rem] gap-2 bg-slate-50 dark:bg-slate-800/60 px-3 py-2 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              <span>Recipe</span>
              <span>Quantity</span>
              <span className="sr-only">Actions</span>
            </div>
            {recipeRows.map((row, index) => (
              <div
                key={row.key}
                className="grid grid-cols-[1fr_9rem_2.25rem] gap-2 items-center px-3 py-2 border-t border-slate-100 dark:border-slate-800"
              >
                <select
                  aria-label={index === 0 ? 'Recipe' : `Recipe ${index + 1}`}
                  value={row.recipe_name}
                  onChange={(e) => setRowField(index, 'recipe_name', e.target.value)}
                  className={inputCls}
                >
                  <option value="">Select recipe…</option>
                  {rowOptions(row).map((rec) => (
                    <option key={rec.name} value={rec.name}>{rec.name}</option>
                  ))}
                </select>
                <input
                  aria-label={index === 0 ? 'Quantity' : `Recipe qty ${index + 1}`}
                  type="number"
                  min="0"
                  step="any"
                  value={row.qty}
                  onChange={(e) => setRowField(index, 'qty', e.target.value)}
                  placeholder="0"
                  className={inputCls}
                />
                <div className="flex justify-center">
                  {index > 0 && (
                    <button
                      type="button"
                      onClick={() => removeRecipeRow(index)}
                      aria-label={`Remove recipe ${index + 1}`}
                      title="Remove row"
                      className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 cursor-pointer"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          {companyId && !loadingRefs && !refsError && companyRecipes.length === 0 && (
            <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500 italic">
              No recipes registered for this company yet.
            </p>
          )}
        </div>

        {error && (
          <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
        )}
      </div>
    </Modal>
  );
}
