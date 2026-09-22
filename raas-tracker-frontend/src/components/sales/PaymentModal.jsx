import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { formatNumber, formatDate } from '../../utils/format';
import { useAuth } from '../../context/AuthContext';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function PaymentModal({ isOpen, sale, onClose, onSaved }) {
  const { apiFetch } = useAuth();
  const [detail, setDetail] = useState(null);
  const [paymentDate, setPaymentDate] = useState('');
  const [paymentAmount, setPaymentAmount] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen && sale) {
      (async () => {
        try {
          const res = await apiFetch(`/api/sales/${sale.id}`);
          const data = await res.json();
          if (!data.error) {
            setDetail(data);
            const bal = data.balance ?? 0;
            setPaymentAmount(bal > 0 ? bal.toFixed(2) : '');
          }
        } catch {
          // Detail stays null; totals fall back to the card's values.
        }
      })();
      setPaymentDate(new Date().toISOString().slice(0, 10));
      setNotes('');
      setError(null);
      setSaving(false);
    } else {
      setDetail(null);
    }
  }, [isOpen, sale]);

  const invoiceTotal = detail?.invoice_total ?? sale?.total_value ?? 0;
  const totalPaid = detail?.total_paid ?? 0;
  const balance = detail?.balance ?? invoiceTotal;

  const recordPayment = async (andComplete) => {
    setError(null);
    const amount = parseFloat(paymentAmount);
    if (!amount || amount <= 0) {
      setError('Enter a valid payment amount (USD)');
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch(`/api/sales/${sale.id}/payment`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payment_date: paymentDate || null, payment_amount: amount, notes: notes.trim() || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (andComplete && data.stage !== 'completed') {
        await apiFetch(`/api/sales/${sale.id}/move`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ notes: `Payment $${amount} recorded — manually completed` }),
        });
      }
      onClose?.();
      onSaved?.();
    } catch (err) {
      setError(err.message || 'Failed to record payment');
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Record Payment — ${sale?.pi_number || ''}`}
      subtitle={`${sale?.client_name || ''} • partial payments allowed, any amount accepted`}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" icon={Loader2} loading={saving} onClick={() => recordPayment(false)}>
            Record Payment
          </Button>
          <Button variant="success" size="sm" onClick={() => recordPayment(true)} disabled={saving}>
            Record & Complete
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-3 gap-2.5 text-center">
          <div className="rounded-lg bg-slate-50 dark:bg-slate-800/60 px-2 py-2">
            <p className="text-[11px] uppercase tracking-wider text-slate-400">Invoice</p>
            <p className="text-sm font-bold text-slate-900 dark:text-white">${formatNumber(invoiceTotal)}</p>
          </div>
          <div className="rounded-lg bg-emerald-50 dark:bg-emerald-950/40 px-2 py-2">
            <p className="text-[11px] uppercase tracking-wider text-emerald-600 dark:text-emerald-400">Paid</p>
            <p className="text-sm font-bold text-emerald-700 dark:text-emerald-300">${formatNumber(totalPaid)}</p>
          </div>
          <div className="rounded-lg bg-amber-50 dark:bg-amber-950/40 px-2 py-2">
            <p className="text-[11px] uppercase tracking-wider text-amber-600 dark:text-amber-400">Balance</p>
            <p className="text-sm font-bold text-amber-700 dark:text-amber-300">${formatNumber(balance)}</p>
          </div>
        </div>

        {(detail?.payments?.length ?? 0) > 0 && (
          <div>
            <p className="text-xs font-medium text-slate-600 dark:text-slate-300 mb-1.5">
              Previous payments ({detail.payments.length})
            </p>
            <div className="max-h-28 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-800 text-xs">
              {detail.payments.map((p) => (
                <div key={p.id} className="flex items-center justify-between px-2.5 py-1.5">
                  <span className="text-slate-500 dark:text-slate-400">{formatDate(p.payment_date)}</span>
                  <span className="font-semibold text-slate-800 dark:text-slate-100">${formatNumber(p.payment_amount)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Payment Date</label>
            <input type="date" value={paymentDate || ''} onChange={(e) => setPaymentDate(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Amount (USD) *</label>
            <input
              type="number"
              min="0"
              step="any"
              value={paymentAmount}
              onChange={(e) => setPaymentAmount(e.target.value)}
              className={inputCls}
              placeholder="0.00"
            />
          </div>
        </div>
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Notes (optional)</label>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} className={inputCls} placeholder="T/T ref, short-shipment reason…" />
        </div>
        {error && <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    </Modal>
  );
}
