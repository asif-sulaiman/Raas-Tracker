import { useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import Button from '../ui/Button';
import { formatDate } from '../../utils/format';
import { useAuth } from '../../context/AuthContext';
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
export default function InvoicePanel({ saleId, isAdmin = false, onChanged }) {
  const { apiFetch } = useAuth();
  const [invoices, setInvoices] = useState([]);
  const [completion, setCompletion] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [step, setStep] = useState(null); // { invoiceId, kind: 'book' | 'ship' | 'pay' }
  const [stepDate, setStepDate] = useState('');
  const [payAmount, setPayAmount] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);

  const fetchData = async () => {
    const [invRes, compRes] = await Promise.all([
      apiFetch(`/api/sales/${saleId}/invoices`),
      apiFetch(`/api/sales/${saleId}/completion`),
    ]);
    const [inv, comp] = await Promise.all([invRes.json(), compRes.json()]);
    return {
      invoices: Array.isArray(inv) ? inv : [],
      completion: comp && typeof comp.total === 'number' ? comp : null,
    };
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchData();
        if (cancelled) return;
        setInvoices(data.invoices);
        setCompletion(data.completion);
        setLoadError(null);
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
      setInvoices(data.invoices);
      setCompletion(data.completion);
      setLoadError(null);
    } catch {
      // Rows stay as they are — the mutation itself already reported success.
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
    setFormError(null);
  };

  const closeAdd = () => {
    setAdding(false);
    setInvoiceNumber('');
    setFormError(null);
  };

  const handleAdd = async () => {
    const number = invoiceNumber.trim();
    if (!number) {
      setFormError('Invoice number is required');
      return;
    }
    setSaving(true);
    try {
      await apiFetch(`/api/sales/${saleId}/invoices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoice_number: number }),
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
      amount = parseFloat(payAmount);
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
        await apiFetch(`/api/sales/${saleId}/invoices/${invoiceId}/pay`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ payment_amount: amount, payment_date: stepDate }),
        });
        toast.success('Payment recorded');
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

  const paidCount = completion && typeof completion.paid === 'number'
    ? completion.paid
    : invoices.filter((i) => i.status === 'paid').length;
  const totalCount = completion && typeof completion.total === 'number'
    ? completion.total
    : invoices.length;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-semibold text-slate-800 dark:text-white">
          Invoices ({invoices.length})
          {totalCount > 0 && (
            <span className="ml-2 text-xs font-normal text-slate-500 dark:text-slate-400">
              {paidCount}/{totalCount} invoices paid
            </span>
          )}
        </h4>
        {isAdmin && !adding && !step && (
          <Button variant="secondary" size="sm" icon={Plus} onClick={openAdd}>
            Add invoice
          </Button>
        )}
      </div>

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
