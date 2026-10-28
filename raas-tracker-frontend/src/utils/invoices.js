import { toCents } from './format.js';

/**
 * Board vocabulary for invoice states. The data statuses stay
 * planned/produced/booked/shipped/paid; the UI calls the last two
 * "Payment due" and "Completed" so the card reads like the pipeline.
 */
export const INVOICE_STATUS_LABELS = {
  planned: 'Planned',
  produced: 'Produced',
  booked: 'Booked',
  shipped: 'Payment due',
  paid: 'Completed',
};

// The next thing a user can do with an invoice, per status.
export const INVOICE_NEXT_STEP = {
  planned: 'book',
  produced: 'book',
  booked: 'ship',
  shipped: 'pay',
  paid: null,
};

export const INVOICE_STEP_LABEL = {
  book: 'Book shipment',
  ship: 'Ship',
  pay: 'Record payment',
};

/**
 * Add whole days to a YYYY-MM-DD date in UTC. The API slices dates as plain
 * calendar strings, so local-time `Date` math could shift a leap day.
 */
export function addDaysISO(dateStr, days) {
  const parts = String(dateStr || '').split('-').map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return '';
  const base = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  base.setUTCDate(base.getUTCDate() + Number(days || 0));
  return base.toISOString().slice(0, 10);
}

/**
 * The maturity the server will compute on ship: at sight = the actual ship
 * date; deferred = actual + tenor. It is only a prefill — the operator may
 * override it in the form, and the server re-validates.
 */
export function computeMaturity(shipDate, invoice) {
  if (!shipDate) return '';
  if (invoice?.payment_terms === 'deferred' && invoice.tenor_days) {
    return addDaysISO(shipDate, invoice.tenor_days);
  }
  return shipDate;
}

/** Exact remaining balance in integer cents (no float subtraction on display). */
export function balanceCents(amount, paidAmount) {
  if (amount === null || amount === undefined) return null;
  return Math.max(0, toCents(amount) - toCents(paidAmount));
}
