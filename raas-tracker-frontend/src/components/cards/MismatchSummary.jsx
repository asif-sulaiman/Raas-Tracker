import clsx from 'clsx';
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  HelpCircle,
  TrendingUp,
  TrendingDown,
  Percent
} from 'lucide-react';

const SUMMARY_CARDS = [
  {
    key: 'matched',
    label: 'Matched in DB',
    description: 'Chemicals verified perfectly',
    icon: CheckCircle2,
    color: 'emerald',
    bgClass: 'bg-emerald-50 dark:bg-emerald-950/40',
    iconClass: 'text-emerald-600 dark:text-emerald-400',
    borderClass: 'border-emerald-200 dark:border-emerald-800',
    ringClass: 'ring-emerald-500/20'
  },
  {
    key: 'last_month_mismatches',
    label: 'Last Month Diff',
    description: 'Balance last month mismatch',
    icon: TrendingDown,
    color: 'amber',
    bgClass: 'bg-amber-50 dark:bg-amber-950/40',
    iconClass: 'text-amber-600 dark:text-amber-400',
    borderClass: 'border-amber-200 dark:border-amber-800',
    ringClass: 'ring-amber-500/20'
  },
  {
    key: 'this_month_mismatches',
    label: 'This Month Diff',
    description: 'Balance this month mismatch',
    icon: TrendingUp,
    color: 'sky',
    bgClass: 'bg-sky-50 dark:bg-sky-950/40',
    iconClass: 'text-sky-600 dark:text-sky-400',
    borderClass: 'border-sky-200 dark:border-sky-800',
    ringClass: 'ring-sky-500/20'
  },
  {
    key: 'both_mismatches',
    label: 'Both Mismatch',
    description: 'Both months differ',
    icon: AlertTriangle,
    color: 'rose',
    bgClass: 'bg-rose-50 dark:bg-rose-950/40',
    iconClass: 'text-rose-600 dark:text-rose-400',
    borderClass: 'border-rose-200 dark:border-rose-800',
    ringClass: 'ring-rose-500/20'
  },
  {
    key: 'not_in_db',
    label: 'Not in DB',
    description: 'New items in upload',
    icon: HelpCircle,
    color: 'indigo',
    bgClass: 'bg-indigo-50 dark:bg-indigo-950/40',
    iconClass: 'text-indigo-600 dark:text-indigo-400',
    borderClass: 'border-indigo-200 dark:border-indigo-800',
    ringClass: 'ring-indigo-500/20'
  },
  {
    key: 'not_in_upload',
    label: 'Not in Upload',
    description: 'Missing from upload file',
    icon: XCircle,
    color: 'slate',
    bgClass: 'bg-slate-50 dark:bg-slate-800',
    iconClass: 'text-slate-600 dark:text-slate-400',
    borderClass: 'border-slate-200 dark:border-slate-700',
    ringClass: 'ring-slate-500/20'
  }
];

export default function MismatchSummary({ stats = {} }) {
  const total = stats.total || 0;
  const matchPercentage = stats.match_percentage || 0;

  return (
    <div className="space-y-4">
      {/* Match Percentage Banner */}
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className={clsx(
              'flex h-16 w-16 items-center justify-center rounded-2xl',
              matchPercentage >= 90
                ? 'bg-emerald-100 dark:bg-emerald-950/50'
                : matchPercentage >= 70
                ? 'bg-amber-100 dark:bg-amber-950/50'
                : 'bg-rose-100 dark:bg-rose-950/50'
            )}>
              <Percent className={clsx(
                'h-8 w-8',
                matchPercentage >= 90
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : matchPercentage >= 70
                  ? 'text-amber-600 dark:text-amber-400'
                  : 'text-rose-600 dark:text-rose-400'
              )} />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                Overall Match Rate
              </p>
              <div className="flex items-baseline gap-2">
                <span className={clsx(
                  'text-4xl font-bold tracking-tight',
                  matchPercentage >= 90
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : matchPercentage >= 70
                    ? 'text-amber-600 dark:text-amber-400'
                    : 'text-rose-600 dark:text-rose-400'
                )}>
                  {matchPercentage.toFixed(2)}%
                </span>
                <span className="text-sm text-slate-500 dark:text-slate-400">
                  of {total} rows
                </span>
              </div>
            </div>
          </div>
          
          {/* Progress bar */}
          <div className="hidden sm:block w-48">
            <div className="h-2.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden">
              <div
                className={clsx(
                  'h-full rounded-full transition-all duration-500',
                  matchPercentage >= 90
                    ? 'bg-emerald-500'
                    : matchPercentage >= 70
                    ? 'bg-amber-500'
                    : 'bg-rose-500'
                )}
                style={{ width: `${matchPercentage}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Summary Cards Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {SUMMARY_CARDS.map((card) => {
          const value = stats[card.key] || 0;
          const Icon = card.icon;
          
          return (
            <div
              key={card.key}
              className={clsx(
                'rounded-xl border p-4 transition-all hover:shadow-md',
                card.borderClass,
                'bg-white dark:bg-slate-900'
              )}
            >
              <div className="flex items-center gap-2.5 mb-3">
                <div className={clsx(
                  'flex h-8 w-8 items-center justify-center rounded-lg',
                  card.bgClass
                )}>
                  <Icon className={clsx('h-4 w-4', card.iconClass)} />
                </div>
              </div>
              <div>
                <p className={clsx(
                  'text-2xl font-bold tracking-tight',
                  card.iconClass
                )}>
                  {value}
                </p>
                <p className="text-[11px] font-medium text-slate-500 dark:text-slate-400 mt-0.5 leading-tight">
                  {card.label}
                </p>
                <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-0.5">
                  {card.description}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
