// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import {
  render, screen, waitFor, cleanup, fireEvent, within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
import { toast } from 'sonner';
import Recipes from './Recipes';

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

const COMPANIES = [{ id: 7, name: 'Acme' }, { id: 8, name: 'Beta' }];

const OPEN_LCS = [
  {
    id: 1, company_id: 7, lc_number: 'LC-2026-001', stage: 'lc_received',
    pi_count: 2, open_pi_count: 2, producible_pi_count: 1,
  },
  {
    id: 2, company_id: 7, lc_number: 'LC-2026-002', stage: 'lc_received',
    pi_count: 1, open_pi_count: 1, producible_pi_count: 0,
  },
];

const LC_DETAIL = {
  id: 1, company_id: 7, lc_number: 'LC-2026-001', stage: 'lc_received',
  pis: [
    {
      id: 5, pi_number: 'PI-2026-014', stage: 'shipment_ongoing',
      client_name: 'Globex', company_id: 7, producible_invoice_count: 1,
    },
    {
      id: 6, pi_number: 'PI-2026-015', stage: 'lc_received',
      client_name: 'Initech', company_id: 7, producible_invoice_count: 0,
    },
  ],
};

const INVOICES = [
  {
    invoice_id: 9, invoice_number: 'INV-014', status: 'planned',
    amount: 100, paid_amount: 0, payment_terms: 'at_sight', tenor_days: null,
  },
];

const INVOICE_ITEMS = [
  {
    id: 31, invoice_id: 9, sale_item_id: 41, product_name: 'Caustic Soda 50%',
    unit: 'KG', quantity: 120, unit_price: 2, line_total: 240,
  },
  {
    id: 32, invoice_id: 9, sale_item_id: 42, product_name: 'Caustic Soda 50%',
    unit: 'KG', quantity: 80, unit_price: 2, line_total: 160,
  },
  {
    id: 33, invoice_id: 9, sale_item_id: 43, product_name: 'Soda Ash',
    unit: 'KG', quantity: 50, unit_price: 1, line_total: 50,
  },
  {
    id: 34, invoice_id: 9, sale_item_id: 44, product_name: 'Sodium Silicate',
    unit: 'KG', quantity: 30, unit_price: 1, line_total: 30,
  },
];

const CHEMICALS = [
  { name: 'NaOH', current_qty: 500, unit: 'KG' },
  { name: 'Caustic Flakes', current_qty: 20, unit: 'KG' },
  { name: 'Soda Ash', current_qty: 10, unit: 'KG' },
];

const BASE_PRODUCTS = [
  {
    product_name: 'Caustic Soda 50%', unit: 'KG', invoiced_qty: 200,
    produced_qty: 50, remaining_qty: 150, line_count: 2,
    lines: [{ unit: 'KG', quantity: 120 }, { unit: 'KG', quantity: 80 }],
    unconverted: [],
  },
  {
    product_name: 'Soda Ash', unit: 'KG', invoiced_qty: 50,
    produced_qty: 0, remaining_qty: 50, line_count: 1,
    lines: [{ unit: 'KG', quantity: 50 }], unconverted: [],
  },
  {
    product_name: 'Sodium Silicate', unit: 'KG', invoiced_qty: 30,
    produced_qty: 0, remaining_qty: 30, line_count: 1,
    lines: [{ unit: 'KG', quantity: 30 }], unconverted: [],
  },
];

const RECIPE_ROW = {
  id: 3, name: 'PI-2026-014 · INV-014', company_id: 7, company_name: 'Acme',
  invoice_id: 9, invoice_number: 'INV-014', invoice_status: 'planned',
  sale_id: 5, pi_number: 'PI-2026-014', client_name: 'Globex',
  lc_id: 1, lc_number: 'LC-2026-001',
  product_count: 3, formulated_count: 1, run_count: 0, created: '2026-10-29',
};

function jsonResponse(data, ok = true, status = 200) {
  // apiFetch reads Retry-After off the headers of a failed response.
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

const SALES = [
  {
    id: 5, company_id: 7, pi_number: 'PI-2026-014', client_name: 'Globex',
    stage: 'shipment_ongoing',
  },
];

function installFetch({
  lcs = OPEN_LCS,
  lcDetail = LC_DETAIL,
  invoices = INVOICES,
  invoiceItems = INVOICE_ITEMS,
  recipes = [],
  detailMissing = false,
  putError = null,
  firstItem = { chemical_name: 'NaOH', percentage: 40 },
} = {}) {
  const state = {
    recipes: [...recipes],
    items: {
      'Caustic Soda 50%': firstItem
        ? [{ id: 101, chemical_id: 1, chemical_name: firstItem.chemical_name,
          current_stock: 500, unit: 'KG', percentage: firstItem.percentage }]
        : [],
      'Soda Ash': [],
      'Sodium Silicate': [],
    },
    runs: false,
    nextId: 200,
  };
  const detailFor = (rec) => ({
    recipe: { ...rec, run_count: state.runs ? 1 : (rec.run_count || 0) },
    products: BASE_PRODUCTS.map((base) => {
      const items = state.items[base.product_name] || [];
      const invoiced = base.invoiced_qty;
      const produced = state.runs ? invoiced : base.produced_qty;
      return {
        ...base,
        id: 900 + BASE_PRODUCTS.findIndex((p) => p.product_name === base.product_name),
        on_invoice: true,
        produced_qty: produced,
        remaining_qty: Math.max(invoiced - produced, 0),
        formulated: items.length > 0,
        percentage: items.reduce((sum, i) => sum + Number(i.percentage || 0), 0),
        items,
      };
    }),
    items: Object.values(state.items).flat(),
  });
  let runs = state.runs;
  const fetchMock = vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/produce') && method === 'POST') {
      state.runs = true;
      runs = true;
      return jsonResponse({ runs: [] }, true, 201);
    }
    if (/\/api\/recipes\/by-id\/\d+\/items\/\d+$/.test(u)) {
      const itemId = Number(u.match(/items\/(\d+)$/)[1]);
      if (method === 'DELETE') {
        for (const name of Object.keys(state.items)) {
          state.items[name] = state.items[name].filter((i) => i.id !== itemId);
        }
        return jsonResponse({ success: true });
      }
      const body = JSON.parse(options.body);
      for (const name of Object.keys(state.items)) {
        state.items[name] = state.items[name].map((i) => (
          i.id === itemId ? { ...i, percentage: body.percentage } : i
        ));
      }
      return jsonResponse({ success: true });
    }
    const productItems = u.match(/\/api\/recipes\/by-id\/\d+\/products\/(.+)\/items$/);
    if (productItems && method === 'POST') {
      const product = decodeURIComponent(productItems[1]);
      const body = JSON.parse(options.body);
      state.nextId += 1;
      state.items[product] = [...(state.items[product] || []), {
        id: state.nextId, chemical_id: 2, chemical_name: body.chemical,
        current_stock: 100, unit: 'KG', percentage: body.percentage,
      }];
      return jsonResponse({ success: true });
    }
    const byId = u.match(/\/api\/recipes\/by-id\/(\d+)$/);
    if (byId) {
      const rec = state.recipes.find((r) => String(r.id) === byId[1]);
      if (method === 'PUT') {
        const body = JSON.parse(options.body);
        if (putError) return jsonResponse({ error: putError }, false, 409);
        // Replace, never mutate: RECIPE_ROW is shared by every test.
        if (rec) {
          state.recipes = state.recipes.map(
            (r) => (r.id === rec.id ? { ...r, name: body.name } : r),
          );
        }
        return jsonResponse({ success: true, name: body.name });
      }
      if (method === 'DELETE') {
        state.recipes = state.recipes.filter((r) => r.id !== rec?.id);
        return jsonResponse({ success: true });
      }
      if (!rec || detailMissing) {
        return jsonResponse({ error: 'Recipe not found' }, false, 404);
      }
      return jsonResponse(detailFor(rec));
    }
    if (u.includes('/api/recipes') && method === 'POST') {
      const body = JSON.parse(options.body);
      const created = {
        id: 99, name: body.name, company_id: body.company_id,
        company_name: 'Acme', invoice_id: body.invoice_id,
        invoice_number: 'INV-014', invoice_status: 'planned',
        sale_id: 5, pi_number: 'PI-2026-014', client_name: 'Globex',
        lc_id: 1, lc_number: 'LC-2026-001',
        product_count: 3, formulated_count: 0, run_count: 0,
        created: '2026-10-29',
      };
      state.recipes = [...state.recipes, created];
      return jsonResponse({ success: true, name: created.name, recipe: created }, true, 201);
    }
    if (u.includes('/api/recipes') && method === 'GET' && u.includes('?company_id=')) {
      const name = decodeURIComponent(u.match(/\/api\/recipes\/([^?]+)/)[1]);
      const rec = state.recipes.find((r) => r.name === name) || { name };
      return jsonResponse(detailFor(rec));
    }
    if (u.includes('/api/recipes')) return jsonResponse(state.recipes);
    if (u.includes('/api/chemicals')) return jsonResponse(CHEMICALS);
    if (u.includes('/api/companies')) return jsonResponse(COMPANIES);
    if (u.includes('/production-source')) {
      const rec = state.recipes.find((r) => r.invoice_id === 9) || RECIPE_ROW;
      const detail = detailFor(rec);
      return jsonResponse({
        invoice_id: 9, invoice_number: 'INV-014',
        recipe: detail.recipe, products: detail.products,
      });
    }
    if (/\/api\/lcs\/\d+/.test(u)) return jsonResponse(lcDetail);
    if (u.includes('/api/lcs')) return jsonResponse(lcs);
    if (/\/api\/sales\/\d+\/invoices/.test(u)) return jsonResponse(invoices);
    if (/\/api\/invoices\/\d+\/items/.test(u)) return jsonResponse(invoiceItems);
    if (u.includes('/api/sales')) {
      return jsonResponse({ sales: SALES, total: SALES.length });
    }
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock, state, runs: () => runs };
}

function renderRecipes(opts = {}) {
  const { fetchMock, state } = installFetch(opts);
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <Recipes />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
  return { ...utils, fetchMock, state };
}

async function openCreateModal() {
  fireEvent.click(await screen.findByRole('button', { name: /New Recipe/ }));
  return screen.findByRole('heading', { name: 'Create New Recipe' });
}

const called = (fetchMock, needle) => fetchMock.mock.calls
  .some(([url]) => String(url).includes(needle));

async function pickInvoice() {
  fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText(/^LC/), { target: { value: '1' } });
  await screen.findByRole('option', { name: /PI-2026-014/ });
  fireEvent.change(screen.getByLabelText(/^PI/), { target: { value: '5' } });
  await screen.findByRole('option', { name: /INV-014/ });
  fireEvent.change(screen.getByLabelText(/^Invoice/), { target: { value: '9' } });
  await screen.findByText('3 products on this invoice');
}

describe('Create New Recipe sourcing', () => {
  it('never reads the clamped sales list and asks for open LCs only', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();

    await waitFor(() => expect(called(fetchMock, '/api/lcs?')).toBe(true));
    expect(called(fetchMock, '/api/lcs?open_only=true')).toBe(true);
    expect(called(fetchMock, '/api/sales?page_size')).toBe(false);
    expect(called(fetchMock, '/api/sales?')).toBe(false);
  });

  it('labels running LCs by their producible PIs and disables empty ones', async () => {
    renderRecipes();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });

    const withWork = await screen.findByRole('option', { name: /LC-2026-001/ });
    expect(withWork.textContent).toContain('1 PI in production');
    expect(withWork.textContent).not.toContain('lc_received');

    const noWork = screen.getByRole('option', { name: /LC-2026-002/ });
    expect(noWork.disabled).toBe(true);
    expect(noWork.textContent).toContain('no invoices in production');
  });

  it('keeps a PI with nothing left to produce visible but disabled', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText(/^LC/), { target: { value: '1' } });

    await waitFor(() => expect(called(fetchMock, '/api/lcs/1?open_only=true')).toBe(true));

    const openPi = await screen.findByRole('option', { name: /PI-2026-014/ });
    expect(openPi.disabled).toBe(false);
    expect(openPi.textContent).toContain('1 invoice in production');

    const donePi = screen.getByRole('option', { name: /PI-2026-015/ });
    expect(donePi.disabled).toBe(true);
    expect(donePi.textContent).toContain('no invoices in production');
  });

  it('offers only invoices still in production', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText(/^LC/), { target: { value: '1' } });
    await screen.findByRole('option', { name: /PI-2026-014/ });
    fireEvent.change(screen.getByLabelText(/^PI/), { target: { value: '5' } });

    await waitFor(() => expect(called(fetchMock, '/api/sales/5/invoices?producible=true')).toBe(true));
    const option = await screen.findByRole('option', { name: /INV-014/ });
    expect(option.textContent).toContain('Planned');
    expect(option.textContent).not.toContain('planned');
    expect(screen.queryByRole('option', { name: /INV-BOOKED/ })).toBeNull();
  });

  it('lists the invoice products read-only and drops product, quantity and water', async () => {
    renderRecipes();
    await openCreateModal();
    await pickInvoice();

    // Duplicate lines are merged for display: 120 + 80 KG.
    const merged = screen.getByText('Caustic Soda 50%').closest('li');
    expect(within(merged).getByText('200.00 KG')).toBeTruthy();
    screen.getByText('Soda Ash');
    screen.getByText('Sodium Silicate');

    // The invoice, not the operator, defines the products and quantities.
    expect(screen.queryByLabelText(/^Product/)).toBeNull();
    expect(screen.queryByLabelText(/^Total Quantity/)).toBeNull();
    expect(screen.queryByLabelText(/^Water/)).toBeNull();
  });

  it('creates the invoice-scoped recipe with a minimal body and opens it', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    await pickInvoice();

    fireEvent.change(screen.getByLabelText(/^Recipe Name/), {
      target: { value: 'PI-2026-014 · INV-014' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }));

    await waitFor(() => expect(called(fetchMock, '/api/recipes')).toBe(true));
    const post = fetchMock.mock.calls.find(
      ([url, options]) => String(url).includes('/api/recipes')
        && options?.method === 'POST');
    expect(JSON.parse(post[1].body)).toEqual({
      name: 'PI-2026-014 · INV-014',
      company_id: 7,
      invoice_id: 9,
    });

    // A new recipe is useless until its products are formulated, so the detail
    // opens on the created row instead of leaving the list.
    expect(await screen.findByRole('heading', {
      name: 'PI-2026-014 · INV-014',
    })).toBeTruthy();
    await waitFor(() => expect(called(fetchMock, '/api/recipes/by-id/99')).toBe(true));
  });

  it('names the invoice that already has a recipe and disables Create', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openCreateModal();
    await pickInvoice();

    expect((await screen.findByText(/already has a recipe/i)).textContent)
      .toContain('INV-014');
    // One recipe per invoice is the model, so the request is not sent.
    expect(screen.getByRole('button', { name: /^Create$/ }).disabled).toBe(true);
    expect(fetchMock.mock.calls.some(
      ([url, options]) => String(url).includes('/api/recipes')
        && options?.method === 'POST')).toBe(false);
  });

  it('offers a link to Sales when the company has no running LC', async () => {
    renderRecipes({ lcs: [] });
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });

    const link = await screen.findByRole('link', { name: /Sales/ });
    expect(link.getAttribute('href')).toBe('/sales');
  });
});

async function openRecipeDetail() {
  fireEvent.click(await screen.findByRole('button', { name: /View/ }));
  return screen.findByRole('heading', { name: 'PI-2026-014 · INV-014' });
}

const card = (name) => screen.getByLabelText(`${name} formulation`);

describe('Recipe detail lookup', () => {
  it('opens the detail by row id, not by company and name', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    await waitFor(() => expect(called(fetchMock, '/api/recipes/by-id/3')).toBe(true));
    // The name route misses whenever the pair drifts — the live 404 — so the
    // detail must not depend on it while a row id is available.
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('?company_id=')))
      .toBe(false);
  });

  it('refreshes the list when the recipe was deleted before View', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW], detailMissing: true });
    const listCalls = () => fetchMock.mock.calls.filter(
      ([url, options]) => String(url).endsWith('/api/recipes') && (options?.method || 'GET') === 'GET',
    ).length;
    const before = listCalls();

    fireEvent.click(await screen.findByRole('button', { name: /View/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining('no longer exists'),
    ));
    // A 404 by id means the row is gone, not that the request was wrong.
    await waitFor(() => expect(listCalls()).toBeGreaterThan(before));
  });

  it('falls back to the name route for a row without an id', async () => {
    const { fetchMock } = renderRecipes({ recipes: [{ ...RECIPE_ROW, id: undefined }] });
    await openRecipeDetail();

    await waitFor(() => expect(
      called(fetchMock,
        '/api/recipes/PI-2026-014%20%C2%B7%20INV-014?company_id=7')
    ).toBe(true));
  });
});

describe('Per-product formulation', () => {
  it('shows one card per invoice product with its formulation state', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    expect(screen.getByText('1 of 3 products formulated')).toBeTruthy();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByText('Ready')).toBeTruthy();
    expect(within(soda).getByText(/Invoiced/).textContent).toContain('200.00 KG');
    expect(within(soda).getByText(/Remaining/).textContent).toContain('150.00 KG');
    // Duplicate invoice lines are merged, not silently mixed.
    expect(within(soda).getByText(/2 lines merged/)).toBeTruthy();

    const ash = card('Soda Ash');
    expect(within(ash).getByText('Empty formulation')).toBeTruthy();
    expect(within(card('Sodium Silicate')).getByText('Empty formulation')).toBeTruthy();
  });

  it('adds an ingredient to a specific product', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    fireEvent.change(within(ash).getByLabelText('Chemical for Soda Ash'), {
      target: { value: 'NaOH' },
    });
    fireEvent.change(within(ash).getByLabelText('Percentage for Soda Ash'), {
      target: { value: '60' },
    });
    fireEvent.click(within(ash).getByRole('button', { name: 'Add ingredient to Soda Ash' }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([url, options]) => String(url).includes('/api/recipes/by-id/3/products/')
          && options?.method === 'POST');
      expect(post).toBeTruthy();
      expect(decodeURIComponent(String(post[0])))
        .toContain('/api/recipes/by-id/3/products/Soda Ash/items');
      expect(JSON.parse(post[1].body)).toEqual({ chemical: 'NaOH', percentage: 60 });
    });

    // The card re-reads the recipe so it can never show a stale formulation.
    expect(await within(card('Soda Ash')).findByText('60%')).toBeTruthy();
  });

  it('updates and removes an ingredient by its row id', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    fireEvent.click(within(soda).getByRole('button', {
      name: 'Edit NaOH percentage in Caustic Soda 50%',
    }));
    fireEvent.change(within(soda).getByLabelText('Percentage for NaOH in Caustic Soda 50%'), {
      target: { value: '45' },
    });
    fireEvent.click(within(soda).getByRole('button', {
      name: 'Save NaOH percentage in Caustic Soda 50%',
    }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, options]) => String(url).endsWith('/api/recipes/by-id/3/items/101')
          && options?.method === 'PUT');
      expect(put).toBeTruthy();
      expect(JSON.parse(put[1].body)).toEqual({ percentage: 45 });
    });
    expect(await within(card('Caustic Soda 50%')).findByText('45%')).toBeTruthy();

    fireEvent.click(within(card('Caustic Soda 50%')).getByRole('button', {
      name: 'Remove NaOH from Caustic Soda 50%',
    }));
    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }));

    await waitFor(() => {
      const del = fetchMock.mock.calls.find(
        ([url, options]) => String(url).endsWith('/api/recipes/by-id/3/items/101')
          && options?.method === 'DELETE');
      expect(del).toBeTruthy();
    });
    expect(await within(card('Caustic Soda 50%')).findByText('Empty formulation')).toBeTruthy();
  });
});

describe('Recipe revision', () => {
  it('renames by row id and follows the server-returned name', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));

    const nameInput = screen.getByLabelText(/^Recipe Name/);
    expect(nameInput.value).toBe('PI-2026-014 · INV-014');
    // The invoice and its products are the recipe's identity, not header fields.
    expect(screen.queryByLabelText(/^Product/)).toBeNull();
    expect(screen.queryByLabelText(/^Total Quantity/)).toBeNull();

    fireEvent.change(nameInput, { target: { value: '  INV-014 production  ' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, options]) => String(url).includes('/api/recipes/by-id/3')
          && options?.method === 'PUT');
      expect(put).toBeTruthy();
      expect(JSON.parse(put[1].body)).toEqual({ name: 'INV-014 production' });
    });

    await screen.findByRole('heading', { name: 'INV-014 production' });
    await waitFor(() => expect(called(fetchMock, '/api/recipes/by-id/3')).toBe(true));

    fireEvent.click(screen.getByRole('button', { name: /Back to recipes/ }));
    expect(await screen.findByText('INV-014 production')).toBeTruthy();
    expect(screen.queryByText('PI-2026-014 · INV-014')).toBeNull();
  });

  it('keeps the form open and shows a rename conflict inline', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      putError: "Recipe 'INV-015 production' already exists for this company",
    });
    await openRecipeDetail();
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));

    fireEvent.change(screen.getByLabelText(/^Recipe Name/), {
      target: { value: 'INV-015 production' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    expect(await screen.findByText(/already exists for this company/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Edit Recipe' })).toBeTruthy();
    expect(screen.getByLabelText(/^Recipe Name/).value).toBe('INV-015 production');
  });
});

describe('Production from the detail', () => {
  it('opens production against the fixed invoice and re-reads the recipe', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();
    const detailReads = () => fetchMock.mock.calls.filter(
      ([url, options]) => String(url).includes('/api/recipes/by-id/3')
        && (options?.method || 'GET') === 'GET',
    ).length;
    const before = detailReads();

    fireEvent.click(screen.getByRole('button', { name: /Go for Production/ }));
    expect(await screen.findByRole('heading', { name: 'Go for Production' })).toBeTruthy();
    await waitFor(() => expect(
      screen.getByLabelText('Quantity for Caustic Soda 50%').value,
    ).toBe('150'));

    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    await waitFor(() => expect(
      screen.queryByRole('heading', { name: 'Go for Production' }),
    ).toBeNull());
    await waitFor(() => expect(detailReads()).toBeGreaterThan(before));
    // Every product on the invoice was covered by the run.
    expect((await within(card('Soda Ash')).findByText(/Remaining/)).textContent)
      .toContain('0.00 KG');
  });
});

describe('Recipe list', () => {
  it('shows the invoice, the product count and what is still unformulated', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });

    expect(await screen.findByText('INV-014')).toBeTruthy();
    expect(screen.getByText('1 of 3 formulated')).toBeTruthy();
    expect(screen.getByText('Products awaiting formulation')).toBeTruthy();
    // 3 products, 1 formulated.
    expect(screen.getByText('2')).toBeTruthy();
    expect(screen.queryByText(/Recipes with Water/)).toBeNull();
  });
});
