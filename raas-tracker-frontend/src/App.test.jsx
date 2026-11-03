// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, act } from '@testing-library/react';
import App from './App';
import { DEFAULT_TIMEOUT_MS } from './utils/api';

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

describe('request deadline', () => {
  it('stops showing "Loading…" when the session check never answers', async () => {
    // The 2026-11-02 outage: the request hung, so `loading` stayed true and
    // ProtectedRoute rendered its spinner forever. The deadline has to end it.
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((path, opts = {}) => {
      if (path === '/api/auth/status') {
        return Promise.resolve({ ok: true, json: async () => ({ setup_needed: false }) });
      }
      // The session check hangs, exactly like a stalled pooler connection.
      return new Promise((_resolve, reject) => {
        opts.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'));
        });
      });
    }));
    window.history.pushState({}, '', '/');
    render(<App />);
    expect(screen.getByText('Loading…')).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS + 100);
    });
    vi.useRealTimers();
    expect(screen.queryByText('Loading…')).toBeNull();
    expect(await screen.findByText('RAAS Tracker Login')).toBeTruthy();
  });
});

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
