// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import Reports from './Reports';

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

const COMPANIES = [{ id: 7, name: 'Acme' }, { id: 8, name: 'Beta' }];

function jsonResponse(data, ok = true, status = 200, headers = {}) {
  return {
    ok,
    status,
    json: async () => data,
    headers: { get: (name) => (name in headers ? headers[name] : null) },
  };
}

function installFetch({ role = 'admin', companiesFailTimes = 0, companiesError = 'Companies service unavailable', recipes = [], report = [], periods = [], filteredTotal = null } = {}) {
  let companyFailsLeft = companiesFailTimes;
  const fetchMock = vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: role, role });
    if (u.includes('/api/companies')) {
      if (companyFailsLeft > 0) {
        companyFailsLeft -= 1;
        return jsonResponse({ error: companiesError }, false, 500);
      }
      return jsonResponse(COMPANIES);
    }
    if (u.includes('/api/recipes')) return jsonResponse(recipes);
    // The report's edit path opens the existing sale editor.
    if (/\/api\/sales\/\d+$/.test(u)) {
      const id = Number(u.match(/(\d+)$/)[1]);
      if (method === 'PUT') return jsonResponse({ comments: 'ok' });
      return jsonResponse({
        id, stage: 'payment_due', pi_number: `PI-${id}`, pi_date: '2026-01-15',
        client_name: 'Cust', items: [], history: [], payments: [], shipments: [],
        invoices: [], invoice_total: 0, total_paid: 0, balance: 0, comments: null,
      });
    }
    if (u.includes('/invoice-lines')) return jsonResponse({ lines: [], has_line_less_invoices: false });
    if (u.includes('/invoices')) return jsonResponse([]);
    if (u.includes('/completion')) return jsonResponse({ status: 'none', paid: 0, total: 0 });
    // Honest contracts: /live/summary returns {periods:[...]}, /live/filtered a bare array.
    if (u.includes('/api/reports/live/summary')) return jsonResponse({ periods });
    if (u.includes('/api/reports/live/filtered')) {
      const headers = filteredTotal != null ? { 'X-Total-Count': String(filteredTotal) } : {};
      return jsonResponse(report, true, 200, headers);
    }
    if (u.includes('/api/reports/live/')) return jsonResponse(report);
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderReports(opts = {}) {
  const fetchMock = installFetch(opts);
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <Reports />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
  return { ...utils, fetchMock };
}

async function openCommercialTab() {
  fireEvent.click(await screen.findByRole('button', { name: /Commercial/ }));
}

describe('Reports company filter', () => {
  it('never presents a failed company fetch as a filter with no companies', async () => {
    renderReports({ companiesFailTimes: 10 });
    await openCommercialTab();

    // Loading, failed and genuinely-empty must look different from each other.
    expect(await screen.findByText('Could not load companies')).toBeTruthy();
    expect(screen.getByText('Companies service unavailable')).toBeTruthy();
    // The dropdown still exists but is an explicit fallback, not a claim.
    expect(screen.getByLabelText('Company')).toBeTruthy();
    screen.getByRole('button', { name: 'Retry companies' });
  });

  it('populates the company filter from a successful load', async () => {
    renderReports();
    await openCommercialTab();

    await waitFor(() =>
      expect(screen.getByLabelText('Company').querySelectorAll('option')).toHaveLength(3)
    );
    expect(screen.queryByText('Could not load companies')).toBeNull();
  });

  it('recovers the company filter when the retry succeeds', async () => {
    renderReports({ companiesFailTimes: 1 });
    await openCommercialTab();

    expect(await screen.findByText('Could not load companies')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry companies' }));
    await waitFor(() =>
      expect(screen.getByLabelText('Company').querySelectorAll('option')).toHaveLength(3)
    );
    expect(screen.queryByText('Could not load companies')).toBeNull();
  });
});

describe('Commercial grouped report', () => {
  it('renders period rows from the {periods} summary contract without a false error', async () => {
    renderReports({
      periods: [{
        period_start: '2026-09-01',
        period_end: '2026-09-30',
        kpis: { order_count: 2, gross_sales: 100, received: 40, due: 60, overdue: 0 },
        items: [],
      }],
    });
    await openCommercialTab();
    fireEvent.change(await screen.findByLabelText('Group By'), { target: { value: 'month' } });

    expect(await screen.findByText(/Expand 0 items/)).toBeTruthy();
    expect(screen.queryByText(/Could not load the commercial summary/)).toBeNull();
    expect(screen.queryByText(/Failed to load the commercial summary/)).toBeNull();
  });

  it('never renders a pager in grouped mode (the summary already returns every period)', async () => {
    renderReports({
      periods: [{
        period_start: '2026-09-01',
        period_end: '2026-09-30',
        kpis: { order_count: 120, gross_sales: 100, received: 40, due: 60, overdue: 0 },
        items: [],
      }],
    });
    await openCommercialTab();
    fireEvent.change(await screen.findByLabelText('Group By'), { target: { value: 'month' } });
    await screen.findByText(/Expand 0 items/);

    expect(screen.queryByText(/Page 1 of/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Next page' })).toBeNull();
  });
});

function makeRow(i, overrides = {}) {
  return {
    sale_id: i, customer_name: `Cust ${i}`, pi_number: `PI-${i}`, pi_date: '2026-01-15',
    lc_number: null, lc_date: null, product_name: 'Prod', unit: 'KG',
    quantity: 1, unit_price: 10, total_price: 10,
    invoice_date: null, latest_ship_date: null, actual_ship_date: null,
    maturity_date: null, receive_date: null, received_amount: 0, due_amount: 10,
    payment_status: 'Pending', payment_comment: '',
    ...overrides,
  };
}

describe('Commercial detail report pagination', () => {
  it('shows the server total in the pager and refetches the next page', async () => {
    renderReports({
      report: Array.from({ length: 50 }, (_, i) => makeRow(i + 1)),
      filteredTotal: '120',
    });
    await openCommercialTab();

    expect(await screen.findByText('Page 1 of 3 · 120 rows')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    await waitFor(() => {
      expect(globalThis.fetch.mock.calls.some(([u]) => String(u).includes('page=2'))).toBe(true);
    });
  });

  it('detail KPI cards use whole-filter totals from the summary, not the loaded page', async () => {
    renderReports({
      report: [makeRow(1, { total_price: 10 }), makeRow(2, { total_price: 20 })],
      filteredTotal: '99',
      periods: [{
        period_start: '2026-01-01',
        period_end: '2026-12-31',
        kpis: { gross_sales: 9999, received: 8888, due: 7777, overdue: 6666, order_count: 42 },
        items: [],
      }],
    });
    await openCommercialTab();

    expect(await screen.findByText('$9,999.00')).toBeTruthy();
    // Captions name rows: gross/received/due each sum report rows.
    expect(screen.getAllByText('99 report rows · USD').length).toBe(3);
    // Page-only sums (10 + 20) must not be presented as the report totals.
    expect(screen.queryByText('$30.00')).toBeNull();
  });

  it('KPI fallback sums report rows instead of deduping them by sale', async () => {
    renderReports({
      report: [
        makeRow(1, { sale_id: 7, total_price: 80, received_amount: 50, due_amount: 30, payment_status: 'Partial' }),
        makeRow(2, { sale_id: 7, total_price: 20, received_amount: 10, due_amount: 10, payment_status: 'Partial' }),
        makeRow(3, { sale_id: 8, total_price: 100, received_amount: 0, due_amount: 100, payment_status: 'Overdue' }),
      ],
      filteredTotal: '3',
    });
    await openCommercialTab();

    // Gross 200, received 60 (50 + 10), due 140 (30 + 10 + 100), overdue 100.
    expect(await screen.findByText('$200.00')).toBeTruthy();
    expect(screen.getByText('$60.00')).toBeTruthy();
    expect(screen.getByText('$140.00')).toBeTruthy();
    expect(screen.getByText('$100.00')).toBeTruthy();
    // Deduping by sale would drop the second row's 10.
    expect(screen.queryByText('$50.00')).toBeNull();
    expect(screen.getAllByText('3 report rows (this page) · USD').length).toBe(3);
  });
});

describe('Commercial report provisional rows and editing', () => {
  it('marks a provisional PI row and explains the fallback', async () => {
    renderReports({
      report: [makeRow(1, { is_provisional: true })],
      filteredTotal: '1',
    });
    await openCommercialTab();

    expect(await screen.findByText('Provisional')).toBeTruthy();
    expect(screen.getByText(/uninvoiced remainder of a PI line/i)).toBeTruthy();
  });

  it('opens the sale editor from a report row and refreshes after a save', async () => {
    const { fetchMock } = renderReports({
      report: [makeRow(1, { is_provisional: true })],
      filteredTotal: '1',
    });
    await openCommercialTab();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit sale PI-1' }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/sales/1')).toBe(true);
    });

    const reportReads = () =>
      fetchMock.mock.calls.filter(([u]) => String(u).includes('/api/reports/live/filtered')).length;
    const before = reportReads();

    // Saving from the editor must reload the report (and its KPI summary).
    fireEvent.change(await screen.findByLabelText('Sale comments'), {
      target: { value: 'corrected after completion' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(reportReads()).toBeGreaterThan(before));
  });
});
