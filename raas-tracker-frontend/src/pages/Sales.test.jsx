// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import Sales from './Sales';
import { toast } from 'sonner';

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

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function sale(overrides = {}) {
  return {
    id: 1,
    pi_number: 'PI-001',
    stage: 'lc_received',
    lc_id: 10,
    lc_number: 'LC-100',
    company_id: 1,
    company_name: 'Acme',
    client_name: 'Acme',
    total_value: 1000,
    ...overrides,
  };
}

function lcMeta(overrides = {}) {
  return {
    id: 10,
    lc_number: 'LC-100',
    company_id: 1,
    company_name: 'Acme',
    stage: 'lc_received',
    lc_date: '2026-09-01',
    expiry_date: null,
    ...overrides,
  };
}

function installFetch({ sales = [], lcs = [], readiness = null, invoices = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/lcs/') && u.includes('/readiness')) {
      return jsonResponse(readiness || {
        invoices_ok: true, recipes_ok: true, produced_ok: true, booked_ok: true, detail: {},
      });
    }
    if (u.includes('/api/lcs') && method === 'GET') {
      return jsonResponse(lcs);
    }
    if (u.includes('/api/sales/summary')) {
      return jsonResponse({ total_pipeline_value: 0, total_sales: sales.length });
    }
    if (u.includes('/invoices') && method === 'GET') {
      return jsonResponse(invoices);
    }
    // The detail modal reads one sale; keep it renderable so opening it from
    // the board is observable without a crash.
    if (/\/api\/sales\/\d+$/.test(u) && method === 'GET') {
      return jsonResponse({
        id: 7, stage: 'payment_due', pi_number: 'PI-007', client_name: 'Acme',
        items: [], history: [], payments: [], shipments: [], invoices: [],
        invoice_total: 0, total_paid: 0, balance: 0,
      });
    }
    if (u.includes('/api/sales') && method === 'GET') {
      return jsonResponse({ sales, total: sales.length });
    }
    return jsonResponse({});
  }));
}

function fetchCalls() {
  return fetch.mock.calls.map(([url, options]) => ({
    url: String(url),
    method: options?.method || 'GET',
    body: options?.body,
  }));
}

function postTo(url) {
  return fetchCalls().find((c) => c.method === 'POST' && c.url === url);
}

function renderPage() {
  render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <Sales />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('Sales board LC action honesty (INV-link Phase B)', () => {
  it('offers no move action on an LC card sitting in PI Issued', async () => {
    installFetch({ sales: [sale({ stage: 'pi_issued' })], lcs: [lcMeta()] });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    // The LC (stage lc_received) renders under its earliest PI in PI Issued.
    expect(screen.getByText('LC-100')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Enter LC' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Start Shipment' })).toBeNull();
  });

  it('starts an LC shipment directly, with no readiness probe or invoice barrier', async () => {
    installFetch({
      sales: [sale({ stage: 'lc_received' })],
      lcs: [lcMeta()],
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start Shipment' }));
    await waitFor(() => {
      expect(postTo('/api/lcs/10/move')).toBeTruthy();
    });
    expect(JSON.parse(postTo('/api/lcs/10/move').body)).toEqual({
      new_stage: 'shipment_ongoing',
    });
    expect(fetchCalls().some((c) => c.url.includes('/api/lcs/10/readiness'))).toBe(false);
    expect(screen.queryByText('Invoices Required')).toBeNull();
  });

  it('starts a standalone sale shipment without fetching invoices or warning about one', async () => {
    installFetch({
      sales: [sale({ id: 7, stage: 'lc_received', lc_id: null, lc_number: null })],
      // The removed reminder probed this endpoint and warned on an empty list;
      // keeping it empty makes a regression show up as the toast, not just a
      // stray request.
      invoices: [],
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start Shipment' }));
    await waitFor(() => {
      expect(postTo('/api/sales/7/move')).toBeTruthy();
    });
    expect(fetchCalls().some((c) => c.url === '/api/sales/7/invoices')).toBe(false);
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it('blocks the LC Payment Due move until an invoice is booked', async () => {
    installFetch({
      sales: [sale({ stage: 'shipment_ongoing' })],
      lcs: [lcMeta({ stage: 'shipment_ongoing' })],
      readiness: {
        invoices_ok: false,
        recipes_ok: true,
        produced_ok: true,
        booked_ok: false,
        detail: {},
      },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Move to Payment Due' }));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalled();
    });
    expect(String(toast.error.mock.calls[0][0])).toMatch(/book/i);
    expect(postTo('/api/lcs/10/move')).toBeFalsy();
  });

  it('moves the LC to Payment Due once one invoice is booked', async () => {
    installFetch({
      sales: [sale({ stage: 'shipment_ongoing' })],
      lcs: [lcMeta({ stage: 'shipment_ongoing' })],
      readiness: {
        invoices_ok: true, recipes_ok: false, produced_ok: false,
        booked_ok: true, detail: {},
      },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Move to Payment Due' }));
    await waitFor(() => {
      expect(postTo('/api/lcs/10/move')).toBeTruthy();
    });
    expect(JSON.parse(postTo('/api/lcs/10/move').body)).toEqual({
      new_stage: 'payment_due',
    });
  });

  it('keeps the shipment-column LC action intact', async () => {
    installFetch({
      sales: [sale({ stage: 'shipment_ongoing' })],
      lcs: [lcMeta({ stage: 'shipment_ongoing' })],
      readiness: { booked_ok: false, recipes_ok: false, produced_ok: false, detail: {} },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    expect(screen.getByRole('button', { name: 'Move to Payment Due' })).toBeTruthy();
  });
});

describe('Sales board invoice-first stages', () => {
  async function settle() {
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
  }

  it('renders four board columns — the Completed column is archived', async () => {
    installFetch({ sales: [sale({ stage: 'completed' })] });
    renderPage();
    await settle();
    // Scope to the board: the KPI strip above also renders h3 values.
    const board = screen.getByRole('region', { name: 'Sales pipeline board' });
    const headings = within(board)
      .getAllByRole('heading', { level: 3 })
      .map((h) => h.textContent.trim());
    expect(headings).toEqual([
      'PI Issued', 'LC Received', 'Shipment Ongoing', 'Payment Due',
    ]);
    // A completed sale that leaks into the payload must not reappear on the board.
    expect(screen.queryByText('PI-001')).toBeNull();
  });

  it('offers "Move to Payment Due" on a Shipment Ongoing card', async () => {
    installFetch({
      sales: [sale({ id: 7, stage: 'shipment_ongoing', lc_id: null, lc_number: null })],
    });
    renderPage();
    await settle();
    expect(screen.queryByRole('button', { name: 'Mark Shipped' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Move to Payment Due' }));
    await waitFor(() => {
      expect(postTo('/api/sales/7/move')).toBeTruthy();
    });
  });

  it('opens the sale editor from a Payment Due card instead of a payment form', async () => {
    installFetch({
      sales: [sale({ id: 7, stage: 'payment_due', lc_id: null, lc_number: null })],
    });
    renderPage();
    await settle();
    // The legacy sale-level payment form is no longer the Payment Due action.
    expect(screen.queryByRole('button', { name: 'Record Payment' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Invoices' }));
    await waitFor(() => {
      expect(fetchCalls().some((c) => c.url === '/api/sales/7')).toBe(true);
    });
    // Opening the editor is not a stage move.
    expect(postTo('/api/sales/7/move')).toBeFalsy();
  });

  it('opens the first PI editor from a Payment Due LC card', async () => {
    installFetch({
      sales: [sale({ id: 7, stage: 'payment_due' })],
      lcs: [lcMeta({ stage: 'payment_due' })],
    });
    renderPage();
    await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Manage Invoices' }));
    await waitFor(() => {
      expect(fetchCalls().some((c) => c.url === '/api/sales/7')).toBe(true);
    });
    expect(postTo('/api/lcs/10/move')).toBeFalsy();
  });
});
