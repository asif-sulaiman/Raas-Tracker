/**
 * Packaging register rules — the one place the frontend decides what "low"
 * means for a counted container.
 *
 * Deliberately NOT `utils/stock.js`: that file carries a second rule (below 20%
 * of last month) on top of the alarm level. Packaging has no last-month
 * balance, and the server's `notify_packaging_short` uses exactly the rule
 * below, so bell alerts and the table badge cannot disagree.
 */

export const MOVEMENT_KINDS = [
  { value: 'RECEIVED', label: 'Received', sign: 1 },
  { value: 'RETURNED', label: 'Returned', sign: 1 },
  { value: 'ISSUED', label: 'Issued', sign: -1 },
  { value: 'DAMAGED', label: 'Damaged', sign: -1 },
  { value: 'SCRAPPED', label: 'Scrapped', sign: -1 },
  { value: 'COUNT', label: 'Physical count', sign: 0 },
];

export const COUNT_KIND = 'COUNT';

export function kindOf(value) {
  return MOVEMENT_KINDS.find((k) => k.value === value) || null;
}

/** 'out' at zero, 'low' at or below the alarm level, otherwise 'ok'. */
export function packagingStatus(item) {
  if (!item) return 'out';
  const qty = Number(item.qty) || 0;
  if (qty === 0) return 'out';
  const level = Number(item.reorder_level) || 0;
  if (level > 0 && qty <= level) return 'low';
  return 'ok';
}

/**
 * KG one stack of containers could hold: SUM(qty x capacity_kg).
 *
 * Accumulated in integer thousandths because `capacity_kg` is NUMERIC(12,3) and
 * a plain float sum drifts (3 x 0.1 is not 0.3). This is also the only
 * unit-consistent aggregate on the page — unlike the chemical "Total Quantity"
 * card, which adds KG to L to DRUM.
 */
export function packableKg(items) {
  const thousandths = (items || []).reduce(
    (sum, it) => sum
      + Math.round((Number(it?.qty) || 0) * (Number(it?.capacity_kg) || 0) * 1000),
    0,
  );
  return thousandths / 1000;
}

export function packagingTotals(items) {
  const rows = items || [];
  return {
    types: rows.length,
    pieces: rows.reduce((sum, it) => sum + (Number(it?.qty) || 0), 0),
    low: rows.filter((it) => packagingStatus(it) === 'low').length,
    out: rows.filter((it) => packagingStatus(it) === 'out').length,
    packableKg: packableKg(rows),
  };
}

/**
 * The POST body for one ledger row.
 *
 * A COUNT sends the number a person counted; every other kind sends the
 * operator a positive piece count and takes its sign from the kind, so nobody
 * has to remember whether "Issued 3" is -3.
 */
export function buildMovementBody(kind, pieces, { label, reason } = {}) {
  const count = Math.trunc(Number(pieces) || 0);
  const body = { label, kind };
  if (kind === COUNT_KIND) body.counted_qty = count;
  else body.delta = count * (kindOf(kind)?.sign ?? 1);
  const text = (reason || '').trim();
  if (text) body.reason = text;
  return body;
}

/** Client-side guard for the pieces field; the server re-checks everything. */
export function piecesError(value, kind) {
  const text = String(value ?? '').trim();
  if (!text) return 'Enter a number of pieces';
  const number = Number(text);
  if (!Number.isFinite(number)) return 'Enter a valid number';
  if (number < 0) return 'Pieces cannot be negative';
  if (number !== Math.trunc(number)) return 'Containers are whole pieces';
  if (number === 0 && kind !== COUNT_KIND) return 'A movement needs at least one piece';
  return null;
}
