import { describe, it, expect } from 'vitest';
import { stockStatus } from './stock.js';

describe('stockStatus', () => {
  it('marks exact zero as out regardless of threshold', () => {
    expect(stockStatus({ qty: 0, reorder_level: 50, balance_last_month: 100 })).toBe('out');
    expect(stockStatus({ qty: 0 })).toBe('out');
  });

  it('marks at/below the reorder level as low', () => {
    expect(stockStatus({ qty: 10, reorder_level: 10, balance_last_month: 10 })).toBe('low');
    expect(stockStatus({ qty: 5, reorder_level: 10, balance_last_month: 10 })).toBe('low');
    expect(stockStatus({ qty: 11, reorder_level: 10, balance_last_month: 10 })).toBe('matched');
  });

  it('falls back to the 20% heuristic when no threshold is set', () => {
    expect(stockStatus({ qty: 50, reorder_level: 0, balance_last_month: 100 })).toBe('matched');
    expect(stockStatus({ qty: 50, balance_last_month: 100 })).toBe('matched');
    expect(stockStatus({ qty: 10, reorder_level: 0, balance_last_month: 100 })).toBe('low');
  });

  it('falls back to the 20% heuristic without a threshold', () => {
    expect(stockStatus({ qty: 10, balance_last_month: 100 })).toBe('low');
    expect(stockStatus({ qty: 50, balance_last_month: 100 })).toBe('matched');
  });

  it('treats missing chemicals as out', () => {
    expect(stockStatus(null)).toBe('out');
    expect(stockStatus(undefined)).toBe('out');
  });
});
