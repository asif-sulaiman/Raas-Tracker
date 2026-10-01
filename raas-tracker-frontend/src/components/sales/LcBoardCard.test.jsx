// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import LcBoardCard from './LcBoardCard';

afterEach(() => cleanup());

const LC = {
  id: 10,
  lc_number: 'LC-100',
  company_name: 'Acme',
  stage: 'lc_received',
  lc_date: '2026-09-01',
  expiry_date: null,
  total_value: 3000,
  pis: [
    { id: 1, pi_number: 'PI-001', total_value: 1000, item_count: 2 },
    { id: 2, pi_number: 'PI-002', total_value: 2000, item_count: 3 },
  ],
};

describe('LcBoardCard nested PIs', () => {
  it('renders one nested row per PI', () => {
    render(<LcBoardCard lc={LC} />);
    expect(screen.getByText('LC-100')).toBeTruthy();
    expect(screen.getByLabelText('View PI PI-001')).toBeTruthy();
    expect(screen.getByLabelText('View PI PI-002')).toBeTruthy();
    expect(screen.getByText('Acme')).toBeTruthy();
  });

  it('routes unlink clicks to onUnlink, never onViewPI', () => {
    const onViewPI = vi.fn();
    const onUnlink = vi.fn();
    render(<LcBoardCard lc={LC} onViewPI={onViewPI} onUnlink={onUnlink} />);

    fireEvent.click(screen.getByLabelText('Unlink PI PI-001'));
    expect(onUnlink).toHaveBeenCalledTimes(1);
    expect(onUnlink.mock.calls[0][0]).toMatchObject({ id: 1, pi_number: 'PI-001' });
    expect(onViewPI).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText('View PI PI-002'));
    expect(onViewPI).toHaveBeenCalledTimes(1);
    expect(onViewPI.mock.calls[0][0]).toMatchObject({ id: 2 });
    expect(onUnlink).toHaveBeenCalledTimes(1);
  });

  it('F9 labels the roll-up as PI total from integer cents', () => {
    const centsLc = {
      id: 11,
      lc_number: 'LC-101',
      company_name: 'Acme',
      stage: 'lc_received',
      lc_date: '2026-09-01',
      pis: [
        { id: 1, pi_number: 'PI-001', total_value: 0.1, item_count: 1 },
        { id: 2, pi_number: 'PI-002', total_value: 0.2, item_count: 1 },
      ],
    };
    render(<LcBoardCard lc={centsLc} />);
    expect(screen.getByText(/PI total/i)).toBeTruthy();
    expect(screen.queryByText(/invoice total/i)).toBeNull();
  });
});
