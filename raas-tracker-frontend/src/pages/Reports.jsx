import { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Download,
  FlaskConical,
  CheckCircle2,
  AlertTriangle,
  BarChart3,
  BookOpen,
  TrendingDown,
  TrendingUp,
  Minus,
  Building2,
  DollarSign,
  CreditCard
} from 'lucide-react';
import clsx from 'clsx';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import { formatNumber } from '../utils/format';
import { useAuth } from '../context/AuthContext';
import { toast } from 'sonner';
import CommercialFilters from '../components/reports/CommercialFilters';
import CommercialTable from '../components/reports/CommercialTable';
import CommercialCalendarNavigator from '../components/reports/CommercialCalendarNavigator';

/**
 * Commercial report rows are per sale-item.
 * - Item-level fields (quantity, unit_price, total_price) sum across every row.
 * - Sale-level fields (received_amount, due_amount, receive_date, maturity_date,
 *   payment_status) are repeated on each row of the same sale, so they must be
 *   deduped by sale_id before summing.
 */
function dedupeSales(rows) {
  const seen = new Set();
  const sales = [];
  let prevSaleTuple = null;
  rows.forEach(row => {
    if (row.sale_id != null) {
      const key = `sale:${row.sale_id}`;
      if (seen.has(key)) return;
      seen.add(key);
      prevSaleTuple = null;
      sales.push(row);
      return;
    }
    // Fallback when a row carries no sale_id: rows of one sale are contiguous
    // and repeat the same sale-level fields, so compare against the previous row.
    const tuple = [
      row.customer_name, row.pi_number, row.pi_date,
      row.lc_number, row.lc_date, row.maturity_date,
      row.receive_date, row.received_amount, row.due_amount, row.payment_status
    ].join('|');
    if (tuple === prevSaleTuple) return;
    prevSaleTuple = tuple;
    sales.push(row);
  });
  return sales;
}

export default function Reports() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [activeTab, setActiveTab] = useState('production');
  const [searchParams] = useSearchParams();

  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);

  const [selectedRecipes, setSelectedRecipes] = useState([]);
  const [productionQty, setProductionQty] = useState('');
  const [autoCalc, setAutoCalc] = useState(true);

  const [report, setReport] = useState(null);
  const [generating, setGenerating] = useState(false);

  // Commercial report (admin tab)
  const [commercialReport, setCommercialReport] = useState([]);
  const [commercialSummary, setCommercialSummary] = useState([]);
  const [commercialLoading, setCommercialLoading] = useState(false);
  const [commercialError, setCommercialError] = useState(null);
  const [commercialExporting, setCommercialExporting] = useState(false);
  const [commercialPage, setCommercialPage] = useState(1);
  const [companies, setCompanies] = useState([]);
  // The company filter silently falling back to "All Companies" looks identical
  // to a real filter, so loading/failed/empty are kept distinct here.
  const [companiesLoading, setCompaniesLoading] = useState(false);
  const [companiesError, setCompaniesError] = useState(null);
  const [companiesAttempt, setCompaniesAttempt] = useState(0);
  const commercialFetchedRef = useRef(false);

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
  }, [apiFetch]);

  // Fetch companies for the filter dropdown
  useEffect(() => {
    if (!isAdmin) return undefined;
    let cancelled = false;
    setCompaniesLoading(true);
    setCompaniesError(null);
    (async () => {
      try {
        const res = await apiFetch('/api/companies');
        const data = await res.json();
        if (cancelled) return;
        if (Array.isArray(data)) setCompanies(data);
      } catch (err) {
        if (cancelled) return;
        // An empty list is not evidence that no companies exist — record the
        // failure so the filter can say it is only a partial fallback.
        setCompanies([]);
        setCompaniesError((err && err.message) || 'Unknown error');
      } finally {
        if (!cancelled) setCompaniesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [apiFetch, isAdmin, companiesAttempt]);

  const retryCompanies = () => setCompaniesAttempt((n) => n + 1);

  // CommercialFilters owns the dropdown markup, so the company load state is
  // surfaced beside it instead of inside it. A failed load must never read as
  // "there are no companies to filter by".
  const companiesNotice = companiesError ? (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/40">
      <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
        Could not load companies
      </p>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        The company filter is limited to All Companies.
      </p>
      <p className="text-xs text-slate-500 dark:text-slate-400">{companiesError}</p>
      <Button variant="secondary" size="sm" onClick={retryCompanies} loading={companiesLoading}>
        Retry companies
      </Button>
    </div>
  ) : companiesLoading ? (
    <p className="text-xs text-slate-400 dark:text-slate-500">Loading companies…</p>
  ) : null;

  const fetchCommercialReport = useCallback(async () => {
    setCommercialError(null);
    setCommercialLoading(true);
    try {
      const params = new URLSearchParams(searchParams);
      const res = await apiFetch(`/api/reports/live/filtered?${params.toString()}`);
      const data = await res.json();
      if (Array.isArray(data)) {
        setCommercialReport(data);
      } else {
        setCommercialError((data && data.error) || 'Failed to load the commercial report');
      }
    } catch (err) {
      setCommercialError((err && err.message) || 'Failed to load the commercial report');
    } finally {
      setCommercialLoading(false);
    }
  }, [apiFetch, searchParams]);

  const fetchCommercialSummary = useCallback(async () => {
    setCommercialError(null);
    setCommercialLoading(true);
    try {
      const params = new URLSearchParams(searchParams);
      const res = await apiFetch(`/api/reports/live/summary?${params.toString()}`);
      const data = await res.json();
      const periods = Array.isArray(data) ? data : (Array.isArray(data?.periods) ? data.periods : null);
      if (periods) {
        setCommercialSummary(periods);
      } else {
        setCommercialError((data && data.error) || 'Failed to load the commercial summary');
      }
    } catch (err) {
      setCommercialError((err && err.message) || 'Failed to load the commercial summary');
    } finally {
      setCommercialLoading(false);
    }
  }, [apiFetch, searchParams]);

  // Fetch when filters change (searchParams changes)
  useEffect(() => {
    if (!isAdmin || activeTab !== 'commercial') return;
    commercialFetchedRef.current = false; // Allow refetch on filter change
    setCommercialPage(1); // Reset to first page on filter change
    const groupBy = searchParams.get('group_by') || 'none';
    if (groupBy === 'none') {
      // eslint-disable-next-line react-hooks/exhaustive-deps
      fetchCommercialReport();
    } else {
      // eslint-disable-next-line react-hooks/exhaustive-deps
      fetchCommercialSummary();
    }
  }, [isAdmin, activeTab, searchParams, fetchCommercialReport, fetchCommercialSummary]);

  // Auto-calc quantity derives from the selection. It is recomputed in the
  // event handlers that change the inputs — recipes load exactly once in the
  // mount fetch while the loading skeleton (and no selection UI) is showing,
  // so selection/checkbox changes are the only post-load triggers, matching
  // the old [selectedRecipes, autoCalc, recipes] effect 1:1.
  const computeAutoQty = (selected) =>
    String(recipes
      .filter(r => selected.includes(r.name))
      .reduce((sum, r) => sum + (r.total_quantity || 0), 0));

  const toggleRecipe = (name) => {
    const next = selectedRecipes.includes(name)
      ? selectedRecipes.filter(n => n !== name)
      : [...selectedRecipes, name];
    setSelectedRecipes(next);
    if (autoCalc && next.length > 0) setProductionQty(computeAutoQty(next));
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

  const PAGE_SIZE = 50;

  const handleCommercialPageChange = useCallback((page) => {
    setCommercialPage(page);
  }, []);

  const handleCommercialExport = useCallback(async () => {
    if (commercialExporting || commercialLoading) return;
    const hasData = commercialReport.length > 0 || commercialSummary.length > 0;
    if (!hasData) return;
    setCommercialExporting(true);
    try {
      // Build filters from current searchParams, remove pagination for export
      const params = new URLSearchParams(searchParams);
      params.delete('page');
      params.delete('page_size');
      const filters = Object.fromEntries(params);
      const res = await apiFetch('/api/reports/live/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(filters)
      });
      const data = await res.json();
      if (data.content) {
        const blob = new Blob([data.content], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', data.filename || 'commercial_report.csv');
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        toast.success('Exported to: ' + (data.filename || 'commercial_report.csv'));
      } else {
        toast.error(data.error || 'Export failed');
      }
    } catch (err) {
      toast.error(err.message || 'Export failed');
    } finally {
      setCommercialExporting(false);
    }
  }, [apiFetch, searchParams, commercialExporting, commercialLoading, commercialReport.length, commercialSummary.length]);

  const renderCommercialTab = () => {
    if (!isAdmin) return null;

    const groupBy = searchParams.get('group_by') || 'none';
    const isGrouped = groupBy !== 'none';
    const totalRows = isGrouped
      ? (commercialSummary.length > 0 ? commercialSummary.reduce((sum, p) => sum + (p.kpis?.order_count || 0), 0) : 0)
      : commercialReport.length;

    if (commercialLoading) {
      return (
        <div className="space-y-4">
          <>
            <CommercialFilters companies={companies} onFiltersChange={() => {}} />
            {companiesNotice}
          </>
          <CommercialTable
            data={isGrouped ? { periods: [] } : { rows: [] }}
            groupBy={groupBy}
            onPageChange={handleCommercialPageChange}
            currentPage={commercialPage}
            pageSize={PAGE_SIZE}
            totalRows={0}
            isLoading={true}
          />
        </div>
      );
    }

    if (commercialError) {
      return (
        <div className="space-y-4">
          <>
            <CommercialFilters companies={companies} onFiltersChange={() => {}} />
            {companiesNotice}
          </>
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 shadow-xs text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-rose-100 dark:bg-rose-950/40">
              <AlertTriangle className="h-6 w-6 text-rose-500" />
            </div>
            <p className="text-sm font-semibold text-slate-900 dark:text-white">Could not load the commercial report</p>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">{commercialError}</p>
            <Button variant="secondary" size="sm" onClick={() => {
              const groupByNow = searchParams.get('group_by') || 'none';
              if (groupByNow === 'none') fetchCommercialReport();
              else fetchCommercialSummary();
            }} className="mt-4">
              Retry
            </Button>
          </div>
        </div>
      );
    }

    if ((isGrouped && commercialSummary.length === 0) || (!isGrouped && commercialReport.length === 0)) {
      return (
        <div className="space-y-4">
          <>
            <CommercialFilters companies={companies} onFiltersChange={() => {}} />
            {companiesNotice}
          </>
          <CommercialTable
            data={isGrouped ? { periods: [] } : { rows: [] }}
            groupBy={groupBy}
            onPageChange={handleCommercialPageChange}
            currentPage={commercialPage}
            pageSize={PAGE_SIZE}
            totalRows={0}
          />
        </div>
      );
    }

    // For detail mode, compute KPIs for the header
    if (!isGrouped) {
      const rows = commercialReport;
      const sales = dedupeSales(rows);
      const grossSales = rows.reduce((sum, r) => sum + (Number(r.total_price) || 0), 0);
      const receivedTotal = sales.reduce((sum, r) => sum + (Number(r.received_amount) || 0), 0);
      const dueTotal = sales.reduce((sum, r) => sum + (Number(r.due_amount) || 0), 0);
      const overdueSales = sales.filter(r => r.payment_status === 'Overdue');
      const overdueTotal = overdueSales.reduce((sum, r) => sum + (Number(r.due_amount) || 0), 0);

      const kpis = [
        {
          label: 'Gross Sales',
          value: grossSales,
          caption: `${rows.length} sale line${rows.length !== 1 ? 's' : ''} · USD`,
          icon: DollarSign,
          box: 'bg-blue-100 dark:bg-blue-950/50',
          iconCls: 'text-blue-600 dark:text-blue-400',
          valueCls: 'text-slate-900 dark:text-white'
        },
        {
          label: 'Received',
          value: receivedTotal,
          caption: `${sales.length} sale${sales.length !== 1 ? 's' : ''} · USD`,
          icon: CreditCard,
          box: 'bg-emerald-100 dark:bg-emerald-950/50',
          iconCls: 'text-emerald-600 dark:text-emerald-400',
          valueCls: 'text-emerald-600 dark:text-emerald-400'
        },
        {
          label: 'Due',
          value: dueTotal,
          caption: `${sales.length} sale${sales.length !== 1 ? 's' : ''} · USD`,
          icon: CreditCard,
          box: 'bg-sky-100 dark:bg-sky-950/50',
          iconCls: 'text-sky-600 dark:text-sky-400',
          valueCls: 'text-sky-600 dark:text-sky-400'
        },
        {
          label: 'Overdue',
          value: overdueTotal,
          caption: `${overdueSales.length} sale${overdueSales.length !== 1 ? 's' : ''} past maturity`,
          icon: AlertTriangle,
          box: 'bg-rose-100 dark:bg-rose-950/50',
          iconCls: 'text-rose-600 dark:text-rose-400',
          valueCls: 'text-rose-600 dark:text-rose-400'
        }
      ];

      return (
        <div className="space-y-6">
          <>
            <CommercialFilters companies={companies} onFiltersChange={() => {}} />
            {companiesNotice}
          </>

          {/* KPI Cards (USD) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {kpis.map(kpi => (
              <div key={kpi.label} className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
                <div className="flex items-center gap-3">
                  <div className={clsx('flex h-9 w-9 shrink-0 items-center justify-center rounded-lg', kpi.box)}>
                    <kpi.icon className={clsx('h-4 w-4', kpi.iconCls)} />
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs text-slate-500 dark:text-slate-400">{kpi.label}</p>
                    <p className={clsx('text-xl font-bold truncate', kpi.valueCls)}>${formatNumber(kpi.value, 2)}</p>
                  </div>
                </div>
                <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-2">{kpi.caption}</p>
              </div>
            ))}
          </div>

          {/* Export Button */}
          <div className="flex justify-end">
            <Button
              variant="secondary"
              size="sm"
              icon={Download}
              onClick={handleCommercialExport}
              loading={commercialExporting}
              disabled={commercialLoading || commercialReport.length === 0}
            >
              Export CSV
            </Button>
          </div>

          <CommercialTable
            data={{ rows: commercialReport }}
            groupBy="none"
            onPageChange={handleCommercialPageChange}
            currentPage={commercialPage}
            pageSize={PAGE_SIZE}
            totalRows={totalRows}
          />
        </div>
      );
    }

    // Grouped mode
    // Determine current period from summary data (first period)
    const currentPeriod = commercialSummary[0];
    const periodStart = currentPeriod?.period_start;
    const periodEnd = currentPeriod?.period_end;
    const dateAnchor = searchParams.get('date_anchor') || 'pi_date';
    const dateFrom = searchParams.get('date_from') || '';
    const dateTo = searchParams.get('date_to') || '';

    return (
      <div className="space-y-6">
        <CommercialFilters companies={companies} onFiltersChange={() => {}} />
        
        {/* Period Navigator for grouped views */}
        <CommercialCalendarNavigator
          groupBy={groupBy}
          dateAnchor={dateAnchor}
          dateFrom={dateFrom}
          dateTo={dateTo}
          onFiltersChange={() => {}}
          periodStart={periodStart}
          periodEnd={periodEnd}
          isLoading={commercialLoading}
        />

        {/* Export Button */}
        <div className="flex justify-end">
          <Button
            variant="secondary"
            size="sm"
            icon={Download}
            onClick={handleCommercialExport}
            loading={commercialExporting}
            disabled={commercialLoading || commercialSummary.length === 0}
          >
            Export CSV
          </Button>
        </div>

        <CommercialTable
          data={{ periods: commercialSummary }}
          groupBy={groupBy}
          onPageChange={handleCommercialPageChange}
          currentPage={commercialPage}
          pageSize={PAGE_SIZE}
          totalRows={totalRows}
        />
      </div>
    );
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

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Tab Navigation */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">
            {activeTab === 'production' ? 'Production Reports' : 'Commercial Report'}
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            {activeTab === 'production'
              ? 'Check stock availability against recipe requirements'
              : 'Live commercial pipeline — orders, shipments, payments, and due amounts'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab('production')}
            className={clsx(
              'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer',
              activeTab === 'production'
                ? 'bg-blue-600 text-white'
                : 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700'
            )}
          >
            <BookOpen className="h-4 w-4" /> Production
          </button>
          {isAdmin && (
            <button
              onClick={() => setActiveTab('commercial')}
              className={clsx(
                'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer',
                activeTab === 'commercial'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700'
              )}
            >
              <Building2 className="h-4 w-4" /> Commercial
            </button>
          )}
        </div>
      </div>

      {/* Production Reports Tab */}
      {activeTab === 'production' && (
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
                    onChange={e => {
                      const checked = e.target.checked;
                      setAutoCalc(checked);
                      if (checked && selectedRecipes.length > 0) {
                        setProductionQty(computeAutoQty(selectedRecipes));
                      }
                    }}
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
      )}

      {/* Commercial Report Tab (admin only) */}
      {isAdmin && activeTab === 'commercial' && renderCommercialTab()}
    </div>
  );
}
