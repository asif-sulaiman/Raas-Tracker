import clsx from 'clsx';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

const COLOR_MAP = {
  blue: {
    bg: 'bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400',
    ring: 'hover:border-blue-300 dark:hover:border-blue-700',
    glow: 'from-blue-500/10 to-transparent'
  },
  emerald: {
    bg: 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-600 dark:text-emerald-400',
    ring: 'hover:border-emerald-300 dark:hover:border-emerald-700',
    glow: 'from-emerald-500/10 to-transparent'
  },
  amber: {
    bg: 'bg-amber-50 dark:bg-amber-950/40 text-amber-600 dark:text-amber-400',
    ring: 'hover:border-amber-300 dark:hover:border-amber-700',
    glow: 'from-amber-500/10 to-transparent'
  },
  rose: {
    bg: 'bg-rose-50 dark:bg-rose-950/40 text-rose-600 dark:text-rose-400',
    ring: 'hover:border-rose-300 dark:hover:border-rose-700',
    glow: 'from-rose-500/10 to-transparent'
  },
  purple: {
    bg: 'bg-purple-50 dark:bg-purple-950/40 text-purple-600 dark:text-purple-400',
    ring: 'hover:border-purple-300 dark:hover:border-purple-700',
    glow: 'from-purple-500/10 to-transparent'
  },
  indigo: {
    bg: 'bg-indigo-50 dark:bg-indigo-950/40 text-indigo-600 dark:text-indigo-400',
    ring: 'hover:border-indigo-300 dark:hover:border-indigo-700',
    glow: 'from-indigo-500/10 to-transparent'
  }
};

export default function KpiCard({
  title,
  value,
  subvalue,
  icon: Icon,
  trend,
  trendLabel = 'vs last month',
  color = 'blue',
  badge,
  className = ''
}) {
  const scheme = COLOR_MAP[color] || COLOR_MAP.blue;
  const isPositive = typeof trend === 'number' ? trend > 0 : trend === 'up';
  const isNeutral = trend === 0 || trend === 'neutral' || trend === undefined || trend === null;

  return (
    <div
      className={clsx(
        'relative overflow-hidden rounded-xl border border-slate-200/80 dark:border-slate-800',
        'bg-white dark:bg-slate-900 p-5 shadow-xs transition-all duration-200 hover:shadow-md',
        scheme.ring,
        className
      )}
    >
      {/* Background ambient gradient glow */}
      <div
        className={clsx(
          'pointer-events-none absolute -right-6 -top-6 h-28 w-28 rounded-full bg-gradient-to-br opacity-60 blur-xl',
          scheme.glow
        )}
      />

      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 dark:text-slate-400">
            {title}
          </p>
          <div className="flex items-baseline gap-2">
            <h3 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
              {value}
            </h3>
            {subvalue && (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {subvalue}
              </span>
            )}
          </div>
        </div>

        {Icon && (
          <div className={clsx('flex h-12 w-12 items-center justify-center rounded-xl p-2.5 shadow-xs', scheme.bg)}>
            <Icon className="h-6 w-6" />
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-800/80">
        <div className="flex items-center gap-1.5 text-xs">
          {!isNeutral && (
            <span
              className={clsx(
                'inline-flex items-center gap-0.5 font-semibold px-1.5 py-0.5 rounded-sm',
                isPositive
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400'
                  : 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400'
              )}
            >
              {isPositive ? (
                <TrendingUp className="h-3.5 w-3.5" />
              ) : (
                <TrendingDown className="h-3.5 w-3.5" />
              )}
              {typeof trend === 'number' ? `${Math.abs(trend)}%` : trend}
            </span>
          )}
          {trend === 0 && (
            <span className="inline-flex items-center gap-0.5 font-semibold px-1.5 py-0.5 rounded-sm bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400">
              <Minus className="h-3.5 w-3.5" /> 0%
            </span>
          )}
          {trendLabel && (
            <span className="text-slate-400 dark:text-slate-500 truncate max-w-[140px]">
              {trendLabel}
            </span>
          )}
        </div>

        {badge && <div>{badge}</div>}
      </div>
    </div>
  );
}
