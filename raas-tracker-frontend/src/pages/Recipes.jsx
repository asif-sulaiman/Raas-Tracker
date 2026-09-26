import { useState, useEffect } from 'react';
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
  Weight
} from 'lucide-react';
import clsx from 'clsx';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import Modal from '../components/modals/Modal';
import KpiCard from '../components/cards/KpiCard';
import { formatNumber } from '../utils/format';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';

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

  const [newName, setNewName] = useState('');
  const [newYield, setNewYield] = useState('');
  const [newWater, setNewWater] = useState('');

  const [editYield, setEditYield] = useState('');
  const [editWater, setEditWater] = useState('');

  const [addChemical, setAddChemical] = useState('');
  const [addPct, setAddPct] = useState('');
  const [editItemId, setEditItemId] = useState(null);
  const [editItemPct, setEditItemPct] = useState('');

  const fetchData = async () => {
    setLoading(true);
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
  };

  useEffect(() => { fetchData(); }, []);

  const fetchRecipeDetail = async (name) => {
    try {
      const res = await apiFetch('/api/recipes/' + encodeURIComponent(name));
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
    try {
      const res = await apiFetch('/api/recipes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newName.trim(),
          yield: parseFloat(newYield) || 1,
          water_percentage: parseFloat(newWater) || 0
        })
      });
      const data = await res.json();
      if (data.success) {
        setShowCreateModal(false);
        setNewName('');
        setNewYield('');
        setNewWater('');
        toast.success(`Recipe "${newName.trim()}" created`);
        fetchData();
      } else {
        toast.error('Failed to create recipe');
      }
    } catch (err) {
      toast.error(err.message || 'Failed to create recipe');
    }
  };

  const handleDelete = async (name) => {
    const ok = await confirm({
      title: `Delete recipe "${name}"?`,
      message: 'The recipe and all its ingredients will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch('/api/recipes/' + encodeURIComponent(name), { method: 'DELETE' });
      const data = await res.json();
      if (data.success) {
        if (showDetail && selectedRecipe?.name === name) setShowDetail(false);
        toast.success(`Recipe "${name}" deleted`);
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
          total_quantity: parseFloat(editYield) || selectedRecipe.total_quantity,
          water_percentage: parseFloat(editWater) || selectedRecipe.water_percentage
        })
      });
      const data = await res.json();
      if (data.success) {
        setShowEditMeta(false);
        toast.success('Recipe updated');
        fetchRecipeDetail(selectedRecipe.name);
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
        body: JSON.stringify({ chemical: addChemical, percentage: parseFloat(addPct) })
      });
      const data = await res.json();
      if (data.success) {
        setAddChemical('');
        setAddPct('');
        toast.success('Ingredient added');
        fetchRecipeDetail(selectedRecipe.name);
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
          body: JSON.stringify({ percentage: parseFloat(editItemPct) })
        }
      );
      const data = await res.json();
      if (data.success) {
        setEditItemId(null);
        setEditItemPct('');
        toast.success('Ingredient updated');
        fetchRecipeDetail(selectedRecipe.name);
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
        '/api/recipes/' + encodeURIComponent(selectedRecipe.name) + '/items/' + encodeURIComponent(chem),
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (data.success) {
        toast.success('Ingredient removed');
        fetchRecipeDetail(selectedRecipe.name);
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
            onClick={() => { setShowDetail(false); setSelectedRecipe(null); fetchData(); }}
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
              <Button variant="danger" size="sm" icon={Trash2} onClick={() => handleDelete(selectedRecipe.name)}>Delete</Button>
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
                    <Button variant="ghost" size="sm" icon={Eye} onClick={() => fetchRecipeDetail(recipe.name)}>
                      View
                    </Button>
                    {isAdmin && (
                      <button
                        onClick={() => handleDelete(recipe.name)}
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
          <div>
            <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Recipe Name *</label>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder="e.g. Product name"
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Total Quantity</label>
              <input
                type="number"
                value={newYield}
                onChange={e => setNewYield(e.target.value)}
                placeholder="15630"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Water % (optional)</label>
              <input
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
