// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { NotificationProvider } from '../../context/NotificationContext';
import Layout from './Layout.jsx';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data) {
  return { ok: true, status: 200, json: async () => data, headers: { get: () => null } };
}

// The real app shell: Layout owns the drawer state, Sidebar renders it,
// Header carries the control that opens it.
function renderShell() {
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin', must_change_password: false });
    }
    return jsonResponse({ setup_needed: false, notifications: [], unread: 0 });
  }));
  return render(
    <MemoryRouter initialEntries={['/reports']}>
      <AuthProvider>
        <NotificationProvider>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<p>home marker</p>} />
              <Route path="/reports" element={<p>reports marker</p>} />
              <Route path="/sales" element={<p>sales marker</p>} />
            </Route>
          </Routes>
        </NotificationProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

const menuButton = () => screen.getByRole('button', { name: 'Open navigation' });
const drawer = () => screen.queryByRole('dialog', { name: 'Navigation' });

describe('mobile navigation drawer', () => {
  it('keeps the drawer closed on load', () => {
    renderShell();
    expect(drawer()).toBeNull();
  });

  it('opens the drawer from the header menu button', () => {
    renderShell();
    fireEvent.click(menuButton());
    expect(drawer()).toBeTruthy();
  });

  it('reports expanded state on the menu button', () => {
    renderShell();
    expect(menuButton().getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(menuButton());
    expect(menuButton().getAttribute('aria-expanded')).toBe('true');
  });

  it('closes the drawer when the backdrop is pressed', () => {
    renderShell();
    fireEvent.click(menuButton());
    fireEvent.click(within(drawer()).getByRole('button', { name: 'Close navigation' }));
    expect(drawer()).toBeNull();
  });

  it('closes the drawer when a nav item is chosen', () => {
    renderShell();
    fireEvent.click(menuButton());
    fireEvent.click(within(drawer()).getByRole('link', { name: /Sales Pipeline/ }));
    expect(drawer()).toBeNull();
  });

  it('closes the drawer on Escape', () => {
    renderShell();
    fireEvent.click(menuButton());
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(drawer()).toBeNull();
  });

  it('returns focus to the menu button when the drawer closes', () => {
    renderShell();
    const trigger = menuButton();
    trigger.focus();
    fireEvent.click(trigger);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.activeElement).toBe(trigger);
  });

  it('keeps Tab inside the open drawer', () => {
    renderShell();
    const trigger = menuButton();
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = drawer();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Close menu' }));
    const links = within(dialog).getAllByRole('link');
    links[0].focus();
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(links[links.length - 1]);
  });

  it('closes the drawer when the route changes', () => {
    renderShell();
    fireEvent.click(menuButton());
    // The header brand is a link to Home; navigating is a route change, not a
    // nav-item click, so the drawer must close from the router effect.
    fireEvent.click(within(screen.getByRole('banner')).getByRole('link', { name: /RAAS Tracker/ }));
    return screen.findByText('home marker').then(() => {
      expect(drawer()).toBeNull();
    });
  });
});

describe('desktop rail', () => {
  it('collapses to icons and offers an expand control', () => {
    renderShell();
    const shell = screen.getByRole('complementary');
    const sales = within(shell).getByRole('link', { name: /Sales Pipeline/ });
    expect(sales.getAttribute('title')).toBe('');

    fireEvent.click(within(shell).getByRole('button', { name: 'Collapse navigation' }));

    expect(within(shell).getByRole('button', { name: 'Expand navigation' })).toBeTruthy();
    expect(within(shell).getByRole('link', { name: /Sales Pipeline/ }).getAttribute('title'))
      .toBe('Sales Pipeline');
  });
});
