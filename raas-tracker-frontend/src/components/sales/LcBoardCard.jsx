import { Eye, Trash2, ArrowRight, Receipt, Layers, Unlink, ChevronRight } from 'lucide-react';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { formatNumber, formatDate, toCents, fromCents } from '../../utils/format';
import { STAGE_BADGE, isOverdue } from '../../utils/sales';

/**
 * Phase 3 — ONE card per LC on the pipeline board (replaces N per-PI cards).
 *
 * Anatomy mirrors SaleCard so the board keeps one rhythm: header (number +
 * client, badges right), value row, nested PI rows, action row. Unlinked PIs
 * keep rendering as SaleCard — PipelineColumn untouched. The wiring lane owns
 * move/unlink/invoice behaviour; preserve layout, badges and click targets.
 */
export default function LcBoardCard({
  lc,
  actionLabel,
  actionVariant = 'primary',
  onAction,
  onOpen,
  onViewPI,
  onUnlink,
  onManageInvoices,
  onDelete,
}) {
  const pis = Array.isArray(lc.pis) ? lc.pis : [];
  // PI total (visible slice) in integer cents — never an invoice total and
  // never sourced for Phase-4 invoice math.
  const total =
    lc.total_value ?? fromCents(pis.reduce((sum, p) => sum + toCents(p.total_value), 0));
  const itemCount = pis.reduce((sum, p) => sum + (p.item_count ?? 0), 0);
  const company = lc.company_name || lc.client_name || 'Unknown client';

  return (
    <div className="rounded-xl border-2 border-indigo-200 dark:border-indigo-800 bg-white dark:bg-slate-800 p-3.5 shadow-xs hover:shadow-md transition-shadow">
      {/* Header — LC number + company, badges right (same row as SaleCard) */}
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          onClick={() => onOpen?.(lc)}
          title="Open LC details"
          className="min-w-0 text-left cursor-pointer rounded focus:outline-none focus:ring-2 focus:ring-blue-500/40"
        >
          <p className="text-sm font-semibold text-slate-900 dark:text-white truncate">
            <span className="font-mono">{lc.lc_number || `#${lc.id}`}</span>
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5">
            {company}
          </p>
        </button>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge variant={STAGE_BADGE[lc.stage] || 'default'} size="xs">
            {pis.length} PI{pis.length === 1 ? '' : 's'} · {itemCount} items
          </Badge>
          {isOverdue({ stage: lc.stage, shipment_date: lc.expiry_date }) && (
            <Badge variant="error" size="xs" dot>
              Overdue
            </Badge>
          )}
        </div>
      </div>

      {/* Value row — PI total (visible slice) = sum of nested PIs */}
      <div className="flex items-center gap-1.5 mt-2.5 text-xs text-slate-500 dark:text-slate-400">
        <Layers className="h-3.5 w-3.5 shrink-0 text-indigo-500" />
        <span className="font-semibold text-slate-800 dark:text-slate-100 text-sm">
          ${formatNumber(total)}
        </span>
        <span className="text-[11px] text-slate-400">PI total (visible)</span>
        <span className="text-slate-400">•</span>
        <span>{formatDate(lc.lc_date)}</span>
      </div>

      {/* Nested PI rows — each clickable to its PI detail */}
      <div className="mt-2.5 rounded-lg border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-700/60 overflow-hidden">
        {pis.map((pi) => (
          <div
            key={pi.id}
            className="flex items-center gap-1 bg-slate-50/70 dark:bg-slate-900/50 hover:bg-slate-100 dark:hover:bg-slate-900 transition-colors"
          >
            <button
              type="button"
              onClick={() => onViewPI?.(pi)}
              aria-label={`View PI ${pi.pi_number || pi.id}`}
              title={`View PI ${pi.pi_number || pi.id}`}
              className="flex-1 min-w-0 flex items-center gap-1.5 px-2.5 py-1.5 text-left cursor-pointer"
            >
              <span className="min-w-0 truncate text-xs font-medium text-slate-800 dark:text-slate-200">
                {pi.pi_number || `#${pi.id}`}
              </span>
              <span className="shrink-0 text-[11px] text-slate-400">
                ${formatNumber(pi.total_value ?? 0)} · {pi.item_count ?? 0} items
              </span>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-300 dark:text-slate-600" />
            </button>
            {onUnlink && (
              <button
                type="button"
                onClick={() => onUnlink?.(pi)}
                aria-label={`Unlink PI ${pi.pi_number || pi.id}`}
                title={`Unlink PI ${pi.pi_number || pi.id} from this LC`}
                className="p-1.5 mr-1 rounded-lg text-slate-400 hover:text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-950/40 cursor-pointer"
              >
                <Unlink className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Action row — same shape as SaleCard: primary flex-1 + icon buttons */}
      <div className="flex items-center gap-1.5 mt-3">
        {actionLabel && (
          <Button
            variant={actionVariant}
            size="sm"
            icon={ArrowRight}
            onClick={() => onAction?.(lc)}
            className="flex-1 justify-center"
          >
            {actionLabel}
          </Button>
        )}
        {onManageInvoices && (
          <Button
            variant="secondary"
            size="sm"
            icon={Receipt}
            onClick={() => onManageInvoices?.(lc)}
            title="Manage invoices"
            aria-label={`Manage invoices for ${lc.lc_number || lc.id}`}
            className="!px-2"
          >
            {null}
          </Button>
        )}
        <Button
          variant="secondary"
          size="sm"
          icon={Eye}
          onClick={() => onOpen?.(lc)}
          title="View LC details"
          aria-label={`View LC ${lc.lc_number || lc.id}`}
          className="!px-2"
        >
          {null}
        </Button>
        {onDelete && (
          <Button
            variant="secondary"
            size="sm"
            icon={Trash2}
            onClick={() => onDelete?.(lc)}
            title="Delete LC"
            className="!px-2 !text-rose-600 dark:!text-rose-400 hover:!bg-rose-50 dark:hover:!bg-rose-950/40"
          >
            {null}
          </Button>
        )}
      </div>
    </div>
  );
}
