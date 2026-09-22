import { useState, useEffect } from 'react';
import {
  FileText,
  Download,
  FileSpreadsheet,
  FlaskConical,
  CheckCircle2,
  AlertTriangle,
  BarChart3,
  BookOpen,
  TrendingDown,
  TrendingUp,
  Minus
} from 'lucide-react';
import clsx from 'clsx';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import { formatNumber } from '../utils/format';
import { useAuth } from '../context/AuthContext';
import { toast } from 'sonner';

export default function Reports() {
  const { apiFetch } = useAuth();
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);

  const [selectedRecipes, setSelectedRecipes] = useState([]);
  const [productionQty, setProductionQty] = useState('');
  const [autoCalc, setAutoCalc] = useState(true);

  const [report, setReport] = useState(null);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await apiFetch('/api/recipes');
        const data = await res.json();
        if (Array.isArray(data)) setRecipes(data);
      } catch {
        // List stays empty on failure; generate/export surface their own errors.
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (autoCalc && selectedRecipes.length > 0) {
      const total = recipes
        .filter(r => selectedRecipes.includes(r.name))
        .reduce((sum, r) => sum + (r.total_quantity || 0), 0);
      setProductionQty(String(total));
    }
  }, [selectedRecipes, autoCalc, recipes]);

  const toggleRecipe = (name) => {
    setSelectedRecipes(prev =>
      prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]
    );
  };

  const handleGenerate = async () => {
    if (selectedRecipes.length === 0) return toast.error('Select at least one recipe');
    setGenerating(true);
    try {
      const res = await apiFetch('/api/reports/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recipes: selectedRecipes,
          qty: parseFloat(productionQty) || 0
        })
      });
      const data = await res.json();
      if (data.report) {
        setReport(data);
        toast.success('Report generated');
      }
    } catch (err) {
      toast.error(err.message || 'Failed to generate report');
    } finally {
      setGenerating(false);
    }
  };

  const handleExport = async () => {
    if (!report) return;
    try {
      const res = await apiFetch('/api/reports/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recipes: report.recipes, qty: report.qty })
      });
      const data = await res.json();
      if (data.filename) toast.success('Exported to: ' + data.filename);
      else toast.error('Export failed');
    } catch (err) {
      toast.error(err.message || 'Export failed');
    }
  };

  const totalShortage = report ? report.report.filter(r => r.status === 'SHORTAGE').length : 0;
  const totalOk = report ? report.report.filter(r => r.status !== 'SHORTAGE').length : 0;

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

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">Production Reports</h2>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Check stock availability against recipe requirements</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          {/* Recipe Selection */}
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
            <h3 className="text-base font-semibold text-slate-900 dark:text-white mb-4">Select Recipes</h3>
            {recipes.length === 0 ? (
              <p className="text-sm text-slate-400 dark:text-slate-500 py-4 text-center">
                No recipes found. Create recipes first.
              </p>
            ) : (
              <div className="space-y-2">
                {recipes.map(recipe => {
                  const isSelected = selectedRecipes.includes(recipe.name);
                  return (
                    <button
                      key={recipe.name}
                      onClick={() => toggleRecipe(recipe.name)}
                      className={clsx(
                        'w-full flex items-center gap-3 p-3.5 rounded-xl border-2 text-left transition-all cursor-pointer',
                        isSelected
                          ? 'border-blue-500 bg-blue-50/50 dark:bg-blue-950/20 dark:border-blue-600'
                          : 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600'
                      )}
                    >
                      <div className={clsx(
                        'flex h-5 w-5 shrink-0 items-center justify-center rounded-md border-2',
                        isSelected
                          ? 'border-blue-500 bg-blue-500'
                          : 'border-slate-300 dark:border-slate-600'
                      )}>
                        {isSelected && <CheckCircle2 className="h-3 w-3 text-white" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <BookOpen className={clsx('h-4 w-4', isSelected ? 'text-blue-600 dark:text-blue-400' : 'text-slate-400')} />
                          <span className={clsx('text-sm font-semibold', isSelected ? 'text-blue-700 dark:text-blue-300' : 'text-slate-800 dark:text-white')}>
                            {recipe.name}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 ml-6">
                          Qty: {formatNumber(recipe.total_quantity)} {recipe.water_percentage > 0 ? ` | Water: ${recipe.water_percentage}%` : ''}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Report Results */}
          {report && (
            <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
              <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Stock Availability</h3>
                <div className="flex gap-2">
                  <Badge variant={totalShortage === 0 ? 'success' : 'error'} dot>
                    {totalShortage === 0 ? 'All OK' : `${totalShortage} shortage${totalShortage !== 1 ? 's' : ''}`}
                  </Badge>
                </div>
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 dark:border-slate-800">
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Chemical</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Current Stock</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Required</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Difference</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Status</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Recipes</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
                  {report.report.map((item) => (
                    <tr key={item.chemical_name} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-slate-100 dark:bg-slate-800">
                            <FlaskConical className="h-3.5 w-3.5 text-slate-500 dark:text-slate-400" />
                          </div>
                          <span className="font-medium text-slate-900 dark:text-white">{item.chemical_name}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono">{formatNumber(item.current_stock)} {item.unit}</td>
                      <td className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono">{formatNumber(item.required_total)} {item.unit}</td>
                      <td className="px-4 py-3">
                        <span className={clsx('font-mono font-semibold flex items-center gap-1', {
                          'text-emerald-600 dark:text-emerald-400': item.shortage > 0,
                          'text-slate-500 dark:text-slate-400': item.shortage === 0,
                          'text-rose-600 dark:text-rose-400': item.shortage < 0
                        })}>
                          {item.shortage > 0 ? <TrendingUp className="h-3 w-3" /> : item.shortage < 0 ? <TrendingDown className="h-3 w-3" /> : <Minus className="h-3 w-3" />}
                          {item.shortage > 0 ? '+' : ''}{formatNumber(item.shortage)} {item.unit}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={
                          item.status === 'SHORTAGE' ? 'error' : item.status === 'EXACT' ? 'info' : 'success'
                        } dot>
                          {item.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {item.recipes && item.recipes.map((r, i) => (
                            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
                              {r.recipe_name}: {formatNumber(r.qty_from_this_recipe)}
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-4">
          {/* Production Quantity */}
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
            <h3 className="text-base font-semibold text-slate-900 dark:text-white mb-3">Production Quantity</h3>
            <div className="space-y-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoCalc}
                  onChange={e => setAutoCalc(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm text-slate-700 dark:text-slate-300">Auto-calculate from recipes</span>
              </label>
              <input
                type="number"
                value={productionQty}
                onChange={e => { setAutoCalc(false); setProductionQty(e.target.value); }}
                placeholder="0"
                disabled={autoCalc}
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
              />
              <p className="text-[11px] text-slate-400 dark:text-slate-500">
                {selectedRecipes.length} recipe{selectedRecipes.length !== 1 ? 's' : ''} selected
              </p>
            </div>
          </div>

          {/* Summary */}
          {report && (
            <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
              <h3 className="text-base font-semibold text-slate-900 dark:text-white mb-3">Summary</h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between py-1.5 border-b border-slate-100 dark:border-slate-800">
                  <span className="text-slate-500 dark:text-slate-400">Recipes</span>
                  <span className="font-semibold text-slate-800 dark:text-white">{report.recipes.length}</span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-slate-100 dark:border-slate-800">
                  <span className="text-slate-500 dark:text-slate-400">Production Qty</span>
                  <span className="font-semibold text-slate-800 dark:text-white">{formatNumber(report.qty)}</span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-slate-100 dark:border-slate-800">
                  <span className="text-slate-500 dark:text-slate-400">Total Chemicals</span>
                  <span className="font-semibold text-slate-800 dark:text-white">{report.report.length}</span>
                </div>
                <div className="flex justify-between py-1.5 border-b border-slate-100 dark:border-slate-800">
                  <span className="text-slate-500 dark:text-slate-400">Available</span>
                  <span className="font-semibold text-emerald-600 dark:text-emerald-400">{totalOk}</span>
                </div>
                <div className="flex justify-between py-1.5">
                  <span className="text-slate-500 dark:text-slate-400">Shortages</span>
                  <span className={clsx('font-semibold', totalShortage > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400')}>
                    {totalShortage}
                  </span>
                </div>
              </div>
              {totalShortage > 0 && (
                <div className="mt-3 p-3 rounded-lg bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800">
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="h-4 w-4 text-rose-500 mt-0.5 shrink-0" />
                    <p className="text-xs text-rose-700 dark:text-rose-300">
                      {totalShortage} chemical{totalShortage !== 1 ? 's' : ''} with insufficient stock for production.
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs space-y-3">
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">Actions</h3>
            <Button
              variant="primary"
              className="w-full justify-center"
              icon={BarChart3}
              loading={generating}
              onClick={handleGenerate}
              disabled={selectedRecipes.length === 0}
            >
              Generate Report
            </Button>
            {report && (
              <Button
                variant="secondary"
                className="w-full justify-center"
                icon={Download}
                onClick={handleExport}
              >
                Export as CSV
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
