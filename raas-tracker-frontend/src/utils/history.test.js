import { describe, it, expect } from 'vitest';
import {
  dayKey, weekKey, monthKey, bucketKey,
  rollupByBucket, inRange, defaultRange,
} from './history';

describe('history bucketing', () => {
  it('dayKey takes the date part', () => {
    expect(dayKey('2026-09-26 10:00:00')).toBe('2026-09-26');
  });

  it('weekKey snaps to Monday (UTC, no TZ drift)', () => {
    expect(weekKey('2026-09-26 10:00:00')).toBe('2026-09-21'); // Saturday
    expect(weekKey('2026-09-21 00:00:01')).toBe('2026-09-21'); // Monday
    expect(weekKey('2026-09-20 23:59:59')).toBe('2026-09-14'); // Sunday
  });

  it('monthKey takes year-month', () => {
    expect(monthKey('2026-09-26 10:00:00')).toBe('2026-09');
  });

  it('bucketKey dispatches on granularity', () => {
    expect(bucketKey('2026-09-26 10:00:00', 'day')).toBe('2026-09-26');
    expect(bucketKey('2026-09-26 10:00:00', 'week')).toBe('2026-09-21');
    expect(bucketKey('2026-09-26 10:00:00', 'month')).toBe('2026-09');
  });

  it('rollupByBucket sums net per bucket, ascending', () => {
    const rows = [
      { timestamp: '2026-09-09 09:00:00', delta: 7 },
      { timestamp: '2026-09-02 10:00:00', delta: 5 },
      { timestamp: '2026-09-02 18:00:00', delta: -2 },
    ];
    expect(rollupByBucket(rows, 'day')).toEqual([
      { key: '2026-09-02', label: '2026-09-02', net: 3, count: 2 },
      { key: '2026-09-09', label: '2026-09-09', net: 7, count: 1 },
    ]);
  });

  it('inRange is inclusive on both ends', () => {
    const rows = [
      { timestamp: '2026-09-01 00:00:00' },
      { timestamp: '2026-09-15 12:00:00' },
      { timestamp: '2026-09-30 23:59:59' },
    ];
    expect(inRange(rows, '2026-09-01', '2026-09-30')).toHaveLength(3);
    expect(inRange(rows, '2026-09-02', '2026-09-29')).toHaveLength(1);
  });

  it('skips rows without a usable date instead of crashing', () => {
    const rows = [
      { timestamp: '2026-09-02 10:00:00', delta: 5 },
      { timestamp: null, delta: 99 },
      { noTimestamp: true, delta: 99 },
    ];
    expect(rollupByBucket(rows, 'day')).toEqual([
      { key: '2026-09-02', label: '2026-09-02', net: 5, count: 1 },
    ]);
    expect(dayKey(null)).toBeNull();
    expect(weekKey('not-a-date')).toBeNull();
  });

  it('defaultRange spans 30 days ending today', () => {
    const { since, until } = defaultRange();
    const today = new Date().toISOString().slice(0, 10);
    const start = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
    expect(until).toBe(today);
    expect(since).toBe(start);
  });
});
