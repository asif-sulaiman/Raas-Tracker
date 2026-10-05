import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { Plus, X, AlertCircle, Loader2, Factory } from 'lucide-react';

export default function ProductionRequiredModal({
  isOpen,
  onClose,
  lcId,
  saleId,
  missingDetail,
  onSuccess,
}) {
  const { apiFetch } = useAuth();
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState(null);
  const [recipeName, setRecipeName] = useState('');
  const [productionQty, setProductionQty] = useState('');
  const [creatingRecipe, setCreatingRecipe] = useState(false);
  const [creatingRun, setCreatingRun] = useState(false);

  const handleCreateRecipe = async () => {
    if (!recipeName.trim()) {
      setFormError('Recipe name is required');
      return;
    }
    setCreatingRecipe(true);
    setFormError(null);
    try {
      const res = await fetch('/api/recipes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: recipeName.trim(), total_qty: 100, water_percentage: 0 }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Failed to create recipe');
      }
      toast.success('Recipe created');
      setRecipeName('');
    } catch (err) {
      setFormError(err.message || 'Failed to create recipe');
    } finally {
      setCreatingRecipe(false);
    }
  };

  const handleCreateRun = async () => {
    if (!productionQty.trim()) {
      setFormError('Production quantity is required');
      return;
    }
    const qty = parseFloat(productionQty);
    if (!isFinite(qty) || qty <= 0) {
      setFormError('Quantity must be a positive number');
      return;
    }
    setCreatingRun(true);
    setFormError(null);
    try {
      const res = await fetch(`/api/recipes/${encodeURIComponent(recipeName.trim())}/produce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recipe_name: recipeName.trim(),
          production_qty: qty,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Failed to start production');
      }
      toast.success('Production run started');
      if (onSuccess) onSuccess();
      onClose();
    } catch (err) {
      setFormError(err.message || 'Failed to start production');
    } finally {
      setCreatingRun(false);
    }
  };

  if (!isOpen) return null;

  const missingRecipes = missingDetail?.missing_recipes || [];
  const missingProduction = missingDetail?.missing_production || [];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Production Required"
      description="This shipment needs recipes and production runs for all invoiced products before it can proceed to Payment Due."
      size="md"
    >
      <div className="space-y-4">
        <div className="space-y-4">
          <div className="text-sm text-slate-600 dark:text-slate-400">
            <p className="flex items-center gap-2">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-orange-100 dark:bg-orange-900/50 text-orange-600 dark:text-orange-400">
                <span className="text-[10px] font-bold">!</span>
              </span>
              <span>This shipment cannot proceed to <strong>Payment Due</strong> until all invoiced products have recipes and production runs.</span>
            </p>
          </div>

          {missingRecipes.length > 0 && (
            <div className="border border-orange-200 dark:border-orange-800 rounded-lg p-3 bg-orange-50 dark:bg-orange-900/20">
              <p className="text-xs font-medium text-orange-800 dark:text-orange-200 mb-2">
                Missing Recipes
              </p>
              <ul className="list-disc list-inside text-xs text-orange-700 dark:text-orange-300 space-y-0.5">
                {missingRecipes.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          {missingProduction.length > 0 && (
            <div className="border border-orange-200 dark:border-orange-800 rounded-lg p-3 bg-orange-50 dark:bg-orange-900/20">
              <p className="text-xs font-medium text-orange-800 dark:text-orange-200 mb-2">
                Missing Production Runs
              </p>
              <ul className="list-disc list-inside text-xs text-orange-700 dark:text-orange-300 space-y-0.5">
                {missingProduction.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-3">
            <div>
              <label htmlFor="recipeName" className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                Recipe Name *
              </label>
              <input
                id="recipeName"
                type="text"
                value={recipeName}
                onChange={(e) => setRecipeName(e.target.value)}
                placeholder="e.g. Recipe for Product A"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={creatingRecipe}
                onKeyDown={(e) => e.key === 'Enter' && handleCreateRecipe()}
              />
              {formError && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{formError}</p>}
            </div>

            <div>
              <label htmlFor="productionQty" className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                Production Quantity *
              </label>
              <input
                id="productionQty"
                type="number"
                step="0.01"
                min="0.01"
                value={productionQty}
                onChange={(e) => setProductionQty(e.target.value)}
                placeholder="e.g. 100"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={creatingRun || !recipeName.trim()}
              />
              {formError && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{formError}</p>}
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" size="sm" onClick={onClose} disabled={creatingRecipe || creatingRun}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleCreateRecipe}
              disabled={creatingRecipe || !recipeName.trim()}
              loading={creatingRecipe}
            >
              <span className="flex items-center gap-1.5">
                <Plus className="h-3.5 w-3.5" />
                Create Recipe
              </span>
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleCreateRun}
              disabled={creatingRun || !recipeName.trim() || !productionQty.trim()}
              loading={creatingRun}
            >
              <span className="flex items-center gap-1.5">
                <Factory className="h-3.5 w-3.5" />
                Start Production
              </span>
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
EOF