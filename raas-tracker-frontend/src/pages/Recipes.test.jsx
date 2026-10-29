// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfirmProvider } from '../context/ConfirmContext';
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

function jsonResponse(data, ok = true, status = 200) {
  // apiFetch reads Retry-After off the headers of a failed response.
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

const recipeNameFromUrl = (url) =>
  decodeURIComponent(String(url).match(/\/api\/recipes\/([^?]+)/)[1]);

function installFetch({
  lcs = OPEN_LCS,
  lcDetail = LC_DETAIL,
  invoices = INVOICES,
  invoiceItems = INVOICE_ITEMS,
  recipes = [],
  recipeItems = [],
  runCount = 0,
  putError = null,
} = {}) {
  const state = { recipes: [...recipes] };
  const fetchMock = vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/recipes') && method === 'POST') {
      const body = JSON.parse(options.body);
      state.recipes = [...state.recipes, {
        id: 99, name: body.name, company_id: body.company_id,
        product_name: body.product_name, total_quantity: body.yield_qty,
        water_percentage: body.water_percentage, created: '2026-10-29',
      }];
      return jsonResponse({ success: true, name: body.name }, true, 201);
    }
    if (u.includes('/api/recipes') && method === 'PUT') {
      const oldName = recipeNameFromUrl(u);
      const body = JSON.parse(options.body);
      if (putError) return jsonResponse({ error: putError }, false, 409);
      const nextName = body.new_name || oldName;
      const idx = state.recipes.findIndex((r) => r.name === oldName);
      if (idx >= 0) {
        state.recipes[idx] = {
          ...state.recipes[idx],
          name: nextName,
          product_name: body.product_name ?? state.recipes[idx].product_name,
          total_quantity: body.total_quantity ?? state.recipes[idx].total_quantity,
          water_percentage: body.water_percentage ?? state.recipes[idx].water_percentage,
        };
      }
      return jsonResponse({ success: true, name: nextName });
    }
    if (u.includes('/api/recipes') && method === 'GET' && u.includes('?company_id=')) {
      const name = recipeNameFromUrl(u);
      const rec = state.recipes.find((r) => r.name === name) || { name };
      return jsonResponse({ recipe: { ...rec, run_count: runCount }, items: recipeItems });
    }
    if (u.includes('/api/recipes')) return jsonResponse(state.recipes);
    if (u.includes('/api/chemicals')) return jsonResponse([]);
    if (u.includes('/api/companies')) return jsonResponse(COMPANIES);
    if (/\/api\/lcs\/\d+/.test(u)) return jsonResponse(lcDetail);
    if (u.includes('/api/lcs')) return jsonResponse(lcs);
    if (/\/api\/sales\/\d+\/invoices/.test(u)) return jsonResponse(invoices);
    if (/\/api\/invoices\/\d+\/items/.test(u)) return jsonResponse(invoiceItems);
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderRecipes(opts = {}) {
  const fetchMock = installFetch(opts);
  const utils = render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <Recipes />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
  return { ...utils, fetchMock };
}

async function openCreateModal() {
  fireEvent.click(await screen.findByRole('button', { name: /New Recipe/ }));
  return screen.findByRole('heading', { name: 'Create New Recipe' });
}

const called = (fetchMock, needle) => fetchMock.mock.calls
  .some(([url]) => String(url).includes(needle));

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

  it('hints the selected PI and product without filling the name', async () => {
    renderRecipes({
      recipes: [{
        id: 3, name: 'PI-2026-000 - Caustic Soda 50%', company_id: 7,
        product_name: 'Caustic Soda 50%', total_quantity: 1, water_percentage: 0,
      }, {
        id: 4, name: 'PI-2026-000 - Soda Ash', company_id: 7,
        product_name: 'Soda Ash', total_quantity: 1, water_percentage: 0,
      }],
    });
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText(/^LC/), { target: { value: '1' } });
    await screen.findByRole('option', { name: /PI-2026-014/ });
    fireEvent.change(screen.getByLabelText(/^PI/), { target: { value: '5' } });
    await screen.findByRole('option', { name: /INV-014/ });
    fireEvent.change(screen.getByLabelText(/^Invoice/), { target: { value: '9' } });

    const existing = await screen.findByRole('option', { name: /Caustic Soda 50%/ });
    expect(existing.textContent).toContain('recipe exists');
    expect(existing.textContent).toContain('200.00 KG');

    fireEvent.change(screen.getByLabelText(/^Product/), {
      target: { value: 'Caustic Soda 50%' },
    });

    expect(screen.getByLabelText(/^Recipe Name/).value).toBe('');
    expect(screen.getByText(/Selected: PI-2026-014/).textContent)
      .toContain('Caustic Soda 50%');
    expect(screen.getByLabelText(/^Total Quantity/).value).toBe('200');
    expect(screen.getByText(/2 of 3 products on this invoice have a recipe/)).toBeTruthy();
  });

  it('keeps the LC, PI and invoice selected after creating, and advances progress', async () => {
    const { fetchMock } = renderRecipes({
      recipes: [{
        id: 3, name: 'old', company_id: 7, product_name: 'Soda Ash',
        total_quantity: 1, water_percentage: 0,
      }],
    });
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });
    fireEvent.change(screen.getByLabelText(/^LC/), { target: { value: '1' } });
    await screen.findByRole('option', { name: /PI-2026-014/ });
    fireEvent.change(screen.getByLabelText(/^PI/), { target: { value: '5' } });
    await screen.findByRole('option', { name: /INV-014/ });
    fireEvent.change(screen.getByLabelText(/^Invoice/), { target: { value: '9' } });
    await screen.findByRole('option', { name: /Caustic Soda 50%/ });
    fireEvent.change(screen.getByLabelText(/^Product/), {
      target: { value: 'Caustic Soda 50%' },
    });
    expect(screen.getByText(/1 of 3 products on this invoice have a recipe/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/^Recipe Name/), {
      target: { value: 'PI-2026-014 - Caustic Soda 50%' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }));

    await waitFor(() => expect(called(fetchMock, '/api/recipes')).toBe(true));
    const post = fetchMock.mock.calls.find(
      ([url, options]) => String(url).includes('/api/recipes') && options?.method === 'POST');
    const body = JSON.parse(post[1].body);
    expect(body).toMatchObject({
      name: 'PI-2026-014 - Caustic Soda 50%',
      company_id: 7,
      product_name: 'Caustic Soda 50%',
      yield_qty: 200,
    });

    await waitFor(() => {
      expect(screen.getByLabelText(/^LC/).value).toBe('1');
      expect(screen.getByLabelText(/^PI/).value).toBe('5');
      expect(screen.getByLabelText(/^Invoice/).value).toBe('9');
      expect(screen.getByLabelText(/^Recipe Name/).value).toBe('');
    });
    await waitFor(() => {
      expect(screen.getByText(/2 of 3 products on this invoice have a recipe/)).toBeTruthy();
    });
  });

  it('offers a link to Sales when the company has no running LC', async () => {
    renderRecipes({ lcs: [] });
    await openCreateModal();
    fireEvent.change(screen.getByLabelText(/^Company/), { target: { value: '7' } });

    const link = await screen.findByRole('link', { name: /Sales/ });
    expect(link.getAttribute('href')).toBe('/sales');
  });
});

const RECIPE_ROW = {
  id: 3, name: 'PI-2026-014 - Caustic Soda 50%', company_id: 7,
  product_name: 'Caustic Soda 50%', total_quantity: 200, water_percentage: 0,
};

async function openRecipeDetail() {
  fireEvent.click(await screen.findByRole('button', { name: /View/ }));
  return screen.findByRole('heading', { name: 'PI-2026-014 - Caustic Soda 50%' });
}

async function openEditRecipe() {
  fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
  return screen.findByRole('heading', { name: 'Edit Recipe' });
}

describe('Recipe revision', () => {
  it('renames from the edit form and follows the server-returned name', async () => {
    const { fetchMock } = renderRecipes({ recipes: [RECIPE_ROW] });
    await openRecipeDetail();
    await openEditRecipe();

    const nameInput = screen.getByLabelText(/^Recipe Name/);
    expect(nameInput.value).toBe('PI-2026-014 - Caustic Soda 50%');
    fireEvent.change(nameInput, { target: { value: '  PI-2026-014 - Renamed  ' } });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        ([url, options]) => String(url).includes('/api/recipes') && options?.method === 'PUT');
      expect(put).toBeTruthy();
      expect(decodeURIComponent(String(put[0])))
        .toContain('/api/recipes/PI-2026-014 - Caustic Soda 50%?company_id=7');
      expect(JSON.parse(put[1].body)).toMatchObject({
        company_id: 7,
        new_name: 'PI-2026-014 - Renamed',
      });
    });

    // The detail view follows the returned name, so the next item call cannot
    // hit the old one.
    await screen.findByRole('heading', { name: 'PI-2026-014 - Renamed' });
    await waitFor(() => expect(
      called(fetchMock, '/api/recipes/PI-2026-014%20-%20Renamed?company_id=7')
    ).toBe(true));

    // And the list row is no longer under the old name.
    fireEvent.click(screen.getByRole('button', { name: /Back to recipes/ }));
    expect(await screen.findByText('PI-2026-014 - Renamed')).toBeTruthy();
    expect(screen.queryByText('PI-2026-014 - Caustic Soda 50%')).toBeNull();
  });

  it('locks the product once production has run', async () => {
    renderRecipes({ recipes: [RECIPE_ROW], runCount: 2 });
    await openRecipeDetail();
    await openEditRecipe();

    const product = screen.getByLabelText(/^Product/);
    expect(product.disabled).toBe(true);
    expect(product.value).toBe('Caustic Soda 50%');
    expect(screen.getByText(/Production has run — the product is fixed/)).toBeTruthy();
  });

  it('keeps the form open and shows a rename conflict inline', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      putError: "Recipe 'PI-2026-014 - Other' already exists for this company",
    });
    await openRecipeDetail();
    await openEditRecipe();

    fireEvent.change(screen.getByLabelText(/^Recipe Name/), {
      target: { value: 'PI-2026-014 - Other' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));

    expect(await screen.findByText(/already exists for this company/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Edit Recipe' })).toBeTruthy();
    expect(screen.getByLabelText(/^Recipe Name/).value).toBe('PI-2026-014 - Other');
  });

  it('opens production under the renamed recipe', async () => {
    renderRecipes({
      recipes: [RECIPE_ROW],
      recipeItems: [{ chemical_name: 'Caustic Soda', percentage: 40 }],
    });
    await openRecipeDetail();
    await openEditRecipe();

    fireEvent.change(screen.getByLabelText(/^Recipe Name/), {
      target: { value: 'PI-2026-014 - Renamed' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Save$/ }));
    await screen.findByRole('heading', { name: 'PI-2026-014 - Renamed' });

    fireEvent.click(screen.getByRole('button', { name: /Go for Production/ }));
    expect(await screen.findByText('PI-2026-014 - Renamed · 1 ingredient')).toBeTruthy();
  });
});
