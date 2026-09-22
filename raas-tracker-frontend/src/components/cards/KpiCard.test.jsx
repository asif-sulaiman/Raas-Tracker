// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import KpiCard from './KpiCard.jsx';

afterEach(() => cleanup());

describe('KpiCard trend footer', () => {
  it('renders no trend pill when trend is null', () => {
    render(<KpiCard title="Reconciliation" value="—" trend={null} trendLabel="latest check" />);
    expect(screen.queryByText('0%')).toBeNull();
    expect(screen.queryByText('latest check')).toBeTruthy();
  });

  it('renders no trend pill when trend is undefined', () => {
    render(<KpiCard title="Recipes" value="3" trendLabel="Active recipes" />);
    expect(screen.queryByText('0%')).toBeNull();
    expect(screen.queryByText('Active recipes')).toBeTruthy();
  });

  it('renders no trend pill when trend is the string neutral', () => {
    render(<KpiCard title="Active Stock" value="12" trend="neutral" trendLabel="Positive balances" />);
    expect(screen.queryByText('0%')).toBeNull();
  });

  it('renders the neutral 0% pill only for a numeric zero', () => {
    render(<KpiCard title="Reconciliation" value="92.1%" trend={0} trendLabel="vs previous check" />);
    expect(screen.queryByText('0%')).toBeTruthy();
    expect(screen.queryByText('vs previous check')).toBeTruthy();
  });

  it('renders an up pill for a positive number', () => {
    render(<KpiCard title="Reconciliation" value="95.0%" trend={2.5} trendLabel="vs previous check" />);
    expect(screen.queryByText('2.5%')).toBeTruthy();
    expect(screen.queryByText('vs previous check')).toBeTruthy();
  });

  it('renders a down pill for a negative number', () => {
    render(<KpiCard title="Reconciliation" value="90.0%" trend={-3.2} trendLabel="vs previous check" />);
    expect(screen.queryByText('3.2%')).toBeTruthy();
  });
});
