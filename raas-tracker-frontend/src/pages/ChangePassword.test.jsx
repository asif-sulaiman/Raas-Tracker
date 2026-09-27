// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import ChangePassword from './ChangePassword.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function renderChange({ forced = true, puts = [] } = {}) {
  const meUser = forced
    ? { id: 1, username: 'bob', role: 'user', must_change_password: true }
    : { id: 1, username: 'bob', role: 'user', must_change_password: false };
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) return jsonResponse(meUser);
    if (u.includes('/api/auth/password') && options && options.method === 'PUT') {
      const body = JSON.parse(options.body);
      puts.push(body);
      return jsonResponse({ user: { ...meUser, must_change_password: 0 } });
    }
    return jsonResponse({});
  }));
  return render(
    <MemoryRouter initialEntries={['/change-password']}>
      <AuthProvider>
        <Routes>
          <Route path="/change-password" element={<ChangePassword />} />
          <Route path="/" element={<p>home marker</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

function fillAndSubmit(container, current, next, confirm) {
  const inputs = container.querySelectorAll('input[type="password"]');
  fireEvent.change(inputs[0], { target: { value: current } });
  fireEvent.change(inputs[1], { target: { value: next } });
  fireEvent.change(inputs[2], { target: { value: confirm } });
  fireEvent.click(screen.getByRole('button', { name: /set new password/i }));
}

describe('ChangePassword page', () => {
  it('explains the forced change and offers no back link', async () => {
    renderChange({ forced: true });
    expect(await screen.findByText(/must be changed before you can continue/i)).toBeTruthy();
    expect(screen.queryByText(/back to dashboard/i)).toBeNull();
  });

  it('offers a back link for voluntary changes', async () => {
    renderChange({ forced: false });
    expect(await screen.findByText(/back to dashboard/i)).toBeTruthy();
  });

  it('rejects short new passwords without calling the API', async () => {
    const puts = [];
    const { container } = renderChange({ forced: true, puts });
    await screen.findByText(/must be changed before you can continue/i);
    fillAndSubmit(container, 'oldpass1', 'short', 'short');
    expect(screen.getByText(/at least 8 characters/i)).toBeTruthy();
    expect(puts).toHaveLength(0);
  });

  it('rejects mismatched passwords without calling the API', async () => {
    const puts = [];
    const { container } = renderChange({ forced: true, puts });
    await screen.findByText(/must be changed before you can continue/i);
    fillAndSubmit(container, 'oldpass1', 'newpassword1', 'different2');
    expect(screen.getByText(/do not match/i)).toBeTruthy();
    expect(puts).toHaveLength(0);
  });

  it('updates the user before navigating home on success', async () => {
    const puts = [];
    const { container } = renderChange({ forced: true, puts });
    await screen.findByText(/must be changed before you can continue/i);
    fillAndSubmit(container, 'oldpass1', 'newpassword1', 'newpassword1');
    // Landing on home proves setUser cleared the flag first —
    // otherwise the password gate would bounce straight back.
    expect(await screen.findByText('home marker')).toBeTruthy();
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({ current_password: 'oldpass1', new_password: 'newpassword1' });
  });

  it('surfaces a wrong-current-password error and stays put', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      if (u.includes('/api/auth/me')) {
        return jsonResponse({ id: 1, username: 'bob', role: 'user', must_change_password: true });
      }
      return jsonResponse({ error: 'Current password is incorrect' }, false, 401);
    }));
    const { container } = render(
      <MemoryRouter initialEntries={['/change-password']}>
        <AuthProvider>
          <Routes>
            <Route path="/change-password" element={<ChangePassword />} />
            <Route path="/" element={<p>home marker</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );
    await screen.findByText(/must be changed before you can continue/i);
    fillAndSubmit(container, 'wrongpass', 'newpassword1', 'newpassword1');
    expect(await screen.findByText(/current password is incorrect/i)).toBeTruthy();
    expect(screen.queryByText('home marker')).toBeNull();
  });
});
