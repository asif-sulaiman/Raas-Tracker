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
  it('blocks over-quantity before submit', () => {
    const onAdd = vi.fn(async () => {});
    render(<InvoiceLineEditor lines={LINES} onAdd={onAdd} />);

    expect(screen.getByText(/40\.00 KG remaining/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Invoice quantity'), { target: { value: '50' } });

    const add = screen.getByRole('button', { name: 'Add line' });
    expect(add.disabled).toBe(true);
    fireEvent.click(add);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('renders a server 409 in the alert slot', () => {
    render(
      <InvoiceLineEditor
        lines={LINES}
        onAdd={vi.fn(async () => {})}
        serverError="quantity 50 exceeds remaining 40 for this sale item"
      />
    );
    expect(screen.getByRole('alert').textContent).toMatch(/exceeds remaining 40/);
  });

  it('posts a valid quantity through onAdd', async () => {
    const onAdd = vi.fn(async () => {});
    render(<InvoiceLineEditor lines={LINES} onAdd={onAdd} />);

    fireEvent.change(screen.getByLabelText('Invoice quantity'), { target: { value: '10' } });
    const add = screen.getByRole('button', { name: 'Add line' });
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await waitFor(() => expect(onAdd).toHaveBeenCalledTimes(1));
    expect(onAdd.mock.calls[0][0]).toEqual({ sale_item_id: 1, quantity: 10 });
  });
});
