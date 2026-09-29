// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { ConfirmProvider } from '../../context/ConfirmContext';
import { toast } from 'sonner';
import InvoicePanel from './InvoicePanel';

vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), {
    error: vi.fn(), warning: vi.fn(), info: vi.fn(), success: vi.fn(),
  });
  return { toast: toastFn };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

const INVOICES = [
  {
    invoice_id: 7, invoice_number: 'INV-1', status: 'planned',
    approx_ship_date: null, actual_ship_date: null, notes: null,
    created_at: '2026-09-01 10:00:00',
    amount: null, paid_amount: 0, // legacy row — no amount to show
  },
  {
    invoice_id: 8, invoice_number: 'INV-2', status: 'booked',
    approx_ship_date: '2026-09-20', actual_ship_date: null, notes: null,
    created_at: '2026-09-02 10:00:00',
    amount: 1200, paid_amount: 800,
  },
  {
    invoice_id: 9, invoice_number: 'INV-3', status: 'paid',
    approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
    created_at: '2026-09-03 10:00:00',
    amount: 2400, paid_amount: 2400,
  },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ invoices = INVOICES, posts = [], completion, payStatus = 'paid', deleteStatus = 200, deleteBody = null } = {}) {
  const deletes = [];
  posts.deletes = deletes;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/invoices') && options && options.method === 'DELETE') {
      deletes.push({ url: u });
      if (deleteStatus >= 400) {
        return jsonResponse(deleteBody || { error: 'Could not void invoice' }, false, deleteStatus);
      }
      return jsonResponse({ success: true });
    }
    if (u.includes('/invoices') && options && options.method === 'POST') {
      posts.push({ url: u, body: JSON.parse(options.body) });
      if (u.endsWith('/book')) return jsonResponse({ success: true, status: 'booked' });
      if (u.endsWith('/ship')) return jsonResponse({ invoice_id: 7, shipment_id: 3, status: 'shipped' }, true, 201);
      if (u.endsWith('/pay')) return jsonResponse({ payment_id: 4, status: payStatus }, true, 201);
      return jsonResponse({ invoice_id: 10, invoice_number: 'NEW', status: 'planned' }, true, 201);
    }
    if (u.includes('/completion')) {
      if (completion) return jsonResponse(completion);
      const paid = invoices.filter((i) => i.status === 'paid').length;
      return jsonResponse({ status: paid > 0 ? 'partial' : 'none', paid, total: invoices.length, invoices });
    }
    if (u.includes('/invoices')) {
      return jsonResponse(invoices);
    }
    return jsonResponse({});
  }));
}

function renderPanel(opts = {}) {
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <InvoicePanel
            saleId={5}
            isAdmin={opts.isAdmin ?? true}
            totalValue={opts.totalValue ?? null}
            onChanged={opts.onChanged || vi.fn()}
          />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

function invoiceGetCount() {
  return globalThis.fetch.mock.calls.filter(
    ([u, o]) => String(u).includes('/invoices') && !(o && o.method && o.method !== 'GET')
  ).length;
}

describe('InvoicePanel', () => {
  it('lists invoices with status badges and the paid summary', async () => {
    renderPanel();
    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(screen.getByText('INV-2')).toBeTruthy();
    expect(screen.getByText('Planned')).toBeTruthy();
    expect(screen.getByText('Booked')).toBeTruthy();
    expect(screen.getByText('Paid')).toBeTruthy();
    expect(screen.getByText('1/3 invoices paid')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add invoice' })).toBeTruthy();
  });

  it('requires an approximate ship date before booking', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Book shipment' }));
    const dateInput = await screen.findByLabelText('Approximate ship date');
    expect(dateInput.value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Save booking' }));
    expect(await screen.findByText(/approximate ship date is required/i)).toBeTruthy();
    expect(posts.filter((p) => p.url.includes('/book'))).toHaveLength(0);
  });

  it('posts the book endpoint with the chosen date', async () => {
    const posts = [];
    const onChanged = vi.fn();
    renderPanel({ posts, onChanged });
    fireEvent.click(await screen.findByRole('button', { name: 'Book shipment' }));
    fireEvent.change(await screen.findByLabelText('Approximate ship date'), {
      target: { value: '2026-09-10' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save booking' }));
    await waitFor(() => expect(posts.filter((p) => p.url.includes('/book'))).toHaveLength(1));
    expect(posts.find((p) => p.url.includes('/book'))).toMatchObject({
      url: expect.stringContaining('/api/sales/5/invoices/7/book'),
      body: { approx_ship_date: '2026-09-10' },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('adds an invoice from the inline number input', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-3' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    await waitFor(() => expect(posts.filter((p) => p.body && p.body.invoice_number === 'INV-3')).toHaveLength(1));
    const post = posts.find((p) => p.body && p.body.invoice_number === 'INV-3');
    expect(post.url).toContain('/api/sales/5/invoices');
    // Amount is optional — blank means the server defaults it to the sale total.
    expect(post.body.amount).toBeUndefined();
  });

  it('shows amount and paid progress on each invoice row', async () => {
    renderPanel();
    expect(await screen.findByText('INV-2')).toBeTruthy();
    expect(screen.getByText('$1,200.00 · paid $800.00')).toBeTruthy();
    expect(screen.getByText('$2,400.00 · paid $2,400.00')).toBeTruthy();
    // Legacy row (amount null) shows no money line at all.
    const legacyRow = screen.getByText('INV-1').closest('div');
    expect(legacyRow.textContent).not.toContain('· paid');
    // Paid row carries a checkmark, unpaid row does not.
    expect(screen.getByText('$2,400.00 · paid $2,400.00').querySelector('svg')).toBeTruthy();
    expect(screen.getByText('$1,200.00 · paid $800.00').querySelector('svg')).toBeFalsy();
  });

  it('renders money totals in the completion header when the API provides them', async () => {
    renderPanel({
      completion: {
        status: 'partial', paid: 1, total: 3, invoices: INVOICES,
        total_amount: 2400, paid_amount: 800,
      },
    });
    expect(await screen.findByText('1/3 invoices paid · $800.00 of $2,400.00')).toBeTruthy();
  });

  it('offers the sale total as the Amount placeholder and sends a typed amount', async () => {
    const posts = [];
    renderPanel({ posts, totalValue: 2400 });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    const amountInput = await screen.findByLabelText('Amount');
    expect(amountInput.placeholder).toBe('$2,400.00');
    fireEvent.change(screen.getByLabelText('Invoice number'), { target: { value: 'INV-9' } });
    fireEvent.change(amountInput, { target: { value: '1234.50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    await waitFor(() => expect(posts.filter((p) => p.body && p.body.invoice_number === 'INV-9')).toHaveLength(1));
    expect(posts.find((p) => p.body.invoice_number === 'INV-9').body.amount).toBe(1234.5);
  });

  it('shows the remaining balance while recording a payment', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 11, invoice_number: 'INV-11', status: 'shipped',
      approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
      created_at: '2026-09-01 10:00:00',
      amount: 1200, paid_amount: 800,
    }];
    renderPanel({ invoices, posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Record payment' }));
    expect(await screen.findByText('Balance $400.00')).toBeTruthy();
    // Validation is unchanged: an empty payment amount is still rejected.
    fireEvent.click(screen.getByRole('button', { name: 'Save payment' }));
    expect(await screen.findByText(/payment amount greater than 0/i)).toBeTruthy();
    expect(posts.filter((p) => p.url.includes('/pay'))).toHaveLength(0);
  });

  it('records a partial payment without claiming the invoice is paid', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 11, invoice_number: 'INV-11', status: 'shipped',
      approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
      created_at: '2026-09-01 10:00:00',
      amount: 1200, paid_amount: 800,
    }];
    // A partial payment leaves the invoice short of paid.
    renderPanel({ invoices, posts, payStatus: 'shipped' });
    fireEvent.click(await screen.findByRole('button', { name: 'Record payment' }));
    fireEvent.change(await screen.findByLabelText('Payment amount'), { target: { value: '100' } });
    fireEvent.change(screen.getByLabelText('Payment date'), { target: { value: '2026-09-15' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save payment' }));
    await waitFor(() => expect(posts.filter((p) => p.url.includes('/pay'))).toHaveLength(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalledWith('Payment recorded');
    expect(toast.success).not.toHaveBeenCalledWith('Invoice paid');
  });

  it('announces paid only when the pay response says paid', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 11, invoice_number: 'INV-11', status: 'shipped',
      approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
      created_at: '2026-09-01 10:00:00',
      amount: 1200, paid_amount: 800,
    }];
    renderPanel({ invoices, posts, payStatus: 'paid' });
    fireEvent.click(await screen.findByRole('button', { name: 'Record payment' }));
    fireEvent.change(await screen.findByLabelText('Payment amount'), { target: { value: '400' } });
    fireEvent.change(screen.getByLabelText('Payment date'), { target: { value: '2026-09-15' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save payment' }));
    await waitFor(() => expect(posts.filter((p) => p.url.includes('/pay'))).toHaveLength(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invoice paid'));
  });

  it('voids an invoice after confirmation and refetches', async () => {
    const posts = [];
    const onChanged = vi.fn();
    renderPanel({ posts, onChanged });
    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(invoiceGetCount()).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: 'Void invoice INV-1' }));
    // The app confirm pattern names the destructive action.
    fireEvent.click(await screen.findByRole('button', { name: 'Void' }));

    await waitFor(() => expect(posts.deletes).toHaveLength(1));
    expect(posts.deletes[0].url).toContain('/api/sales/5/invoices/7');
    expect(toast.success).toHaveBeenCalledWith('Invoice voided');
    await waitFor(() => expect(invoiceGetCount()).toBeGreaterThan(1));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('toasts the server error when voiding fails', async () => {
    const posts = [];
    renderPanel({ posts, deleteStatus: 404, deleteBody: { error: 'Invoice not found for this sale' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Void invoice INV-1' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Void' }));

    await waitFor(() => expect(posts.deletes).toHaveLength(1));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Invoice not found for this sale'));
    expect(toast.success).not.toHaveBeenCalledWith('Invoice voided');
  });

  it('hides invoice actions (including void) for non-admins', async () => {
    renderPanel({ isAdmin: false });
    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add invoice' })).toBeNull();
    expect(screen.queryByRole('button', { name: /void invoice/i })).toBeNull();
  });
});
