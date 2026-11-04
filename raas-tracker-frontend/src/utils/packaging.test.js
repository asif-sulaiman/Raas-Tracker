import { describe, it, expect } from 'vitest';
import {
  packagingStatus,
  packagingTotals,
  packableKg,
  buildMovementBody,
  MOVEMENT_KINDS,
} from './packaging.js';

describe('packagingStatus', () => {
  it('is one rule, not three', () => {
    // `utils/stock.js` adds a 20%-of-last-month heuristic on top of the alarm
    // level. Packaging has no last-month balance, so a single rule applies —
    // and it must agree with the server's `notify_packaging_short`.
    expect(packagingStatus({ qty: 0, reorder_level: 0 })).toBe('out');
    expect(packagingStatus({ qty: 4, reorder_level: 4 })).toBe('low');
    expect(packagingStatus({ qty: 4, reorder_level: 10 })).toBe('low');
    expect(packagingStatus({ qty: 5, reorder_level: 4 })).toBe('ok');
    expect(packagingStatus({ qty: 5, reorder_level: 0 })).toBe('ok');
  });

  it('treats zero as out even with no alarm configured', () => {
    expect(packagingStatus({ qty: 0 })).toBe('out');
  });

  it('is missing-item safe', () => {
    expect(packagingStatus(null)).toBe('out');
    expect(packagingStatus(undefined)).toBe('out');
  });
});

describe('packableKg', () => {
  it('multiplies pieces by each type capacity', () => {
    expect(packableKg([{ qty: 40, capacity_kg: 200 }, { qty: 3, capacity_kg: 25 }]))
      .toBe(8075);
  });

  it('stays exact on a capacity the binary floats cannot hold', () => {
    // 0.1-style drift is why chemical money math uses integer minor units; the
    // capacity column is NUMERIC(12,3), so thousandths are the unit here.
    expect(packableKg([{ qty: 3, capacity_kg: 0.1 }])).toBe(0.3);
    expect(packableKg([{ qty: 7, capacity_kg: 1.005 }])).toBe(7.035);
  });

  it('ignores missing rows and quantities', () => {
    expect(packableKg([])).toBe(0);
    expect(packableKg(null)).toBe(0);
    expect(packableKg([{ qty: null, capacity_kg: 200 }, { capacity_kg: null }])).toBe(0);
  });
});

describe('packagingTotals', () => {
  it('counts types, pieces and the packable aggregate', () => {
    const totals = packagingTotals([
      { qty: 40, capacity_kg: 200, reorder_level: 0 },
      { qty: 4, capacity_kg: 25, reorder_level: 10 },
      { qty: 0, capacity_kg: 30, reorder_level: 0 },
    ]);
    expect(totals).toEqual({
      types: 3, pieces: 44, low: 1, out: 1, packableKg: 8100,
    });
  });

  it('handles an empty register', () => {
    expect(packagingTotals([])).toEqual({
      types: 0, pieces: 0, low: 0, out: 0, packableKg: 0,
    });
  });
});

describe('buildMovementBody', () => {
  it('sends a physical count as an absolute number', () => {
    // The server owns the subtraction, so two clerks counting the same stack do
    // not lose one of the two edits the way the chemical adjust modal does.
    expect(buildMovementBody('COUNT', '37', { label: '50 kg drum' }))
      .toEqual({ label: '50 kg drum', kind: 'COUNT', counted_qty: 37 });
  });

  it('derives the sign of a movement from its kind', () => {
    expect(buildMovementBody('RECEIVED', '40', { label: '200 kg drum' }))
      .toEqual({ label: '200 kg drum', kind: 'RECEIVED', delta: 40 });
    expect(buildMovementBody('SCRAPPED', '3', { label: '200 kg drum' }))
      .toEqual({ label: '200 kg drum', kind: 'SCRAPPED', delta: -3 });
  });

  it('carries the reason through', () => {
    expect(buildMovementBody('ISSUED', '2', { label: '25 kg bag', reason: 'Line 2' }))
      .toEqual({ label: '25 kg bag', kind: 'ISSUED', delta: -2, reason: 'Line 2' });
  });

  it('names every kind the API accepts', () => {
    expect(MOVEMENT_KINDS.map((k) => k.value)).toEqual([
      'RECEIVED', 'RETURNED', 'ISSUED', 'DAMAGED', 'SCRAPPED', 'COUNT',
    ]);
  });
});
