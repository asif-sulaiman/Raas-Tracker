import { describe, it, expect } from 'vitest';
import { toCents, fromCents, lineTotal, sumLineTotals, formatNumber } from './format.js';

describe('cents money math', () => {
  it('kills the classic 0.1 + 0.2 drift', () => {
    expect(toCents(0.1) + toCents(0.2)).toBe(30);
    expect(fromCents(toCents(0.1) + toCents(0.2))).toBe(0.3);
  });

  it('rounds each line to the cent', () => {
    expect(lineTotal(3, 19.99)).toBe(59.97);
    expect(lineTotal(3, 0.1)).toBe(0.3);
  });

  it('sums invoice lines exactly', () => {
    const items = [
      { quantity: 3, unit_price: 19.99 },
      { quantity: 2, unit_price: 0.1 },
      { quantity: 1, unit_price: 100.1 },
    ];
    expect(sumLineTotals(items)).toBe(160.27);
  });

  it('renders exact $100.10 style display', () => {
    expect(formatNumber(sumLineTotals([{ quantity: 1, unit_price: 100.1 }]))).toBe('100.10');
    expect(formatNumber(lineTotal(7, 142.86))).toBe('1,000.02');
  });

  it('tolerates junk input', () => {
    expect(toCents(null)).toBe(0);
    expect(toCents('abc')).toBe(0);
    expect(sumLineTotals(null)).toBe(0);
    expect(sumLineTotals([])).toBe(0);
    expect(lineTotal(undefined, undefined)).toBe(0);
  });
});
