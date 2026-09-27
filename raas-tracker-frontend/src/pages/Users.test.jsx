// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import Users from './Users.jsx';

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

const SEED_USERS = [
  {
    id: 1, username: 'bob', role: 'user', created_at: '2026-09-01T10:00:00',
    must_change_password: true, has_pending_reset: true,
  },
  {
    id: 2, username: 'cara', role: 'user', created_at: '2026-09-02T10:00:00',
    must_change_password: false, has_pending_reset: false,
  },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function renderUsers({ posts = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 9, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/users/1/password') && options && options.method === 'POST') {
      const body = JSON.parse(options.body);
      posts.push({ url: u, body });
      return jsonResponse({ temp_password: 'TempPass123!' });
    }
    if (u.includes('/api/users/1/reset-token') && options && options.method === 'POST') {
      posts.push({ url: u, body: null });
      return jsonResponse({ token: 'tok123', link: 'http://app/reset?token=tok123' });
    }
    if (u.includes('/api/users')) return jsonResponse(SEED_USERS);
    if (u.includes('/api/keys')) return jsonResponse([]);
    return jsonResponse({});
  }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <Users />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

async function openResetForBob() {
  expect(await screen.findByText('bob')).toBeTruthy();
  const resetButtons = screen.getAllByTitle('Reset password / issue link');
  fireEvent.click(resetButtons[0]);
  expect(await screen.findByText(/reset password — bob/i)).toBeTruthy();
}

describe('Users password reset', () => {
  it('badges rows with pending resets and forced changes', async () => {
    renderUsers();
    expect(await screen.findByText('must change password')).toBeTruthy();
    expect(screen.getByText('reset pending')).toBeTruthy();
  });

  it('sets an auto-generated temp password and shows it once', async () => {
    const posts = [];
    renderUsers({ posts });
    await openResetForBob();
    fireEvent.click(screen.getByRole('button', { name: /set temp password/i }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({});
    expect(await screen.findByText('TempPass123!')).toBeTruthy();
    expect(screen.getByText(/shown once/i)).toBeTruthy();
  });

  it('sets a typed temp password', async () => {
    const posts = [];
    renderUsers({ posts });
    await openResetForBob();
    fireEvent.change(screen.getByPlaceholderText(/auto-generate if blank/i), {
      target: { value: 'CustomPass9' },
    });
    fireEvent.click(screen.getByRole('button', { name: /set temp password/i }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body).toEqual({ temp_password: 'CustomPass9' });
    expect(await screen.findByText('TempPass123!')).toBeTruthy();
  });

  it('rejects short temp passwords without calling the API', async () => {
    const posts = [];
    renderUsers({ posts });
    await openResetForBob();
    fireEvent.change(screen.getByPlaceholderText(/auto-generate if blank/i), {
      target: { value: 'abc' },
    });
    fireEvent.click(screen.getByRole('button', { name: /set temp password/i }));
    expect(await screen.findByText(/at least 8 characters/i)).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it('issues a reset link and shows it once', async () => {
    const posts = [];
    renderUsers({ posts });
    await openResetForBob();
    fireEvent.click(screen.getByRole('button', { name: /issue reset link/i }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/api/users/1/reset-token');
    expect(await screen.findByText('http://app/reset?token=tok123')).toBeTruthy();
    expect(screen.getByText(/shown once/i)).toBeTruthy();
  });
});
