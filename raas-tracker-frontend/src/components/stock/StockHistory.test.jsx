// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import StockHistory from './StockHistory';

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

const CHEMICALS = [{ id: 1, name: 'Acid' }, { id: 2, name: 'Salt' }];

const MOVEMENTS = [
  { id: 1, action: 'ADD_CHEMICAL', chemical_id: 1, chemical: 'Acid', unit: 'L', old: null, new: 10, delta: 10, actor: 'admin', purpose: 'New registration', source: 'registration', timestamp: '2026-09-02 10:00:00' },
  { id: 2, action: 'ADJUST_STOCK', chemical_id: 1, chemical: 'Acid', unit: 'L', old: 10, new: 7, delta: -3, actor: 'admin', purpose: 'Disposal / expiry', source: 'manual', timestamp: '2026-09-09 09:00:00' },
  { id: 3, action: 'ADJUST_STOCK', chemical_id: 2, chemical: 'Salt', unit: 'KG', old: 5, new: 25, delta: 20, actor: 'admin', purpose: 'Adjusted from upload 4', source: 'upload', timestamp: '2026-08-15 09:00:00' },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch(captured) {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/chemicals/history')) {
      captured.push(u);
      const m = u.match(/chemical_id=(\d+)/);
      const list = m ? MOVEMENTS.filter((x) => x.chemical_id === Number(m[1])) : MOVEMENTS;
      return jsonResponse(list);
    }
    return jsonResponse({});
  }));
}

function renderHistory(captured) {
  installFetch(captured);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <StockHistory chemicals={CHEMICALS} />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('StockHistory', () => {
  it('loads movements and groups the feed by week by default', async () => {
    const captured = [];
    renderHistory(captured);
    expect(await screen.findByText('w/c 2026-09-07')).toBeTruthy();
    expect(screen.getByText('w/c 2026-08-31')).toBeTruthy();
    expect(screen.getByText(/Disposal \/ expiry/)).toBeTruthy();
    expect(captured.some((u) => u.includes('/api/chemicals/history') && u.includes('limit='))).toBe(true);
  });

  it('re-buckets the feed across Day and Month tabs', async () => {
    const captured = [];
    renderHistory(captured);
    await screen.findByText('w/c 2026-09-07');

    fireEvent.click(screen.getByRole('button', { name: 'Day' }));
    expect(await screen.findByText('2026-09-09')).toBeTruthy();
    expect(screen.getByText('2026-09-02')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Month' }));
    expect(await screen.findByText('2026-09')).toBeTruthy();
    expect(screen.getByText('2026-08')).toBeTruthy();
    expect(captured).toHaveLength(1);
  });

  it('filters by chemical through the server', async () => {
    const captured = [];
    renderHistory(captured);
    await screen.findByText('w/c 2026-09-07');

    fireEvent.change(screen.getByLabelText('Chemical'), { target: { value: '2' } });
    await waitFor(() => expect(captured.some((u) => u.includes('chemical_id=2'))).toBe(true));
    expect(await screen.findByText(/Adjusted from upload 4/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(/Disposal \/ expiry/)).toBeNull());
  });

  it('refetches when the date range changes', async () => {
    const captured = [];
    renderHistory(captured);
    await screen.findByText('w/c 2026-09-07');

    fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-01' } });
    await waitFor(() => expect(captured.some((u) => u.includes('since=2026-09-01'))).toBe(true));
  });
});
