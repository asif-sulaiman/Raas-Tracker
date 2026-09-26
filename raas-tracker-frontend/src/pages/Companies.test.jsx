// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import { toast } from 'sonner';
import Companies from './Companies';

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
  { id: 1, name: 'Acme Ltd', code: 'ACME', country: 'BD', address: null, contact_person: 'A. Rahman', swift: null, lc_bank: null },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ role = 'admin', companies = SEED, posts = [], puts = [], deletes = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.includes('/api/companies')) {
      if (options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        posts.push(body);
        return { ok: true, status: 201, json: async () => ({ success: true, id: 99, ...body }), headers: { get: () => null } };
      }
      if (options && options.method === 'PUT') {
        const body = JSON.parse(options.body);
        puts.push({ url: u, body });
        return jsonResponse({ success: true });
      }
      if (options && options.method === 'DELETE') {
        deletes.push(u);
        return jsonResponse({ success: true });
      }
      return jsonResponse(companies);
    }
    return jsonResponse({});
  }));
}

function renderCompanies(opts = {}) {
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <Companies />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('Companies page', () => {
  it('refuses non-admins', async () => {
    renderCompanies({ role: 'user' });
    expect(await screen.findByText('Admins only.')).toBeTruthy();
    expect(screen.queryByText('Acme Ltd')).toBeNull();
  });

  it('lists companies for admins', async () => {
    renderCompanies();
    expect(await screen.findByText('Acme Ltd')).toBeTruthy();
    expect(screen.getByText('A. Rahman')).toBeTruthy();
  });

  it('adds a company with optional metadata', async () => {
    const posts = [];
    renderCompanies({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Company' }));
    fireEvent.change(screen.getByLabelText('Company name'), { target: { value: 'NewCo' } });
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: 'BD' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ name: 'NewCo', country: 'BD' });
    expect(toast.success).toHaveBeenCalled();
  });

  it('rejects a blank name without posting', async () => {
    const posts = [];
    renderCompanies({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Company' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(posts).toHaveLength(0);
  });

  it('edits a company', async () => {
    const puts = [];
    renderCompanies({ puts });
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Acme Ltd' }));
    const input = screen.getByLabelText('Company name');
    fireEvent.change(input, { target: { value: 'Acme Limited' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].url).toMatch(/\/api\/companies\/1/);
    expect(puts[0].body).toMatchObject({ name: 'Acme Limited' });
  });

  it('deletes after confirm', async () => {
    const deletes = [];
    renderCompanies({ deletes });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Acme Ltd' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deletes).toHaveLength(1));
    expect(deletes[0]).toMatch(/\/api\/companies\/1/);
    expect(toast.success).toHaveBeenCalled();
  });
});
