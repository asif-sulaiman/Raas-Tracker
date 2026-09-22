/** Pure notification helpers (no React — unit-testable). */

export const SEVERITY_META = {
  critical: {
    toast: 'error',
    icon: 'alert',
    iconWrap: 'bg-rose-50 text-rose-600 dark:bg-rose-950/50 dark:text-rose-400',
  },
  warning: {
    toast: 'warning',
    icon: 'alert',
    iconWrap: 'bg-amber-50 text-amber-600 dark:bg-amber-950/50 dark:text-amber-400',
  },
  info: {
    toast: 'info',
    icon: 'info',
    iconWrap: 'bg-sky-50 text-sky-600 dark:bg-sky-950/50 dark:text-sky-400',
  },
};

export function severityMeta(severity) {
  return SEVERITY_META[severity] || SEVERITY_META.info;
}

/** Notifications in `items` whose ids are not in `seenIds` (a Set). */
export function diffNewNotifications(items, seenIds) {
  return (items || []).filter((n) => !seenIds.has(n.id));
}

/**
 * Compact relative time. Backend stores SQLite UTC ("YYYY-MM-DD HH:MM:SS");
 * treat naive timestamps as UTC so offsets never double-shift.
 */
export function timeAgo(dateStr) {
  if (!dateStr) return '';
  const hasZone = dateStr.includes('T')
    ? dateStr.endsWith('Z') || dateStr.includes('+')
    : false;
  const iso = dateStr.includes('T') ? dateStr : dateStr.replace(' ', 'T');
  const ts = new Date(hasZone || !iso ? iso : `${iso}Z`).getTime();
  if (Number.isNaN(ts)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** In-app route for a notification's entity, or null when it has no target. */
export function entityRoute(notification) {
  switch (notification?.entity_type) {
    case 'upload':
      return '/upload';
    case 'chemical':
      return '/chemicals';
    case 'sale':
      return '/sales';
    default:
      return null;
  }
}
