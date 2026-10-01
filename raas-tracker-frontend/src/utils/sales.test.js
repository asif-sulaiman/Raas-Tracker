import { describe, it, expect } from 'vitest';
import { isOverdue, countOverdue, groupSalesByLc } from './sales.js';

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

describe('F4 numeric group id when LC meta missing', () => {
  it('parses numeric id from id:-prefixed keys, keeping key separate', () => {
    const sales = [
      { id: 1, lc_id: 10, lc_number: 'LC-100', company_id: 7, company_name: 'Acme', stage: 'lc_received', total_value: 100 },
    ];
    const { groups } = groupSalesByLc(sales, []);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('id:10');
    expect(groups[0].lc.id).toBe(10);
    expect(typeof groups[0].lc.id).toBe('number');
  });
});

describe('F6 null-wildcard must not merge across companies', () => {
  it('attaches no meta when sale.company_id is null', () => {
    const sales = [
      { id: 1, lc_number: 'LC-100', company_id: null, client_name: 'Acme', stage: 'pi_issued', total_value: 50 },
    ];
    const lcs = [{ id: 10, lc_number: 'LC-100', company_id: 7, company_name: 'Acme' }];
    const { groups } = groupSalesByLc(sales, lcs);
    expect(groups).toHaveLength(1);
    expect(groups[0].meta).toBeNull();
    expect(groups[0].lc._mirror).toBe(true);
  });

  it('does not share one LC meta across different companies', () => {
    const sales = [
      { id: 1, lc_number: 'LC-100', company_id: 7, company_name: 'Acme', stage: 'pi_issued', total_value: 100 },
      { id: 2, lc_number: 'LC-100', company_id: 8, company_name: 'Beta', stage: 'pi_issued', total_value: 200 },
    ];
    // Null-company LC row must not wildcard-match every company.
    const lcs = [{ id: 10, lc_number: 'LC-100', company_id: null, company_name: 'Acme' }];
    const { groups } = groupSalesByLc(sales, lcs);
    expect(groups).toHaveLength(2);
    for (const g of groups) {
      expect(g.meta).toBeNull();
      expect(g.lc._mirror).toBe(true);
    }
  });
});

describe('F9 LC roll-ups use integer cents', () => {
  it('sums PI totals exactly (0.1 + 0.2 = 0.3)', () => {
    const sales = [
      { id: 1, lc_id: 10, lc_number: 'LC-100', company_id: 7, stage: 'lc_received', total_value: 0.1 },
      { id: 2, lc_id: 10, lc_number: 'LC-100', company_id: 7, stage: 'lc_received', total_value: 0.2 },
    ];
    const { groups } = groupSalesByLc(sales, [{ id: 10, lc_number: 'LC-100', company_id: 7 }]);
    expect(groups[0].lc.total_value).toBe(0.3);
  });
});
