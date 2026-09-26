// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { toast } from 'sonner';
import Chemicals from './Chemicals';

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

const SEED = [
  { id: 1, name: 'Acid', qty: 10, balance_last_month: 8, unit: 'L', last_updated: '2026-09-01', reorder_level: 2 },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ role = 'admin', chemicals = SEED, posts = [], postHandler } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.includes('/api/chemicals')) {
      if (options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        posts.push(body);
        if (postHandler) return postHandler(body);
        return jsonResponse({ success: true, name: body.name });
      }
      return jsonResponse(chemicals);
    }
    return jsonResponse({});
  }));
}

function renderChemicals(opts = {}) {
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Chemicals />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('Chemicals add flow', () => {
  it('shows the Add Chemical button for admins only', async () => {
    renderChemicals({ role: 'admin' });
    expect(await screen.findByRole('button', { name: 'Add Chemical' })).toBeTruthy();
    cleanup();

    renderChemicals({ role: 'user' });
    await screen.findByText('Acid');
    expect(screen.queryByRole('button', { name: 'Add Chemical' })).toBeNull();
  });

  it('opens the add modal and posts the new chemical', async () => {
    const posts = [];
    renderChemicals({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Chemical' }));

    fireEvent.change(screen.getByLabelText('Chemical name'), { target: { value: 'NewAcid' } });
    fireEvent.change(screen.getByLabelText('Opening quantity'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Unit'), { target: { value: 'L' } });
    fireEvent.change(screen.getByLabelText('Reorder level'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ name: 'NewAcid', qty: 10, unit: 'L', reorder_level: 2 });
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByLabelText('Chemical name')).toBeNull());
  });

  it('surfaces the server duplicate message and keeps the modal open', async () => {
    renderChemicals({
      postHandler: (body) => jsonResponse(
        { success: false, name: body.name, error: `Chemical '${body.name}' already exists` },
        false, 409),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Chemical' }));
    fireEvent.change(screen.getByLabelText('Chemical name'), { target: { value: 'Acid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0][0]).toMatch(/already exists/i);
    expect(screen.getByLabelText('Chemical name')).toBeTruthy();
  });

  it('offers an add CTA in the empty state for admins', async () => {
    renderChemicals({ chemicals: [] });
    const cta = await screen.findByRole('button', { name: /add your first chemical/i });
    fireEvent.click(cta);
    expect(screen.getByLabelText('Chemical name')).toBeTruthy();
  });
});
