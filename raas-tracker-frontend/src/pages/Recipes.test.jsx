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

// One client-owned outward receipt line, as /outward-sources hands it back.
const OUTWARD_SOURCE = {
  line_id: 71, purchase_id: 7, purchase_number: 'PO-OUT',
  purchase_date: '2026-10-30', supplier_name: 'SupCo', client_id: 7,
  client_name: 'Globex', chemical_id: 1, chemical_name: 'NaOH',
  quantity: 900, unit: 'KG', frozen: false,
};

// Frozen means uneditable in the register — it must stay pickable here.
const FROZEN_SOURCE = {
  ...OUTWARD_SOURCE, line_id: 72, purchase_number: 'PO-FROZEN', frozen: true,
};

function installFetch({
  lcs = OPEN_LCS,
  lcDetail = LC_DETAIL,
  invoices = INVOICES,
  invoiceItems = INVOICE_ITEMS,
  recipes = [],
  detailMissing = false,
  putError = null,
  firstItem = { chemical_name: 'NaOH', percentage: 40 },
  water = {},
  items = {},
  chemicals = CHEMICALS,
  products = BASE_PRODUCTS,
  omitQuantities = false,
  outwardSources = [],
  sourcesError = null,
  outwardHandler = null,
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
      ...items,
    },
    water: {
      'Caustic Soda 50%': null, 'Soda Ash': null, 'Sodium Silicate': null,
      ...water,
    },
    runs: false,
    nextId: 200,
  };
  const stockOf = (name) => {
    const chemical = chemicals.find((c) => c.name === name);
    return chemical ? chemical.current_qty : null;
  };
  const detailFor = (rec) => {
    const rows = products.map((base) => {
      const rowItems = state.items[base.product_name] || [];
      const invoiced = base.invoiced_qty;
      const produced = state.runs ? invoiced : base.produced_qty;
      const ingredientPct = rowItems.reduce(
        (sum, i) => sum + Number(i.percentage || 0), 0);
      const storedWater = state.water[base.product_name];
      const waterEffective = storedWater != null
        ? storedWater
        : (rowItems.length ? 100 - ingredientPct : null);
      return {
        base,
        rowItems,
        invoiced,
        produced,
        remaining: Math.max(invoiced - produced, 0),
        ingredientPct,
        storedWater,
        waterEffective,
        // The detail read projects the remainder, then subtracts the whole
        // invoice's need once per chemical.
        items: rowItems.map((item) => ({
          ...item,
          current_stock: stockOf(item.chemical_name) ?? item.current_stock,
          required_qty: omitQuantities
            ? undefined
            : (Number(item.percentage) / 100) * Math.max(invoiced - produced, 0),
        })),
      };
    });
    const needs = {};
    for (const row of rows) {
      for (const item of row.items) {
        needs[item.chemical_name] = (needs[item.chemical_name] || 0)
          + Number(item.percentage) / 100 * row.remaining;
      }
    }
    return {
      recipe: { ...rec, run_count: state.runs ? 1 : (rec.run_count || 0) },
      products: rows.map((row, index) => ({
        ...row.base,
        id: 900 + index,
        on_invoice: true,
        produced_qty: row.produced,
        remaining_qty: row.remaining,
        formulated: row.rowItems.length > 0,
        percentage: row.ingredientPct,
        water_percentage: row.storedWater ?? null,
        water_effective: row.waterEffective,
        grand_total: row.waterEffective == null
          ? null
          : row.ingredientPct + row.waterEffective,
        water_qty: omitQuantities || row.waterEffective == null
          ? undefined
          : row.waterEffective / 100 * row.remaining,
        items: row.items.map((item) => ({
          ...item,
          // An outward row never stood on our shelf: the API answers null here
          // on purpose, so a false shortage cannot be manufactured.
          stock_remaining: item.source_type === 'outward'
            ? null
            : (omitQuantities || stockOf(item.chemical_name) == null
              ? undefined
              : stockOf(item.chemical_name) - needs[item.chemical_name]),
        })),
      })),
      items: Object.values(state.items).flat(),
    };
  };
  let runs = state.runs;
  const fetchMock = vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/chemical-purchases/outward-sources')) {
      if (outwardHandler) return outwardHandler(u);
      if (sourcesError) {
        return jsonResponse(sourcesError.body, false, sourcesError.status);
      }
      const clientId = new URL(u, 'http://localhost')
        .searchParams.get('client_id');
      return jsonResponse({
        sources: outwardSources.filter(
          (s) => String(s.client_id) === String(clientId),
        ),
      });
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
        state.items[name] = state.items[name].map((i) => (i.id === itemId ? {
          ...i,
          percentage: body.percentage,
          // Omitting both source fields preserves the stored source.
          ...('source_type' in body
            ? {
              source_type: body.source_type,
              outward_purchase_line_id: body.outward_purchase_line_id ?? null,
            }
            : {}),
        } : i));
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
        source_type: body.source_type ?? 'in_house',
        outward_purchase_line_id: body.outward_purchase_line_id ?? null,
        ...(body.source_type === 'outward'
          ? {
            purchase_number: 'PO-OUT', purchase_date: '2026-10-30',
            supplier_name: 'SupCo', client_name: 'Globex',
            source_qty: 900, source_unit: 'KG',
          }
          : {}),
      }];
      return jsonResponse({ success: true });
    }
    const productWater = u.match(
      /\/api\/recipes\/by-id\/\d+\/products\/(.+)\/water$/);
    if (productWater && method === 'PUT') {
      const product = decodeURIComponent(productWater[1]);
      const body = JSON.parse(options.body);
      state.water[product] = body.percentage;
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

function renderRecipes({ initialEntries, ...opts } = {}) {
  const { fetchMock, state } = installFetch(opts);
  const utils = render(
    <MemoryRouter initialEntries={initialEntries}>
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

async function pickCompany(name = 'Acme') {
  const company = screen.getByLabelText(/^Company/);
  // The combobox ignores typed text until the register has arrived.
  await waitFor(() => expect(company).toHaveAttribute('placeholder', 'Search companies…'));
  fireEvent.focus(company);
  await screen.findByRole('option', { name });
  fireEvent.change(company, { target: { value: name } });
}

async function pickInvoice() {
  await pickCompany();

  const lc = await screen.findByLabelText(/^LC/);
  fireEvent.focus(lc);
  fireEvent.change(lc, { target: { value: 'LC-2026-001' } });
  fireEvent.keyDown(lc, { key: 'Enter' });

  const pi = await screen.findByLabelText(/^PI/);
  await waitFor(() => expect(pi.disabled).toBe(false));
  fireEvent.focus(pi);
  await screen.findByRole('option', { name: /PI-2026-014/ });
  fireEvent.change(pi, { target: { value: 'PI-2026-014' } });
  fireEvent.keyDown(pi, { key: 'Enter' });

  const invoice = await screen.findByLabelText(/^Invoice/);
  await waitFor(() => expect(invoice.disabled).toBe(false));
  fireEvent.focus(invoice);
  await screen.findByRole('option', { name: /INV-014/ });
  fireEvent.change(invoice, { target: { value: 'INV-014' } });
  fireEvent.keyDown(invoice, { key: 'Enter' });

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
    await pickCompany();

    const lc = screen.getByLabelText(/^LC/);
    fireEvent.focus(lc);

    const withWork = await screen.findByRole('option', { name: /LC-2026-001/ });
    expect(withWork.textContent).toContain('1 PI in production');
    expect(withWork.textContent).not.toContain('lc_received');

    const noWork = screen.getByRole('option', { name: /LC-2026-002/ });
    expect(noWork).toHaveAttribute('aria-disabled', 'true');
    expect(noWork.textContent).toContain('no invoices in production');
  });

  it('keeps a PI with nothing left to produce visible but disabled', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    await pickCompany();

    const lc = screen.getByLabelText(/^LC/);
    fireEvent.focus(lc);
    fireEvent.click(await screen.findByRole('option', { name: /LC-2026-001/ }));
    await waitFor(() => expect(called(fetchMock, '/api/lcs/1?open_only=true')).toBe(true));

    const pi = screen.getByLabelText(/^PI/);
    await waitFor(() => expect(pi.disabled).toBe(false));
    fireEvent.focus(pi);
    const openPi = await screen.findByRole('option', { name: /PI-2026-014/ });
    expect(openPi).not.toHaveAttribute('aria-disabled', 'true');
    expect(openPi.textContent).toContain('1 invoice in production');

    const donePi = screen.getByRole('option', { name: /PI-2026-015/ });
    expect(donePi).toHaveAttribute('aria-disabled', 'true');
    expect(donePi.textContent).toContain('no invoices in production');
  });

  it('offers only invoices still in production', async () => {
    const { fetchMock } = renderRecipes();
    await openCreateModal();
    await pickCompany();

    const lc = screen.getByLabelText(/^LC/);
    fireEvent.focus(lc);
    fireEvent.click(await screen.findByRole('option', { name: /LC-2026-001/ }));

    const pi = await screen.findByLabelText(/^PI/);
    await waitFor(() => expect(pi.disabled).toBe(false));
    fireEvent.focus(pi);
    fireEvent.click(await screen.findByRole('option', { name: /PI-2026-014/ }));

    await waitFor(() => expect(called(fetchMock, '/api/sales/5/invoices?producible=true')).toBe(true));
    fireEvent.focus(screen.getByLabelText(/^Invoice/));
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
    await pickCompany();

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
      expect(JSON.parse(post[1].body)).toEqual({
        chemical: 'NaOH',
        percentage: 60,
        source_type: 'in_house',
        outward_purchase_line_id: null,
      });
    });

    // The card re-reads the recipe so it can never show a stale formulation.
    expect(await within(card('Soda Ash')).findByText('60%')).toBeTruthy();
  });

  it('lists the stock chemicals when the chemical field is focused', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    fireEvent.focus(within(ash).getByLabelText('Chemical for Soda Ash'));

    // Scoped to the popup: the footer's own source select also has options.
    const listbox = await screen.findByRole('listbox');
    expect(within(listbox).getAllByRole('option').map((o) => o.textContent))
      .toEqual(['NaOH', 'Caustic Flakes', 'Soda Ash']);
    // The card is overflow-hidden; the list must live outside it.
    expect(ash.contains(screen.getByRole('listbox'))).toBe(false);

    // Typing still narrows the stock list.
    fireEvent.change(within(ash).getByLabelText('Chemical for Soda Ash'), {
      target: { value: 'caust' },
    });
    expect(within(screen.getByRole('listbox')).getAllByRole('option')
      .map((o) => o.textContent)).toEqual([
      'Caustic Flakes',
    ]);
  });

  it('explains a typed name that matches no stock chemical', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    const chemical = within(ash).getByLabelText('Chemical for Soda Ash');
    fireEvent.focus(chemical);
    fireEvent.change(chemical, { target: { value: 'Acclofol' } });

    // The empty row is portaled with the listbox, so it is not inside the card.
    expect(screen.getByText(/No matching chemical/)).toBeTruthy();
    expect(within(ash).queryByText(/No matching chemical/)).toBeNull();

    // Leaving the field keeps the unmatched text and explains it in place.
    fireEvent.blur(chemical);
    expect(within(ash).getByRole('status').textContent)
      .toContain('No chemical by that name');
  });

  it('refuses a chemical that is not registered', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    const chemical = within(ash).getByLabelText('Chemical for Soda Ash');
    fireEvent.focus(chemical);
    fireEvent.change(chemical, { target: { value: 'Unobtainium' } });
    fireEvent.blur(chemical);

    expect(within(ash).getByRole('status').textContent).toContain('No chemical by that name');
    fireEvent.change(within(ash).getByLabelText('Percentage for Soda Ash'), { target: { value: '10' } });
    fireEvent.click(within(ash).getByRole('button', { name: 'Add ingredient to Soda Ash' }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.some(
        ([url, options]) => String(url).includes('/api/recipes/by-id/') &&
          String(url).includes('/products/') && options?.method === 'POST',
      );
      expect(posted).toBe(false);
    });
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

  it('shows the remainder as water and flags a total that is not 100%', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    // 40% NaOH -> 60% water -> 100%.
    expect(within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%')).toHaveValue(null);
    expect(within(soda).getByText('Grand total 100.0%')).toBeTruthy();
    expect(within(soda).queryByText(/do not add up to 100/)).toBeNull();

    // Empty product: no remainder to show, so no invented grand total.
    expect(within(card('Soda Ash')).getByText('Grand total —')).toBeTruthy();
  });

  it('saves and clears water per product without blocking', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    const water = within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%');
    fireEvent.change(water, { target: { value: '25' } });
    // Preview is immediate; the warning is informational only.
    expect(within(soda).getByText('Grand total 65.0%')).toBeTruthy();
    expect(within(soda).getByText(/do not add up to 100/)).toBeTruthy();

    fireEvent.click(within(soda).getByRole('button', {
      name: 'Save water for Caustic Soda 50%',
    }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, options]) => options?.method === 'PUT'
          && decodeURIComponent(String(url))
            .endsWith('/api/recipes/by-id/3/products/Caustic Soda 50%/water'));
      expect(put).toBeTruthy();
      expect(JSON.parse(put[1].body)).toEqual({ percentage: 25 });
    });
    expect(await within(card('Caustic Soda 50%')).findByText('Grand total 65.0%'))
      .toBeTruthy();

    // Blank means "auto": the server stores NULL and the remainder returns.
    fireEvent.change(
      within(card('Caustic Soda 50%')).getByLabelText(
        'Water percentage for Caustic Soda 50%'),
      { target: { value: '' } });
    fireEvent.click(within(card('Caustic Soda 50%')).getByRole('button', {
      name: 'Save water for Caustic Soda 50%',
    }));

    await waitFor(() => {
      const puts = fetchMock.mock.calls.filter(
        ([url, options]) => options?.method === 'PUT'
          && decodeURIComponent(String(url)).endsWith('/water'));
      expect(JSON.parse(puts[puts.length - 1][1].body))
        .toEqual({ percentage: null });
    });
    expect(await within(card('Caustic Soda 50%')).findByText('Grand total 100.0%'))
      .toBeTruthy();
  });

  it('shows a stored float4 water rounded to the input step', async () => {
    // recipe_products.water_percentage is REAL, so 33.33 reads back noisy.
    renderRecipes({
      recipes: [RECIPE_ROW],
      water: { 'Caustic Soda 50%': 33.332998275756836 },
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%')).toHaveValue(33.33);
    expect(within(soda).getByText('Grand total 73.3%')).toBeTruthy();
  });

  it('keeps an explicit stored 0 instead of treating it as blank', async () => {
    renderRecipes({ recipes: [RECIPE_ROW], water: { 'Caustic Soda 50%': 0 } });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%')).toHaveValue(0);
    // 40% NaOH + 0% water is a warning, never a blocked save.
    expect(within(soda).getByText('Grand total 40.0%')).toBeTruthy();
    expect(within(soda).getByText(/do not add up to 100/)).toBeTruthy();
    expect(within(soda).getByLabelText('Water quantity for Caustic Soda 50%')
      .textContent).toBe('Water 0.00 KG');
  });

  it('refuses water outside 0-100 without calling the server', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    fireEvent.change(within(soda).getByLabelText(
      'Water percentage for Caustic Soda 50%'), { target: { value: '150' } });
    fireEvent.click(within(soda).getByRole('button', {
      name: 'Save water for Caustic Soda 50%',
    }));

    await waitFor(() => expect(toast.error)
      .toHaveBeenCalledWith('Water must be between 0 and 100'));
    expect(fetchMock.mock.calls.some(
      ([, options]) => options?.method === 'PUT')).toBe(false);
  });

  it('shows how much chemical the invoice still needs and what will be left', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getAllByRole('columnheader').map((th) => th.textContent))
      .toEqual(['#', 'Chemical', 'Percentage', 'Source', 'Chemical needed',
        'Current Stock', 'Stock remaining', 'Actions']);
    // 40% of the 150 KG still to produce, from a stock of 500.
    expect(within(soda).getByText('60.00 KG')).toBeTruthy();
    expect(within(soda).getByText('440.00 KG')).toBeTruthy();
    expect(within(soda).getByText(
      /Quantities are for the 150.00 KG still to produce/)).toBeTruthy();
  });

  it("shows a shared chemical's invoice-wide remainder on every row", async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      items: {
        'Soda Ash': [{
          id: 102, chemical_id: 1, chemical_name: 'NaOH',
          current_stock: 500, unit: 'KG', percentage: 20,
        }],
      },
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    const ash = card('Soda Ash');
    expect(within(soda).getByText('60.00 KG')).toBeTruthy();
    expect(within(ash).getByText('10.00 KG')).toBeTruthy();
    // 500 KG of NaOH minus the invoice-wide need of 70.
    expect(within(soda).getByText('430.00 KG')).toBeTruthy();
    expect(within(ash).getByText('430.00 KG')).toBeTruthy();
  });

  it('shows a negative remainder as a shortage', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      firstItem: { chemical_name: 'Caustic Flakes', percentage: 40 },
    });
    await openRecipeDetail();

    // 40% of 150 = 60 needed, but only 20 is in stock.
    const shortage = within(card('Caustic Soda 50%')).getByText('-40.00 KG');
    expect(shortage.className).toContain('text-rose-500');
  });

  it('renders the water quantity next to the grand total', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    // 40% NaOH leaves 60% water of the 150 KG still to produce.
    expect(within(soda).getByLabelText('Water quantity for Caustic Soda 50%')
      .textContent).toBe('Water 90.00 KG');
    // An empty product has no invented water quantity.
    expect(within(card('Sodium Silicate'))
      .queryByLabelText('Water quantity for Sodium Silicate')).toBeNull();
  });

  it('renders an em dash when the quantities are unavailable', async () => {
    renderRecipes({ recipes: [RECIPE_ROW], omitQuantities: true });
    await openRecipeDetail();

    expect(within(card('Caustic Soda 50%')).getAllByText('—')).toHaveLength(2);
  });

  it('keeps the empty-formulation row spanning every column', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const empty = within(card('Soda Ash')).getByText(/No ingredients yet/);
    expect(empty.closest('td').getAttribute('colspan')).toBe('8');
  });

  it('marks an unbalanced formulation as not balanced', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      water: { 'Caustic Soda 50%': 25 },
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByText('Not balanced')).toBeTruthy();
    expect(within(soda).getByText(/production is blocked/)).toBeTruthy();
    expect(within(soda).getByText('Grand total 65.0%')).toBeTruthy();
  });

  it('accepts a rounding hairline as balanced', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      water: { 'Caustic Soda 50%': 59.99 },
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByText('Ready')).toBeTruthy();
    expect(within(soda).queryByText('Not balanced')).toBeNull();
    expect(within(soda).queryByText(/do not add up to 100/)).toBeNull();
  });

  it('pins the 100 kg example on the card', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      products: [{
        product_name: 'Caustic Soda 50%', unit: 'KG', invoiced_qty: 100,
        produced_qty: 0, remaining_qty: 100, line_count: 1,
        lines: [{ unit: 'KG', quantity: 100 }], unconverted: [],
      }],
      firstItem: { chemical_name: 'NaOH', percentage: 95 },
      water: { 'Caustic Soda 50%': 5 },
      chemicals: [{ name: 'NaOH', current_qty: 100, unit: 'KG' }],
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByText('95.00 KG')).toBeTruthy();
    expect(within(soda).getByText('5.00 KG')).toBeTruthy();
    expect(within(soda).getByLabelText('Water quantity for Caustic Soda 50%')
      .textContent).toBe('Water 5.00 KG');
    expect(within(soda).getByText('Ready')).toBeTruthy();
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

const OUTWARD_ITEM = {
  id: 105, chemical_id: 1, chemical_name: 'NaOH', current_stock: 500,
  unit: 'KG', percentage: 40, source_type: 'outward',
  outward_purchase_line_id: 71, purchase_number: 'PO-OUT',
  purchase_date: '2026-10-30', supplier_name: 'SupCo',
  client_name: 'Globex', source_qty: 900, source_unit: 'KG',
};

async function openSourceEditor(soda, chemical = 'NaOH') {
  fireEvent.click(within(soda).getByRole('button', {
    name: `Change source for ${chemical} in Caustic Soda 50%`,
  }));
  expect(within(soda).getByLabelText(
    `Source for ${chemical} in Caustic Soda 50%`,
  )).toBeTruthy();
}

describe('Ingredient source selector', () => {
  it('gives every ingredient row a source and reads a legacy row as In house', async () => {
    renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    // No source data at all (Slice 1 recipe) is an in-house row, not a blank.
    expect(within(soda).getByRole('button', {
      name: 'Change source for NaOH in Caustic Soda 50%',
    }).textContent).toBe('In house');
    // An in-house row keeps showing what stands on the shelf.
    expect(within(soda).getByText('500.00 KG')).toBeTruthy();
  });

  it('loads this client\'s outward receipts when a row is switched to Outward', async () => {
    const { fetchMock } = renderRecipes({
      recipes: [RECIPE_ROW],
      outwardSources: [OUTWARD_SOURCE],
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    await openSourceEditor(soda);
    fireEvent.change(within(soda).getByLabelText(
      'Source for NaOH in Caustic Soda 50%',
    ), { target: { value: 'outward' } });

    // The client id is the recipe's own company_id — never picked by hand.
    await waitFor(() => expect(called(
      fetchMock, '/api/chemical-purchases/outward-sources?client_id=7',
    )).toBe(true));

    const picker = within(soda).getByLabelText(
      'Outward receipt for NaOH in Caustic Soda 50%',
    );
    fireEvent.focus(picker);
    const option = await screen.findByRole('option', { name: /PO-OUT/ });
    expect(option.textContent).toContain('NaOH');
    expect(option.textContent).toContain('900.00 KG');
    expect(option.textContent).toContain('SupCo');
    expect(option.textContent).toContain('2026-10-30');
    fireEvent.click(option);

    fireEvent.click(within(soda).getByRole('button', {
      name: 'Save source for NaOH in Caustic Soda 50%',
    }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, options]) => String(url).endsWith('/api/recipes/by-id/3/items/101')
          && options?.method === 'PUT',
      );
      expect(put).toBeTruthy();
      expect(JSON.parse(put[1].body)).toEqual({
        percentage: 40, source_type: 'outward', outward_purchase_line_id: 71,
      });
    });
  });

  it('keeps the picker on the current client when an older client response lands last', async () => {
    const gates = {};
    const outwardHandler = (url) => {
      const cid = new URL(url, 'http://localhost').searchParams.get('client_id');
      return new Promise((resolve) => { gates[cid] = resolve; });
    };
    const recipeA = { ...RECIPE_ROW, id: 3, company_id: 7, name: 'PI-A · INV-A' };
    const recipeB = { ...RECIPE_ROW, id: 4, company_id: 8, name: 'PI-B · INV-B' };
    renderRecipes({ recipes: [recipeA, recipeB], outwardHandler });

    const openRecipe = async (name) => {
      const row = (await screen.findByText(name)).closest('tr');
      fireEvent.click(within(row).getByRole('button', { name: 'View' }));
      await screen.findByRole('heading', { name });
    };
    const switchNaohOutward = async () => {
      const soda = card('Caustic Soda 50%');
      await openSourceEditor(soda);
      fireEvent.change(within(soda).getByLabelText(
        'Source for NaOH in Caustic Soda 50%',
      ), { target: { value: 'outward' } });
      return soda;
    };

    // Client A's read goes on the wire and stays pending.
    await openRecipe('PI-A · INV-A');
    await switchNaohOutward();
    await waitFor(() => expect(gates['7']).toBeTruthy());

    // Move to client B and switch a row outward: B's read goes out behind A's.
    fireEvent.click(screen.getByRole('button', { name: 'Back to recipes' }));
    await openRecipe('PI-B · INV-B');
    const soda = await switchNaohOutward();
    await waitFor(() => expect(gates['8']).toBeTruthy());

    // While B's read is on the wire the picker is inert and the previous
    // client's rows are gone — nothing belonging to A stays pickable.
    expect(within(soda).getByLabelText(
      'Outward receipt for NaOH in Caustic Soda 50%',
    )).toBeDisabled();

    // B answers first; the superseded A answers last and must change nothing.
    gates['8'](jsonResponse({ sources: [
      { ...OUTWARD_SOURCE, line_id: 81, client_id: 8, purchase_number: 'PO-B' },
    ] }));
    gates['7'](jsonResponse({ sources: [
      { ...OUTWARD_SOURCE, line_id: 71, client_id: 7, purchase_number: 'PO-A' },
    ] }));

    const picker = within(soda).getByLabelText(
      'Outward receipt for NaOH in Caustic Soda 50%',
    );
    // The picker stays disabled until the current client's lines arrive.
    await waitFor(() => expect(picker).not.toBeDisabled());
    fireEvent.focus(picker);
    expect(await screen.findByRole('option', { name: /PO-B/ })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /PO-A/ })).toBeNull();
  });

  it('changes only the source: the row keeps its own percentage and chemical', async () => {
    const { fetchMock } = renderRecipes({
      recipes: [RECIPE_ROW],
      outwardSources: [OUTWARD_SOURCE],
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    await openSourceEditor(soda);
    fireEvent.change(within(soda).getByLabelText(
      'Source for NaOH in Caustic Soda 50%',
    ), { target: { value: 'outward' } });
    await waitFor(() => expect(called(fetchMock, 'outward-sources')).toBe(true));

    expect(within(soda).getByText('NaOH')).toBeTruthy();
    expect(within(soda).getByText('40%')).toBeTruthy();
    expect(within(soda).queryByLabelText(
      'Percentage for NaOH in Caustic Soda 50%',
    )).toBeNull();

    // An outward row with no receipt picked cannot be saved — the pairing is
    // refused client-side so the server's 400 never arrives as a surprise.
    fireEvent.click(within(soda).getByRole('button', {
      name: 'Save source for NaOH in Caustic Soda 50%',
    }));
    expect(within(soda).getByText(
      /pick the receipt line the client delivered/,
    )).toBeTruthy();
    expect(fetchMock.mock.calls.some(
      ([url, options]) => options?.method === 'PUT'
        && String(url).endsWith('/items/101'),
    )).toBe(false);
  });

  it('shows outward evidence instead of a stock balance or a shortage', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      items: { 'Caustic Soda 50%': [OUTWARD_ITEM] },
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    expect(within(soda).getByRole('button', {
      name: 'Change source for NaOH in Caustic Soda 50%',
    }).textContent).toBe('Outward');

    const evidence = within(soda).getByLabelText(
      'Outward source for NaOH in Caustic Soda 50%',
    );
    expect(evidence.textContent).toContain('SupCo');
    expect(evidence.textContent).toContain('PO-OUT');
    expect(evidence.textContent).toContain('2026-10-30');
    expect(evidence.textContent).toContain('Globex');
    expect(evidence.textContent).toContain('Received 900.00 KG');
    expect(evidence.textContent).toContain('Deducted 0.00 KG');

    // The gross need is still shown — the material really is used.
    expect(within(soda).getByText('60.00 KG')).toBeTruthy();
    // Never a balance, never an availability pill for what we never held.
    expect(within(soda).getByText('Not in our stock')).toBeTruthy();
    expect(within(soda).queryByText('500.00 KG')).toBeNull();
    expect(within(soda).queryByText('440.00 KG')).toBeNull();
  });

  it('adds a new ingredient sourced outward with both source fields', async () => {
    const { fetchMock } = renderRecipes({
      recipes: [RECIPE_ROW],
      outwardSources: [OUTWARD_SOURCE],
    });
    await openRecipeDetail();

    const ash = card('Soda Ash');
    fireEvent.change(within(ash).getByLabelText('Source for Soda Ash'), {
      target: { value: 'outward' },
    });
    await waitFor(() => expect(called(fetchMock, 'outward-sources')).toBe(true));

    const picker = within(ash).getByLabelText('Outward receipt for Soda Ash');
    fireEvent.focus(picker);
    fireEvent.click(await screen.findByRole('option', { name: /PO-OUT/ }));
    // The receipt decides the material — it cannot be typed into a mismatch.
    expect(within(ash).getByLabelText('Chemical for Soda Ash')).toHaveValue('NaOH');

    fireEvent.change(within(ash).getByLabelText('Percentage for Soda Ash'), {
      target: { value: '10' },
    });
    fireEvent.click(within(ash).getByRole('button', {
      name: 'Add ingredient to Soda Ash',
    }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([url, options]) => decodeURIComponent(String(url))
          .includes('/api/recipes/by-id/3/products/Soda Ash/items')
          && options?.method === 'POST',
      );
      expect(post).toBeTruthy();
      expect(JSON.parse(post[1].body)).toEqual({
        chemical: 'NaOH', percentage: 10,
        source_type: 'outward', outward_purchase_line_id: 71,
      });
    });
  });

  it('keeps a frozen receipt line selectable and says what frozen means', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      outwardSources: [OUTWARD_SOURCE, FROZEN_SOURCE],
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    await openSourceEditor(soda);
    fireEvent.change(within(soda).getByLabelText(
      'Source for NaOH in Caustic Soda 50%',
    ), { target: { value: 'outward' } });

    const picker = within(soda).getByLabelText(
      'Outward receipt for NaOH in Caustic Soda 50%',
    );
    await waitFor(() => expect(picker).not.toBeDisabled());
    fireEvent.focus(picker);
    const option = await screen.findByRole('option', { name: /PO-FROZEN/ });
    expect(option).not.toHaveAttribute('aria-disabled', 'true');
    expect(option.textContent).toContain('referenced');
    fireEvent.click(option);

    const badge = within(soda).getByText('Referenced');
    expect(badge.textContent).toBe('Referenced');
    expect(within(soda).getByText(/cannot be edited here, but it is usable/))
      .toBeTruthy();
  });

  it('surfaces a 400 field error on the picker instead of a generic toast', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      outwardSources: [OUTWARD_SOURCE],
      sourcesError: {
        status: 400,
        body: {
          error: 'Invalid client_id',
          details: [{
            field: 'client_id',
            message: 'client_id must be a positive integer',
          }],
        },
      },
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    await openSourceEditor(soda);
    fireEvent.change(within(soda).getByLabelText(
      'Source for NaOH in Caustic Soda 50%',
    ), { target: { value: 'outward' } });

    expect(await within(soda).findByText(
      'client_id must be a positive integer',
    )).toBeTruthy();
    expect(within(soda).getByText(/Could not list the outward receipts/))
      .toBeTruthy();
  });

  it('carries the register\'s outward line into the create flow and the new row', async () => {
    const { fetchMock } = renderRecipes({
      outwardSources: [{
        ...OUTWARD_SOURCE, line_id: 73, purchase_number: 'PO-CAUSTIC',
        chemical_id: 2, chemical_name: 'Caustic Flakes', quantity: 300,
      }],
      initialEntries: [{
        pathname: '/recipes',
        state: {
          useOutwardLine: {
            line_id: 73, client_id: 7, client_name: 'Globex',
            chemical_id: 2, chemical_name: 'Caustic Flakes', quantity: 300,
            unit: 'KG', supplier_name: 'SupCo',
            purchase_number: 'PO-CAUSTIC', purchase_date: '2026-10-30',
          },
        },
      }],
    });

    // Straight into the existing workflow, with the client already set.
    expect(await screen.findByRole('heading', {
      name: 'Create New Recipe',
    })).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText(/^Company/))
      .toHaveValue('Acme'));
    expect(screen.getByLabelText('Outward receipt carried from the register')
      .textContent).toContain('PO-CAUSTIC');

    await pickInvoice();
    fireEvent.change(screen.getByLabelText(/^Recipe Name/), {
      target: { value: 'PI-2026-014 · INV-014' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }));

    await screen.findByRole('heading', { name: 'PI-2026-014 · INV-014' });
    const ash = card('Soda Ash');
    expect(within(ash).getByLabelText('Source for Soda Ash').value)
      .toBe('outward');
    expect(within(ash).getByLabelText('Chemical for Soda Ash'))
      .toHaveValue('Caustic Flakes');
    // The receipt list is read for this client before the row can name it.
    await waitFor(() => expect(
      within(ash).getByLabelText('Outward receipt for Soda Ash').value,
    ).toContain('PO-CAUSTIC'));

    // Only the percentage is left to choose.
    fireEvent.change(within(ash).getByLabelText('Percentage for Soda Ash'), {
      target: { value: '10' },
    });
    fireEvent.click(within(ash).getByRole('button', {
      name: 'Add ingredient to Soda Ash',
    }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(
        ([url, options]) => options?.method === 'POST'
          && decodeURIComponent(String(url)).includes('/products/Soda Ash/items'),
      );
      expect(post).toBeTruthy();
      expect(JSON.parse(post[1].body)).toEqual({
        chemical: 'Caustic Flakes', percentage: 10,
        source_type: 'outward', outward_purchase_line_id: 73,
      });
    });
  });

  it('refuses to open the outward picker without a client on the recipe', async () => {
    renderRecipes({
      recipes: [{ ...RECIPE_ROW, company_id: null }],
      outwardSources: [OUTWARD_SOURCE],
    });
    await openRecipeDetail();

    const soda = card('Caustic Soda 50%');
    await openSourceEditor(soda);
    fireEvent.change(within(soda).getByLabelText(
      'Source for NaOH in Caustic Soda 50%',
    ), { target: { value: 'outward' } });

    expect(await within(soda).findByText(/has no client on its PI/)).toBeTruthy();
  });
});
