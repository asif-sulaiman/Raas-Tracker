export function formatNumber(num, decimals = 2) {
  if (num === null || num === undefined) return '-';
  return Number(num).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/**
 * Exact money math in integer cents (avoids 0.1 + 0.2 float drift).
 * Invoice semantics: each line rounds to the cent, lines add as integers.
 */
export function toCents(amount) {
  return Math.round((Number(amount) || 0) * 100);
}

export function fromCents(cents) {
  return (cents || 0) / 100;
}

/** Dollar value of one line, rounded to the cent. */
export function lineTotal(quantity, unitPrice) {
  return fromCents(toCents((Number(quantity) || 0) * (Number(unitPrice) || 0)));
}

/** Exact invoice total for [{quantity, unit_price}]. */
export function sumLineTotals(items) {
  return fromCents(
    (items || []).reduce(
      (sum, i) => sum + toCents((Number(i.quantity) || 0) * (Number(i.unit_price) || 0)),
      0
    )
  );
}

export function formatDate(dateStr) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(dateStr) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function truncate(str, maxLength = 50) {
  if (!str || str.length <= maxLength) return str;
  return str.substring(0, maxLength) + '...';
}
