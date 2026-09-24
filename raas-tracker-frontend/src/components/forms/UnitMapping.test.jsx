// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import UnitMapping from './UnitMapping';

vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), {
    error: vi.fn(), warning: vi.fn(), info: vi.fn(), success: vi.fn(),
  });
  return { toast: toastFn };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ROWS = [
  { name: 'LAB', upload_this: 10, upload_unit: 'Drum', db_unit: 'KG', unit_match: false },
  { name: 'SLES', upload_this: 5, upload_unit: 'Drum', db_unit: 'KG', unit_match: false },
  { name: 'Salt', upload_this: 2, upload_unit: 'Bag', db_unit: 'KG', unit_match: false },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch(posts) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/unit-conversions')) {
      if (options && options.method === 'POST') {
        posts.push(JSON.parse(options.body));
        return jsonResponse({ success: true });
      }
      return jsonResponse([{ from_unit: 'DRUM', to_unit: 'KG', factor: 200 }]);
    }
    return jsonResponse({});
  }));
}

function renderMapping(rows = ROWS, onMappingsSaved = vi.fn()) {
  installFetch([]);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <UnitMapping rows={rows} onMappingsSaved={onMappingsSaved} />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('UnitMapping', () => {
  it('groups identical units into one editor with a banner', async () => {
    renderMapping();
    expect(await screen.findByText(/2 unmapped units/i)).toBeTruthy();
    // Two Drum rows share one factor input; the Bag row has its own.
    expect(screen.getAllByPlaceholderText('200')).toHaveLength(2);
    expect(screen.getByText(/LAB/)).toBeTruthy();
  });

  it('shows a live preview once target and factor are set', async () => {
    renderMapping();
    const inputs = await screen.findAllByPlaceholderText('200');
    fireEvent.change(inputs[0], { target: { value: '200' } });
    // Drum group: (10 + 5) * 200 = 3,000 KG (target defaults to KG).
    expect(await screen.findByText(/3,000.*KG/)).toBeTruthy();
  });

  it('disables save until every group is valid, then posts mappings', async () => {
    const posts = [];
    const saved = vi.fn();
    installFetch(posts);
    render(
      <MemoryRouter>
        <AuthProvider>
          <UnitMapping rows={ROWS} onMappingsSaved={saved} />
        </AuthProvider>
      </MemoryRouter>
    );
    const btn = await screen.findByRole('button', { name: /save mappings/i });
    expect(btn.disabled).toBe(true);
    const inputs = await screen.findAllByPlaceholderText('200');
    fireEvent.change(inputs[0], { target: { value: '200' } });
    fireEvent.change(inputs[1], { target: { value: '25' } });
    await waitFor(() => expect(btn.disabled).toBe(false));
    fireEvent.click(btn);
    await waitFor(() => expect(saved).toHaveBeenCalledWith(['DRUM', 'BAG']));
    expect(posts).toHaveLength(2);
    expect(posts[0]).toMatchObject({ from_unit: 'DRUM', to_unit: 'KG', factor: 200 });
    expect(posts[1]).toMatchObject({ from_unit: 'BAG', factor: 25 });
  });

  it('renders nothing without flagged rows', () => {
    installFetch([]);
    const { container } = render(
      <MemoryRouter>
        <AuthProvider>
          <UnitMapping rows={[]} onMappingsSaved={vi.fn()} />
        </AuthProvider>
      </MemoryRouter>
    );
    expect(container.textContent).toBe('');
  });
});
