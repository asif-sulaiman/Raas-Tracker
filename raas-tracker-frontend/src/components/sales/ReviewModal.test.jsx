// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import ReviewModal from './ReviewModal';

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

function renderReview(onSave) {
  installFetch();
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ReviewModal isOpen initialData={INITIAL} onClose={vi.fn()} onSave={onSave} />
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
