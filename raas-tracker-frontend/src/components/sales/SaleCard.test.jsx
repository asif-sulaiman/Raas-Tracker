// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import SaleCard from './SaleCard';
import { todayLocalISO } from '../../utils/format';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const BOOKED = {
  invoice_id: 55, invoice_number: 'INV-55', status: 'booked',
  invoice_date: '2026-10-01', approx_ship_date: '2026-10-20',
  actual_ship_date: null, maturity_date: null,
  amount: 600, paid_amount: 0,
  payment_terms: 'at_sight', tenor_days: null,
};

const SHIPPED = {
  invoice_id: 56, invoice_number: 'INV-56', status: 'shipped',
  invoice_date: '2026-10-02', approx_ship_date: '2026-10-18',
  actual_ship_date: '2026-10-21', maturity_date: '2026-11-20',
  amount: 400, paid_amount: 100,
  payment_terms: 'deferred', tenor_days: 30,
};

function sale(overrides = {}) {
  return {
    id: 7,
    pi_number: 'PI-007',
    stage: 'shipment_ongoing',
    client_name: 'Acme',
    total_value: 1000,
    lc_number: 'LC-100',
    invoices: [BOOKED, SHIPPED],
    ...overrides,
  };
}

function renderCard(props = {}) {
  const handlers = {
    onInvoiceStep: vi.fn(),
    onManageInvoices: vi.fn(),
  };
  const utils = render(<SaleCard sale={sale(props.sale)} {...handlers} {...props} />);
  return { ...utils, ...handlers };
}

describe('SaleCard invoice visibility', () => {
  it('lists every invoice with its board label and money', () => {
    renderCard();
    expect(screen.getByText('INV-55')).toBeTruthy();
    expect(screen.getByText('INV-56')).toBeTruthy();
    // Board vocabulary: shipped reads "Payment due", paid reads "Completed".
    expect(screen.getByText('Payment due')).toBeTruthy();
    expect(screen.getByText('Booked')).toBeTruthy();
    expect(screen.getByText('$600.00')).toBeTruthy();
    expect(screen.getByText('$400.00')).toBeTruthy();
    // The shipped invoice shows the maturity the server computed on ship.
    expect(screen.getByText(/Matures Nov 20, 2026/)).toBeTruthy();
  });

  it('offers each invoice its own next step, not a sale-level move', () => {
    renderCard();
    expect(screen.getByRole('button', { name: 'Ship' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Record payment' })).toBeTruthy();
    // Shipping advances the sale automatically — no manual column move.
    expect(screen.queryByRole('button', { name: 'Move to Payment Due' })).toBeNull();
  });

  it('ships an invoice from the card with its actual date and maturity', () => {
    const { onInvoiceStep } = renderCard({
      sale: sale({
        invoices: [{ ...BOOKED, payment_terms: 'deferred', tenor_days: 30 }],
      }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ship' }));
    fireEvent.change(screen.getByLabelText('Actual ship date'), {
      target: { value: '2026-10-25' },
    });
    // Deferred terms are prefilled from the invoice summary, not guessed here.
    expect(screen.getByLabelText('Maturity date').value).toBe('2026-11-24');
    fireEvent.click(screen.getByRole('button', { name: 'Save shipment' }));
    expect(onInvoiceStep).toHaveBeenCalledWith(
      expect.objectContaining({ id: 7 }),
      expect.objectContaining({ invoice_id: 55 }),
      'ship',
      { actual_ship_date: '2026-10-25', maturity_date: '2026-11-24' },
    );
  });

  it('records a payment from the card against the shipped invoice', () => {
    const { onInvoiceStep } = renderCard();
    fireEvent.click(screen.getByRole('button', { name: 'Record payment' }));
    fireEvent.change(screen.getByLabelText('Payment amount'), {
      target: { value: '150' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save payment' }));
    expect(onInvoiceStep).toHaveBeenCalledWith(
      expect.objectContaining({ id: 7 }),
      expect.objectContaining({ invoice_id: 56 }),
      'pay',
      { payment_amount: 150, payment_date: todayLocalISO() },
    );
  });

  it('blocks booking an invoice that has not been produced yet', () => {
    renderCard({
      sale: {
        ...sale(),
        invoices: [{ ...BOOKED, status: 'planned', approx_ship_date: null }],
      },
    });
    const book = screen.getByRole('button', { name: 'Book shipment' });
    expect(book.disabled).toBe(true);
  });

  it('renders no invoice section when the sale has none', () => {
    renderCard({ sale: sale({ invoices: [], stage: 'lc_received' }) });
    expect(screen.queryByText(/Invoices \(/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ship' })).toBeNull();
  });

  it('labels a paid invoice "Completed" and offers no further step', () => {
    renderCard({
      sale: {
        ...sale(),
        stage: 'payment_due',
        invoices: [{ ...SHIPPED, status: 'paid', paid_amount: 400 }],
      },
    });
    expect(screen.getByText('Completed')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Record payment' })).toBeNull();
  });
});
