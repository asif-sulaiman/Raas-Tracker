import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  FlaskConical,
  CheckCircle2,
  AlertTriangle,
  UploadCloud,
  FileSpreadsheet,
  Clock,
  ArrowRight,
  TrendingDown,
  Layers,
  History,
  ShieldCheck,
  BookOpen,
  DollarSign,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import KpiCard from '../components/cards/KpiCard';
import ReconciliationChart from '../components/cards/ReconciliationChart';
import StockDistributionChart from '../components/cards/StockDistributionChart';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import { formatNumber, formatDate, formatDateTime } from '../utils/format';
import { STAGES, STAGE_LABELS, STAGE_BADGE, isOverdue, countOverdue } from '../utils/sales';
import { buildTrendData, buildDistribution, uploadMismatches } from '../utils/dashboard';
import { useAuth } from '../context/AuthContext';

const RECENT_DEFAULT = 5;
const RECENT_MAX = 20;

export default function Home() {
  const { apiFetch } = useAuth();
  const [chemicals, setChemicals] = useState([]);
  const [chemLoading, setChemLoading] = useState(true);
  const [chemError, setChemError] = useState(null);
  const [recipes, setRecipes] = useState([]);
  const [sales, setSales] = useState([]);
  const [salesSummary, setSalesSummary] = useState(null);
  const [salesExpanded, setSalesExpanded] = useState(false);
  const [uploads, setUploads] = useState([]);
  const [uploadsError, setUploadsError] = useState(null);
  const [activity, setActivity] = useState([]);
  const [activityHidden, setActivityHidden] = useState(false);

  const loadChemicals = useCallback(async () => {
    setChemLoading(true);
    setChemError(null);
    try {
      const res = await apiFetch('/api/chemicals');
      const chems = await res.json();
      if (!Array.isArray(chems)) throw new Error('Unexpected response from server');
      setChemicals(chems.map(c => ({
        ...c,
        balance_this_month: c.qty,
        status: c.qty === 0 ? 'low' : 'matched'
      })));
    } catch (err) {
      setChemError(err.message || 'Could not load chemical stock');
      setChemicals([]);
    } finally {
      setChemLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadChemicals();
  }, [loadChemicals]);

  const loadUploads = useCallback(async () => {
    setUploadsError(null);
    try {
      const res = await apiFetch('/api/uploads');
      const data = await res.json();
      setUploads(Array.isArray(data) ? data : []);
    } catch (err) {
      setUploadsError(err.message || 'Could not load stock checks');
      setUploads([]);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadUploads();
  }, [loadUploads]);

  useEffect(() => {
    (async () => {
      try {
        const [recsRes, salesRes, summaryRes] = await Promise.all([
          apiFetch('/api/recipes'),
          apiFetch('/api/sales').catch(() => null),
          apiFetch('/api/sales/summary').catch(() => null)
        ]);
        const recs = await recsRes.json();
        if (Array.isArray(recs)) setRecipes(recs);
        if (salesRes) {
          const salesData = await salesRes.json();
          if (Array.isArray(salesData)) setSales(salesData);
        }
        if (summaryRes) {
          const summaryData = await summaryRes.json();
          if (summaryData) setSalesSummary(summaryData);
        }
      } catch {
        // Sections stay empty on failure; chemicals errors have their own panel.
      }
    })();
    // Recent activity is best-effort (audit log is admin-only): hide on failure.
    (async () => {
      try {
        const res = await apiFetch('/api/audit-logs?limit=5');
        const data = await res.json();
        if (Array.isArray(data)) setActivity(data.slice(0, 3));
      } catch {
        setActivityHidden(true);
      }
    })();
  }, [apiFetch]);

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
      {/* CRM Welcome & Status Banner */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-700 p-6 text-white shadow-lg">
        <div className="relative z-10 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              {latestUpload ? (
                <>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-white/20 backdrop-blur-md text-white">
                    <ShieldCheck className="h-3.5 w-3.5" /> Last checked {latestUpload.filename || 'upload'}
                  </span>
                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/30 text-emerald-200">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" /> {Number(latestMatch).toFixed(1)}% match
                  </span>
                </>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-white/20 backdrop-blur-md text-white">
                  <ShieldCheck className="h-3.5 w-3.5" /> No stock check yet
                </span>
              )}
            </div>
            <h2 className="text-2xl md:text-3xl font-bold tracking-tight">
              Warehouse Stock Accountability CRM
            </h2>
            <p className="text-sm text-blue-100 mt-1 max-w-2xl">
              Real-time monitoring of monthly chemical inventories, storekeeper reconciliations,
              batch validations, and mismatch audits.
            </p>
          </div>

          <div className="flex items-center gap-2.5 shrink-0">
            <Link to="/upload">
              <Button
                variant="secondary"
                size="md"
                icon={UploadCloud}
                className="bg-white text-blue-700 hover:bg-blue-50 border-transparent shadow-sm"
              >
                Upload Monthly Stock
              </Button>
            </Link>
            <Link to="/recipes">
              <Button
                variant="ghost"
                size="md"
                icon={BookOpen}
                className="text-white hover:bg-white/15"
              >
                Recipes
              </Button>
            </Link>
            <Link to="/reports">
              <Button
                variant="ghost"
                size="md"
                icon={FileSpreadsheet}
                className="text-white hover:bg-white/15"
              >
                Reports
              </Button>
            </Link>
          </div>
        </div>

        {/* Ambient decorative circle */}
        <div className="absolute -right-12 -bottom-12 w-64 h-64 rounded-full bg-white/10 blur-2xl pointer-events-none" />
      </div>

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
          title="Recipes"
          value={recipes.length}
          subvalue="formulations"
          icon={BookOpen}
          color="blue"
          trendLabel="Active recipes"
        />
      </div>

      {/* Sales Pipeline Overview */}
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-base font-semibold text-slate-900 dark:text-white flex items-center gap-2">
              <DollarSign className="h-4 w-4 text-emerald-600" />
              Sales Pipeline Overview
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Live PI → LC → shipment → payment tracking
            </p>
          </div>
          <div className="flex items-center gap-2">
            {overdueCount > 0 && (
              <Badge variant="error" dot>
                {overdueCount} overdue
              </Badge>
            )}
            <Link
              to="/sales"
              className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1"
            >
              Open Pipeline <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>

        {sales.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <p className="text-sm text-slate-500 dark:text-slate-400">
              No sales yet — upload your first PI to start tracking.
            </p>
            <Link to="/sales">
              <Button variant="primary" size="sm" icon={UploadCloud}>
                Go to Sales Pipeline
              </Button>
            </Link>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 mb-4">
              <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 px-3 py-2.5">
                <p className="text-[11px] uppercase tracking-wider text-emerald-600 dark:text-emerald-400 font-semibold">Pipeline Value</p>
                <p className="text-lg font-bold text-emerald-700 dark:text-emerald-300">
                  ${(salesSummary?.total_pipeline_value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                </p>
                <p className="text-[11px] text-emerald-600/70 dark:text-emerald-400/70">
                  {salesSummary?.total_sales || sales.length} sales
                </p>
              </div>
              {STAGES.map((st) => (
                <div key={st.key} className="rounded-lg bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 px-3 py-2.5">
                  <p className="text-[11px] uppercase tracking-wider text-slate-500 dark:text-slate-400 font-semibold">{st.title}</p>
                  <p className="text-lg font-bold text-slate-900 dark:text-white">
                    {salesSummary?.[st.key]?.count ?? sales.filter((s) => s.stage === st.key).length}
                  </p>
                  <p className="text-[11px] text-slate-500 dark:text-slate-400">
                    ${((salesSummary?.[st.key]?.value) ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </p>
                </div>
              ))}
            </div>

            <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wider">
                  <tr>
                    <th className="px-3 py-2.5">PI Number</th>
                    <th className="px-3 py-2.5">Client</th>
                    <th className="px-3 py-2.5 text-right">Value (USD)</th>
                    <th className="px-3 py-2.5">Stage</th>
                    <th className="px-3 py-2.5">PI Date</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
                  {visibleSales.map((s) => (
                    <tr key={s.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                      <td className="px-3 py-2.5 font-medium text-slate-900 dark:text-white">
                        {s.pi_number || `#${s.id}`}
                      </td>
                      <td className="px-3 py-2.5 text-slate-500 dark:text-slate-400">
                        {s.client_name || '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right font-bold text-slate-800 dark:text-slate-100">
                        ${formatNumber(s.total_value || 0)}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="inline-flex items-center gap-1.5">
                          <Badge variant={STAGE_BADGE[s.stage] || 'default'}>
                            {STAGE_LABELS[s.stage] || s.stage}
                          </Badge>
                          {isOverdue(s) && (
                            <Badge variant="error" dot>Overdue</Badge>
                          )}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-slate-500 dark:text-slate-400">
                        {formatDate(s.pi_date)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {sales.length > RECENT_DEFAULT && (
              <button
                onClick={() => setSalesExpanded((v) => !v)}
                className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
              >
                {salesExpanded ? (
                  <>Show less <ChevronUp className="h-3.5 w-3.5" /></>
                ) : (
                  <>Show {Math.min(sales.length, RECENT_MAX) - RECENT_DEFAULT} more <ChevronDown className="h-3.5 w-3.5" /></>
                )}
              </button>
            )}
          </>
        )}
      </div>

      {/* Charts Section */}
      {uploadsError ? (
        <div className="rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-6 text-center" role="alert">
          <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">Could not load stock checks</p>
          <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-1">{uploadsError}</p>
          <Button variant="primary" size="sm" onClick={loadUploads} className="mt-3">
            Retry
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <ReconciliationChart
              title="Monthly Warehouse Reconciliation Performance"
              data={trendData}
              matchLabel={latestUpload ? `${Number(latestMatch).toFixed(1)}% Current Match` : null}
            />
          </div>
          <div>
            <StockDistributionChart
              title="Audit Resolution Breakdown"
              data={distribution ? distribution.data : []}
              caption={distribution ? distribution.caption : null}
              matchPct={latestUpload ? `${Number(latestMatch).toFixed(1)}%` : null}
            />
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
              <Button variant="primary" size="sm" onClick={loadChemicals} className="mt-3">
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
