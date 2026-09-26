// Bucketing helpers for the stock history feed.
// Timestamps arrive as 'YYYY-MM-DD HH:MM:SS' (server TEXT); all math is UTC
// so bucket edges never shift with the viewer's timezone.

function parseDay(ts) {
  const date = String(ts || '').slice(0, 10);
  const [y, m, d] = date.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return Number.isNaN(dt.getTime()) ? null : dt;
}

function toKey(date) {
  return date.toISOString().slice(0, 10);
}

export function dayKey(ts) {
  const date = parseDay(ts);
  return date ? toKey(date) : null;
}

export function weekKey(ts) {
  const date = parseDay(ts);
  if (!date) return null;
  const mondayOffset = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - mondayOffset);
  return toKey(date);
}

export function monthKey(ts) {
  const date = parseDay(ts);
  return date ? toKey(date).slice(0, 7) : null;
}

export function bucketKey(ts, granularity) {
  if (granularity === 'week') return weekKey(ts);
  if (granularity === 'month') return monthKey(ts);
  return dayKey(ts);
}

export function bucketLabel(key, granularity) {
  if (granularity === 'week') return `w/c ${key}`;
  return key;
}

/** Sum movement deltas per bucket, oldest bucket first. Rows without a usable date are skipped. */
export function rollupByBucket(movements, granularity) {
  const buckets = new Map();
  for (const m of movements || []) {
    const key = bucketKey(m.timestamp, granularity);
    if (!key) continue;
    const slot = buckets.get(key) || { key, label: bucketLabel(key, granularity), net: 0, count: 0 };
    slot.net += Number(m.delta) || 0;
    slot.count += 1;
    buckets.set(key, slot);
  }
  return [...buckets.values()].sort((a, b) => (a.key < b.key ? -1 : 1));
}

/** Keep movements whose date falls within [since, until] (inclusive, YYYY-MM-DD). */
export function inRange(movements, since, until) {
  return (movements || []).filter((m) => {
    const day = dayKey(m.timestamp);
    if (!day) return !since && !until;
    if (since && day < since) return false;
    if (until && day > until) return false;
    return true;
  });
}

/** Default range: last 30 days ending today (YYYY-MM-DD). */
export function defaultRange() {
  const until = new Date().toISOString().slice(0, 10);
  const since = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
  return { since, until };
}
