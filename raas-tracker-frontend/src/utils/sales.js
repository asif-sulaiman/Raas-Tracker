export const STAGES = [
  { key: 'pi_issued', title: 'PI Issued', actionLabel: 'Enter LC', actionVariant: 'primary', action: 'lc' },
  { key: 'lc_received', title: 'LC Received', actionLabel: 'Start Shipment', actionVariant: 'primary', action: 'move' },
  { key: 'shipment_ongoing', title: 'Shipment Ongoing', actionLabel: 'Mark Shipped', actionVariant: 'primary', action: 'move' },
  { key: 'payment_due', title: 'Payment Due', actionLabel: 'Record Payment', actionVariant: 'success', action: 'payment' },
  { key: 'completed', title: 'Completed', actionLabel: null, action: null },
];

export const STAGE_LABELS = {
  pi_issued: 'PI Issued',
  lc_received: 'LC Received',
  shipment_ongoing: 'Shipment Ongoing',
  payment_due: 'Payment Due',
  completed: 'Completed',
};

export const STAGE_BADGE = {
  pi_issued: 'info',
  lc_received: 'new',
  shipment_ongoing: 'pending',
  payment_due: 'warning',
  completed: 'success',
};

const TERMINAL_STAGES = ['payment_due', 'completed'];

function toDayStart(dateStr) {
  if (!dateStr) return null;
  const d = new Date(dateStr.length <= 10 ? dateStr + 'T00:00:00' : dateStr);
  if (Number.isNaN(d.getTime())) return null;
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/**
 * A sale is overdue when its latest shipment date has passed
 * but it has not reached Payment Due / Completed.
 * `todayOverride` (YYYY-MM-DD) exists for deterministic testing.
 */
export function isOverdue(sale, todayOverride) {
  if (!sale || !sale.shipment_date) return false;
  if (TERMINAL_STAGES.includes(sale.stage)) return false;
  const ship = toDayStart(sale.shipment_date);
  if (ship === null) return false;
  const today = todayOverride ? toDayStart(todayOverride) : (() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  })();
  return ship < today;
}

export function countOverdue(sales, todayOverride) {
  if (!Array.isArray(sales)) return 0;
  return sales.filter((s) => isOverdue(s, todayOverride)).length;
}
