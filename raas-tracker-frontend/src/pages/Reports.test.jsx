// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
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
  const fetchMock = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: role, role });
    if (u.includes('/api/companies')) {
      if (companyFailsLeft > 0) {
        companyFailsLeft -= 1;
        return jsonResponse({ error: companiesError }, false, 500);
      }
      return jsonResponse(COMPANIES);
    }
    if (u.includes('/api/recipes')) return jsonResponse(recipes);
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
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Reports />
      </AuthProvider>
    </MemoryRouter>
  );
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
    expect(screen.getByText('99 sale lines · USD')).toBeTruthy();
    expect(screen.getAllByText('42 sales · USD').length).toBe(2);
    // Page-only sums (10 + 20) must not be presented as the report totals.
    expect(screen.queryByText('$30.00')).toBeNull();
  });
});
