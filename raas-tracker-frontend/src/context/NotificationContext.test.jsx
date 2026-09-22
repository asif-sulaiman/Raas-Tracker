// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import React, { createRef, useEffect } from 'react';
import { render, waitFor, cleanup, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { toast } from 'sonner';
import { AuthProvider } from './AuthContext';
import { NotificationProvider, useNotifications } from './NotificationContext';

vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), {
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
  });
  return { toast: toastFn };
});

const N1 = {
  id: 1, type: 'upload_mismatch', title: 'Upload has 3 discrepancies: AUG.pdf',
  body: '127 rows checked, 97.6% match.', severity: 'warning', role_scope: 'all',
  entity_type: 'upload', entity_id: 1, created_at: '2026-09-22 10:00:00', is_read: false,
};
const N2 = {
  id: 2, type: 'stock_out', title: 'Out of stock: Alpha',
  body: 'Alpha has 0 remaining.', severity: 'critical', role_scope: 'all',
  entity_type: 'chemical', entity_id: 5, created_at: '2026-09-22 11:00:00', is_read: false,
};

let notificationQueue;

function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    headers: { get: () => null },
  };
}

function installFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/notifications/read')) {
      return jsonResponse({ marked: 1, unread: 0 });
    }
    if (u.includes('/api/notifications')) {
      const payload = notificationQueue.length > 1 ? notificationQueue.shift() : notificationQueue[0];
      return jsonResponse(payload);
    }
    throw new Error(`unexpected fetch: ${u}`);
  }));
}

const ref = createRef();

function Probe() {
  const ctx = useNotifications();
  useEffect(() => {
    ref.current = ctx;
  });
  return null;
}

function renderProvider() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <NotificationProvider>
          <Probe />
        </NotificationProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

async function settled() {
  // Derived `loading` is false while auth is still resolving — key off real data.
  await waitFor(() => expect(ref.current.items.length).toBeGreaterThan(0));
}

function installFetchError() {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    throw new TypeError('network down');
  }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('NotificationProvider', () => {
  it('loads on mount without toasting the backlog', async () => {
    notificationQueue = [{ items: [N1], unread: 1 }];
    installFetch();
    renderProvider();
    await settled();
    expect(ref.current.items).toHaveLength(1);
    expect(ref.current.unread).toBe(1);
    expect(toast.warning).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.info).not.toHaveBeenCalled();
  });

  it('toasts with the right tone when a refresh reveals a new item', async () => {
    notificationQueue = [{ items: [N1], unread: 1 }];
    installFetch();
    renderProvider();
    await settled();

    notificationQueue = [{ items: [N2, N1], unread: 2 }, ...notificationQueue];
    await act(async () => {
      await ref.current.refresh();
    });

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(toast.error).toHaveBeenCalledWith(N2.title, expect.anything());
    expect(ref.current.unread).toBe(2);
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it('markAllRead clears unread and flags every item read', async () => {
    notificationQueue = [{ items: [N1, N2], unread: 2 }];
    installFetch();
    renderProvider();
    await settled();
    expect(ref.current.unread).toBe(2);

    await act(async () => {
      await ref.current.markAllRead();
    });
    expect(ref.current.unread).toBe(0);
    expect(ref.current.items.every((n) => n.is_read)).toBe(true);
  });

  it('markRead flags only the requested ids', async () => {
    notificationQueue = [{ items: [N1, N2], unread: 2 }];
    installFetch();
    renderProvider();
    await settled();

    await act(async () => {
      await ref.current.markRead([N1.id]);
    });
    const byId = Object.fromEntries(ref.current.items.map((n) => [n.id, n]));
    expect(byId[1].is_read).toBe(true);
    expect(byId[2].is_read).toBe(false);
  });

  it('surfaces a load error without crashing', async () => {
    installFetchError();
    renderProvider();
    await waitFor(() => expect(ref.current.error).toBeTruthy());
    expect(ref.current.error).toContain('Network error');
  });
});
