// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SalesPipelineOverview from './SalesPipelineOverview.jsx';

afterEach(() => cleanup());

const summary = {
  pi_issued: { count: 2, value: 12000 },
  lc_received: { count: 1, value: 5000 },
  shipment_ongoing: { count: 1, value: 8000 },
  payment_due: { count: 1, value: 3000 },
  completed: { count: 2, value: 20000 },
  total_pipeline_value: 48000,
  total_sales: 7,
};

const sales = [
  { id: 1, pi_number: 'PI-001', client_name: 'Acme', total_value: 7000, stage: 'pi_issued', pi_date: '2026-09-01' },
  { id: 2, pi_number: 'PI-002', client_name: 'Globex', total_value: 5000, stage: 'lc_received', pi_date: '2026-09-05' },
  { id: 3, pi_number: 'PI-003', client_name: 'Initech', total_value: 8000, stage: 'completed', pi_date: '2026-08-20' },
];

function renderHero(props = {}) {
  return render(
    <MemoryRouter>
      <SalesPipelineOverview
        sales={sales}
        salesSummary={summary}
        visibleSales={sales}
        salesExpanded={false}
        onToggleExpanded={() => {}}
        overdueCount={0}
        recentDefault={5}
        recentMax={20}
        {...props}
      />
    </MemoryRouter>
  );
}

describe('SalesPipelineOverview hero', () => {
  it('renders the heading with the View all link to /sales', () => {
    renderHero();
    expect(screen.getByRole('heading', { name: 'Sales Pipeline Overview' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /view all/i }).getAttribute('href')).toBe('/sales');
  });

  it('renders the five stages in pipeline order with counts and values', () => {
    renderHero();
    const list = screen.getByRole('list');
    const items = list.querySelectorAll(':scope > li');
    expect(items.length).toBe(5);
    expect(items[0].textContent).toMatch(/PI Issued/);
    expect(items[4].textContent).toMatch(/Completed/);
    expect(screen.getByLabelText(/PI Issued: 2 sales/)).toBeTruthy();
    expect(screen.getByLabelText(/Completed: 2 sales/)).toBeTruthy();
  });

  it('keeps the pipeline totals tied to the flow', () => {
    renderHero();
    expect(screen.getByText('Total pipeline value')).toBeTruthy();
    expect(screen.getByText('$48,000')).toBeTruthy();
    expect(screen.getByText('Total sales')).toBeTruthy();
  });

  it('shows the overdue badge when overdueCount is set', () => {
    renderHero({ overdueCount: 2 });
    expect(screen.getByText('2 overdue')).toBeTruthy();
  });

  it('renders recent-sales rows with stage badges', () => {
    renderHero();
    expect(screen.getByText('PI-001')).toBeTruthy();
    expect(screen.getByText('Acme')).toBeTruthy();
    expect(screen.getByText('Recent sales')).toBeTruthy();
  });

  it('shows the empty-state CTA when there are no sales', () => {
    renderHero({ sales: [], salesSummary: null, visibleSales: [] });
    expect(screen.getByText('No sales yet')).toBeTruthy();
    expect(screen.getByRole('link', { name: /go to sales pipeline/i })).toBeTruthy();
    expect(screen.queryByText('Recent sales')).toBeNull();
  });

  it('offers expand/collapse past the recent default', () => {
    const many = Array.from({ length: 7 }, (_, i) => ({
      id: i + 1,
      pi_number: `PI-00${i + 1}`,
      client_name: 'Acme',
      total_value: 1000,
      stage: 'pi_issued',
      pi_date: '2026-09-01',
    }));
    const onToggleExpanded = vi.fn();
    render(
      <MemoryRouter>
        <SalesPipelineOverview
          sales={many}
          salesSummary={summary}
          visibleSales={many.slice(0, 5)}
          salesExpanded={false}
          onToggleExpanded={onToggleExpanded}
          overdueCount={0}
          recentDefault={5}
          recentMax={20}
        />
      </MemoryRouter>
    );
    const expandButton = screen.getByRole('button', { name: /show 2 more/i });
    fireEvent.click(expandButton);
    expect(onToggleExpanded).toHaveBeenCalled();
  });

  it('F5b keeps one row per visible sale in input order', () => {
    const ordered = [
      { id: 1, pi_number: 'PI-U1', client_name: 'Solo', total_value: 100, stage: 'pi_issued', pi_date: '2026-09-01' },
      { id: 2, pi_number: 'PI-G1', client_name: 'Acme', company_id: 7, company_name: 'Acme', lc_id: 10, lc_number: 'LC-100', total_value: 200, stage: 'pi_issued', pi_date: '2026-09-02' },
      { id: 3, pi_number: 'PI-U2', client_name: 'Solo2', total_value: 300, stage: 'pi_issued', pi_date: '2026-09-03' },
    ];
    renderHero({ sales: ordered, salesSummary: null, visibleSales: ordered });
    const table = document.querySelector('table tbody');
    const text = table.textContent;
    const u1 = text.indexOf('PI-U1');
    const g1 = text.indexOf('PI-G1');
    const u2 = text.indexOf('PI-U2');
    expect(u1).toBeGreaterThanOrEqual(0);
    expect(g1).toBeGreaterThan(u1);
    expect(u2).toBeGreaterThan(g1);
  });

  it('F5c shows LC number in its own column, never in the PI Number column', () => {
    const ordered = [
      { id: 2, pi_number: 'PI-G1', client_name: 'Acme', company_id: 7, company_name: 'Acme', lc_id: 10, lc_number: 'LC-100', total_value: 200, stage: 'pi_issued', pi_date: '2026-09-02' },
    ];
    renderHero({ sales: ordered, salesSummary: null, visibleSales: ordered });
    expect(screen.getByText('LC Number')).toBeTruthy();
    const piCell = screen.getByText('PI-G1');
    expect(piCell.textContent).toBe('PI-G1');
    expect(screen.getByText('LC-100')).toBeTruthy();
    // No more LC header row duplicating LC-100 outside its own column
    expect(screen.queryByText(/PI total \(visible\)/i)).toBeNull();
  });
});
