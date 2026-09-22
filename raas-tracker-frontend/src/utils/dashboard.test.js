import { describe, it, expect } from 'vitest';
import { uploadMismatches, buildTrendData, buildDistribution } from './dashboard.js';

const U = (over = {}) => ({
  id: 1,
  filename: 'AUGUST_2026.pdf',
  upload_date: '2026-09-05 10:00:00',
  matched: 100,
  last_month_mismatches: 3,
  this_month_mismatches: 2,
  both_mismatches: 1,
  not_in_db: 4,
  not_in_upload: 6,
  match_percentage: 88.5,
  ...over,
});

describe('uploadMismatches', () => {
  it('sums the three mismatch buckets', () => {
    expect(uploadMismatches(U())).toBe(6);
  });
  it('tolerates missing upload', () => {
    expect(uploadMismatches(null)).toBe(0);
    expect(uploadMismatches({})).toBe(0);
  });
});

describe('buildTrendData', () => {
  it('maps newest-first history to oldest-first points', () => {
    const out = buildTrendData([U({ id: 2, upload_date: '2026-09-05 10:00:00' }),
                                U({ id: 1, upload_date: '2026-09-05 10:00:00', matched: 90 })]);
    expect(out).toHaveLength(2);
    expect(out[0].matched).toBe(90);
    expect(out[1].matched).toBe(100);
    expect(out[1].month).toMatch(/Current/);
    expect(out[1].mismatches).toBe(6);
    expect(out[1].notInDb).toBe(4);
  });
  it('caps at 6 points and returns [] for empty input', () => {
    const many = Array.from({ length: 9 }, (_, i) => U({ id: i }));
    expect(buildTrendData(many)).toHaveLength(6);
    expect(buildTrendData([])).toEqual([]);
    expect(buildTrendData(null)).toEqual([]);
  });
});

describe('buildDistribution', () => {
  it('splits the latest upload into buckets with a caption', () => {
    const d = buildDistribution(U());
    expect(d.data.find((s) => s.name === 'Matched').value).toBe(100);
    expect(d.data.find((s) => s.name === 'Mismatches').value).toBe(6);
    expect(d.data.find((s) => s.name === 'Not in DB').value).toBe(4);
    expect(d.data.find((s) => s.name === 'Not in Upload').value).toBe(6);
    expect(d.caption).toMatch(/AUGUST_2026/);
  });
  it('returns null without an upload', () => {
    expect(buildDistribution(null)).toBeNull();
  });
});
