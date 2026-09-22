import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  diffNewNotifications, timeAgo, entityRoute, severityMeta, SEVERITY_META,
} from './notifications';

afterEach(() => {
  vi.useRealTimers();
});

describe('diffNewNotifications', () => {
  it('returns only items whose ids were not seen', () => {
    const items = [{ id: 1 }, { id: 2 }, { id: 3 }];
    expect(diffNewNotifications(items, new Set([1, 3])).map((n) => n.id)).toEqual([2]);
  });

  it('treats every item as new against an empty set', () => {
    expect(diffNewNotifications([{ id: 9 }], new Set())).toHaveLength(1);
  });

  it('handles null items', () => {
    expect(diffNewNotifications(null, new Set())).toEqual([]);
  });
});

describe('timeAgo', () => {
  it('returns empty for missing or unparseable input', () => {
    expect(timeAgo('')).toBe('');
    expect(timeAgo(null)).toBe('');
    expect(timeAgo('garbage')).toBe('');
  });

  it('says just now for seconds-old timestamps', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T12:00:05Z'));
    expect(timeAgo('2026-01-01 12:00:00')).toBe('just now');
  });

  it('formats minutes and hours', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T12:00:00Z'));
    expect(timeAgo('2026-01-01 11:55:00')).toBe('5m ago');
    expect(timeAgo('2026-01-01 09:00:00')).toBe('3h ago');
  });
});

describe('entityRoute', () => {
  it('maps known entity types to app routes', () => {
    expect(entityRoute({ entity_type: 'upload' })).toBe('/upload');
    expect(entityRoute({ entity_type: 'chemical' })).toBe('/chemicals');
    expect(entityRoute({ entity_type: 'sale' })).toBe('/sales');
  });

  it('returns null for unknown or missing entities', () => {
    expect(entityRoute({ entity_type: 'user' })).toBeNull();
    expect(entityRoute({})).toBeNull();
    expect(entityRoute(null)).toBeNull();
  });
});

describe('severityMeta', () => {
  it('maps all three severities to toast tones', () => {
    expect(severityMeta('critical').toast).toBe('error');
    expect(severityMeta('warning').toast).toBe('warning');
    expect(severityMeta('info').toast).toBe('info');
  });

  it('falls back to info for unknown severities', () => {
    expect(severityMeta('bogus').toast).toBe('info');
    expect(Object.keys(SEVERITY_META)).toEqual(['critical', 'warning', 'info']);
  });
});
