import { toCents, fromCents } from './format.js';

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

export function nextStageFor(stageKey) {
  const idx = STAGES.findIndex((s) => s.key === stageKey);
  if (idx < 0 || idx + 1 >= STAGES.length) return null;
  return STAGES[idx + 1].key;
}

/**
 * Board grouping for the LC lane (non-visual helper).
 *
 * The list endpoint projects `sales.lc_id` (and `GET /api/lcs` enriches with
 * canonical LC rows), so the link key prefers a real `lc_id` when present and
 * falls back to the legacy `lc_number` mirror scoped by `company_id`. Returns
 * `{ groups: [{ key, lc, pis }], unlinked: [...] }` with groups in first-
 * appearance order; `lcs` is the `GET /api/lcs` list (optional enrichment).
 * Totals are PI totals summed in integer cents (see `utils/format.js`) — never
 * invoice totals, never float `+`.
 */
export function lcGroupKey(sale) {
  if (!sale) return null;
  if (sale.lc_id !== undefined && sale.lc_id !== null && sale.lc_id !== '') {
    return `id:${sale.lc_id}`;
  }
  const num = (sale.lc_number || '').trim();
  if (!num) return null;
  return `mirror:${sale.company_id ?? ''}:${num.toLowerCase()}`;
}

export function isLinkedSale(sale) {
  return lcGroupKey(sale) !== null;
}

function findLcMeta(lcs, sale, key) {
  if (!Array.isArray(lcs) || !key) return null;
  if (key.startsWith('id:')) {
    const id = key.slice(3);
    return lcs.find((l) => String(l.id) === String(id)) || null;
  }
  // Mirror keys: a null sale company never wildcards — attach no meta so the
  // group stays `_mirror:true` and renders as unlinked PI(s). When both sides
  // carry a company, require strict equality (a null LC company no longer
  // merges one lc_number across companies).
  if (sale.company_id == null) return null;
  const num = (sale.lc_number || '').trim().toLowerCase();
  return (
    lcs.find(
      (l) =>
        (l.lc_number || '').trim().toLowerCase() === num &&
        l.company_id === sale.company_id
    ) || null
  );
}

export function groupSalesByLc(sales = [], lcs = []) {
  const list = Array.isArray(sales) ? sales : [];
  const groups = [];
  const byKey = new Map();
  const unlinked = [];
  for (const sale of list) {
    const key = lcGroupKey(sale);
    if (!key) {
      unlinked.push(sale);
      continue;
    }
    let group = byKey.get(key);
    if (!group) {
      const meta = findLcMeta(lcs, sale, key);
      const pis = [];
      group = { key, meta, pis, lc: null };
      byKey.set(key, group);
      groups.push(group);
    }
    group.pis.push(sale);
  }
  for (const group of groups) {
    const pis = group.pis;
    const meta = group.meta;
    const first = pis[0] || {};
    // PI total (visible slice) in integer cents — never an invoice total.
    const total = fromCents(pis.reduce((sum, p) => sum + toCents(p.total_value), 0));
    const lcNumber = (meta?.lc_number || first.lc_number || '').trim();
    // Keep group.key (e.g. "id:10") separate from the numeric id the board
    // actions need: parse id:-prefixed keys when GET /api/lcs failed and no
    // meta row exists, so move/unlink/detail still see a number.
    let numericId;
    if (meta?.id != null) {
      numericId = meta.id;
    } else if (group.key.startsWith('id:')) {
      const parsed = Number(group.key.slice(3));
      numericId = Number.isInteger(parsed) ? parsed : group.key;
    } else {
      numericId = group.key;
    }
    group.lc = {
      id: numericId,
      lc_number: lcNumber || (meta?.id != null ? `#${meta.id}` : 'LC'),
      company_id: meta?.company_id ?? first.company_id ?? null,
      company_name:
        meta?.company_name || meta?.client_name || first.company_name || first.client_name || 'Unknown client',
      client_name: meta?.client_name || first.client_name || null,
      stage: meta?.stage || first.stage,
      lc_date: meta?.lc_date ?? first.lc_date ?? null,
      expiry_date: meta?.expiry_date ?? first.expiry_date ?? null,
      total_value: total,
      pis,
      _mirror: !meta,
    };
  }
  return { groups, unlinked };
}
