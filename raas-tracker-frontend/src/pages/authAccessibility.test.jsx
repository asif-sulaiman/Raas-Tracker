// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import Login from './Login.jsx';
import Setup from './Setup.jsx';
import ResetPassword from './ResetPassword.jsx';
import ChangePassword from './ChangePassword.jsx';

// A control with neither an associated label nor an aria name is invisible to
// a screen reader, and Lighthouse scored the `label` audit zero on the login
// page for exactly that reason. Every auth page is checked, not just the one
// the audit happened to visit.
function unnamedControls(container) {
  return Array.from(container.querySelectorAll('input, select, textarea'))
    .filter((control) => {
      const labelled = control.labels && control.labels.length > 0;
      const named = Boolean(
        control.getAttribute('aria-label') || control.getAttribute('aria-labelledby'),
      );
      return !labelled && !named;
    })
    .map((control) => control.outerHTML.slice(0, 120));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(data) {
  return { ok: true, status: 200, json: async () => data, headers: { get: () => null } };
}

function renderPage(element, start) {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({})));
  return render(
    <MemoryRouter initialEntries={[start]}>
      <AuthProvider>
        {element}
      </AuthProvider>
    </MemoryRouter>
  );
}

// The pages are held as factories, not as elements: an array of elements
// would need keys for no reason.
const PAGES = [
  ['login', () => <Login />, '/'],
  ['setup', () => <Setup />, '/'],
  ['reset password', () => <ResetPassword />, '/reset?token=abc'],
  ['change password', () => <ChangePassword />, '/change-password'],
];

describe('auth page accessibility', () => {
  for (const [name, page, start] of PAGES) {
    it(`names every control on the ${name} page`, () => {
      const { container } = renderPage(page(), start);
      expect(unnamedControls(container)).toEqual([]);
    });

    it(`gives the ${name} page a main landmark`, () => {
      renderPage(page(), start);
      expect(screen.getByRole('main')).toBeTruthy();
    });
  }

  it('names the forgot-password form too, not only the sign-in form', async () => {
    const { container } = renderPage(<Login />, '/');
    fireEvent.click(await screen.findByRole('button', { name: /forgot password\?/i }));
    expect(unnamedControls(container)).toEqual([]);
  });
});
