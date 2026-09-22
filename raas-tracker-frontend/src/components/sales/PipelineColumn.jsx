import SaleCard from './SaleCard';
import { formatNumber } from '../../utils/format';

const HEADER_STYLES = {
  pi_issued: 'bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-800 text-sky-700 dark:text-sky-300',
  lc_received: 'bg-indigo-50 dark:bg-indigo-950/40 border-indigo-200 dark:border-indigo-800 text-indigo-700 dark:text-indigo-300',
  shipment_ongoing: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300',
  payment_due: 'bg-orange-50 dark:bg-orange-950/40 border-orange-200 dark:border-orange-800 text-orange-700 dark:text-orange-300',
  completed: 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300',
};

export default function PipelineColumn({
  stage,
  title,
  sales = [],
  actionLabel,
  actionVariant,
  onAction,
  onView,
  onDelete,
}) {
  const columnValue = sales.reduce((sum, s) => sum + (s.total_value || 0), 0);

  return (
    <div className="flex flex-col rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-900/60 min-h-[200px]">
      <div className={`px-3.5 py-3 border-b rounded-t-xl ${HEADER_STYLES[stage] || ''} border-slate-200 dark:border-slate-700`}>
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold uppercase tracking-wider">{title}</h3>
          <span className="inline-flex items-center justify-center min-w-6 h-6 px-1.5 rounded-full bg-white dark:bg-slate-800 text-xs font-bold shadow-xs">
            {sales.length}
          </span>
        </div>
        <p className="text-xs font-semibold mt-1 opacity-80">
          ${formatNumber(columnValue)}
        </p>
      </div>

      <div className="flex flex-col gap-2.5 p-2.5 flex-1">
        {sales.length === 0 ? (
          <div className="flex-1 flex items-center justify-center py-8 text-xs text-slate-400 dark:text-slate-500">
            No sales
          </div>
        ) : (
          sales.map((sale) => (
            <SaleCard
              key={sale.id}
              sale={sale}
              actionLabel={actionLabel}
              actionVariant={actionVariant}
              onAction={onAction}
              onView={onView}
              onDelete={onDelete}
            />
          ))
        )}
      </div>
    </div>
  );
}
