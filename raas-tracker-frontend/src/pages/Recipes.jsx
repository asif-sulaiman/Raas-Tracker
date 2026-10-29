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
  Droplets,
  Hash,
  Weight,
  Factory
} from 'lucide-react';
import clsx from 'clsx';
import Button from '../components/ui/Button';
import Modal from '../components/modals/Modal';
import KpiCard from '../components/cards/KpiCard';
import { formatNumber } from '../utils/format';
import { INVOICE_STATUS_LABELS } from '../utils/invoices';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';
import ProductionRunModal from '../components/modals/ProductionRunModal';

export default function Recipes() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const [recipes, setRecipes] = useState([]);
  const [chemicals, setChemicals] = useState([]);
  const [loading, setLoading] = useState(true);

  const [selectedRecipe, setSelectedRecipe] = useState(null);
  const [recipeItems, setRecipeItems] = useState([]);

const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDetail, setShowDetail] = useState(false);
  const [showEditMeta, setShowEditMeta] = useState(false);
  const [showProductionRun, setShowProductionRun] = useState(false);

  const [newName, setNewName] = useState('');
  const [newYield, setNewYield] = useState('');
  const [newWater, setNewWater] = useState('');
  const [companies, setCompanies] = useState([]);
  const [allLcs, setAllLcs] = useState([]);
  const [companyId, setCompanyId] = useState('');
  const [lcId, setLcId] = useState('');
  const [lcPis, setLcPis] = useState([]);
  const [saleId, setSaleId] = useState('');
  const [invoices, setInvoices] = useState([]);
  const [invoiceId, setInvoiceId] = useState('');
  const [invoiceItems, setInvoiceItems] = useState([]);
  const [productName, setProductName] = useState('');
  const [loadingRefs, setLoadingRefs] = useState(false);
  const [loadingPis, setLoadingPis] = useState(false);
  const [loadingInvoiceItems, setLoadingInvoiceItems] = useState(false);

  const [editYield, setEditYield] = useState('');
  const [editWater, setEditWater] = useState('');

  const [addChemical, setAddChemical] = useState('');
  const [addPct, setAddPct] = useState('');
  const [editItemId, setEditItemId] = useState(null);
  const [editItemPct, setEditItemPct] = useState('');

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
  // carries (the recipe's batch basis prefill, never a production cap).
  const invoiceProducts = useMemo(() => {
    const byName = new Map();
    for (const item of invoiceItems) {
      const name = (item.product_name || '').trim();
      if (!name) continue;
      const entry = byName.get(name) || { name, unit: item.unit || 'KG', quantity: 0 };
      entry.quantity += Number(item.quantity) || 0;
      byName.set(name, entry);
    }
    return [...byName.values()];
  }, [invoiceItems]);

  const recipeProducts = useMemo(() => new Set(
    recipes
      .filter(r => String(r.company_id) === String(companyId) && r.product_name)
      .map(r => r.product_name.trim().toLowerCase())
  ), [recipes, companyId]);

  const productsWithRecipe = invoiceProducts.filter(
    p => recipeProducts.has(p.name.toLowerCase())
  ).length;

  const selectedPi = lcPis.find(p => String(p.id) === String(saleId));
  const piLabel = selectedPi?.pi_number || (saleId ? `PI #${saleId}` : '');
  const namePlaceholder = piLabel && productName
    ? `e.g. ${piLabel} — ${productName}`
    : 'e.g. PI-2026-014 — Caustic Soda 50%';

  const handleCompanyChange = (value) => {
    setCompanyId(value);
    setLcId('');
    setLcPis([]);
    setSaleId('');
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
    setProductName('');
  };

  const handleLcChange = async (value) => {
    setLcId(value);
    setLcPis([]);
    setSaleId('');
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
    setProductName('');
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
    setProductName('');
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
    setProductName('');
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

  const handleProductChange = (value) => {
    setProductName(value);
    const match = invoiceProducts.find(p => p.name === value);
    setNewYield(match ? String(match.quantity) : '');
  };

  const fetchRecipeDetail = async (name, companyId) => {
    try {
      const res = await apiFetch(
        '/api/recipes/' + encodeURIComponent(name) + '?company_id=' + encodeURIComponent(companyId)
      );
      const data = await res.json();
      if (data.recipe) {
        setSelectedRecipe(data.recipe);
        setRecipeItems(data.items || []);
        setShowDetail(true);
      }
    } catch (err) {
      toast.error(err.message || 'Could not load recipe');
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return toast.error('Recipe name required');
    if (!companyId) return toast.error('Company is required');
    if (!lcId) return toast.error('LC is required');
    if (!saleId) return toast.error('PI / Sale is required');
    if (!invoiceId) return toast.error('Invoice is required');
    if (!productName) return toast.error('Product name is required');
    try {
      const res = await apiFetch('/api/recipes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newName.trim(),
          yield_qty: parseFloat(newYield) || 1,
          water_percentage: parseFloat(newWater) || 0,
          company_id: Number(companyId),
          product_name: productName
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `Failed to create recipe (${res.status})`);
      // Keep the LC/PI/invoice selection: a PI with several products needs one
      // recipe per product, so the next one should be one click away.
      setNewName('');
      setNewYield('');
      setNewWater('');
      setProductName('');
      toast.success(`Recipe "${newName.trim()}" created`);
      setLoading(true);
      fetchData();
    } catch (err) {
      toast.error(err.message || 'Failed to create recipe');
    }
  };

  const handleDelete = async (name, companyId) => {
    const ok = await confirm({
      title: `Delete recipe "${name}"?`,
      message: 'The recipe and all its ingredients will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch(
        '/api/recipes/' + encodeURIComponent(name) + '?company_id=' + encodeURIComponent(companyId),
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (data.success) {
        if (showDetail && selectedRecipe?.name === name) setShowDetail(false);
        toast.success(`Recipe "${name}" deleted`);
        setLoading(true);
        fetchData();
      }
    } catch (err) {
      toast.error(err.message || 'Could not delete recipe');
    }
  };

  const handleUpdateMeta = async () => {
    if (!selectedRecipe) return;
    try {
      const res = await apiFetch('/api/recipes/' + encodeURIComponent(selectedRecipe.name), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_id: selectedRecipe.company_id,
          total_quantity: parseFloat(editYield) || selectedRecipe.total_quantity,
          water_percentage: parseFloat(editWater) || selectedRecipe.water_percentage
        })
      });
      const data = await res.json();
      if (data.success) {
        setShowEditMeta(false);
        toast.success('Recipe updated');
        fetchRecipeDetail(selectedRecipe.name, selectedRecipe.company_id);
        setLoading(true);
        fetchData();
      }
    } catch (err) {
      toast.error(err.message || 'Could not update recipe');
    }
  };

  const handleAddItem = async () => {
    if (!addChemical || !addPct) return toast.error('Select chemical and enter percentage');
    try {
      const res = await apiFetch('/api/recipes/' + encodeURIComponent(selectedRecipe.name) + '/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chemical: addChemical, percentage: parseFloat(addPct), company_id: selectedRecipe.company_id })
      });
      const data = await res.json();
      if (data.success) {
        setAddChemical('');
        setAddPct('');
        toast.success('Ingredient added');
        fetchRecipeDetail(selectedRecipe.name, selectedRecipe.company_id);
      } else {
        toast.error('Failed to add ingredient');
      }
    } catch (err) {
      toast.error(err.message || 'Failed to add ingredient');
    }
  };

  const handleUpdateItem = async (chem) => {
    try {
      const res = await apiFetch(
        '/api/recipes/' + encodeURIComponent(selectedRecipe.name) + '/items/' + encodeURIComponent(chem),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ percentage: parseFloat(editItemPct), company_id: selectedRecipe.company_id })
        }
      );
      const data = await res.json();
      if (data.success) {
        setEditItemId(null);
        setEditItemPct('');
        toast.success('Ingredient updated');
        fetchRecipeDetail(selectedRecipe.name, selectedRecipe.company_id);
      }
    } catch (err) {
      toast.error(err.message || 'Could not update ingredient');
    }
  };

  const handleDeleteItem = async (chem) => {
    const ok = await confirm({
      title: `Remove "${chem}" from recipe?`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch(
        '/api/recipes/' + encodeURIComponent(selectedRecipe.name) + '/items/' + encodeURIComponent(chem) +
          '?company_id=' + encodeURIComponent(selectedRecipe.company_id),
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (data.success) {
        toast.success('Ingredient removed');
        fetchRecipeDetail(selectedRecipe.name, selectedRecipe.company_id);
      }
    } catch (err) {
      toast.error(err.message || 'Could not remove ingredient');
    }
  };

  const availableChemicals = chemicals.filter(
    c => !recipeItems.some(item => item.chemical_name === c.name)
  );

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
    const sumPct = recipeItems.reduce((s, i) => s + (i.percentage || 0), 0);
    const waterPct = selectedRecipe.water_percentage || (100 - sumPct);

    return (
      <div className="space-y-6 max-w-7xl mx-auto">
        <div className="flex items-center gap-4">
          <button
            onClick={() => { setShowDetail(false); setSelectedRecipe(null); setLoading(true); fetchData(); }}
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
          >
            <ArrowLeft className="h-5 w-5 text-slate-600 dark:text-slate-400" />
          </button>
          <div className="flex-1">
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{selectedRecipe.name}</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              Total Quantity: <span className="font-semibold text-blue-600 dark:text-blue-400">{formatNumber(selectedRecipe.total_quantity)}</span>
              {' '}&middot;{' '}
              Water: <span className="font-semibold">{selectedRecipe.water_percentage}%</span>
              {' '}&middot;{' '}
              {recipeItems.length} ingredient{recipeItems.length !== 1 ? 's' : ''}
            </p>
          </div>
<div className="flex gap-2">
            <Button variant="secondary" size="sm" icon={Edit3} onClick={() => {
              setEditYield(String(selectedRecipe.total_quantity));
              setEditWater(String(selectedRecipe.water_percentage));
              setShowEditMeta(true);
            }}>Edit</Button>
            {isAdmin && (
              <Button variant="danger" size="sm" icon={Trash2} onClick={() => handleDelete(selectedRecipe.name, selectedRecipe.company_id)}>Delete</Button>
            )}
            {isAdmin && recipeItems.length > 0 && (
              <Button variant="primary" size="sm" icon={Factory} onClick={() => setShowProductionRun(true)}>
                Go for Production
              </Button>
            )}
          </div>
        </div>

        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs p-5">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-3">Formulation Summary</h3>
          <div className="flex flex-wrap gap-6 items-center">
            <div className="flex items-center gap-2">
              <Weight className="h-4 w-4 text-slate-400" />
              <span className="text-sm text-slate-600 dark:text-slate-400">Raw Materials:</span>
              <span className={clsx('text-lg font-bold', sumPct === 100 ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400')}>
                {sumPct.toFixed(1)}%
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Droplets className="h-4 w-4 text-blue-400" />
              <span className="text-sm text-slate-600 dark:text-slate-400">Water:</span>
              <span className="text-lg font-bold text-blue-600 dark:text-blue-400">{waterPct.toFixed(1)}%</span>
            </div>
            <div className="flex items-center gap-2">
              <Hash className="h-4 w-4 text-slate-400" />
              <span className="text-sm text-slate-600 dark:text-slate-400">Grand Total:</span>
              <span className={clsx('text-lg font-bold', (sumPct + waterPct) === 100 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400')}>
                {(sumPct + waterPct).toFixed(1)}%
              </span>
              {(sumPct + waterPct) !== 100 && (
                <AlertTriangle className="h-4 w-4 text-amber-500" />
              )}
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
          <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Ingredients</h3>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 dark:border-slate-800">
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">#</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Chemical</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Percentage</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Batch Qty</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Current Stock</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
              {recipeItems.map((item, idx) => (
                <tr key={item.chemical_name} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                  <td className="px-4 py-3 text-slate-400">{idx + 1}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-100 dark:bg-slate-800">
                        <FlaskConical className="h-3.5 w-3.5 text-slate-500 dark:text-slate-400" />
                      </div>
                      <span className="font-medium text-slate-900 dark:text-white">{item.chemical_name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {editItemId === item.chemical_name ? (
                      <div className="flex items-center gap-1.5">
                        <input
                          type="number"
                          step="0.01"
                          value={editItemPct}
                          onChange={e => setEditItemPct(e.target.value)}
                          className="w-20 px-2 py-1 text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                          autoFocus
                        />
                        <button onClick={() => handleUpdateItem(item.chemical_name)} className="p-1 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 rounded cursor-pointer">
                          <Check className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => { setEditItemId(item.chemical_name); setEditItemPct(String(item.percentage)); }}
                        className="px-2 py-1 text-sm rounded-md bg-slate-50 dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-mono transition-colors cursor-pointer"
                      >
                        {item.percentage}%
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono">{formatNumber(item.batch_qty)}</td>
                  <td className="px-4 py-3">
                    <span className={clsx('font-mono', item.current_stock > 0 ? 'text-slate-700 dark:text-slate-300' : 'text-rose-500')}>
                      {formatNumber(item.current_stock)} {item.unit}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {isAdmin && (
                      <button
                        onClick={() => handleDeleteItem(item.chemical_name)}
                        className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer"
                        title="Remove ingredient"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {recipeItems.length === 0 && (
                <tr>
                  <td colSpan="6" className="px-4 py-8 text-center text-slate-400 dark:text-slate-500">
                    No ingredients yet. Add chemicals below.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs p-5">
          <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300 mb-3">Add Ingredient</h3>
          <div className="flex items-end gap-3 flex-wrap">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Chemical</label>
              <select
                value={addChemical}
                onChange={e => setAddChemical(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                <option value="">Select chemical...</option>
                {availableChemicals.map(c => (
                  <option key={c.name} value={c.name}>{c.name}</option>
                ))}
              </select>
            </div>
            <div className="w-32">
              <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Percentage %</label>
              <input
                type="number"
                step="0.01"
                min="0"
                max="100"
                value={addPct}
                onChange={e => setAddPct(e.target.value)}
                placeholder="0"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
            <Button variant="primary" size="sm" icon={Plus} onClick={handleAddItem}>
              Add
            </Button>
          </div>
        </div>

        <Modal
          isOpen={showEditMeta}
          onClose={() => setShowEditMeta(false)}
          title="Edit Recipe"
          footer={
            <>
              <Button variant="secondary" size="sm" onClick={() => setShowEditMeta(false)}>Cancel</Button>
              <Button variant="primary" size="sm" onClick={handleUpdateMeta}>Save</Button>
            </>
          }
        >
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Total Quantity</label>
              <input
                type="number"
                value={editYield}
                onChange={e => setEditYield(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Water Percentage</label>
              <input
                type="number"
                step="0.01"
                value={editWater}
                onChange={e => setEditWater(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>
        </Modal>

        <ProductionRunModal
          isOpen={showProductionRun}
          onClose={() => setShowProductionRun(false)}
          recipe={selectedRecipe}
          recipeItems={recipeItems}
          onSaved={() => setShowProductionRun(false)}
        />
      </div>
    );
  }

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
        <KpiCard label="Total Recipes" value={recipes.length} icon={BookOpen} />
        <KpiCard label="Total Chemicals" value={chemicals.length} icon={FlaskConical} />
        <KpiCard label="Recipes with Water" value={recipes.filter(r => r.water_percentage > 0).length} icon={Droplets} />
      </div>

      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">#</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Recipe Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Total Quantity</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Water %</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Created</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
            {recipes.map((recipe, idx) => (
              <tr key={recipe.name} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                <td className="px-4 py-3 text-slate-400">{idx + 1}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400">
                      <BookOpen className="h-4 w-4" />
                    </div>
                    <span className="font-medium text-slate-900 dark:text-white">{recipe.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono">{formatNumber(recipe.total_quantity)}</td>
                <td className="px-4 py-3">
                  <span className={clsx('font-mono', recipe.water_percentage > 0 ? 'text-blue-600 dark:text-blue-400' : 'text-slate-400')}>
                    {recipe.water_percentage}%
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs">{recipe.created || '—'}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <Button variant="ghost" size="sm" icon={Eye} onClick={() => fetchRecipeDetail(recipe.name, recipe.company_id)}>
                      View
                    </Button>
                    {isAdmin && (
                      <button
                        onClick={() => handleDelete(recipe.name, recipe.company_id)}
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
                <td colSpan="6" className="px-4 py-8 text-center text-slate-400 dark:text-slate-500">
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
            <Button variant="primary" size="sm" icon={Plus} onClick={handleCreate}>Create</Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            A recipe is the production formula for one product on an invoice.
            Only LCs with open PIs, and invoices still in production, are listed.
          </p>
          <div className="grid grid-cols-1 gap-3">
            <div>
              <label htmlFor="recipe-company" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Company *</label>
              <select
                id="recipe-company"
                value={companyId}
                onChange={e => handleCompanyChange(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                <option value="">Select company…</option>
                {loadingRefs ? <option disabled>Loading…</option> : companies.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="recipe-lc" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">LC *</label>
              <select
                id="recipe-lc"
                value={lcId}
                onChange={e => handleLcChange(e.target.value)}
                disabled={!companyId}
                aria-describedby="recipe-lc-help"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
              >
                <option value="">{companyId ? 'Select LC…' : 'Select a company first'}</option>
                {companyId && companyLcs.map(l => {
                  const count = l.producible_pi_count ?? 0;
                  return (
                    <option key={l.id} value={l.id} disabled={count === 0}>
                      {l.lc_number} · {count === 0
                        ? 'no invoices in production'
                        : `${count} PI${count === 1 ? '' : 's'} in production`}
                    </option>
                  );
                })}
              </select>
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
              <select
                id="recipe-pi"
                value={saleId}
                onChange={e => handleSaleChange(e.target.value)}
                disabled={!lcId || loadingPis}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
              >
                <option value="">{!lcId ? 'Select an LC first' : loadingPis ? 'Loading PIs…' : 'Select PI/Sale…'}</option>
                {lcId && lcPis.map(s => {
                  const count = s.producible_invoice_count ?? 0;
                  return (
                    <option key={s.id} value={s.id} disabled={count === 0}>
                      {s.pi_number || `PI #${s.id}`}{s.client_name ? ` — ${s.client_name}` : ''} · {count === 0
                        ? 'no invoices in production'
                        : `${count} invoice${count === 1 ? '' : 's'} in production`}
                    </option>
                  );
                })}
              </select>
            </div>
            <div>
              <label htmlFor="recipe-invoice" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Invoice *</label>
              <select
                id="recipe-invoice"
                value={invoiceId}
                onChange={e => handleInvoiceChange(e.target.value)}
                disabled={!saleId}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
              >
                <option value="">{saleId ? 'Select invoice…' : 'Select a PI first'}</option>
                {invoices.map(inv => {
                  const id = inv.invoice_id ?? inv.id;
                  return (
                    <option key={id} value={id}>
                      {inv.invoice_number || `Invoice #${id}`} · {INVOICE_STATUS_LABELS[inv.status] || inv.status}
                    </option>
                  );
                })}
              </select>
            </div>
            <div>
              <label htmlFor="recipe-product" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Product *</label>
              <select
                id="recipe-product"
                value={productName}
                onChange={e => handleProductChange(e.target.value)}
                disabled={!invoiceId || loadingInvoiceItems}
                aria-describedby="recipe-product-help"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
              >
                <option value="">{loadingInvoiceItems ? 'Loading items…' : invoiceId ? 'Select product…' : 'Select an invoice first'}</option>
                {invoiceProducts.map(p => (
                  <option key={p.name} value={p.name}>
                    {p.name} · {formatNumber(p.quantity)} {p.unit}
                    {recipeProducts.has(p.name.toLowerCase()) ? ' · recipe exists' : ''}
                  </option>
                ))}
              </select>
              <p id="recipe-product-help" aria-live="polite" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                {invoiceProducts.length > 0
                  ? `${productsWithRecipe} of ${invoiceProducts.length} products on this invoice have a recipe`
                  : 'Products come from the selected invoice.'}
              </p>
            </div>
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
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <p id="recipe-name-help" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
              {piLabel && productName
                ? <>Selected: {piLabel} · {productName}</>
                : 'Name it yourself — the PI and product appear here once chosen.'}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="recipe-total" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Total Quantity</label>
              <input
                id="recipe-total"
                type="number"
                value={newYield}
                onChange={e => setNewYield(e.target.value)}
                placeholder="15630"
                aria-describedby="recipe-total-help"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <p id="recipe-total-help" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                Batch basis — production quantity is entered when producing.
              </p>
            </div>
            <div>
              <label htmlFor="recipe-water" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Water % (optional)</label>
              <input
                id="recipe-water"
                type="number"
                step="0.01"
                value={newWater}
                onChange={e => setNewWater(e.target.value)}
                placeholder="0"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Add ingredients after creating the recipe.
          </p>
        </div>
      </Modal>
    </div>
  );
}
