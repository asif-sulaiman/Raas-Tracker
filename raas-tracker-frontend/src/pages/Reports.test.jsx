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

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ role = 'admin', companiesFailTimes = 0, companiesError = 'Companies service unavailable', recipes = [], report = [], periods = [] } = {}) {
  let companyFailsLeft = companiesFailTimes;
  vi.stubGlobal('fetch', vi.fn(async (url) => {
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
    if (u.includes('/api/reports/live/')) return jsonResponse(report);
    return jsonResponse({});
  }));
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
});
