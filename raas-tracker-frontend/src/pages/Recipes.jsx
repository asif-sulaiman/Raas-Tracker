import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  BookOpen,
  FlaskConical,
  Plus,
  Trash2,
  Eye,
  ArrowLeft,
  Edit3,
  Check,
  AlertTriangle,
  Factory
} from 'lucide-react';
import clsx from 'clsx';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import EntityCombobox from '../components/ui/EntityCombobox';
import Modal from '../components/modals/Modal';
import KpiCard from '../components/cards/KpiCard';
import { formatNumber } from '../utils/format';
import { INVOICE_STATUS_LABELS } from '../utils/invoices';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';
import ProductionRunModal from '../components/modals/ProductionRunModal';

const inputCls =
  'w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent';

/**
 * One product's formulation. The invoice defines the product and its
 * quantities; this card only owns the chemicals and their percentages, so a
 * product added to the invoice later appears as an empty card on reload.
 */
function ProductFormulationCard({ product, chemicals, isAdmin, onAdd, onUpdate, onDelete }) {
  const [chemical, setChemical] = useState('');
  const [pct, setPct] = useState('');
  const [editItemId, setEditItemId] = useState(null);
  const [editPct, setEditPct] = useState('');
  const [saving, setSaving] = useState(false);

  const available = chemicals.filter(
    (c) => !product.items.some((item) => item.chemical_name === c.name)
  );

  const submitAdd = async () => {
    if (!chemical || pct === '') {
      toast.error('Select chemical and enter percentage');
      return;
    }
    setSaving(true);
    try {
      const ok = await onAdd(product, chemical, parseFloat(pct));
      if (ok) {
        setChemical('');
        setPct('');
      }
    } finally {
      setSaving(false);
    }
  };

  const submitEdit = async (item) => {
    setSaving(true);
    try {
      const ok = await onUpdate(product, item, parseFloat(editPct));
      if (ok) {
        setEditItemId(null);
        setEditPct('');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      aria-label={`${product.product_name} formulation`}
      className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden"
    >
      <div className="p-4 border-b border-slate-100 dark:border-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400">
              <FlaskConical className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                {product.product_name}
              </h3>
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                {product.items.length} ingredient{product.items.length === 1 ? '' : 's'}
                {' '}&middot;{' '}
                {product.percentage.toFixed(1)}% raw materials
              </p>
            </div>
          </div>
          <Badge variant={product.formulated ? 'success' : 'warning'}>
            {product.formulated ? 'Ready' : 'Empty formulation'}
          </Badge>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-2 text-[11px] text-slate-600 dark:text-slate-400">
          <p>Invoiced {formatNumber(product.invoiced_qty)} {product.unit}</p>
          <p>Produced {formatNumber(product.produced_qty)} {product.unit}</p>
          <p className="font-semibold text-slate-800 dark:text-slate-200">
            Remaining {formatNumber(product.remaining_qty)} {product.unit}
          </p>
        </div>

        {product.line_count > 1 && (
          <p className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">
            {product.line_count} lines merged · {formatNumber(product.invoiced_qty)} {product.unit} on the invoice
          </p>
        )}
        {product.unconverted && product.unconverted.length > 0 && (
          <p className="mt-1.5 flex items-start gap-1 text-[11px] text-rose-600 dark:text-rose-400">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              Some invoice lines need a unit conversion (
              {product.unconverted.map((u) => `${u.from_unit} → ${u.to_unit}`).join(', ')}
              ) — add it in Unit Conversions before producing.
            </span>
          </p>
        )}
      </div>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-100 dark:border-slate-800">
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">#</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Chemical</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Percentage</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Current Stock</th>
            <th className="px-4 py-2.5 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
          {product.items.map((item, idx) => (
            <tr key={item.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
              <td className="px-4 py-2.5 text-slate-400">{idx + 1}</td>
              <td className="px-4 py-2.5 font-medium text-slate-900 dark:text-white">
                {item.chemical_name}
              </td>
              <td className="px-4 py-2.5">
                {editItemId === item.id ? (
                  <div className="flex items-center gap-1.5">
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      max="100"
                      aria-label={`Percentage for ${item.chemical_name} in ${product.product_name}`}
                      value={editPct}
                      onChange={(e) => setEditPct(e.target.value)}
                      className="w-20 px-2 py-1 text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white"
                      autoFocus
                    />
                    <button
                      aria-label={`Save ${item.chemical_name} percentage in ${product.product_name}`}
                      onClick={() => submitEdit(item)}
                      disabled={saving}
                      className="p-1 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 rounded cursor-pointer"
                    >
                      <Check className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  <button
                    aria-label={`Edit ${item.chemical_name} percentage in ${product.product_name}`}
                    onClick={() => { setEditItemId(item.id); setEditPct(String(item.percentage)); }}
                    className="px-2 py-1 text-sm rounded-md bg-slate-50 dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-mono transition-colors cursor-pointer"
                  >
                    {item.percentage}%
                  </button>
                )}
              </td>
              <td className="px-4 py-2.5">
                <span className={clsx('font-mono', item.current_stock > 0 ? 'text-slate-700 dark:text-slate-300' : 'text-rose-500')}>
                  {formatNumber(item.current_stock)} {item.unit}
                </span>
              </td>
              <td className="px-4 py-2.5 text-right">
                {isAdmin && (
                  <button
                    aria-label={`Remove ${item.chemical_name} from ${product.product_name}`}
                    onClick={() => onDelete(product, item)}
                    className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </td>
            </tr>
          ))}
          {product.items.length === 0 && (
            <tr>
              <td colSpan="5" className="px-4 py-6 text-center text-slate-400 dark:text-slate-500">
                No ingredients yet. Add this product&apos;s chemicals below.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="p-4 border-t border-slate-100 dark:border-slate-800 flex items-end gap-3 flex-wrap">
        <div className="flex-1 min-w-[180px]">
          <label
            htmlFor={`chem-${product.product_name}`}
            className="block text-xs text-slate-500 dark:text-slate-400 mb-1"
          >
            Chemical
          </label>
          <EntityCombobox
            id={`chem-${product.product_name}`}
            aria-label={`Chemical for ${product.product_name}`}
            options={available.map((c) => ({ value: c.name, label: c.name }))}
            value={chemical}
            onChange={setChemical}
            placeholder="Search chemicals…"
            className={inputCls}
          />
        </div>
        <div className="w-32">
          <label
            htmlFor={`pct-${product.product_name}`}
            className="block text-xs text-slate-500 dark:text-slate-400 mb-1"
          >
            Percentage %
          </label>
          <input
            id={`pct-${product.product_name}`}
            aria-label={`Percentage for ${product.product_name}`}
            type="number"
            step="0.01"
            min="0"
            max="100"
            value={pct}
            onChange={(e) => setPct(e.target.value)}
            placeholder="0"
            className={inputCls}
          />
        </div>
        <Button
          variant="primary"
          size="sm"
          icon={Plus}
          onClick={submitAdd}
          loading={saving}
          aria-label={`Add ingredient to ${product.product_name}`}
        >
          Add
        </Button>
      </div>
    </section>
  );
}

export default function Recipes() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const [recipes, setRecipes] = useState([]);
  const [chemicals, setChemicals] = useState([]);
  const [loading, setLoading] = useState(true);

  const [selectedRecipe, setSelectedRecipe] = useState(null);
  const [detailProducts, setDetailProducts] = useState([]);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [showEditMeta, setShowEditMeta] = useState(false);
  const [showProductionRun, setShowProductionRun] = useState(false);

  const [newName, setNewName] = useState('');
  const [companies, setCompanies] = useState([]);
  const [allLcs, setAllLcs] = useState([]);
  const [companyId, setCompanyId] = useState('');
  const [lcId, setLcId] = useState('');
  const [lcPis, setLcPis] = useState([]);
  const [saleId, setSaleId] = useState('');
  const [invoices, setInvoices] = useState([]);
  const [invoiceId, setInvoiceId] = useState('');
  const [invoiceItems, setInvoiceItems] = useState([]);
  const [loadingRefs, setLoadingRefs] = useState(false);
  const [loadingPis, setLoadingPis] = useState(false);
  const [loadingInvoiceItems, setLoadingInvoiceItems] = useState(false);

  const [editName, setEditName] = useState('');
  const [editError, setEditError] = useState(null);
  const [savingMeta, setSavingMeta] = useState(false);

  // Fetch only — loading is already true on mount; event-handler refreshers
  // flip the spinner on themselves before calling this.
  const fetchData = useCallback(async () => {
    try {
      const [recsRes, chemsRes] = await Promise.all([
        apiFetch('/api/recipes'),
        apiFetch('/api/chemicals')
      ]);
      const [recs, chems] = await Promise.all([recsRes.json(), chemsRes.json()]);
      if (Array.isArray(recs)) setRecipes(recs);
      if (Array.isArray(chems)) setChemicals(chems);
    } catch {
      // List stays as-is on failure; mutations surface their own errors.
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    (async () => {
      await fetchData();
    })();
  }, [fetchData]);

  useEffect(() => {
    if (!showCreateModal) return;
    (async () => {
      setLoadingRefs(true);
      try {
        // Only LCs with an open child PI — a closed LC has nothing left to
        // produce, so it must never be an option here.
        const [coRes, lcRes] = await Promise.all([
          apiFetch('/api/companies'),
          apiFetch('/api/lcs?open_only=true'),
        ]);
        const [cos, lcs] = await Promise.all([coRes.json(), lcRes.json()]);
        setCompanies(Array.isArray(cos) ? cos : []);
        setAllLcs(Array.isArray(lcs) ? lcs : []);
      } catch (err) {
        toast.error(err?.message || 'Could not load running LCs');
      } finally {
        setLoadingRefs(false);
      }
    })();
  }, [showCreateModal, apiFetch]);

  const companyLcs = useMemo(
    () => allLcs.filter(l => String(l.company_id) === String(companyId)),
    [allLcs, companyId]
  );

  // Distinct products on the selected invoice, with the quantity each line
  // carries. Read-only: the invoice, not the operator, defines the products.
  const invoiceProducts = useMemo(() => {
    const byName = new Map();
    for (const item of invoiceItems) {
      const name = (item.product_name || '').trim();
      if (!name) continue;
      const key = name.toLowerCase();
      const entry = byName.get(key) || { name, unit: item.unit || 'KG', quantity: 0 };
      entry.quantity += Number(item.quantity) || 0;
      byName.set(key, entry);
    }
    return [...byName.values()];
  }, [invoiceItems]);

  const invoiceRecipe = useMemo(
    () => recipes.find(r => String(r.invoice_id) === String(invoiceId)) || null,
    [recipes, invoiceId]
  );

  const selectedPi = lcPis.find(p => String(p.id) === String(saleId));
  const piLabel = selectedPi?.pi_number || (saleId ? `PI #${saleId}` : '');
  const selectedInvoice = invoices.find(
    (inv) => String(inv.invoice_id ?? inv.id) === String(invoiceId)
  );
  const namePlaceholder = piLabel && selectedInvoice
    ? `e.g. ${piLabel} · ${selectedInvoice.invoice_number}`
    : 'e.g. PI-2026-014 · INV-014';

  const handleCompanyChange = (value) => {
    setCompanyId(value);
    setLcId('');
    setLcPis([]);
    setSaleId('');
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
  };

  const handleLcChange = async (value) => {
    setLcId(value);
    setLcPis([]);
    setSaleId('');
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
    if (!value) return;
    setLoadingPis(true);
    try {
      // open_only drops completed PIs; a PI with no producible invoice stays
      // visible with a count of 0 so it can be shown disabled, not hidden.
      const res = await apiFetch(`/api/lcs/${encodeURIComponent(value)}?open_only=true`);
      const data = await res.json();
      setLcPis(Array.isArray(data?.pis) ? data.pis : []);
    } catch (err) {
      setLcPis([]);
      toast.error(err?.message || 'Could not load PIs for this LC');
    } finally {
      setLoadingPis(false);
    }
  };

  const handleSaleChange = async (value) => {
    setSaleId(value);
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
    if (!value) return;
    try {
      // booked/shipped/paid invoices are past production.
      const res = await apiFetch(
        `/api/sales/${encodeURIComponent(value)}/invoices?producible=true`
      );
      const invs = await res.json();
      setInvoices(Array.isArray(invs) ? invs : []);
    } catch {
      setInvoices([]);
    }
  };

  const handleInvoiceChange = async (value) => {
    setInvoiceId(value);
    setInvoiceItems([]);
    if (!value) return;
    setLoadingInvoiceItems(true);
    try {
      const res = await apiFetch(`/api/invoices/${encodeURIComponent(value)}/items`);
      const items = await res.json();
      setInvoiceItems(Array.isArray(items) ? items : []);
    } catch (err) {
      toast.error(err?.message || 'Could not load invoice items');
    } finally {
      setLoadingInvoiceItems(false);
    }
  };

  // The list hands out the row id, so detail reads key off it: a (company,
  // name) pair that drifted would 404 on a recipe the list is showing. Older
  // payloads without an id still resolve by name.
  const fetchRecipeDetail = async (recipe) => {
    const path = recipe?.id != null
      ? '/api/recipes/by-id/' + encodeURIComponent(recipe.id)
      : '/api/recipes/' + encodeURIComponent(recipe.name) +
        '?company_id=' + encodeURIComponent(recipe.company_id);
    try {
      const res = await apiFetch(path);
      const data = await res.json();
      if (data.recipe) {
        setSelectedRecipe(data.recipe);
        setDetailProducts(Array.isArray(data.products) ? data.products : []);
        setShowDetail(true);
      }
    } catch (err) {
      if (err.status === 404) {
        // Someone deleted it while the list still had the row.
        toast.error('That recipe no longer exists — the list has been refreshed');
        setLoading(true);
        fetchData();
        return;
      }
      toast.error(err.message || 'Could not load recipe');
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return toast.error('Recipe name required');
    if (!companyId) return toast.error('Company is required');
    if (!lcId) return toast.error('LC is required');
    if (!saleId) return toast.error('PI / Sale is required');
    if (!invoiceId) return toast.error('Invoice is required');
    if (invoiceRecipe) {
      return toast.error(`${invoiceRecipe.invoice_number || 'This invoice'} already has a recipe`);
    }
    try {
      const name = newName.trim();
      const res = await apiFetch('/api/recipes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          company_id: Number(companyId),
          invoice_id: Number(invoiceId),
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `Failed to create recipe (${res.status})`);
      toast.success(`Recipe "${name}" created`);
      setNewName('');
      setShowCreateModal(false);
      setLoading(true);
      await fetchData();
      // The new recipe is empty until its products are formulated, so open it
      // instead of dropping the operator back on the list.
      const created = data.recipe || {};
      if (created.id != null) {
        await fetchRecipeDetail(created);
      }
    } catch (err) {
      toast.error(err.message || 'Failed to create recipe');
    }
  };

  const handleDelete = async (recipe) => {
    const ok = await confirm({
      title: `Delete recipe "${recipe.name}"?`,
      message: 'The recipe and all its formulations will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(recipe.id),
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (data.success) {
        if (showDetail && selectedRecipe?.id === recipe.id) setShowDetail(false);
        toast.success(`Recipe "${recipe.name}" deleted`);
        setLoading(true);
        fetchData();
      }
    } catch (err) {
      toast.error(err.message || 'Could not delete recipe');
    }
  };

  const openEditMeta = () => {
    if (!selectedRecipe) return;
    setEditName(selectedRecipe.name);
    setEditError(null);
    setShowEditMeta(true);
  };

  const handleUpdateMeta = async () => {
    if (!selectedRecipe) return;
    const trimmedName = editName.trim();
    if (!trimmedName) {
      setEditError('Recipe name is required');
      return;
    }
    setSavingMeta(true);
    setEditError(null);
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmedName }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // A name clash (409) stays here: the form is the only place the user
        // can correct it.
        setEditError(data?.error || `Could not update recipe (${res.status})`);
        return;
      }
      const finalName = data.name || trimmedName;
      const renamed = finalName !== selectedRecipe.name;
      setRecipes(prev => prev.map(r => (
        r.id === selectedRecipe.id ? { ...r, name: finalName } : r
      )));
      setSelectedRecipe(prev => (prev ? { ...prev, name: finalName } : prev));
      setShowEditMeta(false);
      toast.success(renamed ? `Recipe renamed to "${finalName}"` : 'Recipe updated');
      // The invoice and its products follow the row id, so a rename cannot
      // detach the detail from what it is showing.
      fetchRecipeDetail({ ...selectedRecipe, name: finalName });
      setLoading(true);
      fetchData();
    } catch (err) {
      setEditError(err.message || 'Could not update recipe');
    } finally {
      setSavingMeta(false);
    }
  };

  // Formulation writes key off the product, not the recipe name: the invoice
  // defines which slot an ingredient belongs to.
  const handleAddItem = async (product, chemical, percentage) => {
    if (!selectedRecipe) return false;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/products/' + encodeURIComponent(product.product_name) + '/items',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chemical, percentage }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Failed to add ingredient');
      toast.success('Ingredient added');
      await fetchRecipeDetail(selectedRecipe);
      return true;
    } catch (err) {
      toast.error(err.message || 'Failed to add ingredient');
      return false;
    }
  };

  const handleUpdateItem = async (product, item, percentage) => {
    if (!selectedRecipe) return false;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/items/' + encodeURIComponent(item.id),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ percentage }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Could not update ingredient');
      toast.success('Ingredient updated');
      await fetchRecipeDetail(selectedRecipe);
      return true;
    } catch (err) {
      toast.error(err.message || 'Could not update ingredient');
      return false;
    }
  };

  const handleDeleteItem = async (product, item) => {
    if (!selectedRecipe) return;
    const ok = await confirm({
      title: `Remove "${item.chemical_name}" from ${product.product_name}?`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/items/' + encodeURIComponent(item.id),
        { method: 'DELETE' }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Could not remove ingredient');
      toast.success('Ingredient removed');
      await fetchRecipeDetail(selectedRecipe);
    } catch (err) {
      toast.error(err.message || 'Could not remove ingredient');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="inline-flex items-center gap-2 text-slate-500 dark:text-slate-400">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />
          Loading recipes...
        </div>
      </div>
    );
  }

  if (showDetail && selectedRecipe) {
    const productCount = detailProducts.length;
    const formulatedCount = detailProducts.filter(p => p.formulated).length;
    const hasFormulated = formulatedCount > 0;

    return (
      <div className="space-y-6 max-w-7xl mx-auto">
        <div className="flex items-center gap-4">
          <button
            aria-label="Back to recipes"
            onClick={() => { setShowDetail(false); setSelectedRecipe(null); setDetailProducts([]); setLoading(true); fetchData(); }}
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
          >
            <ArrowLeft className="h-5 w-5 text-slate-600 dark:text-slate-400" />
          </button>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{selectedRecipe.name}</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              {selectedRecipe.invoice_number || '—'}
              {selectedRecipe.pi_number ? ` · ${selectedRecipe.pi_number}` : ''}
              {selectedRecipe.client_name ? ` · ${selectedRecipe.client_name}` : ''}
              {' '}&middot;{' '}
              <span>{formulatedCount} of {productCount} products formulated</span>
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" icon={Edit3} onClick={openEditMeta}>Edit</Button>
            {isAdmin && (
              <Button variant="danger" size="sm" icon={Trash2} onClick={() => handleDelete(selectedRecipe)}>Delete</Button>
            )}
            {isAdmin && hasFormulated && (
              <Button variant="primary" size="sm" icon={Factory} onClick={() => setShowProductionRun(true)}>
                Go for Production
              </Button>
            )}
          </div>
        </div>

        {!hasFormulated && productCount > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-950/40">
            <p className="text-sm font-medium text-amber-700 dark:text-amber-300">
              Formulate at least one product before producing this invoice.
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Booking stays blocked until every product is formulated and covered by a production run.
            </p>
          </div>
        )}

        {productCount === 0 && (
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 text-center text-sm text-slate-500 dark:text-slate-400">
            This invoice has no lines, so there is nothing to formulate.
          </div>
        )}

        <div className="space-y-4">
          {detailProducts.map((product) => (
            <ProductFormulationCard
              key={product.id ?? product.product_name}
              product={product}
              chemicals={chemicals}
              isAdmin={isAdmin}
              onAdd={handleAddItem}
              onUpdate={handleUpdateItem}
              onDelete={handleDeleteItem}
            />
          ))}
        </div>

        <Modal
          isOpen={showEditMeta}
          onClose={() => setShowEditMeta(false)}
          title="Edit Recipe"
          footer={
            <>
              <Button variant="secondary" size="sm" onClick={() => setShowEditMeta(false)}>Cancel</Button>
              <Button variant="primary" size="sm" onClick={handleUpdateMeta} loading={savingMeta} disabled={savingMeta}>Save</Button>
            </>
          }
        >
          <div className="space-y-4">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Renaming keeps the invoice, the formulations and the run history — they
              follow the recipe, not its label.
            </p>
            <div>
              <label htmlFor="edit-recipe-name" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Recipe Name *</label>
              <input
                id="edit-recipe-name"
                type="text"
                value={editName}
                onChange={e => setEditName(e.target.value)}
                className={inputCls}
              />
            </div>
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
              <p className="text-xs text-slate-500 dark:text-slate-400">Invoice</p>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                {selectedRecipe.invoice_number || '—'}
              </p>
              <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                The invoice and its products are the recipe&apos;s identity and cannot be changed.
              </p>
            </div>
            {editError && (
              <p role="alert" className="text-xs font-medium text-rose-600 dark:text-rose-400">
                {editError}
              </p>
            )}
          </div>
        </Modal>

        <ProductionRunModal
          key={selectedRecipe.id ?? selectedRecipe.name}
          isOpen={showProductionRun}
          onClose={() => setShowProductionRun(false)}
          recipe={selectedRecipe}
          onSaved={() => {
            setShowProductionRun(false);
            // The run changes produced/remaining, which the cards show, so the
            // detail is re-read instead of trusting the pre-run rows.
            if (selectedRecipe) {
              fetchRecipeDetail(selectedRecipe);
            }
          }}
        />
      </div>
    );
  }

  const awaitingFormulation = recipes.reduce(
    (sum, r) => sum + Math.max((r.product_count || 0) - (r.formulated_count || 0), 0),
    0
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Recipes</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {recipes.length} recipe{recipes.length !== 1 ? 's' : ''} in database
          </p>
        </div>
        <Button variant="primary" size="sm" icon={Plus} onClick={() => setShowCreateModal(true)}>
          New Recipe
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KpiCard title="Total Recipes" value={recipes.length} icon={BookOpen} color="blue" />
        <KpiCard title="Total Chemicals" value={chemicals.length} icon={FlaskConical} color="purple" />
        <KpiCard
          title="Products awaiting formulation"
          value={awaitingFormulation}
          icon={AlertTriangle}
          color={awaitingFormulation > 0 ? 'amber' : 'emerald'}
        />
      </div>

      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">#</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Recipe Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Invoice</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Products</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Formulated</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Created</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
            {recipes.map((recipe, idx) => (
              <tr key={recipe.id ?? recipe.name} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                <td className="px-4 py-3 text-slate-400">{idx + 1}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400">
                      <BookOpen className="h-4 w-4" />
                    </div>
                    <span className="font-medium text-slate-900 dark:text-white">{recipe.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono">
                  {recipe.invoice_number || '—'}
                </td>
                <td className="px-4 py-3 text-slate-700 dark:text-slate-300">
                  {recipe.product_count ?? 0}
                </td>
                <td className="px-4 py-3">
                  <span className={clsx(
                    'text-xs font-medium',
                    (recipe.formulated_count || 0) === (recipe.product_count || 0)
                      ? 'text-emerald-600 dark:text-emerald-400'
                      : 'text-amber-600 dark:text-amber-400'
                  )}>
                    {recipe.formulated_count || 0} of {recipe.product_count || 0} formulated
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs">{recipe.created || '—'}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <Button variant="ghost" size="sm" icon={Eye} onClick={() => fetchRecipeDetail(recipe)}>
                      View
                    </Button>
                    {isAdmin && (
                      <button
                        aria-label={`Delete recipe ${recipe.name}`}
                        onClick={() => handleDelete(recipe)}
                        className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer"
                        title="Delete recipe"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {recipes.length === 0 && (
              <tr>
                <td colSpan="7" className="px-4 py-8 text-center text-slate-400 dark:text-slate-500">
                  No recipes found. Create your first recipe to get started.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Create New Recipe"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setShowCreateModal(false)}>Cancel</Button>
            <Button
              variant="primary"
              size="sm"
              icon={Plus}
              onClick={handleCreate}
              disabled={Boolean(invoiceRecipe)}
            >
              Create
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            One recipe covers every product on one invoice. Only LCs with open
            PIs, and invoices still in production, are listed.
          </p>
          <div className="grid grid-cols-1 gap-3">
            <div>
              <label htmlFor="recipe-company" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Company *</label>
              <EntityCombobox
                id="recipe-company"
                options={companies.map(c => ({ value: c.id, label: c.name }))}
                value={companyId}
                onChange={handleCompanyChange}
                placeholder={loadingRefs ? 'Loading companies…' : 'Search companies…'}
                className={inputCls}
              />
            </div>
            <div>
              <label htmlFor="recipe-lc" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">LC *</label>
              <EntityCombobox
                id="recipe-lc"
                options={companyLcs.map(l => {
                  const count = l.producible_pi_count ?? 0;
                  return {
                    value: l.id,
                    label: `${l.lc_number} · ${count === 0
                      ? 'no invoices in production'
                      : `${count} PI${count === 1 ? '' : 's'} in production`}`,
                    disabled: count === 0,
                  };
                })}
                value={lcId}
                onChange={handleLcChange}
                disabled={!companyId}
                aria-describedby="recipe-lc-help"
                placeholder={companyId ? 'Search LCs…' : 'Select a company first'}
                className={clsx(inputCls, 'disabled:opacity-60')}
              />
              <p id="recipe-lc-help" aria-live="polite" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                {companyId && !loadingRefs && companyLcs.length === 0
                  ? (
                    <>No running LCs for this company. PIs without a linked LC are not shown here — link them from <Link to="/sales" className="text-blue-600 dark:text-blue-400 underline">Sales</Link>.</>
                  )
                  : 'Only LCs with open PIs are listed.'}
              </p>
            </div>
            <div>
              <label htmlFor="recipe-pi" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">PI / Sale *</label>
              <EntityCombobox
                id="recipe-pi"
                options={lcPis.map(s => {
                  const count = s.producible_invoice_count ?? 0;
                  return {
                    value: s.id,
                    label: `${s.pi_number || `PI #${s.id}`}${s.client_name ? ` — ${s.client_name}` : ''} · ${count === 0
                      ? 'no invoices in production'
                      : `${count} invoice${count === 1 ? '' : 's'} in production`}`,
                    disabled: count === 0,
                  };
                })}
                value={saleId}
                onChange={handleSaleChange}
                disabled={!lcId || loadingPis}
                placeholder={!lcId ? 'Select an LC first' : loadingPis ? 'Loading PIs…' : 'Search PIs…'}
                className={clsx(inputCls, 'disabled:opacity-60')}
              />
            </div>
            <div>
              <label htmlFor="recipe-invoice" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Invoice *</label>
              <EntityCombobox
                id="recipe-invoice"
                options={invoices.map(inv => {
                  const id = inv.invoice_id ?? inv.id;
                  return {
                    value: id,
                    label: `${inv.invoice_number || `Invoice #${id}`} · ${INVOICE_STATUS_LABELS[inv.status] || inv.status}`,
                  };
                })}
                value={invoiceId}
                onChange={handleInvoiceChange}
                disabled={!saleId}
                placeholder={saleId ? 'Search invoices…' : 'Select a PI first'}
                className={clsx(inputCls, 'disabled:opacity-60')}
              />
            </div>
            {invoiceId && (
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
                <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                  Invoice products
                </p>
                {loadingInvoiceItems ? (
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">Loading items…</p>
                ) : invoiceProducts.length === 0 ? (
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    This invoice has no lines.
                  </p>
                ) : (
                  <ul className="mt-1.5 space-y-1">
                    {invoiceProducts.map(p => (
                      <li key={p.name} className="flex items-center justify-between gap-2 text-xs">
                        <span className="text-slate-700 dark:text-slate-300">{p.name}</span>
                        <span className="font-mono text-slate-500 dark:text-slate-400">
                          {formatNumber(p.quantity)} {p.unit}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">
                  {invoiceProducts.length} products on this invoice
                </p>
                {invoiceRecipe && (
                  <p className="mt-1.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                    {invoiceRecipe.invoice_number || 'This invoice'} already has a recipe
                    ({invoiceRecipe.name}). Open it to formulate its products — one recipe per invoice.
                  </p>
                )}
              </div>
            )}
          </div>
          <div>
            <label htmlFor="recipe-name" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Recipe Name *</label>
            <input
              id="recipe-name"
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder={namePlaceholder}
              aria-describedby="recipe-name-help"
              className={inputCls}
            />
            <p id="recipe-name-help" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
              {piLabel && selectedInvoice
                ? <>Selected: {piLabel} · {selectedInvoice.invoice_number}</>
                : 'Name it yourself — the PI and invoice appear here once chosen.'}
            </p>
          </div>
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Formulate each invoice product after creating the recipe.
          </p>
        </div>
      </Modal>
    </div>
  );
}
