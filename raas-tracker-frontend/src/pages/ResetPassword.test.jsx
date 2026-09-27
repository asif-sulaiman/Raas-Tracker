// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ResetPassword from './ResetPassword.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function renderReset(entry, fetchImpl) {
  vi.stubGlobal('fetch', fetchImpl);
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <ResetPassword />
    </MemoryRouter>
  );
}

function passwordInputs(container) {
  return container.querySelectorAll('input[type="password"]');
}

describe('ResetPassword page', () => {
  it('shows an error with a login link when the token is missing', () => {
    renderReset('/reset', vi.fn());
    expect(screen.getByText(/missing its token/i)).toBeTruthy();
    expect(screen.getByRole('link', { name: /back to login/i }).getAttribute('href')).toBe('/login');
  });

  it('rejects short passwords without calling the API', () => {
    const fetchMock = vi.fn(async () => jsonResponse({}));
    const { container } = renderReset('/reset?token=abc', fetchMock);
    const [next, confirm] = passwordInputs(container);
    fireEvent.change(next, { target: { value: 'short' } });
    fireEvent.change(confirm, { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: /set new password/i }));
    expect(screen.getByText(/at least 8 characters/i)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects mismatched passwords without calling the API', () => {
    const fetchMock = vi.fn(async () => jsonResponse({}));
    const { container } = renderReset('/reset?token=abc', fetchMock);
    const [next, confirm] = passwordInputs(container);
    fireEvent.change(next, { target: { value: 'newpassword1' } });
    fireEvent.change(confirm, { target: { value: 'different2' } });
    fireEvent.click(screen.getByRole('button', { name: /set new password/i }));
    expect(screen.getByText(/do not match/i)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('submits token + new password and shows success', async () => {
    const calls = [];
    const fetchMock = vi.fn(async (url, options) => {
      calls.push({ url: String(url), body: JSON.parse(options.body) });
      return jsonResponse({ success: true });
    });
    const { container } = renderReset('/reset?token=abc', fetchMock);
    const [next, confirm] = passwordInputs(container);
    fireEvent.change(next, { target: { value: 'newpassword1' } });
    fireEvent.change(confirm, { target: { value: 'newpassword1' } });
    fireEvent.click(screen.getByRole('button', { name: /set new password/i }));
    await waitFor(() => expect(screen.getByText(/password reset/i)).toBeTruthy());
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain('/api/auth/reset-password');
    expect(calls[0].body).toEqual({ token: 'abc', new_password: 'newpassword1' });
    expect(screen.getByRole('link', { name: /go to login now/i })).toBeTruthy();
  });

  it('shows invalid-or-expired error with a login link on 400', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ error: 'bad token' }, false, 400));
    const { container } = renderReset('/reset?token=stale', fetchMock);
    const [next, confirm] = passwordInputs(container);
    fireEvent.change(next, { target: { value: 'newpassword1' } });
    fireEvent.change(confirm, { target: { value: 'newpassword1' } });
    fireEvent.click(screen.getByRole('button', { name: /set new password/i }));
    await waitFor(() => expect(screen.getByText(/invalid or has expired/i)).toBeTruthy());
    expect(screen.getByRole('link', { name: /back to login/i })).toBeTruthy();
  });
});
