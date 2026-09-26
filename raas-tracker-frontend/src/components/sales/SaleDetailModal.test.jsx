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

function installFetch({ role = 'admin', deletes = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.match(/\/api\/sales\/5\/shipments\/\d+/) && options && options.method === 'DELETE') {
      deletes.push(u);
      return jsonResponse({ message: 'Shipment deleted' });
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
});
