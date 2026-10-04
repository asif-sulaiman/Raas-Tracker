import { Link } from 'react-router-dom';
import {
  DollarSign,
  ArrowRight,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  UploadCloud,
  FileText,
  Landmark,
  Ship,
  Wallet,
  BadgeCheck,
} from 'lucide-react';
import clsx from 'clsx';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { formatNumber, formatDate } from '../../utils/format';
import { STAGES, STAGE_LABELS, STAGE_BADGE, isOverdue } from '../../utils/sales';

// Stage accents follow the Sales page pipeline columns
// (PipelineColumn HEADER_STYLES): sky → indigo → amber → orange → emerald.
const STAGE_META = {
  pi_issued: {
    step: 'Step 1',
    icon: FileText,
    tile: 'bg-sky-100 text-sky-700 dark:bg-sky-950/60 dark:text-sky-300',
    bar: 'bg-sky-500',
    hover: 'hover:border-sky-300 dark:hover:border-sky-700',
    value: 'text-sky-700 dark:text-sky-300',
  },
  lc_received: {
    step: 'Step 2',
    icon: Landmark,
    tile: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950/60 dark:text-indigo-300',
    bar: 'bg-indigo-500',
    hover: 'hover:border-indigo-300 dark:hover:border-indigo-700',
    value: 'text-indigo-700 dark:text-indigo-300',
  },
  shipment_ongoing: {
    step: 'Step 3',
    icon: Ship,
    tile: 'bg-amber-100 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300',
    bar: 'bg-amber-500',
    hover: 'hover:border-amber-300 dark:hover:border-amber-700',
    value: 'text-amber-700 dark:text-amber-300',
  },
  payment_due: {
    step: 'Step 4',
    icon: Wallet,
    tile: 'bg-orange-100 text-orange-700 dark:bg-orange-950/60 dark:text-orange-300',
    bar: 'bg-orange-500',
    hover: 'hover:border-orange-300 dark:hover:border-orange-700',
    value: 'text-orange-700 dark:text-orange-300',
  },
  completed: {
    step: 'Step 5',
    icon: BadgeCheck,
    tile: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300',
    bar: 'bg-emerald-500',
    hover: 'hover:border-emerald-300 dark:hover:border-emerald-700',
    value: 'text-emerald-700 dark:text-emerald-300',
  },
};

function money(value) {
  return (value ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

export default function SalesPipelineOverview({
  sales = [],
  salesSummary = null,
  visibleSales = [],
  salesExpanded = false,
  onToggleExpanded = () => {},
  overdueCount = 0,
  recentDefault = 5,
  recentMax = 20,
  loading = false,
  error = null,
}) {
  const stageCount = (key) =>
    salesSummary?.[key]?.count ?? sales.filter((s) => s.stage === key).length;

  const pipelineValue = money(salesSummary?.total_pipeline_value);
  const pipelineCount = salesSummary?.total_sales ?? sales.length;
  const completedCount =
    salesSummary?.completed?.count ?? sales.filter((s) => s.stage === 'completed').length;
  const completedValue = money(salesSummary?.completed?.value);

  if (loading) {
    return (
      <section
        aria-labelledby="sales-pipeline-heading"
        className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="h-1 bg-gradient-to-r from-sky-500 via-indigo-500 to-emerald-500" />
        <div className="p-5 sm:p-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 text-white shadow-sm animate-pulse">
                <DollarSign className="h-5 w-5" />
              </span>
              <div className="space-y-1">
                <div className="h-5 w-48 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                <div className="h-4 w-64 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="h-5 w-24 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
              <div className="h-6 w-20 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
            </div>
          </div>

          <ol className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5 xl:gap-4">
            {STAGES.map((_, i) => (
              <li key={i} className="relative">
                <div className="group flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white transition-all duration-200 dark:border-slate-700 dark:bg-slate-900 animate-pulse">
                  <span className="h-1 w-full bg-gradient-to-r from-sky-500 via-indigo-500 to-emerald-500" />
                  <span className="flex flex-1 flex-col p-3.5">
                    <div className="flex items-center justify-between">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-200 dark:bg-slate-700" />
                      <div className="h-3 w-16 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                    </div>
                    <div className="mt-3 h-4 w-24 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                    <div className="mt-1 h-8 w-20 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                    <div className="mt-0.5 h-4 w-16 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                  </span>
                </div>
                {i < STAGES.length - 1 && (
                  <span aria-hidden="true" className="absolute top-1/2 -right-5 z-10 hidden h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-400 shadow-xs xl:flex dark:border-slate-700 dark:bg-slate-800 dark:text-slate-500" />
                )}
              </li>
            ))}
          </ol>

          <div className="mt-3 grid grid-cols-1 gap-3 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-800/40 animate-pulse">
            <div className="flex items-center justify-between gap-3 overflow-hidden rounded-lg bg-gradient-to-br from-emerald-600 to-teal-600 p-4 text-white shadow-sm">
              <div className="space-y-1">
                <div className="h-3 w-32 bg-white/30 rounded animate-pulse" />
                <div className="h-6 w-24 bg-white/50 rounded animate-pulse" />
                <div className="h-3 w-20 bg-white/30 rounded animate-pulse" />
              </div>
              <div className="h-10 w-10 shrink-0 bg-white/25 rounded animate-pulse" />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900 animate-pulse">
              <div className="space-y-1">
                <div className="h-3 w-24 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                <div className="h-6 w-24 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
                <div className="h-3 w-28 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
              </div>
              <div className="h-10 w-10 shrink-0 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
            </div>
          </div>

          <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-800 animate-pulse">
            <div className="mb-3 flex items-center justify-between">
              <div className="h-4 w-32 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
              <div className="h-3 w-16 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
            </div>
            <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700 animate-pulse">
              <div className="w-full">
                <div className="h-8 bg-slate-100 dark:bg-slate-800/60" />
                <div className="divide-y divide-slate-100 dark:divide-slate-800/60">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="h-10" />
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-3 h-8 w-40 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
          </div>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section
        aria-labelledby="sales-pipeline-heading"
        className="overflow-hidden rounded-2xl border border-rose-200 bg-rose-50 p-5 shadow-sm dark:border-rose-800 dark:bg-rose-950/30"
      >
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-rose-100 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300">
            <DollarSign className="h-5 w-5" />
          </span>
          <div>
            <h2 id="sales-pipeline-heading" className="text-lg font-bold text-slate-900 dark:text-white">
              Sales Pipeline Overview
            </h2>
            <p className="text-sm text-rose-700 dark:text-rose-300">{error}</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="sales-pipeline-heading"
      className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      {/* Stage-progression edge: PI → LC → shipment → payment → done */}
      <div
        aria-hidden="true"
        className="h-1 bg-gradient-to-r from-sky-500 via-indigo-500 to-emerald-500"
      />

      <div className="p-5 sm:p-6">
        {/* Heading row */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-500 to-teal-600 text-white shadow-sm">
              <DollarSign className="h-5 w-5" />
            </span>
            <div>
              <h2
                id="sales-pipeline-heading"
                className="text-lg font-bold tracking-tight text-slate-900 dark:text-white"
              >
                Sales Pipeline Overview
              </h2>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Live PI → LC → shipment → payment tracking
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {overdueCount > 0 && (
              <Badge variant="error" dot>
                {overdueCount} overdue
              </Badge>
            )}
            <Link
              to="/sales"
              className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-2.5 py-1 text-xs font-semibold text-blue-600 transition-colors hover:border-blue-200 hover:bg-blue-50 dark:border-slate-700 dark:text-blue-400 dark:hover:border-blue-800 dark:hover:bg-blue-950/40"
            >
              View all <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>

        {sales.length === 0 ? (
          <div className="mt-5 flex flex-col items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50/60 px-4 py-8 text-center dark:border-slate-700 dark:bg-slate-800/30">
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
              <DollarSign className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">No sales yet</p>
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                Upload your first PI to start tracking.
              </p>
            </div>
            <Link to="/sales">
              <Button variant="primary" size="sm" icon={UploadCloud}>
                Go to Sales Pipeline
              </Button>
            </Link>
          </div>
        ) : (
          <>
            {/* 5-stage flow strip */}
            <ol className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5 xl:gap-4">
              {STAGES.map((st, i) => {
                const meta = STAGE_META[st.key] || STAGE_META.pi_issued;
                const Icon = meta.icon;
                return (
                  <li
                    key={st.key}
                    className={clsx(
                      'relative',
                      i === STAGES.length - 1 && 'sm:col-span-2 xl:col-span-1'
                    )}
                  >
                    <Link
                      to="/sales"
                      aria-label={`${st.title}: ${stageCount(st.key)} sales, $${money(salesSummary?.[st.key]?.value)}`}
                      className={clsx(
                        'group flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md dark:border-slate-700 dark:bg-slate-900',
                        meta.hover
                      )}
                    >
                      <span aria-hidden="true" className={clsx('h-1 w-full', meta.bar)} />
                      <span className="flex flex-1 flex-col p-3.5">
                        <span className="flex items-center justify-between">
                          <span
                            className={clsx(
                              'flex h-8 w-8 items-center justify-center rounded-lg transition-transform duration-200 group-hover:scale-110',
                              meta.tile
                            )}
                          >
                            <Icon className="h-4 w-4" />
                          </span>
                          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                            {meta.step}
                          </span>
                        </span>
                        <span className="mt-3 text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                          {st.title}
                        </span>
                        <span className="mt-1 text-2xl font-bold tabular-nums tracking-tight text-slate-900 dark:text-white">
                          {stageCount(st.key)}
                        </span>
                        <span className={clsx('mt-0.5 text-xs font-semibold tabular-nums', meta.value)}>
                          ${money(salesSummary?.[st.key]?.value)}
                        </span>
                      </span>
                    </Link>
                    {i < STAGES.length - 1 && (
                      <span
                        aria-hidden="true"
                        className="absolute top-1/2 -right-5 z-10 hidden h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-400 shadow-xs xl:flex dark:border-slate-700 dark:bg-slate-800 dark:text-slate-500"
                      >
                        <ChevronRight className="h-3.5 w-3.5" />
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>

            {/* Totals tied to the flow */}
            <div className="mt-3 grid grid-cols-1 gap-3 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 sm:grid-cols-2 dark:border-slate-800 dark:bg-slate-800/40">
              <div className="flex items-center justify-between gap-3 overflow-hidden rounded-lg bg-gradient-to-br from-emerald-600 to-teal-600 p-4 text-white shadow-sm">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-emerald-100">
                    Total pipeline value
                  </p>
                  <p className="mt-1 text-2xl font-bold tabular-nums tracking-tight">
                    ${pipelineValue}
                  </p>
                  <p className="mt-0.5 text-xs text-emerald-100/90">
                    {pipelineCount} {pipelineCount === 1 ? 'sale' : 'sales'} in flow
                  </p>
                </div>
                <DollarSign aria-hidden="true" className="h-10 w-10 shrink-0 text-white/25" />
              </div>
              <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                    Total sales
                  </p>
                  <p className="mt-1 text-2xl font-bold tabular-nums tracking-tight text-slate-900 dark:text-white">
                    {pipelineCount}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {completedCount} completed · ${completedValue} booked
                  </p>
                </div>
                <BadgeCheck aria-hidden="true" className="h-10 w-10 shrink-0 text-emerald-500/40" />
              </div>
            </div>

            {/* Recent sales */}
            <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-800">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                  Recent sales
                </h3>
                <span className="text-xs text-slate-400 dark:text-slate-500">
                  {sales.length} total
                </span>
              </div>
              <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-700">
                <table className="w-full text-left text-xs">
                  <thead className="bg-slate-50 font-semibold uppercase tracking-wider text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                    <tr>
                      <th className="px-3 py-2.5">PI Number</th>
                      <th className="px-3 py-2.5">LC Number</th>
                      <th className="px-3 py-2.5">Client</th>
                      <th className="px-3 py-2.5 text-right">Value (USD)</th>
                      <th className="px-3 py-2.5">Stage</th>
                      <th className="px-3 py-2.5">PI Date</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
                    {visibleSales.map((s) => (
                      <tr
                        key={s.id}
                        className="transition-colors hover:bg-slate-50/80 dark:hover:bg-slate-800/40"
                      >
                        <td className="px-3 py-2.5 font-medium text-slate-900 dark:text-white">
                          {s.pi_number || `#${s.id}`}
                        </td>
                        <td className="px-3 py-2.5 text-slate-500 dark:text-slate-400">
                          {(s.lc_number || '').trim() || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-slate-500 dark:text-slate-400">
                          {s.client_name || '—'}
                        </td>
                        <td className="px-3 py-2.5 text-right font-bold tabular-nums text-slate-800 dark:text-slate-100">
                          ${formatNumber(s.total_value || 0)}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className="inline-flex items-center gap-1.5">
                            <Badge variant={STAGE_BADGE[s.stage] || 'default'}>
                              {STAGE_LABELS[s.stage] || s.stage}
                            </Badge>
                            {isOverdue(s) && (
                              <Badge variant="error" dot>
                                Overdue
                              </Badge>
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

              {sales.length > recentDefault && (
                <button
                  onClick={onToggleExpanded}
                  className="mt-3 inline-flex cursor-pointer items-center gap-1 text-xs font-semibold text-blue-600 hover:underline dark:text-blue-400"
                >
                  {salesExpanded ? (
                    <>
                      Show less <ChevronUp className="h-3.5 w-3.5" />
                    </>
                  ) : (
                    <>
                      Show {Math.min(sales.length, recentMax) - recentDefault} more{' '}
                      <ChevronDown className="h-3.5 w-3.5" />
                    </>
                  )}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
