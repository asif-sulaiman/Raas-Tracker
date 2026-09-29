import { useEffect, useState } from 'react';
import { Plus, Check, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import { formatDate, formatNumber, toCents, fromCents } from '../../utils/format';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { toast } from 'sonner';

const inputCls =
  'rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

// One badge tone per lifecycle step: planned → produced → booked → shipped → paid.
const STATUS_STYLES = {
  planned: 'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700',
  produced: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800',
  booked: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800',
  shipped: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800',
  paid: 'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-300 dark:border-green-800',
};

const STATUS_LABELS = {
  planned: 'Planned',
  produced: 'Produced',
  booked: 'Booked',
  shipped: 'Shipped',
  paid: 'Paid',
};

// The next thing a user can do with an invoice, per status.
const NEXT_STEP = {
  planned: 'book',
  produced: 'book',
  booked: 'ship',
  shipped: 'pay',
  paid: null,
};

const STEP_LABEL = {
  book: 'Book shipment',
  ship: 'Ship',
  pay: 'Record payment',
};

function today() {
  return new Date().toISOString().slice(0, 10);
}

/** Money label — same `$1,200.00` shape the rest of the app uses. */
function usd(value) {
  return `$${formatNumber(value)}`;
}

/** Exact remaining balance in integer cents (no float subtraction on display). */
function balanceCents(amount, paidAmount) {
  if (amount === null || amount === undefined) return null;
  return Math.max(0, toCents(amount) - toCents(paidAmount));
}

function StatusChip({ status }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        STATUS_STYLES[status] || STATUS_STYLES.planned
      }`}
    >
      {STATUS_LABELS[status] || status}
    </span>
  );
}

/**
 * Invoice lifecycle for one sale: add → book → ship → pay.
 * Rendered inside SaleDetailModal while the sale sits in Shipment Ongoing.
 * Mutations are admin-only (the API enforces that), so actions hide for users.
 */
export default function InvoicePanel({ saleId, isAdmin = false, onChanged, totalValue = null }) {
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
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [invoiceAmount, setInvoiceAmount] = useState('');
  const [step, setStep] = useState(null); // { invoiceId, kind: 'book' | 'ship' | 'pay' }
  const [stepDate, setStepDate] = useState('');
  const [payAmount, setPayAmount] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);

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
    // `apiFetch` identity shifts on route navigation (same as PaymentModal) —
    // saleId is the real input here. The panel is keyed by sale in the modal.
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
    setStepDate(kind === 'book' ? '' : today());
    setPayAmount('');
    setFormError(null);
  };

  const closeStep = () => {
    setStep(null);
    setStepDate('');
    setPayAmount('');
    setFormError(null);
  };

  const openAdd = () => {
    setAdding(true);
    setInvoiceNumber('');
    setInvoiceAmount('');
    setFormError(null);
  };

  const closeAdd = () => {
    setAdding(false);
    setInvoiceNumber('');
    setInvoiceAmount('');
    setFormError(null);
  };

  const handleAdd = async () => {
    const number = invoiceNumber.trim();
    if (!number) {
      setFormError('Invoice number is required');
      return;
    }
    const body = { invoice_number: number };
    const rawAmount = invoiceAmount.trim();
    if (rawAmount) {
      // Allow pasted "$2,400.00" hints; strip separators before parsing.
      const value = parseFloat(rawAmount.replace(/,/g, ''));
      if (Number.isNaN(value) || value < 0) {
        setFormError('Enter an amount of 0 or more');
        return;
      }
      body.amount = fromCents(toCents(value));
    }
    setSaving(true);
    try {
      await apiFetch(`/api/sales/${saleId}/invoices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      toast.success('Invoice added');
      closeAdd();
      await reload();
      onChanged?.();
    } catch (err) {
      setFormError(err?.message || 'Could not add invoice');
    } finally {
      setSaving(false);
    }
  };

  const handleStep = async () => {
    if (!step) return;
    const { invoiceId, kind } = step;
    if (kind === 'book' && !stepDate) {
      setFormError('Approximate ship date is required');
      return;
    }
    if (kind === 'ship' && !stepDate) {
      setFormError('Actual ship date is required');
      return;
    }
    let amount = 0;
    if (kind === 'pay') {
      // Money is exact: round to the cent the same way invoice creation does,
      // so a typed 25.555 never posts an unrounded float.
      amount = fromCents(toCents(parseFloat(payAmount)));
      if (!amount || amount <= 0) {
        setFormError('Enter a payment amount greater than 0');
        return;
      }
      if (!stepDate) {
        setFormError('Payment date is required');
        return;
      }
    }
    setSaving(true);
    try {
      if (kind === 'book') {
        await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/book`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approx_ship_date: stepDate }),
        });
        toast.success('Shipment booked');
      } else if (kind === 'ship') {
        await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/ship`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ actual_ship_date: stepDate }),
        });
        toast.success('Invoice shipped');
      } else {
        const payRes = await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/pay`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ payment_amount: amount, payment_date: stepDate }),
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
  // Balance hint for the open "Record payment" form (exact, in cents).
  const stepInvoice = step ? invoices.find((i) => i.invoice_id === step.invoiceId) : null;
  const stepBalance = stepInvoice ? balanceCents(stepInvoice.amount, stepInvoice.paid_amount) : null;
  const amountPlaceholder = totalValue === null || totalValue === undefined ? '0.00' : usd(totalValue);

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
          {totalsUnavailable && !totalsReady && (
            <span className="ml-2 text-xs font-normal text-amber-600 dark:text-amber-400">
              Money totals unavailable
            </span>
          )}
        </h4>
        {isAdmin && !adding && !step && (
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
            const kind = NEXT_STEP[inv.status];
            const active = step?.invoiceId === inv.invoice_id;
            return (
              <div key={inv.invoice_id} className="px-2.5 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono font-medium text-slate-800 dark:text-slate-200">
                    {inv.invoice_number}
                  </span>
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
                  {!inv.approx_ship_date && !inv.actual_ship_date && inv.created_at && (
                    <span className="text-[11px] text-slate-400">
                      added {formatDate(inv.created_at)}
                    </span>
                  )}
                  <span className="flex-1" />
                  {isAdmin && kind && !active && !adding && (
                    <Button variant="secondary" size="sm" onClick={() => openStep(inv.invoice_id, kind)}>
                      {STEP_LABEL[kind]}
                    </Button>
                  )}
                  {isAdmin && !active && !adding && (
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
                  <div className="mt-2 flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 dark:bg-slate-800/60 px-2.5 py-2">
                    {step.kind === 'pay' ? (
                      <>
                        <div>
                          <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">
                            Amount (USD) *
                          </label>
                          <input
                            aria-label="Payment amount"
                            type="number"
                            min="0"
                            step="any"
                            value={payAmount}
                            onChange={(e) => setPayAmount(e.target.value)}
                            placeholder="0.00"
                            className={`${inputCls} w-24`}
                          />
                          {stepBalance !== null && (
                            <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                              Balance {usd(fromCents(stepBalance))}
                            </p>
                          )}
                        </div>
                        <div>
                          <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">
                            Payment date *
                          </label>
                          <input
                            aria-label="Payment date"
                            type="date"
                            value={stepDate}
                            onChange={(e) => setStepDate(e.target.value)}
                            className={`${inputCls} w-36`}
                          />
                        </div>
                      </>
                    ) : (
                      <div>
                        <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">
                          {step.kind === 'book' ? 'Approximate ship date *' : 'Actual ship date *'}
                        </label>
                        <input
                          aria-label={step.kind === 'book' ? 'Approximate ship date' : 'Actual ship date'}
                          type="date"
                          value={stepDate}
                          onChange={(e) => setStepDate(e.target.value)}
                          className={`${inputCls} w-36`}
                        />
                      </div>
                    )}
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={handleStep}
                        loading={saving}
                        disabled={saving}
                      >
                        {step.kind === 'book' ? 'Save booking' : step.kind === 'ship' ? 'Save shipment' : 'Save payment'}
                      </Button>
                      <Button variant="secondary" size="sm" onClick={closeStep} disabled={saving}>
                        Cancel
                      </Button>
                    </div>
                    {formError && (
                      <p className="w-full text-xs font-medium text-rose-600 dark:text-rose-400">{formError}</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {adding && (
            <div className="flex flex-wrap items-center gap-2 px-2.5 py-2">
              <input
                aria-label="Invoice number"
                type="text"
                value={invoiceNumber}
                onChange={(e) => setInvoiceNumber(e.target.value)}
                placeholder="INV-2026-001"
                className={`${inputCls} flex-1 min-w-40`}
              />
              <div>
                <label
                  htmlFor="invoice-amount"
                  className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1"
                >
                  Amount
                </label>
                <input
                  id="invoice-amount"
                  type="number"
                  min="0"
                  step="any"
                  value={invoiceAmount}
                  onChange={(e) => setInvoiceAmount(e.target.value)}
                  placeholder={amountPlaceholder}
                  className={`${inputCls} w-28`}
                />
              </div>
              <Button variant="primary" size="sm" onClick={handleAdd} loading={saving} disabled={saving}>
                Save invoice
              </Button>
              <Button variant="secondary" size="sm" onClick={closeAdd} disabled={saving}>
                Cancel
              </Button>
              {formError && (
                <p className="w-full text-xs font-medium text-rose-600 dark:text-rose-400">{formError}</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
