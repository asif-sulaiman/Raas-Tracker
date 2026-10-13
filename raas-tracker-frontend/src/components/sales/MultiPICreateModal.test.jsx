// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import MultiPICreateModal, { MAX_DOCS } from './MultiPICreateModal';

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
  // P1-B: the server caps a batch at 50 sales. Mirror the ceiling client-side so
  // the user cannot assemble a request that will hard-400 on save.
  describe('batch document cap', () => {
    async function addDocs(n) {
      const add = screen.getByRole('button', { name: 'Add PI document' });
      for (let i = 0; i < n; i += 1) {
        fireEvent.click(add);
      }
    }

    it('disables "Add PI document" once the cap is reached', async () => {
      renderModal();
      await screen.findByLabelText('Company');

      // One document exists on open, so MAX_DOCS-1 further clicks reach the cap.
      await addDocs(MAX_DOCS - 1);

      const add = screen.getByRole('button', { name: 'Add PI document' });
      expect(add).toBeTruthy();
      expect(add.disabled).toBe(true);
      expect(add.getAttribute('title')).toContain(String(MAX_DOCS));
      // Rendering 50 full document cards is legitimately slow; the default 5s is
      // not enough once the suite runs many files in parallel.
    }, 30000);

    it('keeps the button enabled below the cap', async () => {
      renderModal();
      await screen.findByLabelText('Company');

      await addDocs(2);

      const add = screen.getByRole('button', { name: 'Add PI document' });
      expect(add.disabled).toBe(false);
      expect(add.getAttribute('title')).toBeNull();
    });

    it('never assembles more documents than the cap allows', async () => {
      const posts = [];
      renderModal({ postsOpts: { posts } });
      await screen.findByLabelText('Company');

      // Click well past the cap, including while the button is already disabled.
      await addDocs(MAX_DOCS + 10);

      // The save button counts the documents; it must never exceed the cap, and
      // no document past the cap may exist in the DOM at all.
      const save = screen.getByRole('button', { name: /Save \d+ PIs to PI Issued/ });
      expect(save.textContent).toMatch(new RegExp(`Save ${MAX_DOCS} PIs`));
      expect(screen.getByLabelText(`PI number document ${MAX_DOCS}`)).toBeTruthy();
      expect(screen.queryByLabelText(`PI number document ${MAX_DOCS + 1}`)).toBeNull();
      expect(posts).toHaveLength(0);
    }, 30000);
  });
});
