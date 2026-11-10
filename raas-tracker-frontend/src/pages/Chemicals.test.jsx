// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { toast } from 'sonner';
import Chemicals from './Chemicals';

vi.mock('sonner', () => {
  const toastFn = Object.assign(vi.fn(), {
    error: vi.fn(), warning: vi.fn(), info: vi.fn(), success: vi.fn(),
  });
  return { toast: toastFn };
});

// The outward register hands a receipt line to the recipe flow through the
// router, so the tests read the location the page navigated to.
function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">
      {JSON.stringify({ pathname: location.pathname, state: location.state })}
    </div>
  );
}

function lastLocation() {
  return JSON.parse(screen.getByTestId('location').textContent);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SEED = [
  {
    id: 1, name: 'Acid', qty: 10, balance_last_month: 8, unit: 'L', last_updated: '2026-09-01',
    reorder_level: 2, last_purchase_date: '2026-10-20', last_purchase_quantity: 12.5,
    supplier_name: 'Acme Chemicals',
  },
];

const PACKAGING_SEED = [
  { id: 11, label: '25 kg bag', qty: 4, capacity_kg: 25, unit: 'PCS', reorder_level: 10, last_moved_at: null },
  { id: 12, label: '200 kg drum', qty: 40, capacity_kg: 200, unit: 'PCS', reorder_level: 0, last_moved_at: '2026-11-04 09:12:00' },
  { id: 13, label: '30 kg jar', qty: 0, capacity_kg: 30, unit: 'PCS', reorder_level: 0, last_moved_at: null },
];

function item(id, purchaseId, chemId, name, quantity, unit) {
  return { id, purchase_id: purchaseId, chemical_id: chemId, chemical_name: name, quantity, unit };
}

const OUTWARD_PURCHASE = {
  id: 7, supplier_name: 'SupCo', purchase_number: 'PO-OUT', purchase_date: '2026-10-30',
  delivery_type: 'outward', client_id: 7, client_name: 'Client Co', cancelled: false, version: 1,
  items: [item(71, 7, 3, 'Solvent', 20, 'L')],
};

const CANCELLED_OUTWARD = {
  ...OUTWARD_PURCHASE, id: 8, purchase_number: 'PO-GONE', cancelled: true,
  items: [item(81, 8, 3, 'Glacial', 5, 'KG')],
};

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({
  role = 'admin', chemicals = SEED, posts = [], puts = [], postHandler,
  packaging = PACKAGING_SEED, pkgPosts = [], pkgPuts = [], history = [],
  purchases = [], purchasePosts = [], coverage = null, unstockedRequirements = [],
  inventoryHandler,
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
    if (u.includes('/api/chemical-purchases')) {
      if (options && options.method === 'POST') {
        const body = JSON.parse(options.body);
        purchasePosts.push(body);
        return jsonResponse({ id: 99, ...body }, true, 201);
      }
      const parsed = new URL(u, 'http://localhost');
      const dt = parsed.searchParams.get('delivery_type');
      const list = dt ? purchases.filter((p) => p.delivery_type === dt) : purchases;
      return jsonResponse({ purchases: list, total: list.length, page: 1, per_page: 50 });
    }
    if (u.includes('/api/companies')) {
      return jsonResponse([{ id: 7, name: 'Client Co' }]);
    }
    if (u.includes('/api/chemicals/catalog')) {
      return jsonResponse([{ id: 1, name: 'Acid', unit: 'L', in_house_registered: true }]);
    }
    if (u.includes('/api/chemicals/inventory')) {
      if (inventoryHandler) return inventoryHandler();
      return jsonResponse({ materials: chemicals, coverage, unstocked_requirements: unstockedRequirements });
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
      <LocationProbe />
      <AuthProvider>
        <Chemicals />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('Chemicals stock table', () => {
  it('shows the nine Slice 1 columns and latest purchase fields', async () => {
    renderChemicals();
    expect(await screen.findByText('Raw Material')).toBeTruthy();
    for (const header of ['Current Stock', 'Unit', 'Last Purchase Date', 'Last Purchase Quantity', 'Supplier Name', 'Projected Quantity', 'Status', 'Actions']) {
      expect(screen.getByText(header)).toBeTruthy();
    }
    expect(screen.queryByText('Last Month')).toBeNull();
    expect(screen.queryByText('This Month')).toBeNull();
    expect(screen.queryByText('Variance')).toBeNull();
    expect(screen.getByText('2026-10-20')).toBeTruthy();
    expect(screen.getByText('12.50')).toBeTruthy();
    expect(screen.getByText('Acme Chemicals')).toBeTruthy();
  });

  it('Projected Quantity shows a dash for missing data with the live planning tooltip', async () => {
    renderChemicals();
    const cell = await screen.findByTitle('Additional quantity to purchase for active PI plans');
    expect(cell.textContent).toBe('—');
    expect(cell.getAttribute('aria-label')).toMatch(/unknown.*conversion.*active plan/i);
  });

  it.each([
    [null, '—'], [0, '0'], [1234.5, '1,234.50 L'],
  ])('renders projected quantity %s without confusing zero and unknown', async (quantity, expected) => {
    renderChemicals({ chemicals: [{ ...SEED[0], projected_quantity: quantity }] });
    const cell = await screen.findByTitle('Additional quantity to purchase for active PI plans');
    expect(cell.textContent).toBe(expected);
    expect(screen.getAllByRole('columnheader')).toHaveLength(9);
  });

  it('lists open PIs without plans even when the active plans themselves are complete', async () => {
    renderChemicals({ coverage: {
      state: 'complete', active_plan_count: 2, open_pi_count: 4,
      open_pis_without_plan: [
        { sale_id: 5, pi_number: 'PI-5', products: ['Cotton'] },
        { sale_id: 6, pi_number: 'PI-6', products: ['Linen'] },
      ], incomplete_plans: [],
    } });
    const warning = await screen.findByRole('alert');
    expect(warning).toHaveTextContent('2 open PIs lack an active formulation');
    expect(warning).toHaveTextContent('PI-5');
    expect(warning).toHaveTextContent('PI-6');
    expect(warning).toHaveTextContent(/incomplete/i);
  });

  it('distinguishes incomplete active plans and names their affected products', async () => {
    renderChemicals({ coverage: {
      state: 'incomplete', active_plan_count: 1, open_pi_count: 1,
      open_pis_without_plan: [],
      incomplete_plans: [{ sale_id: 9, pi_number: 'PI-9', products: ['Resin'] }],
    } });
    const warning = await screen.findByRole('alert');
    expect(warning).toHaveTextContent(/active plans.*incomplete/i);
    expect(warning).toHaveTextContent('PI-9');
    expect(warning).toHaveTextContent('Resin');
    expect(screen.queryByText(/open PIs lack/)).toBeNull();
  });

  it('distinguishes no active plans from a complete zero-shortage projection', async () => {
    renderChemicals({ coverage: {
      state: 'no_active_plans', active_plan_count: 0, open_pi_count: 0,
      open_pis_without_plan: [], incomplete_plans: [],
    } });
    expect(await screen.findByText(/No active PI plans/)).toBeTruthy();
    expect(screen.queryByText(/open PIs lack/)).toBeNull();
  });

  it('hides coverage warnings and unstocked section for complete covered inventory', async () => {
    renderChemicals({ coverage: {
      state: 'complete', active_plan_count: 1, open_pi_count: 1,
      open_pis_without_plan: [], incomplete_plans: [],
    } });
    await screen.findByText('Raw Material');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/Unstocked requirements/)).toBeNull();
    expect(screen.queryByText(/No active PI plans/)).toBeNull();
  });

  it('shows collapsible unstocked requirements with required and projected amounts even without stock rows', async () => {
    renderChemicals({ chemicals: [], unstockedRequirements: [
      { name: 'Resin', unit: 'KG', required_quantity: 42.5, projected_quantity: 42.5 },
      { name: 'Binder', unit: 'L', required_quantity: null, projected_quantity: null },
    ] });
    const summary = await screen.findByText(/Unstocked requirements/);
    const details = summary.closest('details');
    expect(details).toBeTruthy();
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    const resin = within(details).getByText('Resin').closest('li');
    expect(resin).toHaveTextContent('Required: 42.50 KG');
    expect(resin).toHaveTextContent('Projected purchase: 42.50 KG');
    const binder = within(details).getByText('Binder').closest('li');
    expect(binder).toHaveTextContent('Required: —');
    expect(binder).toHaveTextContent('Projected purchase: —');
  });

  it('shows inventory failure and retries rather than displaying an empty forecast', async () => {
    let reads = 0;
    renderChemicals({ inventoryHandler: () => {
      reads += 1;
      return reads === 1 ? jsonResponse({ error: 'Projection unavailable' }, false, 503) : jsonResponse({
        materials: [{ ...SEED[0], projected_quantity: 7 }], coverage: null, unstocked_requirements: [],
      });
    } });
    expect(await screen.findByRole('alert')).toHaveTextContent('Projection unavailable');
    expect(screen.queryByText('No chemicals registered yet')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTitle('Additional quantity to purchase for active PI plans')).toHaveTextContent('7.00 L');
  });

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

  it('Add Chemical opens purchase modal with shared header and material rows', async () => {
    renderChemicals();
    fireEvent.click(await screen.findByRole('button', { name: 'Add Chemical' }));
    expect(await screen.findByLabelText('Supplier name')).toBeTruthy();
    expect(screen.getByLabelText('Purchase number')).toBeTruthy();
    expect(screen.getByLabelText('Purchase date')).toBeTruthy();
    expect(screen.getByLabelText('Delivery type')).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Material 1' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add material' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save' })).toBeTruthy();
  });

  it('saves a purchase from the page modal and refreshes the stock table', async () => {
    const purchasePosts = [];
    renderChemicals({ purchasePosts });
    fireEvent.click(await screen.findByRole('button', { name: 'Add Chemical' }));
    fireEvent.change(await screen.findByLabelText('Supplier name'), { target: { value: 'SupCo' } });
    fireEvent.change(screen.getByLabelText('Purchase number'), { target: { value: 'PO-9' } });
    fireEvent.change(screen.getByLabelText('Purchase date'), { target: { value: '2026-11-01' } });
    const row = screen.getByRole('group', { name: 'Material 1' });
    fireEvent.change(within(row).getByLabelText('Chemical name'), { target: { value: 'NewAcid' } });
    fireEvent.change(within(row).getByLabelText('Quantity'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(purchasePosts).toHaveLength(1));
    expect(purchasePosts[0]).toMatchObject({
      supplier_name: 'SupCo', purchase_number: 'PO-9', delivery_type: 'in_house', client_id: null,
    });
    expect(toast.success).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByLabelText('Supplier name')).toBeNull());
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
    expect(await screen.findByLabelText('Supplier name')).toBeTruthy();
  });

  it('shows Purchase History below the table for admins only', async () => {
    renderChemicals({ role: 'admin' });
    expect(await screen.findByText('Purchase History')).toBeTruthy();
    cleanup();

    renderChemicals({ role: 'user' });
    await screen.findByText('Acid');
    expect(screen.queryByText('Purchase History')).toBeNull();
  });
});

describe('Outward tab', () => {
  it('Outward tab shows outward purchases', async () => {
    renderChemicals({ purchases: [OUTWARD_PURCHASE] });
    fireEvent.click(await screen.findByRole('button', { name: 'Outward' }));
    expect(await screen.findByText('Solvent')).toBeTruthy();
    expect(screen.getByText('PO-OUT')).toBeTruthy();
    expect(screen.getByText('Client Co')).toBeTruthy();
    expect(screen.getByText('20.00')).toBeTruthy();
    expect(screen.getByText('Active')).toBeTruthy();
    expect(screen.getByRole('button', { name: /use in invoice recipe/i })).toBeTruthy();
    expect(screen.queryByText('Acid')).toBeNull();
  });

  it('adds an Actions column with the recipe entry point on active lines only', async () => {
    renderChemicals({ purchases: [OUTWARD_PURCHASE, CANCELLED_OUTWARD] });
    fireEvent.click(await screen.findByRole('button', { name: 'Outward' }));

    expect(await screen.findByText('Actions')).toBeTruthy();
    // A cancelled delivery can never back a recipe, so it offers nothing.
    const buttons = screen.getAllByRole('button', { name: /use in invoice recipe/i });
    expect(buttons).toHaveLength(1);
    expect(buttons[0].closest('tr')).toHaveTextContent('Solvent');
    expect(within(screen.getByText('Glacial').closest('tr'))
      .queryByRole('button', { name: /use in invoice recipe/i })).toBeNull();
  });

  it('carries the receipt line and its client into the recipe flow', async () => {
    renderChemicals({ purchases: [OUTWARD_PURCHASE] });
    fireEvent.click(await screen.findByRole('button', { name: 'Outward' }));
    fireEvent.click(await screen.findByRole('button', {
      name: /use in invoice recipe/i,
    }));

    const location = lastLocation();
    expect(location.pathname).toBe('/recipes');
    expect(location.state.useOutwardLine).toEqual({
      line_id: 71,
      purchase_id: 7,
      client_id: 7,
      client_name: 'Client Co',
      chemical_id: 3,
      chemical_name: 'Solvent',
      quantity: 20,
      unit: 'L',
      supplier_name: 'SupCo',
      purchase_number: 'PO-OUT',
      purchase_date: '2026-10-30',
    });
  });

  it('shows the empty state when no outward purchases exist', async () => {
    renderChemicals({ purchases: [] });
    fireEvent.click(await screen.findByRole('button', { name: 'Outward' }));
    expect(await screen.findByText('No outward purchases recorded yet')).toBeTruthy();
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
    expect(await screen.findByText('Raw Material')).toBeTruthy();
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
