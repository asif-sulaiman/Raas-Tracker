// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import ReviewModal from './ReviewModal';
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

function installFetch(companies = [{ id: 7, name: 'Acme' }, { id: 8, name: 'Beta' }]) {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/companies')) {
      return jsonResponse(companies);
    }
    return jsonResponse({});
  }));
}

const INITIAL = {
  pi_number: 'PI-9',
  pi_date: '2026-09-01',
  client_name: 'Acme',
  items: [{ product_name: 'Cotton', quantity: 100, unit_price: 3.25 }],
  warnings: [],
};

function renderReview(onSave, initialData = INITIAL, companies) {
  if (companies !== undefined) {
    installFetch(companies);
  } else {
    installFetch();
  }
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ReviewModal isOpen initialData={initialData} onClose={vi.fn()} onSave={onSave} />
      </AuthProvider>
    </MemoryRouter>
  );
}

function renderReviewWithCustomFetch(onSave, initialData = INITIAL) {
  // Does not call installFetch - caller must set up fetch mock
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ReviewModal isOpen initialData={initialData} onClose={vi.fn()} onSave={onSave} />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('ReviewModal capture fields', () => {
  it('preselects the matching company from the register', async () => {
    renderReview(vi.fn());
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('7'));
  });

  it('saves company_id and per-row units', async () => {
    const saved = vi.fn(async () => {});
    renderReview(saved);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('7'));
    const units = screen.getAllByLabelText('Unit');
    expect(units[0].value).toBe('KG');
    fireEvent.change(units[0], { target: { value: 'DRUM' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save to PI Issued' }));
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1));
    expect(saved.mock.calls[0][0]).toMatchObject({
      sale: { pi_number: 'PI-9', company_id: 7 },
      items: [{ product_name: 'Cotton', unit: 'DRUM' }],
    });
  });
});

describe('Company auto-match tiered fuzzy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('tier 1: exact case-insensitive match auto-selects', async () => {
    renderReview(vi.fn(), { ...INITIAL, client_name: 'acme corp' }, [{ id: 1, name: 'ACME Corp' }]);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('1'));
  });

  it('tier 2: prefix match (PI name is prefix of DB name) auto-selects', async () => {
    renderReview(vi.fn(), { ...INITIAL, client_name: 'COLOR CITY' }, [{ id: 2, name: 'COLOR CITY LTD.' }]);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('2'));
  });

  it('tier 2: prefix match (DB name is prefix of PI name) auto-selects', async () => {
    renderReview(vi.fn(), { ...INITIAL, client_name: 'ABC Corp Ltd' }, [{ id: 3, name: 'ABC' }]);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('3'));
  });

  it('tier 3: word-boundary contains — single match auto-selects', async () => {
    renderReview(vi.fn(), { ...INITIAL, client_name: 'HUEWASH LTW' }, [{ id: 4, name: 'HUEWASH LTW Soaping' }]);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('4'));
  });

  it('tier 3: multiple word-boundary matches → NO auto-select, toast warning', async () => {
    renderReview(vi.fn(), { ...INITIAL, client_name: 'CITY' }, [{ id: 5, name: 'CITY LTD' }, { id: 6, name: 'CITY BANK' }]);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe('')); // no auto-select
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('Multiple matches'));
  });

  it('tier 4: loose substring never auto-selects', async () => {
    // "TY BA" is a substring of "CITY BANK" but not a prefix or word-boundary match
    renderReview(vi.fn(), { ...INITIAL, client_name: 'TY BA' }, [{ id: 7, name: 'CITY BANK' }]);
    const select = await screen.findByLabelText('Company');
    await waitFor(() => expect(select.value).toBe(''));
  });

  it('no companies → Save disabled, message shown', async () => {
    renderReview(vi.fn(), INITIAL, []);
    const msg = await screen.findByText(/No companies registered/i);
    expect(msg).toBeTruthy();
    const saveBtn = screen.getByRole('button', { name: 'Save to PI Issued' });
    expect(saveBtn.disabled).toBe(true);
  });

  it('fetch error (401) → toast error, dropdown empty', async () => {
    // Mock fetch to return 401 for companies endpoint BEFORE rendering
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const u = String(url);
      if (u.includes('/api/auth/me')) {
        return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      }
      if (u.includes('/api/companies')) {
        return { ok: false, status: 401, json: async () => ({}) };
      }
      return jsonResponse({});
    }));
    renderReviewWithCustomFetch(vi.fn(), INITIAL);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringContaining('Could not load companies')));
  });
});