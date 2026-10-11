// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import InvoiceLineEditor from './InvoiceLineEditor';

afterEach(() => cleanup());

const LINES = [
  {
    sale_item_id: 1, product_name: 'Cotton', unit: 'KG',
    pi_quantity: 100, invoiced_quantity: 60, unit_price: 2.5,
  },
];

describe('InvoiceLineEditor guards', () => {
  it('allows quantity above the PI proposal (INV-REVISION over-PI)', async () => {
    const onAdd = vi.fn(async () => {});
    render(<InvoiceLineEditor lines={LINES} onAdd={onAdd} invoiceNumber="INV-1" />);

    // Reference is the variance, not a ceiling.
    expect(screen.getByText('40.00 KG under PI')).toBeTruthy();
    expect(screen.getByText('to INV-1')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Line quantity'), { target: { value: '50' } });
    const add = screen.getByRole('button', { name: 'Add line' });
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    expect(onAdd.mock.calls[0][0]).toEqual({ sale_item_id: 1, quantity: 50 });
  });

  it('renders a server failure in the alert slot', () => {
    render(
      <InvoiceLineEditor
        lines={LINES}
        onAdd={vi.fn(async () => {})}
        invoiceNumber="INV-1"
        serverError="invoice not found"
      />
    );
    expect(screen.getByRole('alert').textContent).toMatch(/invoice not found/);
  });

  it('posts a valid quantity through onAdd', async () => {
    const onAdd = vi.fn(async () => {});
    render(<InvoiceLineEditor lines={LINES} onAdd={onAdd} invoiceNumber="INV-1" />);

    fireEvent.change(screen.getByLabelText('Line quantity'), { target: { value: '10' } });
    const add = screen.getByRole('button', { name: 'Add line' });
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    expect(onAdd.mock.calls[0][0]).toEqual({ sale_item_id: 1, quantity: 10 });
  });

  it('blocks adding with no PI line or no quantity', () => {
    const onAdd = vi.fn(async () => {});
    render(<InvoiceLineEditor lines={LINES} onAdd={onAdd} />);
    const add = screen.getByRole('button', { name: 'Add line' });
    // Defaults to the first PI line, so only the quantity is missing.
    expect(add.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Line quantity'), { target: { value: '0' } });
    expect(add.disabled).toBe(true);
  });
});
