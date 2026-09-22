import { Eye, Trash2, ArrowRight, Package } from 'lucide-react';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { formatNumber, formatDate } from '../../utils/format';
import { STAGE_BADGE, isOverdue } from '../../utils/sales';

export default function SaleCard({ sale, actionLabel, actionVariant = 'primary', onAction, onView, onDelete }) {
  const total = sale.total_value ?? 0;

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-3.5 shadow-xs hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-900 dark:text-white truncate">
            {sale.pi_number || `#${sale.id}`}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5">
            {sale.client_name || 'Unknown client'}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge variant={STAGE_BADGE[sale.stage] || 'default'} size="xs">
            {sale.item_count ?? 0} items
          </Badge>
          {isOverdue(sale) && (
            <Badge variant="error" size="xs" dot>
              Overdue
            </Badge>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 mt-2.5 text-xs text-slate-500 dark:text-slate-400">
        <Package className="h-3.5 w-3.5 shrink-0" />
        <span className="font-semibold text-slate-800 dark:text-slate-100 text-sm">
          ${formatNumber(total)}
        </span>
        <span className="text-slate-400">•</span>
        <span>{formatDate(sale.pi_date)}</span>
      </div>

      {sale.lc_number && (
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1.5 truncate">
          LC: <span className="font-mono font-medium">{sale.lc_number}</span>
        </p>
      )}

      <div className="flex items-center gap-1.5 mt-3">
        {actionLabel && (
          <Button
            variant={actionVariant}
            size="sm"
            icon={ArrowRight}
            onClick={() => onAction?.(sale)}
            className="flex-1 justify-center"
          >
            {actionLabel}
          </Button>
        )}
        <Button
          variant="secondary"
          size="sm"
          icon={Eye}
          onClick={() => onView?.(sale)}
          title="View details"
          className="!px-2"
        >
          {null}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          icon={Trash2}
          onClick={() => onDelete?.(sale)}
          title="Delete sale"
          className="!px-2 !text-rose-600 dark:!text-rose-400 hover:!bg-rose-50 dark:hover:!bg-rose-950/40"
        >
          {null}
        </Button>
      </div>
    </div>
  );
}
