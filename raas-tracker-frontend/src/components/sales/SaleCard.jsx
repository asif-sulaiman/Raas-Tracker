import { useState } from 'react';
import { Eye, Trash2, ArrowRight, Package } from 'lucide-react';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import InvoiceStepForm from './InvoiceStepForm';
import { formatNumber, formatDate, todayLocalISO } from '../../utils/format';
import { STAGE_BADGE, isOverdue } from '../../utils/sales';
import { INVOICE_NEXT_STEP, INVOICE_STATUS_LABELS, INVOICE_STEP_LABEL } from '../../utils/invoices';

const SHIPMENT_SUB_STEPS = {
  production_running: { label: 'Production running', variant: 'pending' },
  production_done: { label: 'Production done', variant: 'info' },
  ship_booked: { label: 'Ship booked', variant: 'new' },
};

// Board vocabulary: shipped reads "Payment due", paid reads "Completed".
const INVOICE_CHIP_STYLES = {
  planned: 'border-slate-200 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300',
  produced: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-300',
  booked: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300',
  shipped: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300',
  paid: 'border-green-200 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950/40 dark:text-green-300',
};

function InvoiceChip({ status }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-1.5 py-px text-[10px] font-medium ${
        INVOICE_CHIP_STYLES[status] || INVOICE_CHIP_STYLES.planned
      }`}
    >
      {INVOICE_STATUS_LABELS[status] || status}
    </span>
  );
}

/**
 * One PI card. Invoices live on the card itself so each one can take its own
 * next step (book → ship → pay) without opening the modal; shipping one
 * advances the sale server-side, so there is no manual column move.
 */
export default function SaleCard({
  sale,
  actionLabel,
  actionVariant = 'primary',
  onAction,
  onView,
  onDelete,
  onInvoiceStep,
  canEdit = true,
}) {
  const [step, setStep] = useState(null); // { invoice, kind }
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);

  const total = sale.total_value ?? 0;
  const subStep = sale.stage === 'shipment_ongoing' ? SHIPMENT_SUB_STEPS[sale.shipment_status] : null;
  const invoices = Array.isArray(sale.invoices) ? sale.invoices : [];

  const openStep = (invoice, kind) => {
    setStep({ invoice, kind });
    setFormError(null);
  };

  const closeStep = () => {
    setStep(null);
    setFormError(null);
  };

  const handleSubmit = async (payload) => {
    if (!step) return;
    setSaving(true);
    setFormError(null);
    try {
      await onInvoiceStep?.(sale, step.invoice, step.kind, payload);
      setStep(null);
    } catch (err) {
      setFormError(err?.message || 'Could not save invoice');
    } finally {
      setSaving(false);
    }
  };

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

      {subStep && (
        <div className="mt-2">
          <Badge variant={subStep.variant} size="xs">
            {subStep.label}
          </Badge>
        </div>
      )}

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

      {invoices.length > 0 && (
        <div className="mt-2.5 pt-2 border-t border-slate-100 dark:border-slate-700">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            Invoices ({invoices.length})
          </p>
          <div className="mt-1 divide-y divide-slate-100 dark:divide-slate-700/60">
            {invoices.map((inv) => {
              const kind = INVOICE_NEXT_STEP[inv.status];
              const active = step?.invoice?.invoice_id === inv.invoice_id;
              // A planned/produced invoice may be booked; only 'planned' is
              // known unproduced from the summary. The server refuses the rest.
              const blocked = kind === 'book' && inv.status === 'planned';
              const overdue = inv.status === 'shipped' && inv.maturity_date
                && inv.maturity_date < todayLocalISO();
              return (
                <div key={inv.invoice_id} className="py-1.5">
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="font-mono font-medium text-slate-800 dark:text-slate-200">
                      {inv.invoice_number}
                    </span>
                    <InvoiceChip status={inv.status} />
                    {inv.amount !== null && inv.amount !== undefined && (
                      <span className="font-medium text-slate-600 dark:text-slate-300">
                        ${formatNumber(inv.amount)}
                      </span>
                    )}
                    {inv.paid_amount > 0 && (
                      <span className="text-slate-400">paid ${formatNumber(inv.paid_amount)}</span>
                    )}
                    {inv.maturity_date && (
                      <span className="text-slate-400">
                        Matures {formatDate(inv.maturity_date)}
                      </span>
                    )}
                    {overdue && (
                      <span className="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-1.5 py-px font-medium text-rose-700 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
                        Overdue
                      </span>
                    )}
                    <span className="flex-1" />
                    {canEdit && onInvoiceStep && kind && !active && (
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={blocked}
                        title={blocked ? 'Produce this invoice before booking a shipment' : undefined}
                        onClick={() => !blocked && openStep(inv, kind)}
                      >
                        {INVOICE_STEP_LABEL[kind]}
                      </Button>
                    )}
                  </div>
                  {active && (
                    <InvoiceStepForm
                      kind={step.kind}
                      invoice={inv}
                      saving={saving}
                      error={formError}
                      onSubmit={handleSubmit}
                      onCancel={closeStep}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
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
        {onDelete && (
          <Button
            variant="secondary"
            size="sm"
            icon={Trash2}
            onClick={() => onDelete?.(sale)}
            title="Delete sale"
            className="!px-2 !text-rose-600 dark:!text-rose-400 hover:!bg-rose-50 dark:hover:bg-rose-950/40"
          >
            {null}
          </Button>
        )}
      </div>
    </div>
  );
}
