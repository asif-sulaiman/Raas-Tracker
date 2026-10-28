import { useState } from 'react';
import Button from '../ui/Button';
import { formatNumber, toCents, fromCents, todayLocalISO } from '../../utils/format';
import { computeMaturity, balanceCents } from '../../utils/invoices';

const inputCls =
  'rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function usd(value) {
  return `$${formatNumber(value)}`;
}

/**
 * One invoice step, shared by the board card and the invoice panel:
 * book (approximate date) → ship (actual date + maturity) → pay (amount+date).
 *
 * `onSubmit` receives the API body for the step. It may return a promise: a
 * rejection is shown inline, a resolution closes the form (the caller owns
 * the request and the refresh).
 */
export default function InvoiceStepForm({ kind, invoice, saving = false, error = null, onSubmit, onCancel }) {
  const [stepDate, setStepDate] = useState(kind === 'book' ? '' : todayLocalISO());
  const [maturityDate, setMaturityDate] = useState(
    kind === 'ship' ? computeMaturity(todayLocalISO(), invoice) : ''
  );
  const [maturityTouched, setMaturityTouched] = useState(false);
  const [payAmount, setPayAmount] = useState('');
  const [localError, setLocalError] = useState(null);

  const submit = () => {
    setLocalError(null);
    if (kind === 'book') {
      if (!stepDate) {
        setLocalError('Approximate ship date is required');
        return;
      }
      onSubmit?.({ approx_ship_date: stepDate });
      return;
    }
    if (kind === 'ship') {
      if (!stepDate) {
        setLocalError('Actual ship date is required');
        return;
      }
      if (!maturityDate) {
        setLocalError('Maturity date is required');
        return;
      }
      onSubmit?.({ actual_ship_date: stepDate, maturity_date: maturityDate });
      return;
    }
    // Money is exact: round to the cent the same way invoice creation does,
    // so a typed 25.555 never posts an unrounded float.
    const amount = fromCents(toCents(parseFloat(payAmount)));
    if (!amount || amount <= 0) {
      setLocalError('Enter a payment amount greater than 0');
      return;
    }
    if (!stepDate) {
      setLocalError('Payment date is required');
      return;
    }
    onSubmit?.({ payment_amount: amount, payment_date: stepDate });
  };

  const shownError = error || localError;
  const balance = balanceCents(invoice?.amount, invoice?.paid_amount);

  return (
    <div className="mt-2 flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 dark:bg-slate-800/60 px-2.5 py-2">
      {kind === 'pay' ? (
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
            {balance !== null && (
              <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                Balance {usd(fromCents(balance))}
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
        <>
          <div>
            <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">
              {kind === 'book' ? 'Approximate ship date *' : 'Actual ship date *'}
            </label>
            <input
              aria-label={kind === 'book' ? 'Approximate ship date' : 'Actual ship date'}
              type="date"
              value={stepDate}
              onChange={(e) => {
                const value = e.target.value;
                setStepDate(value);
                // Follow the ship date until the operator edits the due date.
                if (kind === 'ship' && !maturityTouched) {
                  setMaturityDate(computeMaturity(value, invoice));
                }
              }}
              className={`${inputCls} w-36`}
            />
          </div>
          {kind === 'ship' && (
            <div>
              <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">
                Maturity date *
              </label>
              <input
                aria-label="Maturity date"
                type="date"
                value={maturityDate}
                onChange={(e) => {
                  setMaturityDate(e.target.value);
                  setMaturityTouched(true);
                }}
                className={`${inputCls} w-36`}
              />
              <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                {invoice?.payment_terms === 'deferred' && invoice?.tenor_days
                  ? `Deferred ${invoice.tenor_days} days`
                  : 'At sight'}
              </p>
            </div>
          )}
        </>
      )}
      <div className="flex items-center gap-1.5">
        <Button variant="primary" size="sm" onClick={submit} loading={saving} disabled={saving}>
          {kind === 'book' ? 'Save booking' : kind === 'ship' ? 'Save shipment' : 'Save payment'}
        </Button>
        <Button variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
      {shownError && (
        <p className="w-full text-xs font-medium text-rose-600 dark:text-rose-400">{shownError}</p>
      )}
    </div>
  );
}
