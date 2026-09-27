// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import ProtectedRoute from './ProtectedRoute.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function renderGated({ meUser, start = '/' }) {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/auth/me')) return jsonResponse(meUser);
    return jsonResponse({ setup_needed: false });
  }));
  return render(
    <MemoryRouter initialEntries={[start]}>
      <AuthProvider>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<p>home marker</p>} />
            <Route path="/change-password" element={<p>change marker</p>} />
          </Route>
          <Route path="/login" element={<p>login marker</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('ProtectedRoute password gate', () => {
  it('forces users with must_change_password to /change-password', async () => {
    renderGated({
      meUser: { id: 1, username: 'bob', role: 'user', must_change_password: true },
      start: '/',
    });
    expect(await screen.findByText('change marker')).toBeTruthy();
    expect(screen.queryByText('home marker')).toBeNull();
  });

  it('lets the forced user stay on /change-password', async () => {
    renderGated({
      meUser: { id: 1, username: 'bob', role: 'user', must_change_password: true },
      start: '/change-password',
    });
    expect(await screen.findByText('change marker')).toBeTruthy();
  });

  it('lets cleared users through to the app', async () => {
    renderGated({
      meUser: { id: 1, username: 'bob', role: 'user', must_change_password: false },
      start: '/',
    });
    expect(await screen.findByText('home marker')).toBeTruthy();
  });

  it('still sends logged-out visitors to login', async () => {
    renderGated({ meUser: null, start: '/' });
    expect(await screen.findByText('login marker')).toBeTruthy();
  });
});
