// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { toast } from 'sonner';
import PurchaseHistory from './PurchaseHistory';

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

function item(id, purchaseId, chemId, name, quantity, unit) {
  return { id, purchase_id: purchaseId, chemical_id: chemId, chemical_name: name, quantity, unit };
}

const PURCHASES = [
  {
    id: 1, supplier_name: 'SupCo', purchase_number: 'PO-1', purchase_date: '2026-11-01',
    delivery_type: 'in_house', client_id: null, client_name: null, cancelled: false, version: 1,
    items: [item(11, 1, 1, 'Acid', 10, 'L')],
  },
  {
    id: 2, supplier_name: 'SupCo', purchase_number: 'PO-2', purchase_date: '2026-10-15',
    delivery_type: 'outward', client_id: 7, client_name: 'Client Co', cancelled: false, version: 2,
    items: [item(21, 2, 2, 'Salt', 5, 'KG'), item(22, 2, 1, 'Acid', 3, 'L')],
  },
  {
    id: 3, supplier_name: 'OldSup', purchase_number: 'PO-3', purchase_date: '2026-09-01',
    delivery_type: 'in_house', client_id: null, client_name: null, cancelled: true, version: 3,
    cancellation_reason: 'Duplicate entry', items: [item(31, 3, 1, 'Acid', 4, 'L')],
  },
];

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({ purchases = PURCHASES, puts = [], cancels = [], putHandler, cancelHandler, role = 'admin' } = {}) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.includes('/api/chemical-purchases')) {
      const method = (options && options.method) || 'GET';
      if (method === 'PUT') {
        const body = JSON.parse(options.body);
        puts.push({ url: u, body });
        if (putHandler) return putHandler(body);
        return jsonResponse({ success: true });
      }
      if (method === 'POST' && u.includes('/cancel')) {
        const body = JSON.parse(options.body);
        cancels.push({ url: u, body });
        if (cancelHandler) return cancelHandler(body);
        return jsonResponse({ success: true });
      }
      const parsed = new URL(u, 'http://localhost');
      const page = Number(parsed.searchParams.get('page') || 1);
      const perPage = Number(parsed.searchParams.get('per_page') || 10);
      const start = (page - 1) * perPage;
      return jsonResponse({
        purchases: purchases.slice(start, start + perPage),
        total: purchases.length, page, per_page: perPage,
      });
    }
    return jsonResponse({});
  }));
}

function renderHistory(opts = {}) {
  installFetch(opts);
  const onChanged = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <PurchaseHistory onChanged={onChanged} />
      </AuthProvider>
    </MemoryRouter>
  );
  return { onChanged };
}

function rowFor(purchaseNumber) {
  return screen.getByText(purchaseNumber).closest('tr');
}

describe('PurchaseHistory', () => {
  it('renders the purchase list from the API', async () => {
    renderHistory();
    expect(await screen.findByText('PO-1')).toBeTruthy();
    expect(screen.getByText('Purchase #')).toBeTruthy();
    expect(screen.getByText('Supplier')).toBeTruthy();
    expect(screen.getByText('Materials')).toBeTruthy();
    const r1 = rowFor('PO-1');
    expect(within(r1).getByText('SupCo')).toBeTruthy();
    expect(within(r1).getByText('In house')).toBeTruthy();
    expect(within(r1).getByText('Active')).toBeTruthy();
    expect(within(r1).getByText('1')).toBeTruthy();
    const r2 = rowFor('PO-2');
    expect(within(r2).getByText('Outward')).toBeTruthy();
    expect(within(r2).getByText('Client Co')).toBeTruthy();
    expect(within(r2).getByText('2')).toBeTruthy();
  });

  it('Cancel opens a confirmation dialog with a reason field', async () => {
    renderHistory();
    await screen.findByText('PO-1');
    fireEvent.click(within(rowFor('PO-1')).getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByLabelText('Cancellation reason')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Cancel Purchase' })).toBeTruthy();
  });

  it('Edit shows a quantity correction form with a reason field', async () => {
    renderHistory();
    await screen.findByText('PO-1');
    fireEvent.click(within(rowFor('PO-1')).getByRole('button', { name: 'Edit' }));
    const qty = await screen.findByLabelText('Quantity for Acid');
    expect(qty.value).toBe('10');
    expect(screen.getByLabelText('Correction reason')).toBeTruthy();
  });

  it('a cancelled purchase shows Cancelled and no edit/cancel actions', async () => {
    renderHistory();
    await screen.findByText('PO-3');
    const r3 = rowFor('PO-3');
    expect(within(r3).getByText('Cancelled')).toBeTruthy();
    expect(within(r3).queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(within(r3).queryByRole('button', { name: 'Cancel' })).toBeNull();
    expect(within(r3).getByRole('button', { name: 'View' })).toBeTruthy();
  });

  it('pagination loads the next page from the server', async () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      id: i + 1, supplier_name: 'SupCo', purchase_number: `PO-${i + 1}`, purchase_date: '2026-11-01',
      delivery_type: 'in_house', client_id: null, client_name: null, cancelled: false, version: 1,
      items: [item(100 + i, i + 1, 1, 'Acid', 10, 'L')],
    }));
    renderHistory({ purchases: many });
    await screen.findByText('PO-1');
    expect(screen.getByText('PO-10')).toBeTruthy();
    expect(screen.queryByText('PO-11')).toBeNull();
    expect(screen.getByRole('button', { name: 'Previous' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('PO-11')).toBeTruthy();
    expect(screen.getByText('PO-12')).toBeTruthy();
    expect(screen.queryByText('PO-1')).toBeNull();
  });

  it('saving a correction PUTs quantities with expected_version and reason', async () => {
    const puts = [];
    const { onChanged } = renderHistory({ puts });
    await screen.findByText('PO-1');
    fireEvent.click(within(rowFor('PO-1')).getByRole('button', { name: 'Edit' }));
    const qty = await screen.findByLabelText('Quantity for Acid');
    fireEvent.change(qty, { target: { value: '15' } });
    fireEvent.change(screen.getByLabelText('Correction reason'), { target: { value: 'Quantity typo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Correction' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].url).toContain('/api/chemical-purchases/1');
    expect(puts[0].body).toMatchObject({
      expected_version: 1, reason: 'Quantity typo', line_patches: [{ id: 11, quantity: 15 }],
    });
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('confirming cancellation POSTs expected_version and reason', async () => {
    const cancels = [];
    const { onChanged } = renderHistory({ cancels });
    await screen.findByText('PO-1');
    fireEvent.click(within(rowFor('PO-1')).getByRole('button', { name: 'Cancel' }));
    const reason = await screen.findByLabelText('Cancellation reason');
    fireEvent.change(reason, { target: { value: 'Duplicate entry' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel Purchase' }));
    await waitFor(() => expect(cancels).toHaveLength(1));
    expect(cancels[0].url).toContain('/api/chemical-purchases/1/cancel');
    expect(cancels[0].body).toMatchObject({ expected_version: 1, reason: 'Duplicate entry' });
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('View opens a read-only detail of the purchase', async () => {
    renderHistory();
    await screen.findByText('PO-2');
    fireEvent.click(within(rowFor('PO-2')).getByRole('button', { name: 'View' }));
    expect(await screen.findByRole('heading', { name: 'Purchase Details' })).toBeTruthy();
    expect(screen.getByText('Salt')).toBeTruthy();
    expect(screen.getByText('Acid')).toBeTruthy();
  });
});