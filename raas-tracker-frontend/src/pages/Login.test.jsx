// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import Login from './Login.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function renderLogin({ posts = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) return jsonResponse({});
    if (u.includes('/api/auth/forgot-password')) {
      posts.push(JSON.parse(options.body));
      return jsonResponse({ success: true });
    }
    return jsonResponse({});
  }));
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('Login forgot-password modal', () => {
  it('opens from the Forgot password link', async () => {
    renderLogin();
    await screen.findByRole('button', { name: /sign in/i });
    fireEvent.click(screen.getByRole('button', { name: /forgot password\?/i }));
    expect(screen.getByText(/enter your username to request a reset/i)).toBeTruthy();
  });

  it('requires a username before sending', async () => {
    const posts = [];
    renderLogin({ posts });
    await screen.findByRole('button', { name: /sign in/i });
    fireEvent.click(screen.getByRole('button', { name: /forgot password\?/i }));
    fireEvent.click(screen.getByRole('button', { name: /send reset request/i }));
    expect(screen.getByText(/^Enter your username$/)).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it('posts the username and shows a generic success with no existence hint', async () => {
    const posts = [];
    renderLogin({ posts });
    await screen.findByRole('button', { name: /sign in/i });
    fireEvent.click(screen.getByRole('button', { name: /forgot password\?/i }));
    fireEvent.change(screen.getByPlaceholderText(/storekeeper/i), { target: { value: 'bob' } });
    fireEvent.click(screen.getByRole('button', { name: /send reset request/i }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ username: 'bob' });
    expect(await screen.findByText(/if an account with that username exists/i)).toBeTruthy();
  });
});
