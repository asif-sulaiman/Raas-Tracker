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

function installFetch({ invoices = INVOICES, posts = [], completion, completionStatus = 200, payStatus = 'paid', deleteStatus = 200, deleteBody = null, failInvoicesAfterPost = false, failInvoicesAlways = false, createAmount = 2400 } = {}) {
  const deletes = [];
  let invoiceReadsFail = failInvoicesAfterPost || failInvoicesAlways;
  // Lets a test flip the invoice read back to healthy to exercise a retry.
  posts.failInvoices = (on) => { invoiceReadsFail = on; };
  posts.deletes = deletes;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/invoices') && method === 'DELETE') {
      deletes.push({ url: u });
      if (deleteStatus >= 400) {
        return jsonResponse(deleteBody || { error: 'Could not void invoice' }, false, deleteStatus);
      }
      return jsonResponse({ success: true });
    }
    if (u.includes('/invoices') && method === 'POST') {
      posts.push({ url: u, body: JSON.parse(options.body) });
      if (u.endsWith('/book')) return jsonResponse({ success: true, status: 'booked' });
      if (u.endsWith('/ship')) return jsonResponse({ invoice_id: 7, shipment_id: 3, status: 'shipped' }, true, 201);
      if (u.endsWith('/pay')) return jsonResponse({ payment_id: 4, status: payStatus }, true, 201);
      return jsonResponse({ invoice_id: 10, invoice_number: 'NEW', status: 'planned', amount: createAmount }, true, 201);
    }
    if (u.includes('/completion')) {
      if (completionStatus >= 400) {
        return jsonResponse({ error: 'Completion service unavailable' }, false, completionStatus);
      }
      if (completion) return jsonResponse(completion);
      const paid = invoices.filter((i) => i.status === 'paid').length;
      return jsonResponse({ status: paid > 0 ? 'partial' : 'none', paid, total: invoices.length, invoices });
    }
    if (u.includes('/invoices')) {
      // A transient failure of the read (start-up or post-mutation reload).
      if (invoiceReadsFail && (failInvoicesAlways || posts.length > 0)) {
        return jsonResponse({ error: 'Bad gateway' }, false, 502);
      }
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

  it('adds an invoice from the inline number input without posting an amount', async () => {
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
    // Money is server-derived from the remaining uninvoiced PI lines — the
    // body must never carry an amount of its own.
    expect(post.body).toEqual({ invoice_number: 'INV-3' });
    expect(post.body).not.toHaveProperty('amount');
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

  it('offers no Amount input and explains the server-derived amount instead', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    // The invoice amount is derived from the remaining uninvoiced PI lines, so
    // there is nothing for the operator to type.
    expect(screen.queryByLabelText('Amount')).toBeNull();
    expect(screen.queryByLabelText('Payment amount')).toBeNull();
    expect(
      await screen.findByText(/derived from the PI lines that are not yet invoiced/i)
    ).toBeTruthy();
  });

  it('reports the server-derived amount after creating an invoice', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-9' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    // The create response carries the amount the server derived from the PI lines.
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invoiced $2,400.00'));
  });

  it('falls back to a plain confirmation when the create response has no amount', async () => {
    renderPanel({ createAmount: null });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), { target: { value: 'INV-9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invoice added'));
    expect(toast.success).not.toHaveBeenCalledWith(expect.stringContaining('Invoiced $-'));
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

  it('still lists invoices when only the completion read fails', async () => {
    // The invoice rows are the source of truth; completion only adds the
    // money totals, so a 500 there must not blank the whole panel.
    renderPanel({ completionStatus: 500 });

    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(screen.getByText('INV-2')).toBeTruthy();
    expect(screen.getByText('INV-3')).toBeTruthy();
    // Counts fall back to the loaded rows; only the money chip is unavailable.
    expect(screen.getByText('1/3 invoices paid')).toBeTruthy();
    expect(screen.getByText(/money totals unavailable/i)).toBeTruthy();
    expect(screen.queryByText(/could not load invoices/i)).toBeNull();
  });

  it('still shows the rows and warns when the invoice read itself fails', async () => {
    renderPanel({ failInvoicesAlways: true });

    expect(await screen.findByText(/bad gateway/i)).toBeTruthy();
    expect(screen.queryByText('No invoices yet. Add an invoice number to track production and shipment.')).toBeNull();
  });

  it('warns that the list may be stale when the post-mutation reload fails', async () => {
    const posts = [];
    const onChanged = vi.fn();
    renderPanel({ posts, onChanged, failInvoicesAfterPost: true });
    expect(await screen.findByText('INV-1')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Book shipment' }));
    fireEvent.change(await screen.findByLabelText('Approximate ship date'), {
      target: { value: '2026-09-10' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save booking' }));

    await waitFor(() => expect(posts.filter((p) => p.url.includes('/book'))).toHaveLength(1));
    // The mutation's own success report survives.
    expect(toast.success).toHaveBeenCalledWith('Shipment booked');
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // ... but the rows below are now known to be out of date.
    expect(await screen.findByText(/saved, but could not refresh/i)).toBeTruthy();
    // The row is still on screen, only flagged.
    expect(screen.getByText('INV-1')).toBeTruthy();
    screen.getByRole('button', { name: 'Retry' });
  });

  it('clears the stale warning when the retry succeeds', async () => {
    const posts = [];
    renderPanel({ posts, failInvoicesAfterPost: true });
    expect(await screen.findByText('INV-1')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Book shipment' }));
    fireEvent.change(await screen.findByLabelText('Approximate ship date'), {
      target: { value: '2026-09-10' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save booking' }));
    expect(await screen.findByText(/saved, but could not refresh/i)).toBeTruthy();

    // The API recovers, so the retry repopulates the panel and drops the flag.
    posts.failInvoices(false);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/saved, but could not refresh/i)).toBeNull());
  });

  it('rounds a typed payment amount to the cent before posting', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 11, invoice_number: 'INV-11', status: 'shipped',
      approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
      created_at: '2026-09-01 10:00:00',
      amount: 1200, paid_amount: 800,
    }];
    renderPanel({ invoices, posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Record payment' }));
    fireEvent.change(await screen.findByLabelText('Payment amount'), { target: { value: '25.555' } });
    fireEvent.change(screen.getByLabelText('Payment date'), { target: { value: '2026-09-15' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save payment' }));

    await waitFor(() => expect(posts.filter((p) => p.url.includes('/pay'))).toHaveLength(1));
    // Money is exact — a raw float must never reach the API.
    expect(posts.find((p) => p.url.includes('/pay')).body.payment_amount).toBe(25.56);
  });
});
