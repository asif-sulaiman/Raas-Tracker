// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { toast } from 'sonner';
import { AuthProvider } from '../../context/AuthContext';
import InvoicesRequiredModal from './InvoicesRequiredModal';

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

const PIS = [
  { id: 11, pi_number: 'PI-11' },
  { id: 22, pi_number: 'PI-22' },
];

// Per-sale budgets, keyed by sale id, as `/api/sales/<id>/invoice-lines`
// answers them. Only `remaining` and `sale_item_id` are read here: the barrier
// invoices the whole remainder, it does not re-derive quantities.
function budgetFor(saleId) {
  if (saleId === 11) {
    return {
      lines: [
        { sale_item_id: 201, product_name: 'Cotton', unit: 'KG', remaining: 5 },
        { sale_item_id: 202, product_name: 'Soda', unit: 'KG', remaining: 0 },
      ],
      has_line_less_invoices: false,
    };
  }
  return {
    lines: [{ sale_item_id: 301, product_name: 'Resin', unit: 'KG', remaining: 2.5 }],
    has_line_less_invoices: false,
  };
}

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch(posts, { failWith = null, failSaleIds = null, budgets = {}, budgetFails = false } = {}) {
  const budgetReads = [];
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    const budgetMatch = u.match(/\/api\/sales\/(\d+)\/invoice-lines/);
    if (budgetMatch && method === 'GET') {
      const saleId = Number(budgetMatch[1]);
      budgetReads.push(saleId);
      if (budgetFails) return jsonResponse({ error: 'Sale not found' }, false, 404);
      return jsonResponse(budgets[saleId] || budgetFor(saleId));
    }
    if (u.includes('/invoices') && method === 'POST') {
      posts.push({ url: u, body: JSON.parse(options.body) });
      const postSaleId = Number((u.match(/\/api\/sales\/(\d+)\/invoices/) || [])[1]);
      const shouldFail = failWith && (!failSaleIds || failSaleIds.includes(postSaleId));
      if (shouldFail) return jsonResponse(failWith, false, failWith.status || 400);
      return jsonResponse(
        { invoice_id: 100 + posts.length, status: 'planned', amount: 50 },
        true, 201,
      );
    }
    return jsonResponse({});
  }));
  return { budgetReads };
}

function openModal(props = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <InvoicesRequiredModal
          isOpen
          onClose={onClose}
          pis={PIS}
          onSuccess={onSuccess}
          {...props}
        />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { onClose, onSuccess };
}

describe('InvoicesRequiredModal (LC barrier, one-or-many invoices)', () => {
  it('renders one invoice row per PI of the LC', () => {
    openModal();
    expect(screen.getByText(/PI-11/)).toBeTruthy();
    expect(screen.getByText(/PI-22/)).toBeTruthy();
  });

  it('POSTs each filled row to its own PI with a date and its remaining lines', async () => {
    const posts = [];
    installFetch(posts);
    openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    expect(inputs).toHaveLength(2);
    fireEvent.change(inputs[0], { target: { value: 'INV-A' } });
    fireEvent.change(inputs[1], { target: { value: 'INV-B' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));

    await waitFor(() => {
      expect(posts).toHaveLength(2);
    });
    const urls = posts.map((p) => p.url);
    expect(urls.some((u) => u.includes('/api/sales/11/invoices'))).toBe(true);
    expect(urls.some((u) => u.includes('/api/sales/22/invoices'))).toBe(true);
    expect(urls.some((u) => u.includes('null'))).toBe(false);

    // A line-less create is refused by the API (400: lines required), and the
    // barrier must invoice what is left instead of inheriting it server-side.
    const first = posts.find((p) => p.url.includes('/api/sales/11/invoices'));
    expect(first.body).toEqual({
      invoice_number: 'INV-A',
      invoice_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      lines: [{ sale_item_id: 201, quantity: 5 }],
    });
    const second = posts.find((p) => p.url.includes('/api/sales/22/invoices'));
    expect(second.body.lines).toEqual([{ sale_item_id: 301, quantity: 2.5 }]);
  });

  it('creates a single invoice when only one row is filled', async () => {
    const posts = [];
    installFetch(posts);
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[1], { target: { value: 'INV-ONLY' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(posts).toHaveLength(1);
    });
    expect(posts[0].url).toContain('/api/sales/22/invoices');
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
    });
  });

  it('blocks submit with all rows empty and POSTs nothing', async () => {
    const posts = [];
    installFetch(posts);
    openModal();
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(screen.getByText(/at least one invoice number/i)).toBeTruthy();
    });
    expect(posts).toHaveLength(0);
  });

  it('surfaces the server error and does not call onSuccess on failure', async () => {
    const posts = [];
    installFetch(posts, { failWith: { error: 'duplicate invoice number', status: 400 } });
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-DUP' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(screen.getByText(/duplicate invoice number/i)).toBeTruthy();
    });
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('refuses a row whose PI has nothing left instead of posting a zero line', async () => {
    // The row the operator filled is already fully invoiced. Posting the
    // remainder would send quantity 0, which the writer rejects; dropping the
    // row silently would discard the typed number without saying so.
    const posts = [];
    installFetch(posts, {
      budgets: { 11: { lines: [{ sale_item_id: 201, remaining: 0 }], has_line_less_invoices: false } },
    });
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-EMPTY' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));

    expect(await screen.findByText(/nothing left to invoice on this pi/i)).toBeTruthy();
    expect(posts).toHaveLength(0);
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('never toasts success when no invoice was created', async () => {
    // Every filled PI is exhausted: the LC barrier would still be unsatisfied,
    // so claiming success and closing the modal sends the user straight back
    // into the same refusal.
    const posts = [];
    installFetch(posts, {
      budgets: {
        11: { lines: [{ sale_item_id: 201, remaining: 0 }], has_line_less_invoices: false },
        22: { lines: [{ sale_item_id: 301, remaining: 0 }], has_line_less_invoices: false },
      },
    });
    const { onSuccess, onClose } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-X' } });
    fireEvent.change(inputs[1], { target: { value: 'INV-Y' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));

    await waitFor(() => {
      expect(screen.getAllByText(/nothing left to invoice on this pi/i)).toHaveLength(2);
    });
    expect(posts).toHaveLength(0);
    expect(toast.success).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('reports a failed budget read per row and creates nothing', async () => {
    const posts = [];
    installFetch(posts, { budgetFails: true });
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-A' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));

    expect(await screen.findByText(/sale not found/i)).toBeTruthy();
    expect(posts).toHaveLength(0);
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('stays open and does not advance when only some rows were created', async () => {
    // onSuccess is what closes this modal and retries the LC move; the gate is
    // satisfied by any one invoice, so calling it here would move the LC while a
    // PI still has none - and the failed row's reason would never be read.
    const posts = [];
    installFetch(posts, {
      failWith: { error: 'duplicate invoice number', status: 400 },
      failSaleIds: [22],
    });
    const { onSuccess, onClose } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-A' } });
    fireEvent.change(inputs[1], { target: { value: 'INV-B' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));

    expect(await screen.findByText(/duplicate invoice number/i)).toBeTruthy();
    expect(posts).toHaveLength(2);
    // The successful row is reported, but the modal keeps the failed one.
    expect(screen.getByText(/1 invoice created/i)).toBeTruthy();
    expect(screen.getByDisplayValue('INV-B')).toBeTruthy();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('refuses a PI whose older invoice has no lines instead of re-billing it', async () => {
    // `remaining` cannot see a header-only invoice, so auto-invoicing it would
    // bill the same goods twice.
    const posts = [];
    installFetch(posts, {
      budgets: {
        11: {
          lines: [{ sale_item_id: 201, product_name: 'Cotton', remaining: 5 }],
          has_line_less_invoices: true,
        },
      },
    });
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-A' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));

    expect(await screen.findByText(/older invoice with no lines/i)).toBeTruthy();
    expect(posts).toHaveLength(0);
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
