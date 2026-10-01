// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import LinkPIsModal from './LinkPIsModal';

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
  vi.clearAllMocks();
});

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

const PI_ACME_1 = {
  id: 1, pi_number: 'PI-001', company_id: 7, company_name: 'Acme',
  client_name: 'Acme', total_value: 1000, pi_date: '2026-09-01',
};
const PI_ACME_2 = {
  id: 2, pi_number: 'PI-002', company_id: 7, company_name: 'Acme',
  client_name: 'Acme', total_value: 2000, pi_date: '2026-09-02',
};
const PI_BETA = {
  id: 3, pi_number: 'PI-003', company_id: 8, company_name: 'Beta',
  client_name: 'Beta', total_value: 500, pi_date: '2026-09-03',
};
const LC_ACME = {
  id: 10, lc_number: 'LC-100', company_name: 'Acme', client_name: 'Acme', pi_count: 0,
};

function installFetch({ attaches = [] } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (/\/api\/lcs\/\d+\/pis/.test(u) && method === 'POST') {
      attaches.push({ url: u, body: JSON.parse(options.body) });
      return jsonResponse({ id: 10 });
    }
    if (u.endsWith('/api/lcs') && method === 'POST') {
      return jsonResponse({ id: 11, lc_number: 'LC-NEW' }, true, 201);
    }
    return jsonResponse({});
  }));
}

function renderLink(props, fetchOpts) {
  installFetch(fetchOpts);
  const onClose = vi.fn();
  const onLinked = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <LinkPIsModal
          isOpen
          unlinkedSales={props.unlinkedSales}
          existingLcs={props.existingLcs}
          onClose={onClose}
          onLinked={onLinked}
        />
      </AuthProvider>
    </MemoryRouter>
  );
  return { onClose, onLinked };
}

describe('LinkPIsModal attach wiring', () => {
  it('attaches a checked PI to the existing LC with one POST', async () => {
    const attaches = [];
    const { onLinked } = renderLink(
      { unlinkedSales: [PI_ACME_1, PI_ACME_2], existingLcs: [LC_ACME] },
      { attaches },
    );

    fireEvent.click(await screen.findByLabelText('Select PI PI-001'));
    fireEvent.change(screen.getByLabelText('Existing LC', { selector: 'select' }), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Link 1 PI' }));

    await waitFor(() => expect(attaches).toHaveLength(1));
    expect(attaches[0].url).toContain('/api/lcs/10/pis');
    expect(attaches[0].body).toEqual({ sale_ids: [1] });
    await waitFor(() => expect(onLinked).toHaveBeenCalled());
  });

  it('surfaces the company-mismatch panel and blocks submit', async () => {
    renderLink(
      { unlinkedSales: [PI_ACME_1, PI_BETA], existingLcs: [LC_ACME] },
      {},
    );

    fireEvent.click(await screen.findByLabelText('Select PI PI-001'));
    fireEvent.click(screen.getByLabelText('Select PI PI-003'));

    expect(await screen.findByText(/Company mismatch/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Link 2 PIs/ }).disabled).toBe(true);
  });

  it('F2 blocks new-LC submit when the selected PI has no company', async () => {
    const PI_NO_COMPANY = {
      id: 9, pi_number: 'PI-009', company_id: null, company_name: null,
      client_name: 'Acme', total_value: 100, pi_date: '2026-09-04',
    };
    const posts = [];
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      const method = options?.method || 'GET';
      if (u.includes('/api/auth/me')) {
        return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      }
      if (u.endsWith('/api/lcs') && method === 'POST') {
        posts.push(JSON.parse(options.body));
        return jsonResponse({ id: 11, lc_number: 'LC-NEW' }, true, 201);
      }
      if (/\/api\/lcs\/\d+\/pis/.test(u) && method === 'POST') {
        return jsonResponse({ id: 11 });
      }
      return jsonResponse({});
    }));
    const onClose = vi.fn();
    const onLinked = vi.fn();
    render(
      <MemoryRouter>
        <AuthProvider>
          <LinkPIsModal
            isOpen
            unlinkedSales={[PI_NO_COMPANY]}
            existingLcs={[]}
            onClose={onClose}
            onLinked={onLinked}
          />
        </AuthProvider>
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByLabelText('Select PI PI-009'));
    fireEvent.change(screen.getByLabelText('New LC number'), { target: { value: 'LC-NEW' } });

    expect(await screen.findByText(/Selected PI has no company/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Link 1 PI/ }).disabled).toBe(true);
    expect(posts).toHaveLength(0);
  });
});
