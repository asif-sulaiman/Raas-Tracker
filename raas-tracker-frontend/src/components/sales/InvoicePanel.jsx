import { useEffect, useState } from 'react';
import { Plus, Check, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import InvoiceEditor from './InvoiceEditor';
import InvoiceStepForm from './InvoiceStepForm';
import { formatDate, formatNumber, todayLocalISO } from '../../utils/format';
import {
  INVOICE_NEXT_STEP,
  INVOICE_STATUS_LABELS,
  INVOICE_STEP_LABEL,
} from '../../utils/invoices';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { toast } from 'sonner';

// One badge tone per lifecycle step: planned → produced → booked → shipped → paid.
const STATUS_STYLES = {
  planned: 'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700',
  produced: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800',
  booked: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
  shipped: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800',
  paid: 'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-300 dark:border-green-800',
};

// Booking commits to a real shipment, so it is gated on the invoice having
// been produced from the recipe page. The action stays visible but disabled so
// the prerequisite is discoverable rather than silently absent.
const NEEDS_PRODUCTION = 'Produce this invoice from the recipe page before booking a shipment.';

/** Money label — same `$1,200.00` shape the rest of the app uses. */
function usd(value) {
  return `$${formatNumber(value)}`;
}

function StatusChip({ status }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        STATUS_STYLES[status] || STATUS_STYLES.planned
      }`}
    >
      {INVOICE_STATUS_LABELS[status] || status}
    </span>
  );
}

/**
 * Invoice lifecycle for one sale: add → book → ship → pay.
 * Rendered inside SaleDetailModal while the sale sits in Shipment Ongoing.
 * Mutations are admin-only (the API enforces that), so actions hide for users.
 *
 * Creation is delegated to `InvoiceEditor`, which reads the sale's PI lines
 * from `/api/sales/<id>/invoice-lines` itself. An invoice with no lines is
 * refused: INV-REVISION makes the invoice the practical shipment document, so
 * it must state what is being shipped rather than inheriting the PI remainder
 * (which silently produced a $0 "paid" row).
 */
export default function InvoicePanel({ saleId, isAdmin = false, readOnly = false, onChanged }) {
  const { apiFetch } = useAuth();
  const { confirm } = useConfirm();
  const [invoices, setInvoices] = useState([]);
  const [completion, setCompletion] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  // Set when a mutation succeeded but the follow-up read failed: the rows on
  // screen are then known to be out of date and must be flagged as such.
  const [staleError, setStaleError] = useState(null);
  // The two reads are independent — completion only adds the money totals, so
  // its failure downgrades the totals chip instead of hiding the invoice rows.
  const [totalsUnavailable, setTotalsUnavailable] = useState(false);
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState(null);
  const [step, setStep] = useState(null); // { invoiceId, kind: 'book' | 'ship' | 'pay' }
  const [saving, setSaving] = useState(false);

  /**
   * Independent reads, settled separately. `invoices` is `null` when that read
   * failed (as opposed to a genuinely empty list) so callers can tell the two
   * apart; `completion` is `null` when it failed or carried no usable totals.
   */
  const fetchData = async () => {
    const [invResult, compResult] = await Promise.allSettled([
      apiFetch(`/api/sales/${saleId}/invoices`),
      apiFetch(`/api/sales/${saleId}/completion`),
    ]);
    let invoices = null;
    if (invResult.status === 'fulfilled') {
      const inv = await invResult.value.json();
      invoices = Array.isArray(inv) ? inv : [];
    }
    let completion = null;
    if (compResult.status === 'fulfilled') {
      const comp = await compResult.value.json();
      completion = comp && typeof comp.total === 'number' ? comp : null;
    }
    return {
      invoices,
      completion,
      invoicesError: invResult.status === 'rejected' ? invResult.reason : null,
      completionError: compResult.status === 'rejected' ? compResult.reason : null,
    };
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchData();
        if (cancelled) return;
        if (data.invoices) setInvoices(data.invoices);
        if (data.completion) setCompletion(data.completion);
        setTotalsUnavailable(!!data.completionError && !data.completion);
        if (data.invoicesError) {
          setLoadError(data.invoicesError?.message || 'Could not load invoices');
        } else {
          setLoadError(null);
        }
      } catch (err) {
        if (cancelled) return;
        setLoadError(err?.message || 'Could not load invoices');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // `apiFetch` identity shifts on route navigation — saleId is the real
    // input here. The panel is keyed by sale in the modal.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [saleId]);

  // Refetch after a mutation without flashing the whole panel back to "Loading".
  const reload = async () => {
    try {
      const data = await fetchData();
      if (data.invoices) setInvoices(data.invoices);
      if (data.completion) setCompletion(data.completion);
      setTotalsUnavailable(!!data.completionError && !data.completion);
      if (data.invoicesError) {
        // The mutation landed but the rows on screen are now out of date —
        // saying so beats presenting stale statuses as current.
        setStaleError(data.invoicesError?.message || 'Could not refresh invoices');
        return;
      }
      setStaleError(null);
    } catch (err) {
      setStaleError(err?.message || 'Could not refresh invoices');
    }
  };

  const openStep = (invoiceId, kind) => {
    setStep({ invoiceId, kind });
    setFormError(null);
  };

  const closeStep = () => {
    setStep(null);
    setFormError(null);
  };

  const openAdd = () => {
    setAdding(true);
  };

  // The form has already validated and rounded; `payload` is the API body.
  const handleStep = async (payload) => {
    if (!step) return;
    const { invoiceId, kind } = step;
    setSaving(true);
    try {
      if (kind === 'book') {
        await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/book`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        toast.success('Shipment booked');
      } else if (kind === 'ship') {
        await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/ship`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        toast.success('Invoice shipped');
      } else {
        const payRes = await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/pay`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        // A partial payment leaves the invoice short of paid — only the
        // returned status says whether it is actually paid now.
        const payData = await payRes.json().catch(() => ({}));
        toast.success(payData?.status === 'paid' ? 'Invoice paid' : 'Payment recorded');
      }
      closeStep();
      await reload();
      onChanged?.();
    } catch (err) {
      setFormError(err?.message || 'Could not save invoice');
    } finally {
      setSaving(false);
    }
  };

  const handleVoid = async (inv) => {
    const ok = await confirm({
      title: `Void invoice ${inv.invoice_number}?`,
      message: 'The invoice row will be removed. Payments already recorded stay in the sale totals.',
      confirmLabel: 'Void',
      danger: true,
    });
    if (!ok) return;
    setSaving(true);
    try {
      await apiFetch(`/api/sales/${saleId}/invoices/${inv.invoice_id}`, { method: 'DELETE' });
      toast.success('Invoice voided');
      await reload();
      onChanged?.();
    } catch (err) {
      toast.error(err.message || 'Could not void invoice');
    } finally {
      setSaving(false);
    }
  };

  // A completed sale keeps its invoices on screen as evidence: they may not be
  // advanced or voided. Adding one is still allowed — a late invoice is how a
  // completed PI is corrected, and the server reopens the sale when it lands.
  const canEdit = isAdmin && !readOnly;
  const canAddInvoice = isAdmin;
  const paidCount = completion && typeof completion.paid === 'number'
    ? completion.paid
    : invoices.filter((i) => i.status === 'paid').length;
  const totalCount = completion && typeof completion.total === 'number'
    ? completion.total
    : invoices.length;
  // Money totals ride along on the completion payload when the backend has them.
  const totalsReady = !!completion
    && typeof completion.total_amount === 'number'
    && typeof completion.paid_amount === 'number';
  const stepInvoice = step ? invoices.find((i) => i.invoice_id === step.invoiceId) : null;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-semibold text-slate-800 dark:text-white">
          Invoices ({invoices.length})
          {totalCount > 0 && (
            <span className="ml-2 text-xs font-normal text-slate-500 dark:text-slate-400">
              {paidCount}/{totalCount} invoices paid
              {totalsReady && ` · ${usd(completion.paid_amount)} of ${usd(completion.total_amount)}`}
            </span>
          )}
          {readOnly && (
            <span className="ml-2 text-xs font-normal text-slate-400 dark:text-slate-500">
              read-only · add an invoice to reopen
            </span>
          )}
          {totalsUnavailable && !totalsReady && (
            <span className="ml-2 text-xs font-normal text-amber-600 dark:text-amber-400">
              Money totals unavailable
            </span>
          )}
        </h4>
        {canAddInvoice && !adding && !step && (
          <Button variant="secondary" size="sm" icon={Plus} onClick={openAdd}>
            Add invoice
          </Button>
        )}
      </div>

      {staleError && (
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 dark:border-amber-800 dark:bg-amber-950/40">
          <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
            Saved, but could not refresh — the rows below may be out of date.
          </p>
          <Button variant="secondary" size="sm" onClick={reload} disabled={saving}>
            Retry
          </Button>
        </div>
      )}

      {loading ? (
        <p className="text-xs text-slate-400 dark:text-slate-500 py-2">Loading invoices…</p>
      ) : loadError ? (
        <p className="text-xs font-medium text-rose-600 dark:text-rose-400 py-2">{loadError}</p>
      ) : (
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-800 text-xs">
          {invoices.length === 0 && !adding && (
            <p className="px-2.5 py-2 text-slate-400 dark:text-slate-500">
              No invoices yet. Add an invoice number to track production and shipment.
            </p>
          )}

          {invoices.map((inv) => {
            const kind = INVOICE_NEXT_STEP[inv.status];
            const active = step?.invoiceId === inv.invoice_id;
            // Booking is gated on production CONTENT, which the status alone
            // cannot express: record_production flips an invoice to 'produced'
            // on the first linked run, so a multi-product invoice can read
            // 'produced' while a sibling product is unbuilt. Trust the
            // server's can_book; fall back fail-closed (only 'produced' may
            // be assumed bookable) when an older payload omits the flag.
            const canBook = typeof inv.can_book === 'boolean'
              ? inv.can_book
              : inv.status === 'produced';
            const blocked = kind === 'book' && !canBook;
            const blockedReason = inv.book_blocked_reason || NEEDS_PRODUCTION;
            return (
              <div key={inv.invoice_id} className="px-2.5 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono font-medium text-slate-800 dark:text-slate-200">
                    {inv.invoice_number}
                  </span>
                  {inv.invoice_date || inv.created_at ? (
                    <span className="text-[11px] text-slate-400 ml-1">
                      · {inv.invoice_date || inv.created_at.slice(0, 10)}
                    </span>
                  ) : null}
                  <StatusChip status={inv.status} />
                  {inv.amount !== null && inv.amount !== undefined && (
                    <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300">
                      {usd(inv.amount)} · paid {usd(inv.paid_amount || 0)}
                      {inv.status === 'paid' && (
                        <Check className="ml-1 inline h-3 w-3 text-emerald-600" aria-hidden="true" />
                      )}
                    </span>
                  )}
                  {inv.approx_ship_date && (
                    <span className="text-[11px] text-slate-400">
                      approx ship {formatDate(inv.approx_ship_date)}
                    </span>
                  )}
                  {inv.actual_ship_date && (
                    <span className="text-[11px] text-slate-400">
                      shipped {formatDate(inv.actual_ship_date)}
                    </span>
                  )}
                  {inv.maturity_date && (
                    <span className="text-[11px] text-slate-400">
                      matures {formatDate(inv.maturity_date)}
                    </span>
                  )}
                  {inv.status === 'shipped'
                    && inv.maturity_date
                    && inv.maturity_date < todayLocalISO() && (
                      <span className="inline-flex items-center rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
                        Overdue
                      </span>
                    )}
                  {!inv.approx_ship_date && !inv.actual_ship_date && inv.created_at && (
                    <span className="text-[11px] text-slate-400">
                      added {formatDate(inv.created_at)}
                    </span>
                  )}
                  <span className="flex-1" />
                  {canEdit && kind && !active && !adding && (
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={blocked}
                      title={blocked ? blockedReason : undefined}
                      onClick={() => !blocked && openStep(inv.invoice_id, kind)}
                    >
                      {INVOICE_STEP_LABEL[kind]}
                    </Button>
                  )}
                  {blocked && !readOnly && (
                    <span className="text-[11px] text-amber-600 dark:text-amber-400">
                      {blockedReason}
                    </span>
                  )}
                  {canEdit && !active && !adding && (
                    <button
                      type="button"
                      onClick={() => handleVoid(inv)}
                      disabled={saving}
                      className="p-1 text-slate-400 hover:text-rose-600 disabled:opacity-50 cursor-pointer"
                      title="Void invoice"
                      aria-label={`Void invoice ${inv.invoice_number}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>

                {active && (
                  <InvoiceStepForm
                    kind={step.kind}
                    invoice={stepInvoice}
                    saving={saving}
                    error={formError}
                    onSubmit={handleStep}
                    onCancel={closeStep}
                  />
                )}
              </div>
            );
          })}

          {adding && (
            <InvoiceEditor
              mode="create"
              saleId={saleId}
              onDone={() => {
                setAdding(false);
                reload();
                onChanged?.();
              }}
              onCancel={() => setAdding(false)}
            />
          )}
        </div>
      )}
    </div>
  );
}
