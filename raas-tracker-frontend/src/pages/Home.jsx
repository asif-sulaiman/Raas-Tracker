import { useState, useCallback, lazy, Suspense } from 'react';
import { Link } from 'react-router-dom';
import {
  FlaskConical,
  CheckCircle2,
  AlertTriangle,
  UploadCloud,
  Clock,
  ArrowRight,
  TrendingDown,
  Layers,
  History,
  BookOpen
} from 'lucide-react';
import KpiCard from '../components/cards/KpiCard';
import SalesPipelineOverview from '../components/cards/SalesPipelineOverview';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import BrandLogo from '../components/BrandLogo';
import { formatNumber, formatDate, formatDateTime } from '../utils/format';
import { countOverdue } from '../utils/sales';
import { buildTrendData, buildDistribution, uploadMismatches } from '../utils/dashboard';
import { useAuth } from '../context/AuthContext';
import useCachedFetch from '../hooks/useCachedFetch';

const RECENT_DEFAULT = 5;
const RECENT_MAX = 20;
const ReconciliationChart = lazy(() => import('../components/cards/ReconciliationChart'));
const StockDistributionChart = lazy(() => import('../components/cards/StockDistributionChart'));

const DASHBOARD_KEY = '/api/dashboard';
// The server marks the response private for 15s; the client keeps it a little
// longer and refreshes in the background, so revisits paint instantly.
const DASHBOARD_STALE_MS = 30 * 1000;

export default function Home() {
  const { apiFetch } = useAuth();
  const [salesExpanded, setSalesExpanded] = useState(false);

  const fetchDashboard = useCallback(async () => {
    const res = await apiFetch(DASHBOARD_KEY);
    const data = await res.json();
    if (!data || typeof data !== 'object') {
      throw new Error('Unexpected response from server');
    }
    return data;
  }, [apiFetch]);

  const {
    data: dashboard,
    loading,
    error,
    refresh,
  } = useCachedFetch(DASHBOARD_KEY, fetchDashboard, { staleTime: DASHBOARD_STALE_MS });

  const chemicals = Array.isArray(dashboard?.chemicals)
    ? dashboard.chemicals.map(c => ({
        ...c,
        balance_this_month: c.qty,
        status: c.qty === 0 ? 'low' : 'matched'
      }))
    : [];
  const uploads = Array.isArray(dashboard?.uploads) ? dashboard.uploads : [];
  const recipes = Array.isArray(dashboard?.recipes) ? dashboard.recipes : [];
  const sales = Array.isArray(dashboard?.sales?.sales)
    ? dashboard.sales.sales
    : Array.isArray(dashboard?.sales) ? dashboard.sales : [];
  const lcs = Array.isArray(dashboard?.lcs) ? dashboard.lcs : [];
  const salesSummary = dashboard?.sales_summary ?? null;
  const activity = Array.isArray(dashboard?.activity) ? dashboard.activity.slice(0, 3) : [];
  // Non-admins get `activity: null` from the API; hide the panel for them.
  const activityHidden = Boolean(dashboard) && !Array.isArray(dashboard.activity);

  const chemLoading = loading;
  const salesLoading = loading;
  const chemError = error ? error.message || 'Could not load dashboard' : null;
  const salesError = chemError;
  const uploadsError = chemError;

  const overdueCount = countOverdue(sales);
  const visibleSales = salesExpanded ? sales.slice(0, RECENT_MAX) : sales.slice(0, RECENT_DEFAULT);

  // Computed metrics from real data
  const totalChemicals = chemicals.length;
  const activeStockItems = chemicals.filter((c) => c.qty > 0).length;
  const outStockItems = chemicals.filter((c) => c.qty === 0).length;
  const topActiveChemicals = [...chemicals]
    .filter((c) => c.qty > 0)
    .sort((a, b) => b.qty - a.qty)
    .slice(0, 6);

  // Reconciliation figures come only from real uploads — never mocks.
  const latestUpload = uploads.length > 0 ? uploads[0] : null;
  const prevUpload = uploads.length > 1 ? uploads[1] : null;
  const latestMatch = latestUpload ? (latestUpload.match_percentage ?? 0) : null;
  const latestDiscrepancies = latestUpload ? uploadMismatches(latestUpload) : null;
  const matchDelta = latestUpload && prevUpload
    ? (latestUpload.match_percentage ?? 0) - (prevUpload.match_percentage ?? 0)
    : null;
  const trendData = buildTrendData(uploads);
  const distribution = buildDistribution(latestUpload);

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Command strip */}
      <div className="flex flex-col gap-3 rounded-xl border border-slate-200/80 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between dark:border-slate-800 dark:bg-slate-900">
        <div className="flex min-w-0 items-center gap-2.5">
          <BrandLogo size="h-8 w-8" />
          <div className="min-w-0">
            <h1 className="text-base font-bold tracking-tight text-slate-900 dark:text-white">
              RAAS Tracker
            </h1>
            <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
              {latestUpload
                ? `${formatDate(latestUpload.upload_date)} · Last stock check ${latestUpload.filename || 'upload'} · ${Number(latestMatch).toFixed(1)}% match`
                : 'No stock check yet'}
            </p>
          </div>
        </div>
        <Link to="/upload" className="shrink-0">
          <Button variant="primary" size="sm" icon={UploadCloud}>
            Upload Monthly Stock
          </Button>
        </Link>
      </div>

      {/* Sales Pipeline Overview — hero section, first under the banner */}
      <SalesPipelineOverview
        sales={sales}
        lcs={lcs}
        salesSummary={salesSummary}
        visibleSales={visibleSales}
        salesExpanded={salesExpanded}
        onToggleExpanded={() => setSalesExpanded((v) => !v)}
        overdueCount={overdueCount}
        recentDefault={RECENT_DEFAULT}
        recentMax={RECENT_MAX}
        loading={salesLoading}
        error={salesError}
      />

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        <KpiCard
          title="Total Catalog"
          value={chemLoading || chemError ? '—' : totalChemicals}
          subvalue="items"
          icon={FlaskConical}
          color="blue"
          trend="up"
          trendLabel={chemLoading || chemError ? 'unavailable' : `${totalChemicals} registered`}
        />
        <KpiCard
          title="Reconciliation"
          value={uploadsError ? '—' : latestMatch === null ? '—' : `${Number(latestMatch).toFixed(1)}%`}
          subvalue="match rate"
          icon={CheckCircle2}
          color="emerald"
          trend={matchDelta}
          trendLabel={
            matchDelta !== null
              ? 'vs previous check'
              : latestUpload
                ? 'latest check'
                : undefined
          }
          badge={!uploadsError && !latestUpload ? <Badge variant="default">No checks yet</Badge> : undefined}
        />
        <KpiCard
          title="Active Stock"
          value={chemLoading || chemError ? '—' : activeStockItems}
          subvalue="in warehouse"
          icon={Layers}
          color="purple"
          trendLabel="Positive balances"
        />
        <KpiCard
          title="Discrepancies"
          value={uploadsError ? '—' : latestDiscrepancies === null ? '—' : latestDiscrepancies}
          subvalue="latest check"
          icon={AlertTriangle}
          color="amber"
          trendLabel={latestUpload ? latestUpload.filename || 'latest upload' : 'no uploads yet'}
        />
        <KpiCard
          title="Out of Stock"
          value={chemLoading || chemError ? '—' : outStockItems}
          subvalue="zero balance"
          icon={TrendingDown}
          color="rose"
          trendLabel="Re-order candidate"
        />
        <KpiCard
          title="Audits Run"
          value={uploadsError ? '—' : uploads.length}
          subvalue="uploads"
          icon={History}
          color="indigo"
          trendLabel={latestUpload ? formatDate(latestUpload.upload_date) : 'none yet'}
        />
        <KpiCard
          title="Invoice Recipes"
          value={recipes.length}
          subvalue="invoices"
          icon={BookOpen}
          color="blue"
          trendLabel="One recipe per invoice"
        />
      </div>

      {/* Charts Section */}
      {uploadsError ? (
        <div className="rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-6 text-center" role="alert">
          <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">Could not load stock checks</p>
          <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-1">{uploadsError}</p>
          <Button variant="primary" size="sm" onClick={() => refresh()} className="mt-3">
            Retry
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <Suspense fallback={<div className="h-72 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" aria-hidden="true" />}>
              <ReconciliationChart
                title="Monthly Warehouse Reconciliation Performance"
                data={trendData}
                matchLabel={latestUpload ? `${Number(latestMatch).toFixed(1)}% Current Match` : null}
              />
            </Suspense>
          </div>
          <div>
            <Suspense fallback={<div className="h-72 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" aria-hidden="true" />}>
              <StockDistributionChart
                title="Audit Resolution Breakdown"
                data={distribution ? distribution.data : []}
                caption={distribution ? distribution.caption : null}
                matchPct={latestUpload ? `${Number(latestMatch).toFixed(1)}%` : null}
              />
            </Suspense>
          </div>
        </div>
      )}

      {/* Two Column Section: Top Active Chemicals & Recent Audit Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Top Active Inventory Preview */}
        <div className="lg:col-span-2 rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">
                Highest Volume Chemicals in Warehouse
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Top stock levels verified in current audit period
              </p>
            </div>
            <Link
              to="/chemicals"
              className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1"
            >
              View All {chemLoading || chemError ? '—' : totalChemicals} <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {chemLoading ? (
            <div className="space-y-0" aria-label="Loading top chemicals">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-3 py-2.5 border-b border-slate-100 dark:border-slate-800/60 last:border-0 animate-pulse">
                  <div className="h-4 flex-1 rounded bg-slate-200 dark:bg-slate-700" />
                  <div className="h-4 w-16 rounded bg-slate-100 dark:bg-slate-800" />
                  <div className="h-4 w-16 rounded bg-slate-100 dark:bg-slate-800" />
                </div>
              ))}
            </div>
          ) : chemError ? (
            <div className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-5 text-center" role="alert">
              <p className="text-xs font-semibold text-rose-700 dark:text-rose-300">Could not load chemical stock</p>
              <p className="text-[11px] text-rose-600/80 dark:text-rose-400/80 mt-1">{chemError} — no stock figures are shown.</p>
              <Button variant="primary" size="sm" onClick={() => refresh()} className="mt-3">
                Retry
              </Button>
            </div>
          ) : topActiveChemicals.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400 py-4 text-center">
              No chemicals registered yet.
            </p>
          ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wider">
                <tr>
                  <th className="px-3 py-2.5">Chemical Name</th>
                  <th className="px-3 py-2.5">Last Month</th>
                  <th className="px-3 py-2.5">Current Stock</th>
                  <th className="px-3 py-2.5">Unit</th>
                  <th className="px-3 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
                {topActiveChemicals.map((chem) => (
                  <tr key={chem.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                    <td className="px-3 py-2.5 font-medium text-slate-900 dark:text-white">
                      {chem.name}
                    </td>
                    <td className="px-3 py-2.5 text-slate-500 dark:text-slate-400">
                      {formatNumber(chem.balance_last_month)}
                    </td>
                    <td className="px-3 py-2.5 font-bold text-slate-800 dark:text-slate-100">
                      {formatNumber(chem.qty)}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className="px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-[11px] font-mono text-slate-600 dark:text-slate-300">
                        {chem.unit}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge variant="matched">Reconciled</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}
        </div>

        {/* Recent Activity / Audit Log Preview (hidden without audit access) */}
        {!activityHidden && (
        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">
                Recent Accountability Activity
              </h3>
              <Link
                to="/audit-logs"
                className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline"
              >
                Full Log
              </Link>
            </div>

            <div className="space-y-3.5 text-xs">
              {activity.length === 0 ? (
                <p className="text-slate-500 dark:text-slate-400 text-[11px] py-2 text-center">
                  No recorded activity yet.
                </p>
              ) : (
                activity.map((a) => (
                  <div key={a.id} className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400">
                      <History className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="font-medium text-slate-800 dark:text-slate-200">
                        {a.action || 'Activity'}
                      </p>
                      {(a.new_value || a.old_value) && (
                        <p className="text-slate-500 dark:text-slate-400 text-[11px] truncate max-w-[220px]">
                          {a.new_value || a.old_value}
                        </p>
                      )}
                      <span className="text-[10px] text-slate-400 flex items-center gap-1 mt-0.5">
                        <Clock className="h-3 w-3" /> {formatDateTime(a.timestamp)} by {a.user_id || 'system'}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="pt-4 mt-4 border-t border-slate-100 dark:border-slate-800">
            <Link to="/upload">
              <Button variant="secondary" size="sm" className="w-full justify-center">
                Review Uploaded Batches
              </Button>
            </Link>
          </div>
        </div>
        )}
      </div>
    </div>
  );
}
