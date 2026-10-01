// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { ConfirmProvider } from '../../context/ConfirmContext';
import LcDetailModal from './LcDetailModal';

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

function lcRow(pis) {
  return {
    id: 10,
    lc_number: 'LC-100',
    company_name: 'Acme',
    stage: 'lc_received',
    lc_date: '2026-09-01',
    expiry_date: '2026-12-01',
    bank_ref: 'REF-1',
    notes: '',
    total_value: 1000,
    pis,
  };
}

function installFetch({ lc, puts = [] }) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (/\/api\/lcs\/10$/.test(u) && method === 'PUT') {
      puts.push(JSON.parse(options.body));
      return jsonResponse({ ...lc, ...puts[puts.length - 1] });
    }
    if (/\/api\/lcs\/10$/.test(u)) {
      return jsonResponse(lc);
    }
    return jsonResponse({});
  }));
}

function renderDetail({ lc, onSaved = vi.fn(), puts } = {}) {
  installFetch({ lc, puts });
  const onClose = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <LcDetailModal lcId={10} isOpen onClose={onClose} onSaved={onSaved} />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
  return { onSaved, onClose };
}

describe('LcDetailModal edit + guards', () => {
  it('edits dates and saves via PUT', async () => {
    const puts = [];
    const onSaved = vi.fn();
    renderDetail({
      lc: lcRow([{ id: 1, pi_number: 'PI-001', total_value: 1000, item_count: 1 }]),
      onSaved,
      puts,
    });

    expect(await screen.findByRole('heading', { name: /LC-100/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit LC' }));

    const dateInput = await screen.findByLabelText('LC date');
    fireEvent.change(dateInput, { target: { value: '2026-09-05' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toMatchObject({ lc_date: '2026-09-05' });
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('keeps delete disabled while PIs are attached', async () => {
    renderDetail({
      lc: lcRow([{ id: 1, pi_number: 'PI-001', total_value: 1000, item_count: 1 }]),
    });

    expect(await screen.findByRole('heading', { name: /LC-100/ })).toBeTruthy();
    const del = screen.getByRole('button', { name: 'Delete LC' });
    expect(del.disabled).toBe(true);
    expect(screen.getByText(/still has 1 attached PI/)).toBeTruthy();
  });

  it('enables delete once all PIs are detached', async () => {
    renderDetail({ lc: lcRow([]) });

    expect(await screen.findByRole('heading', { name: /LC-100/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Delete LC' }).disabled).toBe(false);
  });
});
