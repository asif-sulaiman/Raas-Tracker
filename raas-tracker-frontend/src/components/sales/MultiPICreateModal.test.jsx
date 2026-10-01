// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import MultiPICreateModal from './MultiPICreateModal';

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

function installFetch({ companies = [{ id: 7, name: 'Acme' }], batchOk = true, batchBody = null, posts = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/companies')) {
      return jsonResponse(companies);
    }
    if (u.includes('/api/sales/batch') && method === 'POST') {
      posts.push(JSON.parse(options.body));
      if (batchOk) {
        return jsonResponse(batchBody || { ids: [101, 102], warnings: [] }, true, 201);
      }
      return jsonResponse(
        batchBody || { error: 'PI number PI-B1-TEST already exists' },
        false,
        400,
      );
    }
    return jsonResponse({});
  }));
}

function renderModal({ onSaved = vi.fn(), postsOpts } = {}) {
  installFetch(postsOpts);
  const onClose = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <MultiPICreateModal isOpen onClose={onClose} onSaved={onSaved} />
      </AuthProvider>
    </MemoryRouter>
  );
  return { onSaved, onClose };
}

async function fillDoc(idx, piNumber, product = 'Widget', qty = '5', price = '10') {
  fireEvent.change(screen.getByLabelText(`PI number document ${idx}`), {
    target: { value: piNumber },
  });
  fireEvent.change(screen.getByLabelText(`Product name document ${idx} row 1`), {
    target: { value: product },
  });
  fireEvent.change(screen.getByLabelText(`Quantity document ${idx} row 1`), {
    target: { value: qty },
  });
  fireEvent.change(screen.getByLabelText(`Unit price document ${idx} row 1`), {
    target: { value: price },
  });
}

describe('MultiPICreateModal batch wiring', () => {
  it('submits two documents in one batch POST with both payloads', async () => {
    const posts = [];
    const onSaved = vi.fn();
    renderModal({ onSaved, postsOpts: { posts } });

    const company = await screen.findByLabelText('Company');
    fireEvent.change(company, { target: { value: '7' } });

    await fillDoc(1, 'PI-B0-TEST');
    fireEvent.click(screen.getByRole('button', { name: 'Add PI document' }));
    await fillDoc(2, 'PI-B1-TEST');

    fireEvent.click(screen.getByRole('button', { name: /Save 2 PIs to PI Issued/ }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].sales).toHaveLength(2);
    expect(posts[0].sales[0].sale.pi_number).toBe('PI-B0-TEST');
    expect(posts[0].sales[1].sale.pi_number).toBe('PI-B1-TEST');
    expect(posts[0].sales[0].items[0]).toMatchObject({ product_name: 'Widget' });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith([101, 102], []));
  });

  it('blocks submit when a document fails per-document validation', async () => {
    const posts = [];
    renderModal({ postsOpts: { posts } });

    const company = await screen.findByLabelText('Company');
    fireEvent.change(company, { target: { value: '7' } });

    await fillDoc(1, 'PI-B0-TEST');
    fireEvent.click(screen.getByRole('button', { name: 'Add PI document' }));
    // Second document keeps its PI number empty (invalid) but has a product.
    fireEvent.change(screen.getByLabelText('Product name document 2 row 1'), {
      target: { value: 'Gadget' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Save 2 PIs to PI Issued/ }));

    expect(await screen.findByText('PI number is required')).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it('shows "Nothing was saved" and skips onSaved when the batch fails', async () => {
    const posts = [];
    const onSaved = vi.fn();
    renderModal({
      onSaved,
      postsOpts: {
        posts,
        batchOk: false,
        batchBody: { error: 'PI number PI-B1-TEST already exists' },
      },
    });

    const company = await screen.findByLabelText('Company');
    fireEvent.change(company, { target: { value: '7' } });

    await fillDoc(1, 'PI-B0-TEST');
    fireEvent.click(screen.getByRole('button', { name: 'Add PI document' }));
    await fillDoc(2, 'PI-B1-TEST');

    fireEvent.click(screen.getByRole('button', { name: /Save 2 PIs to PI Issued/ }));

    expect(await screen.findByText(/Nothing was saved/)).toBeTruthy();
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(onSaved).not.toHaveBeenCalled();
  });
});
