import { describe, it, expect } from 'vitest';
import { isOverdue, countOverdue } from './sales.js';

const T = '2026-09-21';

describe('isOverdue', () => {
  it('flags a past shipment date in an active stage', () => {
    expect(isOverdue({ stage: 'shipment_ongoing', shipment_date: '2026-09-10' }, T)).toBe(true);
  });

  it('does not flag a future shipment date', () => {
    expect(isOverdue({ stage: 'shipment_ongoing', shipment_date: '2026-09-30' }, T)).toBe(false);
  });

  it('does not flag shipment due today (same-day boundary)', () => {
    expect(isOverdue({ stage: 'lc_received', shipment_date: '2026-09-21' }, T)).toBe(false);
  });

  it('never flags terminal stages, even when past due', () => {
    expect(isOverdue({ stage: 'payment_due', shipment_date: '2020-01-01' }, T)).toBe(false);
    expect(isOverdue({ stage: 'completed', shipment_date: '2020-01-01' }, T)).toBe(false);
  });

  it('returns false without a shipment date or sale', () => {
    expect(isOverdue({ stage: 'shipment_ongoing' }, T)).toBe(false);
    expect(isOverdue(null, T)).toBe(false);
    expect(isOverdue(undefined, T)).toBe(false);
  });

  it('returns false for an unparseable date', () => {
    expect(isOverdue({ stage: 'shipment_ongoing', shipment_date: 'not-a-date' }, T)).toBe(false);
  });

  it('accepts datetime strings, not just dates', () => {
    expect(isOverdue({ stage: 'shipment_ongoing', shipment_date: '2026-09-10T15:30:00' }, T)).toBe(true);
  });
});

describe('countOverdue', () => {
  it('counts only overdue sales', () => {
    const sales = [
      { stage: 'shipment_ongoing', shipment_date: '2026-09-10' },
      { stage: 'shipment_ongoing', shipment_date: '2026-09-30' },
      { stage: 'completed', shipment_date: '2020-01-01' },
    ];
    expect(countOverdue(sales, T)).toBe(1);
  });

  it('returns 0 for non-arrays', () => {
    expect(countOverdue(null, T)).toBe(0);
    expect(countOverdue(undefined, T)).toBe(0);
    expect(countOverdue([], T)).toBe(0);
  });
});
