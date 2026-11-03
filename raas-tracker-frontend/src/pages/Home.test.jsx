// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Home from './Home';
import { clearCachedFetch } from '../hooks/useCachedFetch';

const { apiFetch } = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ apiFetch }),
}));
vi.mock('../components/cards/ReconciliationChart', () => ({
  default: () => <div data-testid="reconciliation-chart" />,
}));
vi.mock('../components/cards/StockDistributionChart', () => ({
  default: () => <div data-testid="distribution-chart" />,
}));

const SALE = {
  id: 1,
  stage: 'pi_issued',
  pi_number: 'PI-DASH',
  pi_date: null,
  client_name: 'Dash Co',
  lc_number: null,
  lc_date: null,
  shipment_date: null,
  payment_date: null,
  maturity_date: null,
  total_value: 10,
  total_paid: 0,
  balance: 10,
  item_count: 1,
};

const DASHBOARD = {
  chemicals: [{
    id: 1, name: 'DashAcid', qty: 12, balance_last_month: 10,
    unit: 'KG', last_updated: null, reorder_level: 0,
  }],
  uploads: [],
  recipes: [],
  sales: { sales: [SALE], total: 1 },
  sales_summary: {
    pi_issued: { count: 1, value: 10 },
    total_pipeline_value: 10,
    total_sales: 1,
  },
  lcs: [],
  activity: null,
};

function ok(data) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    headers: { get: () => null },
  };
}

function renderHome() {
  return render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>
  );
}

beforeEach(() => {
  clearCachedFetch();
  apiFetch.mockReset();
});

afterEach(() => {
  cleanup();
});

describe('Home dashboard loading', () => {
  it('loads everything with a single /api/dashboard request', async () => {
    apiFetch.mockResolvedValue(ok(DASHBOARD));
    renderHome();

    expect(await screen.findByText('DashAcid')).toBeTruthy();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith('/api/dashboard');
    expect(screen.getByText('PI-DASH')).toBeTruthy();
    expect(await screen.findByTestId('reconciliation-chart')).toBeTruthy();
  });

  it('hides the activity panel for non-admin payloads', async () => {
    apiFetch.mockResolvedValue(ok(DASHBOARD));
    renderHome();

    await screen.findByText('DashAcid');
    expect(screen.queryByText('Recent Accountability Activity')).toBeNull();
  });

  it('shows the recent activity for admins', async () => {
    apiFetch.mockResolvedValue(ok({
      ...DASHBOARD,
      activity: [
        { id: 1, action: 'LOGIN_SUCCESS', user_id: 'admin', timestamp: '2026-11-03T10:00:00Z' },
        { id: 2, action: 'SALE_CREATED', user_id: 'admin', timestamp: '2026-11-03T10:01:00Z' },
        { id: 3, action: 'CHEMICAL_UPDATED', user_id: 'admin', timestamp: '2026-11-03T10:02:00Z' },
        { id: 4, action: 'RECIPE_CREATED', user_id: 'admin', timestamp: '2026-11-03T10:03:00Z' },
      ],
    }));
    renderHome();

    expect(await screen.findByText('Recent Accountability Activity')).toBeTruthy();
    expect(screen.getByText('LOGIN_SUCCESS')).toBeTruthy();
    expect(screen.getByText('SALE_CREATED')).toBeTruthy();
    expect(screen.getByText('CHEMICAL_UPDATED')).toBeTruthy();
    // The card shows the three most recent rows.
    expect(screen.queryByText('RECIPE_CREATED')).toBeNull();
  });

  it('reuses cached data on a remount within the stale window', async () => {
    apiFetch.mockResolvedValue(ok(DASHBOARD));
    renderHome();
    await screen.findByText('DashAcid');

    cleanup();
    renderHome();
    await screen.findByText('DashAcid');

    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it('shows the failure and loads on retry', async () => {
    apiFetch
      .mockRejectedValueOnce(new Error('dashboard offline'))
      .mockResolvedValueOnce(ok(DASHBOARD));
    renderHome();

    expect((await screen.findAllByText(/dashboard offline/)).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole('button', { name: /retry/i })[0]);

    expect(await screen.findByText('DashAcid')).toBeTruthy();
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
});
