// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
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
});

const INVOICES = [
  {
    invoice_id: 7, invoice_number: 'INV-1', status: 'planned',
    approx_ship_date: null, actual_ship_date: null, notes: null,
    created_at: '2026-09-01 10:00:00',
  },
  {
    invoice_id: 8, invoice_number: 'INV-2', status: 'booked',
    approx_ship_date: '2026-09-20', actual_ship_date: null, notes: null,
    created_at: '2026-09-02 10:00:00',
  },
  {
    invoice_id: 9, invoice_number: 'INV-3', status: 'paid',
    approx_ship_date: '2026-09-10', actual_ship_date: '2026-09-12', notes: null,
    created_at: '2026-09-03 10:00:00',
  },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ invoices = INVOICES, posts = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/invoices') && options && options.method === 'POST') {
      posts.push({ url: u, body: JSON.parse(options.body) });
      if (u.endsWith('/book')) return jsonResponse({ success: true, status: 'booked' });
      if (u.endsWith('/ship')) return jsonResponse({ invoice_id: 7, shipment_id: 3, status: 'shipped' }, true, 201);
      if (u.endsWith('/pay')) return jsonResponse({ payment_id: 4, status: 'paid' }, true, 201);
      return jsonResponse({ invoice_id: 10, invoice_number: 'NEW', status: 'planned' }, true, 201);
    }
    if (u.includes('/completion')) {
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
        <InvoicePanel saleId={5} isAdmin onChanged={opts.onChanged || vi.fn()} />
      </AuthProvider>
    </MemoryRouter>
  );
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
    expect(posts.find((p) => p.body && p.body.invoice_number === 'INV-3').url)
      .toContain('/api/sales/5/invoices');
  });
});
