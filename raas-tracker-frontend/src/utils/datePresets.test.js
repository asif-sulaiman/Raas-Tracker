// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { DATE_PRESETS, dateAnchorOptions } from './datePresets.js';

describe('DATE_PRESETS', () => {
  // Fixed date for deterministic tests: 2025-06-15 (Sunday, Q2)
  const fixedDate = new Date('2025-06-15T12:00:00Z');

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(fixedDate);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('This Month', () => {
    it('returns first day of current month and today', () => {
      const { from, to } = DATE_PRESETS['This Month']();
      expect(from).toBe('2025-06-01');
      expect(to).toBe('2025-06-15');
    });
  });

  describe('Last Month', () => {
    it('returns first and last day of previous month', () => {
      const { from, to } = DATE_PRESETS['Last Month']();
      expect(from).toBe('2025-05-01');
      expect(to).toBe('2025-05-31');
    });
  });

  describe('This Quarter', () => {
    it('returns first day of current quarter and today', () => {
      const { from, to } = DATE_PRESETS['This Quarter']();
      // June is in Q2 (months 3,4,5 -> April, May, June, 0-indexed)
      expect(from).toBe('2025-04-01');
      expect(to).toBe('2025-06-15');
    });
  });

  describe('Last Quarter', () => {
    it('returns first and last day of previous quarter', () => {
      const { from, to } = DATE_PRESETS['Last Quarter']();
      // Previous quarter is Q1 (Jan-Mar)
      expect(from).toBe('2025-01-01');
      expect(to).toBe('2025-03-31');
    });
  });

  describe('This Year', () => {
    it('returns Jan 1 of current year and today', () => {
      const { from, to } = DATE_PRESETS['This Year']();
      expect(from).toBe('2025-01-01');
      expect(to).toBe('2025-06-15');
    });
  });

  describe('Last Year', () => {
    it('returns Jan 1 and Dec 31 of previous year', () => {
      const { from, to } = DATE_PRESETS['Last Year']();
      expect(from).toBe('2024-01-01');
      expect(to).toBe('2024-12-31');
    });
  });
});

describe('dateAnchorOptions', () => {
  it('contains all expected anchor options', () => {
    expect(dateAnchorOptions).toHaveLength(5);
    expect(dateAnchorOptions).toEqual([
      { value: 'pi_date', label: 'PI Date' },
      { value: 'lc_date', label: 'LC Date' },
      { value: 'shipment_date', label: 'Shipment Date' },
      { value: 'receive_date', label: 'Receive Date' },
      { value: 'maturity_date', label: 'Maturity Date' },
    ]);
  });
});