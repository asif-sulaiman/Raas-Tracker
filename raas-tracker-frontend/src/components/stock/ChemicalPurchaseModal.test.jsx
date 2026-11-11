// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { toast } from 'sonner';
import ChemicalPurchaseModal from './ChemicalPurchaseModal';

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

const CATALOG = [
  { id: 1, name: 'Acid', unit: 'L', in_house_registered: true },
  { id: 2, name: 'Salt', unit: 'KG', in_house_registered: true },
  { id: 3, name: 'Solvent', unit: 'L', in_house_registered: false },
];
const COMPANIES = [
  { id: 7, name: 'Client Co' },
  { id: 8, name: 'Other Co' },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ posts = [], postHandler, catalog = CATALOG, companies = COMPANIES } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/chemicals/catalog')) return jsonResponse(catalog);
    if (u.includes('/api/companies')) return jsonResponse(companies);
    if (u.includes('/api/chemical-purchases')) {
      if (options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        posts.push(body);
        if (postHandler) return postHandler(body);
        return jsonResponse({ id: 99, ...body }, true, 201);
      }
      return jsonResponse({ purchases: [], total: 0, page: 1, per_page: 10 });
    }
    return jsonResponse({});
  }));
}

function renderModal(opts = {}) {
  installFetch(opts);
  const onClose = vi.fn();
  const onSuccess = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <ChemicalPurchaseModal isOpen onClose={onClose} onSuccess={onSuccess} />
      </AuthProvider>
    </MemoryRouter>
  );
  return { onClose, onSuccess };
}

async function fillValidHeader() {
  fireEvent.change(await screen.findByLabelText('Supplier name'), { target: { value: 'SupCo' } });
  fireEvent.change(screen.getByLabelText('Purchase number'), { target: { value: 'PO-1' } });
  fireEvent.change(screen.getByLabelText('Purchase date'), { target: { value: '2026-11-01' } });
}

function fillFirstNewLine({ name = 'NewAcid', unit = 'L', quantity = '10', reorder = '2' } = {}) {
  const row = screen.getByRole('group', { name: 'Material 1' });
  fireEvent.change(within(row).getByLabelText('Chemical name'), { target: { value: name } });
  fireEvent.change(within(row).getByLabelText('Unit'), { target: { value: unit } });
  fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: quantity } });
  if (reorder !== '') {
    fireEvent.change(within(row).getByLabelText('Low-stock alarm'), { target: { value: reorder } });
  }
}

describe('ChemicalPurchaseModal', () => {
  it('renders shared header fields and one material row', async () => {
    renderModal();
    expect(await screen.findByLabelText('Supplier name')).toBeTruthy();
    expect(screen.getByLabelText('Purchase number')).toBeTruthy();
    expect(screen.getByLabelText('Purchase date')).toBeTruthy();
    expect(screen.getByLabelText('Delivery type')).toBeTruthy();
    expect(screen.getByText('Record a multi-material purchase')).toBeTruthy();
    const rows = screen.getAllByRole('group', { name: /Material \d+/ });
    expect(rows).toHaveLength(1);
    expect(within(rows[0]).getByLabelText('Chemical name')).toBeTruthy();
    expect(within(rows[0]).getByLabelText('Unit')).toBeTruthy();
    expect(within(rows[0]).getByLabelText('Quantity')).toBeTruthy();
    expect(screen.queryByLabelText('Client')).toBeNull();
  });

  it('Add material button appends a row', async () => {
    renderModal();
    await screen.findByLabelText('Supplier name');
    fireEvent.click(screen.getByRole('button', { name: 'Add material' }));
    expect(screen.getAllByRole('group', { name: /Material \d+/ })).toHaveLength(2);
    expect(screen.getByRole('group', { name: 'Material 2' })).toBeTruthy();
  });

  it('Remove button deletes a row but keeps at least one', async () => {
    renderModal();
    await screen.findByLabelText('Supplier name');
    fireEvent.click(screen.getByRole('button', { name: 'Add material' }));
    expect(screen.getAllByRole('group', { name: /Material \d+/ })).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Remove material 2' }));
    expect(screen.getAllByRole('group', { name: /Material \d+/ })).toHaveLength(1);
    const onlyRemove = screen.getByRole('button', { name: 'Remove material 1' });
    expect(onlyRemove.disabled).toBe(true);
    fireEvent.click(onlyRemove);
    expect(screen.getAllByRole('group', { name: /Material \d+/ })).toHaveLength(1);
  });

  it('Switching to Outward shows the client selector', async () => {
    renderModal();
    await screen.findByLabelText('Supplier name');
    expect(screen.queryByLabelText('Client')).toBeNull();
    fireEvent.change(screen.getByLabelText('Delivery type'), { target: { value: 'outward' } });
    expect(await screen.findByLabelText('Client')).toBeTruthy();
    expect(await screen.findByText('Client Co')).toBeTruthy();
  });

  it('Switching back to In house hides the client selector', async () => {
    renderModal();
    await screen.findByLabelText('Supplier name');
    fireEvent.change(screen.getByLabelText('Delivery type'), { target: { value: 'outward' } });
    await screen.findByLabelText('Client');
    fireEvent.change(screen.getByLabelText('Delivery type'), { target: { value: 'in_house' } });
    expect(screen.queryByLabelText('Client')).toBeNull();
  });

  it('Existing chemical mode shows a searchable selector with readonly unit', async () => {
    renderModal();
    await screen.findByLabelText('Supplier name');
    const row = screen.getByRole('group', { name: 'Material 1' });
    fireEvent.click(within(row).getByRole('button', { name: 'Existing Chemical' }));
    const selector = within(row).getByLabelText('Existing chemical');
    expect(selector).toBeTruthy();
    await waitFor(() => expect(document.querySelector('datalist option[value="Acid"]')).toBeTruthy());
    fireEvent.change(selector, { target: { value: 'Acid' } });
    const unit = within(row).getByLabelText('Unit');
    expect(unit.value).toBe('L');
    expect(unit.readOnly).toBe(true);
  });

  it('New chemical mode shows editable name and unit inputs', async () => {
    renderModal();
    await screen.findByLabelText('Supplier name');
    const row = screen.getByRole('group', { name: 'Material 1' });
    expect(within(row).getByLabelText('Chemical name')).toBeTruthy();
    expect(within(row).getByLabelText('Unit').readOnly).toBe(false);
    fireEvent.click(within(row).getByRole('button', { name: 'Existing Chemical' }));
    expect(within(row).queryByLabelText('Chemical name')).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: 'New Chemical' }));
    expect(within(row).getByLabelText('Chemical name')).toBeTruthy();
    expect(within(row).getByLabelText('Unit').readOnly).toBe(false);
  });

  it('Save is disabled when quantity is empty or zero', async () => {
    renderModal();
    await fillValidHeader();
    const row = screen.getByRole('group', { name: 'Material 1' });
    fireEvent.change(within(row).getByLabelText('Chemical name'), { target: { value: 'NewAcid' } });
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save.disabled).toBe(true);
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '0' } });
    expect(save.disabled).toBe(true);
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '5' } });
    expect(save.disabled).toBe(false);
  });

  it('Successful save posts the purchase, calls onSuccess and closes', async () => {
    const posts = [];
    const { onClose, onSuccess } = renderModal({ posts });
    await fillValidHeader();
    fillFirstNewLine();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      supplier_name: 'SupCo',
      purchase_number: 'PO-1',
      purchase_date: '2026-11-01',
      delivery_type: 'in_house',
      client_id: null,
    });
    expect(posts[0].lines).toHaveLength(1);
    expect(posts[0].lines[0]).toMatchObject({
      chemical_name: 'NewAcid', chemical_id: null, quantity: 10, unit: 'L', is_new: true, reorder_level: 2,
    });
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalled();
  });

  it('Server error preserves form values and shows a toast', async () => {
    const posts = [];
    const { onClose } = renderModal({
      posts,
      postHandler: () => jsonResponse({ error: "unit mismatch for 'Acid': registered L, requested KG" }, false, 400),
    });
    await fillValidHeader();
    fillFirstNewLine();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.error.mock.calls[0][0]).toMatch(/unit mismatch/i);
    expect(screen.getByLabelText('Supplier name').value).toBe('SupCo');
    expect(screen.getByLabelText('Purchase number').value).toBe('PO-1');
    const row = screen.getByRole('group', { name: 'Material 1' });
    expect(within(row).getByLabelText('Quantity').value).toBe('10');
    expect(within(row).getByLabelText('Chemical name').value).toBe('NewAcid');
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Save' }).disabled).toBe(false);
  });

  it('attaches each server field error to its own material row, not one banner', async () => {
    const posts = [];
    renderModal({
      posts,
      postHandler: () => jsonResponse({
        error: 'Invalid payload',
        details: [
          { field: 'lines.0.quantity', message: 'Input should be greater than 0' },
          { field: 'lines.1.unit', message: 'String should have at least 1 character' },
        ],
      }, false, 400),
    });
    await fillValidHeader();
    fillFirstNewLine();
    fireEvent.click(screen.getByRole('button', { name: 'Add material' }));
    const row2 = screen.getByRole('group', { name: 'Material 2' });
    fireEvent.change(within(row2).getByLabelText('Chemical name'), { target: { value: 'NewBase' } });
    fireEvent.change(within(row2).getByLabelText('Unit'), { target: { value: 'KG' } });
    fireEvent.change(within(row2).getByLabelText('Quantity'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    const row1 = screen.getByRole('group', { name: 'Material 1' });
    await waitFor(() => expect(within(row1).getByRole('alert')).toBeTruthy());
    expect(within(row1).getByRole('alert').textContent).toContain('greater than 0');
    expect(within(row2).getByRole('alert').textContent).toContain('at least 1 character');
    // Both errors landed on their fields, so no generic banner toast is needed.
    expect(toast.error).not.toHaveBeenCalled();
    expect(posts).toHaveLength(1);
  });

  it('Outward save requires a client and posts client_id', async () => {
    const posts = [];
    renderModal({ posts });
    await fillValidHeader();
    fillFirstNewLine();
    fireEvent.change(screen.getByLabelText('Delivery type'), { target: { value: 'outward' } });
    await screen.findByLabelText('Client');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(posts).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Client'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ delivery_type: 'outward', client_id: 7 });
  });
});