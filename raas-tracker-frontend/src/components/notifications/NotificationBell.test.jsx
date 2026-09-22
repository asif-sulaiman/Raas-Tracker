// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { NotificationProvider } from '../../context/NotificationContext';
import NotificationBell from './NotificationBell';

vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), {
    error: vi.fn(), warning: vi.fn(), info: vi.fn(), success: vi.fn(),
  });
  return { toast: toastFn };
});

const N1 = {
  id: 1, type: 'upload_mismatch', title: 'Upload has 3 discrepancies: AUG.pdf',
  body: '127 rows checked, 97.6% match.', severity: 'warning', role_scope: 'all',
  entity_type: 'upload', entity_id: 1, created_at: '2026-09-22 10:00:00', is_read: false,
};
const N2 = {
  id: 2, type: 'sale_completed', title: 'Sale completed: ACME',
  body: 'Sale #1 fully paid.', severity: 'info', role_scope: 'all',
  entity_type: 'sale', entity_id: 1, created_at: '2026-09-22 09:00:00', is_read: true,
};

let notifPayload;
let readUnread;

function jsonResponse(data) {
  return { ok: true, status: 200, json: async () => data, headers: { get: () => null } };
}

function installFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/notifications/read')) {
      return jsonResponse({ marked: 1, unread: readUnread });
    }
    if (u.includes('/api/notifications')) {
      return jsonResponse(notifPayload);
    }
    throw new Error(`unexpected fetch: ${u}`);
  }));
}

function renderBell() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <NotificationProvider>
          <NotificationBell />
        </NotificationProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('NotificationBell', () => {
  it('shows the unread badge, opens the panel, and lists items', async () => {
    notifPayload = { items: [N1, N2], unread: 1 };
    readUnread = 0;
    installFetch();
    renderBell();

    const bell = await screen.findByLabelText('Notifications, 1 unread');
    expect(screen.getByTestId('unread-badge').textContent).toBe('1');

    fireEvent.click(bell);
    expect(screen.getByRole('dialog', { name: 'Notifications' })).toBeTruthy();
    expect(screen.getByText(N1.title)).toBeTruthy();
    expect(screen.getByText(N2.title)).toBeTruthy();
    expect(screen.getByText('1 new')).toBeTruthy();
  });

  it('mark all read clears the badge but keeps the read item listed', async () => {
    notifPayload = { items: [N1], unread: 1 };
    readUnread = 0;
    installFetch();
    renderBell();

    await screen.findByLabelText('Notifications, 1 unread');
    fireEvent.click(screen.getByLabelText('Notifications, 1 unread'));
    fireEvent.click(screen.getByText('Mark all read'));

    await waitFor(() => {
      expect(screen.queryByTestId('unread-badge')).toBeNull();
    });
    expect(screen.getByText(N1.title)).toBeTruthy();
    expect(screen.queryByText('1 new')).toBeNull();
  });

  it('shows empty state when there is nothing to show', async () => {
    notifPayload = { items: [], unread: 0 };
    installFetch();
    renderBell();
    const bell = await screen.findByLabelText('Notifications');
    fireEvent.click(bell);
    expect(await screen.findByText("You're all caught up.")).toBeTruthy();
  });

  it('shows an error panel with retry when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const u = String(url);
      if (u.includes('/api/auth/me')) {
        return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      }
      throw new TypeError('network down');
    }));
    renderBell();
    const bell = await screen.findByLabelText('Notifications');
    fireEvent.click(bell);
    expect(await screen.findByText(/Network error/)).toBeTruthy();
    expect(screen.getByText('Retry')).toBeTruthy();
  });

  it('toggles the sound preference', async () => {
    notifPayload = { items: [], unread: 0 };
    installFetch();
    renderBell();
    const bell = await screen.findByLabelText('Notifications');
    fireEvent.click(bell);
    expect(screen.getByText('Sound on')).toBeTruthy();
    fireEvent.click(screen.getByText('Sound on'));
    expect(screen.getByText('Sound off')).toBeTruthy();
  });
});
