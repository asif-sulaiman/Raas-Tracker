// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import Sales from './Sales';

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

function installFetch({ sales = [], lcs = [], readiness = null } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/lcs/') && u.includes('/readiness')) {
      return jsonResponse(readiness || { invoices_ok: true, recipes_ok: true, produced_ok: true, detail: {} });
    }
    if (u.includes('/api/lcs') && method === 'GET') {
      return jsonResponse(lcs);
    }
    if (u.includes('/api/sales/summary')) {
      return jsonResponse({ total_pipeline_value: 0, total_sales: sales.length });
    }
    if (u.includes('/api/sales') && method === 'GET') {
      return jsonResponse({ sales, total: sales.length });
    }
    return jsonResponse({});
  }));
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

  it('keeps Start Shipment in LC Received and opens the invoice barrier when blocked', async () => {
    installFetch({
      sales: [sale({ stage: 'lc_received' })],
      lcs: [lcMeta()],
      readiness: { invoices_ok: false, recipes_ok: true, produced_ok: true, detail: {} },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start Shipment' }));
    await waitFor(() => {
      expect(screen.getByText('Invoices Required')).toBeTruthy();
    });
    // PI-001 appears both on the board card and in the barrier modal row.
    expect(screen.getAllByText(/PI-001/).length).toBeGreaterThanOrEqual(2);
  });

  it('keeps the shipment-column LC action intact', async () => {
    installFetch({
      sales: [sale({ stage: 'shipment_ongoing' })],
      lcs: [lcMeta({ stage: 'shipment_ongoing' })],
      readiness: { invoices_ok: true, recipes_ok: false, produced_ok: false, detail: {} },
    });
    renderPage();
    await waitFor(() => {
      expect(screen.queryByText(/loading sales pipeline/i)).toBeNull();
    });
    expect(screen.getByRole('button', { name: 'Mark Shipped' })).toBeTruthy();
  });
});
