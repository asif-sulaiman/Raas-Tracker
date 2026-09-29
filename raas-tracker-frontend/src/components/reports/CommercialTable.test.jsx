// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import CommercialTable from './CommercialTable.jsx';

afterEach(() => cleanup());

const mockDetailData = {
  rows: [
    {
      sale_id: 1,
      customer_name: 'Acme Corp',
      pi_number: 'PI-001',
      pi_date: '2025-01-15',
      lc_number: 'LC-001',
      lc_date: '2025-01-20',
      product_name: 'Chemical A',
      unit: 'KG',
      quantity: 1000,
      unit_price: 2.5,
      total_price: 2500,
      invoice_date: '2025-02-01',
      latest_ship_date: '2025-02-10',
      actual_ship_date: '2025-02-12',
      maturity_date: '2025-03-15',
      receive_date: '2025-03-10',
      received_amount: 2500,
      due_amount: 0,
      payment_status: 'Paid',
      payment_comment: 'Full payment received',
    },
    {
      sale_id: 2,
      customer_name: 'Beta Ltd',
      pi_number: 'PI-002',
      pi_date: '2025-02-01',
      lc_number: 'LC-002',
      lc_date: '2025-02-10',
      product_name: 'Chemical B',
      unit: 'L',
      quantity: 500,
      unit_price: 5.0,
      total_price: 2500,
      invoice_date: '2025-02-15',
      latest_ship_date: '2025-02-20',
      actual_ship_date: '2025-02-22',
      maturity_date: '2025-03-25',
      receive_date: null,
      received_amount: 0,
      due_amount: 2500,
      payment_status: 'Due',
      payment_comment: 'Awaiting payment',
    },
  ],
};

const mockGroupedData = {
  periods: [
    {
      period_start: '2025-01-01',
      period_end: '2025-01-31',
      kpis: {
        order_count: 2,
        gross_sales: 5000,
        received: 2500,
        due: 2500,
        overdue: 0,
      },
      items: [
        {
          sale_id: 1,
          customer_name: 'Acme Corp',
          pi_number: 'PI-001',
          pi_date: '2025-01-15',
          lc_number: 'LC-001',
          lc_date: '2025-01-20',
          product_name: 'Chemical A',
          unit: 'KG',
          quantity: 1000,
          unit_price: 2.5,
          total_price: 2500,
          invoice_date: '2025-02-01',
          latest_ship_date: '2025-02-10',
          actual_ship_date: '2025-02-12',
          maturity_date: '2025-03-15',
          receive_date: '2025-03-10',
          received_amount: 2500,
          due_amount: 0,
          payment_status: 'Paid',
          payment_comment: 'Full payment received',
        },
      ],
    },
    {
      period_start: '2025-02-01',
      period_end: '2025-02-28',
      kpis: {
        order_count: 1,
        gross_sales: 2500,
        received: 0,
        due: 2500,
        overdue: 0,
      },
      items: [
        {
          sale_id: 2,
          customer_name: 'Beta Ltd',
          pi_number: 'PI-002',
          pi_date: '2025-02-01',
          lc_number: 'LC-002',
          lc_date: '2025-02-10',
          product_name: 'Chemical B',
          unit: 'L',
          quantity: 500,
          unit_price: 5.0,
          total_price: 2500,
          invoice_date: '2025-02-15',
          latest_ship_date: '2025-02-20',
          actual_ship_date: '2025-02-22',
          maturity_date: '2025-03-25',
          receive_date: null,
          received_amount: 0,
          due_amount: 2500,
          payment_status: 'Due',
          payment_comment: 'Awaiting payment',
        },
      ],
    },
  ],
};

describe('CommercialTable - Detail Mode (groupBy=none)', () => {
  const onPageChange = vi.fn();

  it('renders detail table with correct headers', () => {
    render(<CommercialTable data={mockDetailData} groupBy="none" onPageChange={onPageChange} />);

    expect(screen.getByText('Customer')).toBeTruthy();
    expect(screen.getByText('PI No')).toBeTruthy();
    expect(screen.getByText('PI Date')).toBeTruthy();
    expect(screen.getByText('LC No')).toBeTruthy();
    expect(screen.getByText('LC Date')).toBeTruthy();
    expect(screen.getByText('Product')).toBeTruthy();
    expect(screen.getByText('Unit')).toBeTruthy();
    expect(screen.getByText('Qty')).toBeTruthy();
    expect(screen.getByText('Unit Price')).toBeTruthy();
    expect(screen.getByText('Total ($)')).toBeTruthy();
    expect(screen.getByText('Invoice Date')).toBeTruthy();
    expect(screen.getByText('Latest Ship')).toBeTruthy();
    expect(screen.getByText('Actual Ship')).toBeTruthy();
    expect(screen.getByText('Maturity')).toBeTruthy();
    expect(screen.getByText('Receive Date')).toBeTruthy();
    expect(screen.getByText('Received ($)')).toBeTruthy();
    expect(screen.getByText('Due ($)')).toBeTruthy();
    expect(screen.getByText('Status')).toBeTruthy();
    expect(screen.getByText('Comment')).toBeTruthy();
  });

  it('renders detail rows with correct data', () => {
    render(<CommercialTable data={mockDetailData} groupBy="none" onPageChange={onPageChange} />);

    expect(screen.getByText('Acme Corp')).toBeTruthy();
    expect(screen.getByText('PI-001')).toBeTruthy();
    expect(screen.getByText('PI-002')).toBeTruthy();
    expect(screen.getByText('Beta Ltd')).toBeTruthy();
    expect(screen.getByText('Chemical A')).toBeTruthy();
    expect(screen.getByText('Chemical B')).toBeTruthy();
  });

  it('renders payment status badges correctly', () => {
    render(<CommercialTable data={mockDetailData} groupBy="none" onPageChange={onPageChange} />);

    // First row: Paid -> success variant (green)
    // Second row: Due -> info variant (blue)
    const paidBadge = screen.getByText('Paid');
    const dueBadge = screen.getByText('Due');
    expect(paidBadge).toBeTruthy();
    expect(dueBadge).toBeTruthy();
  });

  it('shows pagination when totalRows > pageSize', () => {
    render(
      <CommercialTable
        data={mockDetailData}
        groupBy="none"
        onPageChange={onPageChange}
        currentPage={1}
        pageSize={1}
        totalRows={2}
      />
    );

    expect(screen.getByText(/Page 1 of 2/)).toBeTruthy();
    expect(screen.getByText(/2 rows/)).toBeTruthy();
  });

  it('calls onPageChange when next page button clicked', () => {
    render(
      <CommercialTable
        data={mockDetailData}
        groupBy="none"
        onPageChange={onPageChange}
        currentPage={1}
        pageSize={1}
        totalRows={2}
      />
    );

    const nextButton = screen.getByLabelText('Next page');
    fireEvent.click(nextButton);
    expect(onPageChange).toHaveBeenCalledWith(2);
  });

  it('disables previous button on first page', () => {
    render(
      <CommercialTable
        data={mockDetailData}
        groupBy="none"
        onPageChange={onPageChange}
        currentPage={1}
        pageSize={1}
        totalRows={2}
      />
    );

    const prevButton = screen.getByLabelText('Previous page');
    expect(prevButton.disabled).toBe(true);
  });

  it('disables next button on last page', () => {
    render(
      <CommercialTable
        data={mockDetailData}
        groupBy="none"
        onPageChange={onPageChange}
        currentPage={2}
        pageSize={1}
        totalRows={2}
      />
    );

    const nextButton = screen.getByLabelText('Next page');
    expect(nextButton.disabled).toBe(true);
  });

  it('shows skeleton loading when isLoading=true', () => {
    render(<CommercialTable data={mockDetailData} groupBy="none" onPageChange={onPageChange} isLoading />);

    // The skeleton rows use animate-pulse class
    const pulseRows = document.querySelectorAll('.animate-pulse');
    expect(pulseRows.length).toBeGreaterThan(0);
  });

  it('shows empty state when no rows', () => {
    render(<CommercialTable data={{ rows: [] }} groupBy="none" onPageChange={onPageChange} />);

    expect(screen.getByText('No commercial data found')).toBeTruthy();
    expect(screen.getByText('Adjust filters or check back later.')).toBeTruthy();
  });

  it('accepts direct array as data (not wrapped in rows)', () => {
    render(<CommercialTable data={mockDetailData.rows} groupBy="none" onPageChange={onPageChange} />);

    expect(screen.getByText('Acme Corp')).toBeTruthy();
  });
});

describe('CommercialTable - Grouped Mode (groupBy=month/week/year)', () => {
  const onPageChange = vi.fn();

  it('renders grouped table with correct headers', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} />);

    expect(screen.getByText('Period')).toBeTruthy();
    expect(screen.getByText('Orders')).toBeTruthy();
    expect(screen.getByText('Gross Sales')).toBeTruthy();
    expect(screen.getByText('Received')).toBeTruthy();
    expect(screen.getByText('Due')).toBeTruthy();
    expect(screen.getByText('Overdue')).toBeTruthy();
    expect(screen.getByText('Action')).toBeTruthy();
  });

  it('renders period rows with kpis', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} />);

    expect(screen.getByText('Jan 1, 2025 → Jan 31, 2025')).toBeTruthy();
    expect(screen.getByText('Feb 1, 2025 → Feb 28, 2025')).toBeTruthy();
    // order_count is "2" for first period, "1" for second
    expect(screen.getByText('2')).toBeTruthy(); // order_count for first period
    expect(screen.getByText('$5,000.00')).toBeTruthy(); // gross_sales (only once)
    expect(screen.getAllByText('$2,500.00').length).toBeGreaterThan(0); // received/due (multiple)
  });

  it('expands period when clicked', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} />);

    // First period should be collapsed initially (both periods have "Expand 1 items")
    const expandTexts = screen.getAllByText('Expand 1 items');
    expect(expandTexts.length).toBe(2);
    expect(screen.queryByText('Collapse 1 items')).toBeNull();

    // Click the first period row
    const firstPeriodRow = screen.getByText('Jan 1, 2025 → Jan 31, 2025').closest('tr');
    fireEvent.click(firstPeriodRow);

    // Now first period should be expanded
    expect(screen.getByText('Collapse 1 items')).toBeTruthy();
    // Detail rows should be visible
    expect(screen.getByText('Acme Corp')).toBeTruthy();
    expect(screen.getByText('PI-001')).toBeTruthy();
  });

  it('collapses period when clicked again', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} />);

    const firstPeriodRow = screen.getByText('Jan 1, 2025 → Jan 31, 2025').closest('tr');
    fireEvent.click(firstPeriodRow); // Expand
    expect(screen.getByText('Collapse 1 items')).toBeTruthy();

    fireEvent.click(firstPeriodRow); // Collapse
    // After collapsing, there should be 2 "Expand 1 items" again
    const expandTexts = screen.getAllByText('Expand 1 items');
    expect(expandTexts.length).toBe(2);
    expect(screen.queryByText('Acme Corp')).toBeNull();
  });

  it('shows multiple periods independently expandable', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} />);

    const firstPeriodRow = screen.getByText('Jan 1, 2025 → Jan 31, 2025').closest('tr');
    const secondPeriodRow = screen.getByText('Feb 1, 2025 → Feb 28, 2025').closest('tr');

    fireEvent.click(firstPeriodRow);
    expect(screen.getByText('Collapse 1 items')).toBeTruthy();
    expect(screen.getByText('Expand 1 items')).toBeTruthy(); // Second still collapsed

    fireEvent.click(secondPeriodRow);
    const collapseTexts = screen.getAllByText('Collapse 1 items');
    expect(collapseTexts).toHaveLength(2); // Both expanded
  });

  it('shows pagination for grouped mode when totalRows > pageSize', () => {
    render(
      <CommercialTable
        data={mockGroupedData}
        groupBy="month"
        onPageChange={onPageChange}
        currentPage={1}
        pageSize={1}
        totalRows={2}
      />
    );

    expect(screen.getByText(/Page 1 of 2/)).toBeTruthy();
  });

  it('shows skeleton loading for grouped mode when isLoading=true', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} isLoading />);

    const pulseRows = document.querySelectorAll('.animate-pulse');
    expect(pulseRows.length).toBeGreaterThan(0);
  });

  it('shows empty state when no periods', () => {
    render(<CommercialTable data={{ periods: [] }} groupBy="month" onPageChange={onPageChange} />);

    expect(screen.getByText('No commercial data found')).toBeTruthy();
  });

  it('accepts direct array as data (not wrapped in periods)', () => {
    render(<CommercialTable data={mockGroupedData.periods} groupBy="month" onPageChange={onPageChange} />);

    expect(screen.getByText('Jan 1, 2025 → Jan 31, 2025')).toBeTruthy();
  });

  it('formats currency with $ prefix and 2 decimals in grouped view', () => {
    render(<CommercialTable data={mockGroupedData} groupBy="month" onPageChange={onPageChange} />);

    expect(screen.getByText('$5,000.00')).toBeTruthy();
    expect(screen.getAllByText('$2,500.00').length).toBeGreaterThan(0);
  });

  it('shows overdue amount in red', () => {
    const dataWithOverdue = {
      periods: [
        {
          period_start: '2025-01-01',
          period_end: '2025-01-31',
          kpis: {
            order_count: 1,
            gross_sales: 1000,
            received: 0,
            due: 500,
            overdue: 500,
          },
          items: [],
        },
      ],
    };

    render(<CommercialTable data={dataWithOverdue} groupBy="month" onPageChange={onPageChange} />);

    const overdueCells = screen.getAllByText('$500.00');
    // The overdue cell should have text-rose-600 class (there are two: due and overdue)
    const overdueCell = overdueCells.find(cell => cell.closest('td')?.classList.contains('text-rose-600'));
    expect(overdueCell).toBeTruthy();
  });
});

describe('CommercialTable - Edge Cases', () => {
  const onPageChange = vi.fn();

  it('handles missing optional fields gracefully', () => {
    const sparseData = {
      rows: [
        {
          sale_id: 1,
          // All optional fields missing
        },
      ],
    };

    render(<CommercialTable data={sparseData} groupBy="none" onPageChange={onPageChange} />);

    // Should show dashes for missing fields (multiple dashes expected)
    const dashes = screen.getAllByText('-');
    expect(dashes.length).toBeGreaterThan(0);
  });

  it('handles null data gracefully', () => {
    render(<CommercialTable data={null} groupBy="none" onPageChange={onPageChange} />);

    expect(screen.getByText('No commercial data found')).toBeTruthy();
  });

  it('handles undefined data gracefully', () => {
    render(<CommercialTable data={undefined} groupBy="none" onPageChange={onPageChange} />);

    expect(screen.getByText('No commercial data found')).toBeTruthy();
  });
});