// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { ConfirmProvider } from '../../context/ConfirmContext';
import { toast } from 'sonner';
import InvoicePanel from './InvoicePanel';
import { todayLocalISO } from '../../utils/format';

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
    // 'produced' so the Book action is live: booking is gated on production,
    // and these cases exercise the booking form itself. The blocked case has
    // its own 'planned' invoice.
    invoice_id: 7, invoice_number: 'INV-1', status: 'produced',
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

// PI lines the sale proposes, as `/api/sales/<id>/invoice-lines` returns them:
// `invoiced_quantity` and `remaining` are informational and never cap a write.
const PI_LINES = [
  {
    sale_item_id: 1, product_name: 'Cotton', unit: 'KG',
    pi_quantity: 100, invoiced_quantity: 60, remaining: 40,
    unit_price: 2.5,
  },
  {
    sale_item_id: 2, product_name: 'Androma LO2', unit: 'KG',
    pi_quantity: 3000, invoiced_quantity: 3000, remaining: 0,
    unit_price: 0.56,
  },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ invoices = INVOICES, posts = [], completion, completionStatus = 200, payStatus = 'paid', deleteStatus = 200, deleteBody = null, failInvoicesAfterPost = false, failInvoicesAlways = false, createAmount = 2400, piLines = PI_LINES, hasLineLess = false, postFail = null } = {}) {
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
    if (u.includes('/invoice-lines') && method === 'GET') {
      // One row per PI line; the figures are informational only.
      return jsonResponse({ lines: piLines, has_line_less_invoices: hasLineLess });
    }
    if (u.includes('/invoices') && method === 'DELETE') {
      deletes.push({ url: u });
      if (deleteStatus >= 400) {
        return jsonResponse(deleteBody || { error: 'Could not void invoice' }, false, deleteStatus);
      }
      return jsonResponse({ success: true });
    }
    if (u.includes('/invoices') && method === 'POST') {
      if (postFail && u.includes(postFail.match)) {
        return jsonResponse(postFail.body || { error: 'Refused' }, false, postFail.status || 409);
      }
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
            readOnly={opts.readOnly ?? false}
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
    expect(screen.getByText('Produced')).toBeTruthy();
    expect(screen.getByText('Booked')).toBeTruthy();
    // Board vocabulary: a paid invoice reads "Completed".
    expect(screen.getByText('Completed')).toBeTruthy();
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

  it('refuses to save an invoice that has no line', async () => {
    // INV-REVISION: an invoice is a practical shipment document, so it must
    // carry its lines at creation. A line-less invoice used to be auto-seeded
    // from the PI remainder, which produced a $0 "paid" row with nothing moved.
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-3' },
    });

    const save = screen.getByRole('button', { name: 'Save invoice' });
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    expect(posts.filter((p) => p.body && p.body.invoice_number === 'INV-3')).toHaveLength(0);
  });

  it('posts the invoice number, its date, and its lines', async () => {
    // The date is defaulted to today; the operator may override it.
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-3' },
    });

    fireEvent.change(screen.getByLabelText(/invoice qty for Cotton/i), {
      target: { value: '40' },
    });

    const save = screen.getByRole('button', { name: 'Save invoice' });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);

    await waitFor(() => expect(posts.filter((p) => p.body && p.body.invoice_number === 'INV-3')).toHaveLength(1));
    const post = posts.find((p) => p.body && p.body.invoice_number === 'INV-3');
    expect(post.url).toContain('/api/sales/5/invoices');
    // Lines, never an amount: the money is derived server-side from the
    // server-owned unit price.
    expect(post.body).toMatchObject({
      invoice_number: 'INV-3',
      invoice_date: expect.stringMatching(/\d{4}-\d{2}-\d{2}/),
      lines: [{ sale_item_id: 1, quantity: 40 }],
    });
    expect(post.body).not.toHaveProperty('amount');
  });

  it('posts several lines whose quantities exceed what remains', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-4' },
    });

    // 100 against 40 remaining and 250 against 0 remaining: no cap stops them.
    fireEvent.change(screen.getByLabelText(/invoice qty for Cotton/i), {
      target: { value: '100' },
    });
    fireEvent.change(screen.getByLabelText(/invoice qty for Androma LO2/i), {
      target: { value: '250' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    await waitFor(() => expect(posts.filter((p) => p.body && p.body.invoice_number === 'INV-4')).toHaveLength(1));
    const post = posts.find((p) => p.body && p.body.invoice_number === 'INV-4');
    expect(post.body.lines).toEqual([
      { sale_item_id: 1, quantity: 100 },
      { sale_item_id: 2, quantity: 250 },
    ]);
  });

  it('shows Remaining as information only', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    // 100 proposed, 60 invoiced -> 40 remaining, under the PI qty cell.
    const cottonRow = (await screen.findByText('Cotton')).closest('tr');
    expect(cottonRow.textContent).toContain('Remaining 40.00 KG');
    // Line 2: 3,000 proposed, 3,000 invoiced -> nothing remains.
    const andromaRow = screen.getByText('Androma LO2').closest('tr');
    expect(andromaRow.textContent).toContain('Remaining 0.00 KG');
    // Typing subtracts from the server's remaining (40 - 25), not from the PI
    // quantity: the subline stays live while the input is open.
    fireEvent.change(screen.getByLabelText(/invoice qty for Cotton/i), {
      target: { value: '25' },
    });
    expect(cottonRow.textContent).toContain('Remaining 15.00 KG');
    // No variance wording, and no cap offered as a target.
    expect(screen.queryByText(/under PI|over PI|matches PI|ceiling/i)).toBeNull();
  });

  it('defaults the invoice date to today and posts number, date, and lines', async () => {
    const posts = [];
    const expected = todayLocalISO();
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    expect(await screen.findByLabelText('Invoice date')).toHaveValue(expected);
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-3' },
    });
    fireEvent.change(screen.getByLabelText(/invoice qty for Cotton/i), {
      target: { value: '25' },
    });
    const save = screen.getByRole('button', { name: 'Save invoice' });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
    await waitFor(() => expect(posts.filter((p) => p.body && p.body.invoice_number === 'INV-3')).toHaveLength(1));
    const post = posts.find((p) => p.body && p.body.invoice_number === 'INV-3');
    expect(post.body.invoice_date).toBe(expected);
    expect(post.body.lines).toEqual([{ sale_item_id: 1, quantity: 25 }]);
  });

  it('keeps Book visible but disabled, with a reason, until produced', async () => {
    // Production is mandatory before a ship is booked. The action stays on
    // screen so the prerequisite is discoverable instead of silently vanishing.
    const invoices = [{
      invoice_id: 21, invoice_number: 'INV-P', status: 'planned',
      approx_ship_date: null, actual_ship_date: null, notes: null,
      created_at: '2026-09-01 10:00:00', amount: 500, paid_amount: 0,
      can_book: false,
      book_blocked_reason: 'invoice not produced yet — run production from the recipe page for: Cotton',
    }];
    renderPanel({ invoices });

    const book = await screen.findByRole('button', { name: 'Book shipment' });
    expect(book.disabled).toBe(true);
    expect(book.getAttribute('title')).toMatch(/recipe/i);
    // The server's reason is shown verbatim, so it names the product.
    expect(screen.getByText(/run production from the recipe page for: Cotton/i)).toBeTruthy();
  });

  it('disables Book on a produced invoice the server still refuses', async () => {
    // The desync this closes: status says 'produced' (record_production flips
    // it on the FIRST linked run) but a sibling product is unbuilt. A
    // status-driven button offered an action that always 409'd.
    const invoices = [{
      invoice_id: 23, invoice_number: 'INV-PARTIAL', status: 'produced',
      approx_ship_date: null, actual_ship_date: null, notes: null,
      created_at: '2026-09-01 10:00:00', amount: 500, paid_amount: 0,
      can_book: false,
      book_blocked_reason: 'invoice not produced yet — run production from the recipe page for: Androma LO2',
    }];
    renderPanel({ invoices });

    const book = await screen.findByRole('button', { name: 'Book shipment' });
    expect(book.disabled).toBe(true);
    // The server's own reason is what the operator is told.
    expect(screen.getByText(/Androma LO2/)).toBeTruthy();
  });

  it('enables Book once the server reports it ready', async () => {
    const invoices = [{
      invoice_id: 22, invoice_number: 'INV-PR', status: 'produced',
      approx_ship_date: null, actual_ship_date: null, notes: null,
      created_at: '2026-09-01 10:00:00', amount: 500, paid_amount: 0,
      can_book: true, book_blocked_reason: null,
    }];
    renderPanel({ invoices });

    const book = await screen.findByRole('button', { name: 'Book shipment' });
    expect(book.disabled).toBe(false);
    expect(screen.queryByText(/recipe page/i)).toBeNull();
  });

  it('falls back to fail-closed when the payload has no can_book field', async () => {
    // Version skew: an older backend omits the flag. Only 'produced' may be
    // assumed bookable, matching the server gate.
    const invoices = [
      {
        invoice_id: 31, invoice_number: 'INV-SKEW-P', status: 'planned',
        approx_ship_date: null, actual_ship_date: null, notes: null,
        created_at: '2026-09-01 10:00:00', amount: 100, paid_amount: 0,
      },
      {
        invoice_id: 32, invoice_number: 'INV-SKEW-R', status: 'produced',
        approx_ship_date: null, actual_ship_date: null, notes: null,
        created_at: '2026-09-01 10:00:00', amount: 100, paid_amount: 0,
      },
    ];
    renderPanel({ invoices });

    const buttons = await screen.findAllByRole('button', { name: 'Book shipment' });
    expect(buttons).toHaveLength(2);
    expect(buttons[0].disabled).toBe(true);
    expect(buttons[1].disabled).toBe(false);
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

  it('offers no Amount input — the server derives money from the PI price', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    // The invoice amount is derived from the server-owned unit price on each
    // staged line, so there is nothing for the operator to type.
    expect(screen.queryByLabelText('Amount')).toBeNull();
    expect(screen.queryByLabelText('Payment amount')).toBeNull();
    expect(await screen.findByLabelText('Invoice date')).toBeTruthy();
  });

  it('reports the server-derived amount after creating an invoice', async () => {
    const posts = [];
    renderPanel({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), {
      target: { value: 'INV-9' },
    });
    fireEvent.change(screen.getByLabelText(/invoice qty for Cotton/i), {
      target: { value: '40' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    // The create response carries the amount the server derived from the lines.
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invoiced $2,400.00'));
  });

  it('falls back to a plain confirmation when the create response has no amount', async () => {
    renderPanel({ createAmount: null });
    fireEvent.click(await screen.findByRole('button', { name: 'Add invoice' }));
    fireEvent.change(await screen.findByLabelText('Invoice number'), { target: { value: 'INV-9' } });
    fireEvent.change(screen.getByLabelText(/invoice qty for Cotton/i), { target: { value: '40' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save invoice' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Invoice created'));
    expect(toast.success).not.toHaveBeenCalledWith(expect.stringContaining('Invoiced $'));
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

describe('InvoicePanel lifecycle order and read-only mode', () => {
  it('offers Ship only for a booked invoice and Pay only for a shipped one', async () => {
    const invoices = [
      {
        invoice_id: 41, invoice_number: 'INV-B', status: 'booked',
        approx_ship_date: '2026-09-20', actual_ship_date: null, notes: null,
        created_at: '2026-09-01 10:00:00', amount: 100, paid_amount: 0,
      },
      {
        invoice_id: 42, invoice_number: 'INV-S', status: 'shipped',
        approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
        created_at: '2026-09-01 10:00:00', amount: 200, paid_amount: 50,
      },
      {
        invoice_id: 43, invoice_number: 'INV-P', status: 'paid',
        approx_ship_date: '2026-09-01', actual_ship_date: '2026-09-02', notes: null,
        created_at: '2026-09-01 10:00:00', amount: 300, paid_amount: 300,
      },
    ];
    renderPanel({ invoices });

    await screen.findByText('INV-B');
    // One Ship button (the booked row), one Record payment (the shipped row).
    expect(screen.getAllByRole('button', { name: 'Ship' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Record payment' })).toHaveLength(1);
    // The booked row gets no Pay action: money cannot precede shipment.
    const bookedRow = screen.getByText('INV-B').closest('div');
    expect(within(bookedRow).queryByRole('button', { name: 'Record payment' })).toBeNull();
    // The shipped row is not offered Ship again.
    const shippedRow = screen.getByText('INV-S').closest('div');
    expect(within(shippedRow).queryByRole('button', { name: 'Ship' })).toBeNull();
  });

  it('surfaces the server 409 message when a step is refused', async () => {
    const invoices = [{
      invoice_id: 51, invoice_number: 'INV-409', status: 'booked',
      approx_ship_date: '2026-09-20', actual_ship_date: null, notes: null,
      created_at: '2026-09-01 10:00:00', amount: 100, paid_amount: 0,
    }];
    renderPanel({
      invoices,
      postFail: { match: '/ship', status: 409, body: { error: 'Invoice must be booked before it can ship' } },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Ship' }));
    fireEvent.change(await screen.findByLabelText('Actual ship date'), {
      target: { value: '2026-09-21' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save shipment' }));

    // The typed refusal is shown verbatim, not collapsed into a generic error.
    expect(await screen.findByText('Invoice must be booked before it can ship')).toBeTruthy();
  });

  it('freezes the existing invoices but lets an admin add one', async () => {
    renderPanel({ readOnly: true });

    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(screen.getByText('INV-2')).toBeTruthy();
    // Status and money stay readable on a completed sale.
    expect(screen.getByText('Booked')).toBeTruthy();
    expect(screen.getByText('$1,200.00 · paid $800.00')).toBeTruthy();
    // The badge names the one affordance left: adding an invoice reopens.
    expect(screen.getByText('read-only · add an invoice to reopen')).toBeTruthy();
    // Existing invoices may not be advanced or voided…
    expect(screen.queryByRole('button', { name: 'Book shipment' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ship' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Record payment' })).toBeNull();
    expect(screen.queryByRole('button', { name: /void invoice/i })).toBeNull();
    // …but a late invoice is how a completed PI is corrected.
    fireEvent.click(screen.getByRole('button', { name: 'Add invoice' }));
    expect(await screen.findByLabelText('Invoice number')).toBeTruthy();
  });

  it('does not offer Add invoice to a non-admin on a completed sale', async () => {
    renderPanel({ readOnly: true, isAdmin: false });

    expect(await screen.findByText('INV-1')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add invoice' })).toBeNull();
  });
});

describe('InvoicePanel invoice maturity', () => {
  it('prefills the maturity from a deferred LC and posts it with the shipment', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 61, invoice_number: 'INV-M1', status: 'booked',
      approx_ship_date: '2026-11-01', actual_ship_date: null, notes: null,
      created_at: '2026-10-01 10:00:00', amount: 100, paid_amount: 0,
      payment_terms: 'deferred', tenor_days: 90,
    }];
    renderPanel({ invoices, posts });

    fireEvent.click(await screen.findByRole('button', { name: 'Ship' }));
    fireEvent.change(await screen.findByLabelText('Actual ship date'), {
      target: { value: '2026-11-10' },
    });

    // Deferred 90 days from the actual ship date — leap-safe UTC arithmetic.
    expect(screen.getByLabelText('Maturity date')).toHaveValue('2027-02-08');

    fireEvent.click(screen.getByRole('button', { name: 'Save shipment' }));
    await waitFor(() => expect(posts.filter((p) => p.url.includes('/ship'))).toHaveLength(1));
    expect(posts.find((p) => p.url.includes('/ship')).body).toMatchObject({
      actual_ship_date: '2026-11-10',
      maturity_date: '2027-02-08',
    });
  });

  it('defaults maturity to the actual ship date on an at-sight LC', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 62, invoice_number: 'INV-M2', status: 'booked',
      approx_ship_date: '2026-11-01', actual_ship_date: null, notes: null,
      created_at: '2026-10-01 10:00:00', amount: 100, paid_amount: 0,
      payment_terms: 'at_sight', tenor_days: null,
    }];
    renderPanel({ invoices, posts });

    fireEvent.click(await screen.findByRole('button', { name: 'Ship' }));
    fireEvent.change(await screen.findByLabelText('Actual ship date'), {
      target: { value: '2026-11-10' },
    });
    expect(screen.getByLabelText('Maturity date')).toHaveValue('2026-11-10');

    fireEvent.click(screen.getByRole('button', { name: 'Save shipment' }));
    await waitFor(() => expect(posts.filter((p) => p.url.includes('/ship'))).toHaveLength(1));
    expect(posts.find((p) => p.url.includes('/ship')).body).toMatchObject({
      actual_ship_date: '2026-11-10',
      maturity_date: '2026-11-10',
    });
  });

  it('posts an operator-edited maturity override', async () => {
    const posts = [];
    const invoices = [{
      invoice_id: 63, invoice_number: 'INV-M3', status: 'booked',
      approx_ship_date: '2026-11-01', actual_ship_date: null, notes: null,
      created_at: '2026-10-01 10:00:00', amount: 100, paid_amount: 0,
      payment_terms: 'deferred', tenor_days: 90,
    }];
    renderPanel({ invoices, posts });

    fireEvent.click(await screen.findByRole('button', { name: 'Ship' }));
    fireEvent.change(await screen.findByLabelText('Actual ship date'), {
      target: { value: '2026-11-10' },
    });
    fireEvent.change(screen.getByLabelText('Maturity date'), {
      target: { value: '2027-03-15' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save shipment' }));

    await waitFor(() => expect(posts.filter((p) => p.url.includes('/ship'))).toHaveLength(1));
    expect(posts.find((p) => p.url.includes('/ship')).body.maturity_date).toBe('2027-03-15');
  });

  it('shows each shipped invoice maturity and flags an overdue one', async () => {
    const invoices = [
      {
        invoice_id: 71, invoice_number: 'INV-OD', status: 'shipped',
        approx_ship_date: '2025-12-01', actual_ship_date: '2025-12-02', notes: null,
        created_at: '2025-11-01 10:00:00', amount: 100, paid_amount: 0,
        maturity_date: '2026-01-01',
      },
      {
        invoice_id: 72, invoice_number: 'INV-OK', status: 'paid',
        approx_ship_date: '2026-09-01', actual_ship_date: '2026-09-02', notes: null,
        created_at: '2026-08-01 10:00:00', amount: 200, paid_amount: 200,
        maturity_date: '2027-01-01',
      },
    ];
    renderPanel({ invoices });

    const overdueRow = (await screen.findByText('INV-OD')).closest('div');
    expect(within(overdueRow).getByText(/matures/i)).toBeTruthy();
    expect(within(overdueRow).getByText('Overdue')).toBeTruthy();

    const paidRow = screen.getByText('INV-OK').closest('div');
    expect(within(paidRow).getByText(/matures/i)).toBeTruthy();
    expect(within(paidRow).queryByText('Overdue')).toBeNull();
  });
});
