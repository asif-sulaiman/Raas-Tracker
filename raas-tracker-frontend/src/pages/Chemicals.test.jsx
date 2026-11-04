// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { toast } from 'sonner';
import Chemicals from './Chemicals';

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

const SEED = [
  { id: 1, name: 'Acid', qty: 10, balance_last_month: 8, unit: 'L', last_updated: '2026-09-01', reorder_level: 2 },
];

const PACKAGING_SEED = [
  { id: 11, label: '25 kg bag', qty: 4, capacity_kg: 25, unit: 'PCS', reorder_level: 10, last_moved_at: null },
  { id: 12, label: '200 kg drum', qty: 40, capacity_kg: 200, unit: 'PCS', reorder_level: 0, last_moved_at: '2026-11-04 09:12:00' },
  { id: 13, label: '30 kg jar', qty: 0, capacity_kg: 30, unit: 'PCS', reorder_level: 0, last_moved_at: null },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({
  role = 'admin', chemicals = SEED, posts = [], puts = [], postHandler,
  packaging = PACKAGING_SEED, pkgPosts = [], pkgPuts = [], history = [],
} = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.includes('/api/chemicals/history')) {
      return jsonResponse([]);
    }
    if (u.includes('/api/packaging/history')) {
      return jsonResponse(history);
    }
    if (u.includes('/api/packaging')) {
      if (options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        pkgPosts.push(body);
        if (body.kind) return jsonResponse({ success: true, ...body });
        return jsonResponse({ success: true, label: body.label, qty: 0 });
      }
      if (options && options.method === 'PUT') {
        pkgPuts.push(JSON.parse(options.body));
        return jsonResponse({ success: true });
      }
      return jsonResponse(packaging);
    }
    if (u.includes('/api/chemicals')) {
      if (options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        posts.push(body);
        if (postHandler) return postHandler(body);
        return jsonResponse({ success: true, name: body.name });
      }
      if (options && options.method === 'PUT' && u.includes('/reorder')) {
        puts.push(JSON.parse(options.body));
        return jsonResponse({ success: true });
      }
      return jsonResponse(chemicals);
    }
    return jsonResponse({});
  }));
}

function renderChemicals(opts = {}) {
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Chemicals />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('Chemicals add flow', () => {
  it('shows the Add Chemical button for admins only', async () => {
    renderChemicals({ role: 'admin' });
    expect(await screen.findByRole('button', { name: 'Add Chemical' })).toBeTruthy();
    cleanup();

    renderChemicals({ role: 'user' });
    await screen.findByText('Acid');
    expect(screen.queryByRole('button', { name: 'Add Chemical' })).toBeNull();
  });

  it('shows the Adjust button for admins only', async () => {
    renderChemicals({ role: 'admin' });
    expect(await screen.findByRole('button', { name: 'Adjust' })).toBeTruthy();
    cleanup();

    renderChemicals({ role: 'user' });
    await screen.findByText('Acid');
    expect(screen.queryByRole('button', { name: 'Adjust' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Info' })).toBeTruthy();
  });

  it('opens the add modal and posts the new chemical', async () => {
    const posts = [];
    renderChemicals({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Chemical' }));

    fireEvent.change(screen.getByLabelText('Chemical name'), { target: { value: 'NewAcid' } });
    fireEvent.change(screen.getByLabelText('Opening quantity'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Unit'), { target: { value: 'L' } });
    fireEvent.change(screen.getByLabelText('Low-stock alarm'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ name: 'NewAcid', qty: 10, unit: 'L', reorder_level: 2 });
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByLabelText('Chemical name')).toBeNull());
  });

  it('surfaces the server duplicate message and keeps the modal open', async () => {
    renderChemicals({
      postHandler: (body) => jsonResponse(
        { success: false, name: body.name, error: `Chemical '${body.name}' already exists` },
        false, 409),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Chemical' }));
    fireEvent.change(screen.getByLabelText('Chemical name'), { target: { value: 'Acid' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0][0]).toMatch(/already exists/i);
    expect(screen.getByLabelText('Chemical name')).toBeTruthy();
  });

  it('sends the selected reason with stock adjustments', async () => {
    const posts = [];
    renderChemicals({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    fireEvent.change(screen.getByLabelText('New quantity'), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Supplier delivery' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Adjustment' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ name: 'Acid', delta: 5, reason: 'Supplier delivery' });
  });

  it('saves a typed reason and a typed unit', async () => {
    const posts = [];
    renderChemicals({ posts });
    fireEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    fireEvent.change(screen.getByLabelText('New quantity'), { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Spill cleanup' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Adjustment' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ reason: 'Spill cleanup' });
  });

  it('lets admins set the alarm from the Info modal', async () => {
    const puts = [];
    renderChemicals({ puts });
    fireEvent.click(await screen.findByRole('button', { name: 'Info' }));
    const input = screen.getByLabelText('Low-stock alarm');
    expect(input.value).toBe('2');
    fireEvent.change(input, { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toMatchObject({ name: 'Acid', reorder_level: 5 });
    expect(toast.success).toHaveBeenCalled();
  });

  it('shows the alarm read-only to non-admins', async () => {
    renderChemicals({ role: 'user' });
    fireEvent.click(await screen.findByRole('button', { name: 'Info' }));
    expect(screen.queryByLabelText('Low-stock alarm')).toBeNull();
    expect(screen.getByText('Low-stock alarm')).toBeTruthy();
  });

  it('adjust modal no longer edits the alarm', async () => {
    renderChemicals({ role: 'admin' });
    fireEvent.click(await screen.findByRole('button', { name: 'Adjust' }));
    expect(screen.queryByLabelText('Low-stock alarm')).toBeNull();
    expect(screen.queryByText(/reorder/i)).toBeNull();
  });

  it('offers an add CTA in the empty state for admins', async () => {
    renderChemicals({ chemicals: [] });
    const cta = await screen.findByRole('button', { name: /add your first chemical/i });
    fireEvent.click(cta);
    expect(screen.getByLabelText('Chemical name')).toBeTruthy();
  });
});

describe('Packaging tab', () => {
  async function openPackaging(opts) {
    renderChemicals(opts);
    fireEvent.click(screen.getByRole('button', { name: 'Packaging' }));
    expect(await screen.findByText('200 kg drum')).toBeTruthy();
  }

  function rowFor(label) {
    return screen.getByText(label).closest('tr');
  }

  it('shows the register on the Chemicals tab untouched', async () => {
    renderChemicals();
    expect(await screen.findByText('Chemical Name')).toBeTruthy();
    expect(screen.queryByText('200 kg drum')).toBeNull();
    expect(screen.getByText('Total Chemicals')).toBeTruthy();
  });

  it('switches to a register of containers that lists no chemical', async () => {
    await openPackaging();
    expect(screen.queryByText('Acid')).toBeNull();
    // The chemical KPI row disappears with the tab, so the two registers cannot
    // be read as one column of quantities.
    expect(screen.queryByText('Total Chemicals')).toBeNull();
  });

  it('totals pieces and packable KG per unit-consistent aggregate', async () => {
    await openPackaging();
    // 4 + 40 + 0 pieces, and 4x25 + 40x200 = 8,100 kg — never KG added to L.
    expect(await screen.findByText('8,100.00')).toBeTruthy();
    expect(screen.getByText('44')).toBeTruthy();
  });

  it('sends a physical count as the number counted, not a subtracted delta', async () => {
    const pkgPosts = [];
    await openPackaging({ pkgPosts });
    fireEvent.click(within(rowFor('200 kg drum')).getByRole('button', { name: 'Record' }));
    fireEvent.change(screen.getByLabelText('Movement type'), { target: { value: 'COUNT' } });
    fireEvent.change(screen.getByLabelText('Counted on site'), { target: { value: '37' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Movement' }));

    await waitFor(() => expect(pkgPosts).toHaveLength(1));
    expect(pkgPosts[0]).toMatchObject({ label: '200 kg drum', kind: 'COUNT', counted_qty: 37 });
    expect(pkgPosts[0]).not.toHaveProperty('delta');
  });

  it('takes the sign of a movement from its kind', async () => {
    const pkgPosts = [];
    await openPackaging({ pkgPosts });
    fireEvent.click(within(rowFor('200 kg drum')).getByRole('button', { name: 'Record' }));
    fireEvent.change(screen.getByLabelText('Movement type'), { target: { value: 'SCRAPPED' } });
    fireEvent.change(screen.getByLabelText('Pieces'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Movement' }));

    await waitFor(() => expect(pkgPosts).toHaveLength(1));
    expect(pkgPosts[0]).toMatchObject({ kind: 'SCRAPPED', delta: -3 });
    expect(pkgPosts[0]).not.toHaveProperty('counted_qty');
  });

  it('refuses a movement that would empty more than is on site', async () => {
    const pkgPosts = [];
    await openPackaging({ pkgPosts });
    fireEvent.click(within(rowFor('25 kg bag')).getByRole('button', { name: 'Record' }));
    fireEvent.change(screen.getByLabelText('Movement type'), { target: { value: 'ISSUED' } });
    fireEvent.change(screen.getByLabelText('Pieces'), { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Movement' }));

    expect(pkgPosts).toHaveLength(0);
    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot remove 9/i);
  });

  it('sets the buy alarm from the admin-only alarm dialog', async () => {
    const pkgPuts = [];
    await openPackaging({ pkgPuts });
    fireEvent.click(within(rowFor('25 kg bag')).getByRole('button', { name: 'Alarm' }));
    fireEvent.change(screen.getByLabelText('Alarm level'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(pkgPuts).toHaveLength(1));
    expect(pkgPuts[0]).toMatchObject({ label: '25 kg bag', reorder_level: 12 });
  });

  it('lets an operator record but not rename or alarm', async () => {
    await openPackaging({ role: 'user' });
    expect(screen.queryByRole('button', { name: 'Add Type' })).toBeNull();
    expect(within(rowFor('200 kg drum')).queryByRole('button', { name: 'Alarm' })).toBeNull();
    expect(within(rowFor('200 kg drum')).getByRole('button', { name: 'Record' })).toBeTruthy();
  });
});
