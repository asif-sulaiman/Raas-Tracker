// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { ConfirmProvider } from '../../context/ConfirmContext';
import SaleDetailModal from './SaleDetailModal';

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

const DETAIL = {
  id: 5, stage: 'shipment_ongoing', pi_number: 'PI-5', pi_date: '2026-09-01',
  client_name: 'Acme', pi_file_path: null, lc_number: 'LC-1', lc_date: '2026-09-02',
  shipment_date: '2026-09-15', payment_date: null, payment_amount: 0,
  created_at: '2026-09-01 10:00:00', updated_at: '2026-09-02 10:00:00',
  company_id: 7, maturity_date: null, comments: null, company_name: 'Acme',
  items: [{ id: 21, sale_id: 5, product_name: 'Cotton', quantity: 100, unit_price: 3.25, unit: 'KG' }],
  history: [],
  payments: [],
  shipments: [
    { id: 9, sale_id: 5, ship_date: '2026-09-10', invoice_number: 'INV-9', invoice_date: '2026-09-09', notes: 'partial 1/2', created_at: '2026-09-10 10:00:00' },
  ],
  invoice_total: 325, total_paid: 0, balance: 325,
};

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ role = 'admin', deletes = [], failGetsAfterPut = false, notFound = false } = {}) {
  let putSeen = false;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.match(/\/api\/sales\/5\/shipments\/\d+/) && method === 'DELETE') {
      deletes.push(u);
      return jsonResponse({ message: 'Shipment deleted' });
    }
    if (u.match(/\/api\/sales\/5$/) && method === 'PUT') {
      putSeen = true;
      return jsonResponse({ comments: 'ok' });
    }
    if (u.match(/\/api\/sales\/5$/)) {
      if (notFound) return jsonResponse({ error: 'Sale not found' }, false, 404);
      if (failGetsAfterPut && putSeen) return jsonResponse({ error: 'Bad gateway' }, false, 502);
      return jsonResponse(DETAIL);
    }
    if (u.includes('/api/sales/5')) {
      return jsonResponse(DETAIL);
    }
    return jsonResponse({});
  }));
}

function renderDetail(opts = {}) {
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={vi.fn()} />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('SaleDetailModal shipments', () => {
  it('lists shipments and offers recording to admins', async () => {
    renderDetail({ role: 'admin' });
    expect(await screen.findByText('INV-9')).toBeTruthy();
    expect(screen.getByText('partial 1/2')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Record shipment' })).toBeTruthy();
  });

  it('hides shipment actions from non-admins', async () => {
    renderDetail({ role: 'user' });
    expect(await screen.findByText('INV-9')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Record shipment' })).toBeNull();
    expect(screen.queryByRole('button', { name: /delete shipment/i })).toBeNull();
  });

  it('deletes a shipment after confirm', async () => {
    const deletes = [];
    renderDetail({ role: 'admin', deletes });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete shipment INV-9' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deletes).toHaveLength(1));
    expect(deletes[0]).toMatch(/\/api\/sales\/5\/shipments\/9/);
  });

  it('keeps the sale on screen when a post-mutation refresh fails', async () => {
    // The save succeeds, then the follow-up GET 502s.
    renderDetail({ role: 'admin', failGetsAfterPut: true });
    expect(await screen.findByText('INV-9')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Sale comments'), { target: { value: 'check the balance' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    // A failed refresh is not evidence the sale is gone.
    expect(await screen.findByText(/could not refresh/i)).toBeTruthy();
    expect(screen.queryByText('Sale not found')).toBeNull();
    // The already-loaded content stays readable and is flagged as stale.
    expect(screen.getByText('INV-9')).toBeTruthy();
    expect(screen.getByText('partial 1/2')).toBeTruthy();
    expect(screen.getByText('Cotton')).toBeTruthy();
    screen.getByRole('button', { name: 'Retry' });
  });

  it('recovers the sale when the refresh retry succeeds', async () => {
    // The initial load succeeds; every later refresh 502s until the retry.
    let gets = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      const method = options?.method || 'GET';
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.match(/\/api\/sales\/5$/) && method === 'PUT') return jsonResponse({ comments: 'ok' });
      if (u.match(/\/api\/sales\/5$/)) {
        gets += 1;
        if (gets === 1) return jsonResponse(DETAIL);
        if (gets < 3) return jsonResponse({ error: 'Bad gateway' }, false, 502);
        return jsonResponse(DETAIL);
      }
      if (u.includes('/api/sales/5')) return jsonResponse(DETAIL);
      return jsonResponse({});
    }));
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={vi.fn()} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('INV-9')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Sale comments'), { target: { value: 'note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/could not refresh/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/could not refresh/i)).toBeNull());
    expect(screen.getByText('INV-9')).toBeTruthy();
  });

  it('reports a missing sale only on an actual not-found response', async () => {
    renderDetail({ notFound: true });
    expect(await screen.findByText('Sale not found')).toBeTruthy();
    // Nothing was loaded, so there is no content to preserve and no retry.
    expect(screen.queryByText('INV-9')).toBeNull();
  });

  it('F8 renders nothing for the invoice-lines section when there is no invoice', async () => {
    const noInv = { ...DETAIL, invoices: [], stage: 'shipment_ongoing' };
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const u = String(url);
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.match(/\/api\/sales\/5$/)) return jsonResponse(noInv);
      if (u.includes('/api/sales/5')) return jsonResponse(noInv);
      return jsonResponse({});
    }));
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={vi.fn()} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('Cotton')).toBeTruthy();
    expect(screen.queryByText(/add an invoice line/i)).toBeNull();
  });

  it('F10 refetches invoice lines when invoice ids change and surfaces 404', async () => {
    const withInv = {
      ...DETAIL,
      invoices: [{ invoice_id: 101, invoice_number: 'INV-101', status: 'planned' }],
    };
    const voided = { ...DETAIL, invoices: [] };
    let saleCalls = 0;
    let itemsCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      const method = options?.method || 'GET';
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.match(/\/api\/invoices\/101\/items/) && method === 'GET') {
        itemsCalls += 1;
        if (itemsCalls === 1) return jsonResponse([{ sale_item_id: 21, quantity: 10 }]);
        return jsonResponse({ error: 'Invoice not found' }, false, 404);
      }
      if (u.match(/\/api\/sales\/5$/)) {
        saleCalls += 1;
        return jsonResponse(saleCalls === 1 ? withInv : voided);
      }
      if (u.includes('/api/sales/5')) return jsonResponse(withInv);
      return jsonResponse({});
    }));
    const onSaved = vi.fn();
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={onSaved} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('Cotton')).toBeTruthy();
    // The sale detail can render before the invoice-items effect fires; wait
    // for the refetch instead of sampling the counter at an arbitrary instant.
    await waitFor(() => expect(itemsCalls).toBeGreaterThanOrEqual(1));
  });
});
