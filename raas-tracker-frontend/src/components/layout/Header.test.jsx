// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { NotificationProvider } from '../../context/NotificationContext';
import Header from './Header.jsx';
import Sidebar from './Sidebar.jsx';
import { ICON_HIT_AREA } from '../ui/Button.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data) {
  return { ok: true, status: 200, json: async () => data, headers: { get: () => null } };
}

// The app shell as Layout renders it: sidebar + header above the routed page.
function renderShell({ start = '/reports' } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin', must_change_password: false });
    }
    return jsonResponse({ setup_needed: false, notifications: [], unread: 0 });
  }));
  return render(
    <MemoryRouter initialEntries={[start]}>
      <AuthProvider>
        <NotificationProvider>
          <Routes>
            <Route path="/" element={<p>home marker</p>} />
            <Route path="/reports" element={(
              <div>
                <Sidebar />
                <Header onOpenNavigation={() => {}} />
              </div>
            )} />
          </Routes>
        </NotificationProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('touch targets', () => {
  it('gives every icon-only header control the shared 44px hit area', () => {
    renderShell();
    const controls = [
      screen.getByRole('button', { name: 'Open navigation' }),
      screen.getByTitle('Change password'),
      screen.getByTitle('Log out'),
      screen.getByRole('button', { name: /Notifications/ }),
    ];

    for (const control of controls) {
      expect(control.className).toContain(ICON_HIT_AREA);
    }
  });
});

describe('brand link to Home', () => {
  it('makes the header brand a link pointing at Home', async () => {
    renderShell();
    const links = await screen.findAllByRole('link', { name: /RAAS Tracker/ });
    expect(links.every((l) => l.getAttribute('href') === '/')).toBe(true);
  });

  it('navigates to Home when the header brand is clicked', async () => {
    renderShell();
    const headerBrand = within(screen.getByRole('banner'))
      .getByRole('link', { name: /RAAS Tracker/ });
    fireEvent.click(headerBrand);
    expect(await screen.findByText('home marker')).toBeTruthy();
  });

  it('links the sidebar brand to Home as well', async () => {
    renderShell();
    const sidebarBrand = within(screen.getByRole('complementary'))
      .getByRole('link', { name: /RAAS Tracker/ });
    expect(sidebarBrand.getAttribute('href')).toBe('/');
    fireEvent.click(sidebarBrand);
    expect(await screen.findByText('home marker')).toBeTruthy();
  });
});
