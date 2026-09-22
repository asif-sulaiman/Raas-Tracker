/**
 * Dashboard builders: turn upload history + audit entries into chart/feed
 * data. Pure functions — every number on screen traces to a real upload.
 * Empty inputs yield empty outputs (callers render empty states, never mocks).
 */

export function uploadMismatches(u) {
  if (!u) return 0;
  return (u.last_month_mismatches || 0) + (u.this_month_mismatches || 0) + (u.both_mismatches || 0);
}

function shortMonthLabel(dateStr) {
  if (!dateStr) return 'Unknown';
  const d = new Date(dateStr.length <= 10 ? dateStr + 'T00:00:00' : dateStr);
  if (Number.isNaN(d.getTime())) return 'Unknown';
  return `${d.toLocaleString('en-US', { month: 'short' })} ${d.getFullYear()}`;
}

/**
 * Oldest-first trend points (cap 6 most recent) for the reconciliation chart.
 * Input: upload history newest-first (API order).
 */
export function buildTrendData(uploads, maxPoints = 6) {
  if (!Array.isArray(uploads) || uploads.length === 0) return [];
  return uploads
    .slice(0, maxPoints)
    .reverse()
    .map((u, i, arr) => ({
      month: shortMonthLabel(u.upload_date) + (i === arr.length - 1 ? ' (Current)' : ''),
      matched: u.matched || 0,
      mismatches: uploadMismatches(u),
      notInDb: u.not_in_db || 0,
    }));
}

/** Bucket split + caption for the latest upload's pie chart. */
export function buildDistribution(upload) {
  if (!upload) return null;
  return {
    data: [
      { name: 'Matched', value: upload.matched || 0, color: '#10b981' },
      { name: 'Mismatches', value: uploadMismatches(upload), color: '#f59e0b' },
      { name: 'Not in DB', value: upload.not_in_db || 0, color: '#ef4444' },
      { name: 'Not in Upload', value: upload.not_in_upload || 0, color: '#6366f1' },
    ],
    caption: `${upload.filename || 'Upload'} • ${shortMonthLabel(upload.upload_date)}`,
  };
}
