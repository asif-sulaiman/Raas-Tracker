// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import InvoicesRequiredModal from './InvoicesRequiredModal';

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

const PIS = [
  { id: 11, pi_number: 'PI-11' },
  { id: 22, pi_number: 'PI-22' },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch(posts, { failWith = null } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/invoices') && method === 'POST') {
      posts.push({ url: u, body: JSON.parse(options.body) });
      if (failWith) return jsonResponse(failWith, false, failWith.status || 400);
      return jsonResponse(
        { invoice_id: 100 + posts.length, status: 'planned', amount: 50 },
        true, 201,
      );
    }
    return jsonResponse({});
  }));
}

function openModal(props = {}) {
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <InvoicesRequiredModal
          isOpen
          onClose={onClose}
          pis={PIS}
          onSuccess={onSuccess}
          {...props}
        />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { onClose, onSuccess };
}

describe('InvoicesRequiredModal (LC barrier, one-or-many invoices)', () => {
  it('renders one invoice row per PI of the LC', () => {
    openModal();
    expect(screen.getByText(/PI-11/)).toBeTruthy();
    expect(screen.getByText(/PI-22/)).toBeTruthy();
  });

  it('POSTs each filled row to its own PI id (never null)', async () => {
    const posts = [];
    installFetch(posts);
    openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    expect(inputs).toHaveLength(2);
    fireEvent.change(inputs[0], { target: { value: 'INV-A' } });
    fireEvent.change(inputs[1], { target: { value: 'INV-B' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(posts).toHaveLength(2);
    });
    const urls = posts.map((p) => p.url);
    expect(urls.some((u) => u.includes('/api/sales/11/invoices'))).toBe(true);
    expect(urls.some((u) => u.includes('/api/sales/22/invoices'))).toBe(true);
    expect(urls.some((u) => u.includes('null'))).toBe(false);
    expect(posts[0].body).toEqual({ invoice_number: 'INV-A' });
  });

  it('creates a single invoice when only one row is filled', async () => {
    const posts = [];
    installFetch(posts);
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[1], { target: { value: 'INV-ONLY' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(posts).toHaveLength(1);
    });
    expect(posts[0].url).toContain('/api/sales/22/invoices');
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
    });
  });

  it('blocks submit with all rows empty and POSTs nothing', async () => {
    const posts = [];
    installFetch(posts);
    openModal();
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(screen.getByText(/at least one invoice number/i)).toBeTruthy();
    });
    expect(posts).toHaveLength(0);
  });

  it('surfaces the server error and does not call onSuccess on failure', async () => {
    const posts = [];
    installFetch(posts, { failWith: { error: 'duplicate invoice number', status: 400 } });
    const { onSuccess } = openModal();
    const inputs = screen.getAllByPlaceholderText(/INV-/i);
    fireEvent.change(inputs[0], { target: { value: 'INV-DUP' } });
    fireEvent.click(screen.getByRole('button', { name: /create invoices?/i }));
    await waitFor(() => {
      expect(screen.getByText(/duplicate invoice number/i)).toBeTruthy();
    });
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
