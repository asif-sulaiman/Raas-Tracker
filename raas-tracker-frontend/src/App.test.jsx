// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import App from './App';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderAt(path) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: false,
    status: 401,
    json: async () => ({ error: 'authentication required' }),
    headers: { get: () => null },
  })));
  window.history.pushState({}, '', path);
  return render(<App />);
}

describe('lazy routes', () => {
  it('renders the lazily loaded login page', async () => {
    renderAt('/login');
    expect(await screen.findByText('RAAS Tracker Login')).toBeTruthy();
  });

  it('renders the lazily loaded setup page', async () => {
    renderAt('/setup');
    expect(await screen.findByText('Initial Setup')).toBeTruthy();
  });

  it('renders the lazily loaded reset page', async () => {
    renderAt('/reset');
    expect(await screen.findByText('Reset password')).toBeTruthy();
  });
});
