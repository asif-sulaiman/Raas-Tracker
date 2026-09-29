// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { toast } from 'sonner';
import ProductionRunModal from './ProductionRunModal';

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

const COMPANIES = [{ id: 7, name: 'Acme' }];
const RECIPES = [
  { id: 1, name: 'Cleaner X', total_quantity: 1500, company_id: 7 },
  { id: 3, name: 'Cleaner Y', total_quantity: 800, company_id: 7 },
  { id: 2, name: 'Other Recipe', total_quantity: 900, company_id: 8 },
];
const SALES = [
  { id: 5, pi_number: 'PI-9', client_name: 'Acme LLC', company_id: 7 },
  { id: 6, pi_number: 'PI-10', client_name: 'Beta', company_id: 8 },
];
const PRODUCTS = [{ product_name: 'Cleaner', quantity: 5000, unit: 'KG', item_no: '1001001' }];
const INVOICES = [{ invoice_id: 31, invoice_number: 'INV-31', status: 'issued' }];

function installFetch({ companies = COMPANIES, produceStatus = 201, produceBody = { runs: [] } } = {}) {
  const posts = [];
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/produce')) {
      posts.push({ url: u, body: options?.body ? JSON.parse(options.body) : null });
      return jsonResponse(produceBody, produceStatus < 400, produceStatus);
    }
    if (u.includes('/api/companies')) return jsonResponse(companies);
    if (u.includes('/api/recipes')) return jsonResponse(RECIPES);
    if (u.includes('/api/production-source')) return jsonResponse({ sale_id: 5, products: PRODUCTS });
    if (u.includes('/invoices')) return jsonResponse(INVOICES);
    if (u.includes('/api/sales')) return jsonResponse(SALES);
    return jsonResponse({});
  }));
  return posts;
}

const RECIPE = { name: 'Cleaner X', total_quantity: 1500, company_id: 7 };
const RECIPE_ITEMS = [{ chemical_name: 'NaOH' }, { chemical_name: 'Water' }];

function renderModal({ recipe = RECIPE, recipeItems = RECIPE_ITEMS, onSaved = vi.fn(), onClose = vi.fn() } = {}) {
  render(
    <MemoryRouter>
      <AuthProvider>
        <ProductionRunModal
          isOpen
          onClose={onClose}
          recipe={recipe}
          recipeItems={recipeItems}
          onSaved={onSaved}
        />
      </AuthProvider>
    </MemoryRouter>
  );
  return { onSaved, onClose };
}

async function pickCompanyAndPi() {
  fireEvent.change(await screen.findByLabelText('Company'), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText('PI / Sale'), { target: { value: '5' } });
  await waitFor(() => expect(screen.getByLabelText('Quantity').value).toBe('5000'));
}

describe('ProductionRunModal', () => {
  it('renders the required fields with the current recipe pre-listed', async () => {
    installFetch();
    renderModal();

    const company = await screen.findByLabelText('Company');
    expect(company.value).toBe('7');
    screen.getByLabelText('PI / Sale');
    screen.getByLabelText('Invoice number');
    screen.getByLabelText('Material number');
    screen.getByLabelText('Packing');
    screen.getByLabelText('Batch number');
    screen.getByLabelText('Production date');
    screen.getByLabelText('Recipe');
    expect(screen.getByLabelText('Quantity').value).toBe('1500');
    screen.getByRole('button', { name: 'Add recipe' });
    screen.getByRole('button', { name: 'Start Production' });
  });

  it('requires a company and a PI before starting', async () => {
    const posts = installFetch();
    renderModal({ recipe: { name: 'Cleaner X', total_quantity: 1500, company_id: null } });

    fireEvent.click(await screen.findByRole('button', { name: 'Start Production' }));
    expect(await screen.findByText('Company is required')).toBeTruthy();

    fireEvent.change(await screen.findByLabelText('Company'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));
    expect(await screen.findByText('PI / Sale is required')).toBeTruthy();

    expect(posts).toHaveLength(0);
  });

  it('auto-fills quantity and material number from the PI, then posts the run', async () => {
    const posts = installFetch();
    const { onSaved } = renderModal();

    await pickCompanyAndPi();
    expect(screen.getByLabelText('Material number').value).toBe('1001001');

    fireEvent.change(screen.getByLabelText('Invoice number'), { target: { value: '31' } });
    fireEvent.change(screen.getByLabelText('Packing'), { target: { value: '30 Kg Drum' } });
    fireEvent.change(screen.getByLabelText('Batch number'), { target: { value: 'B-77' } });
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '4000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/api/recipes/Cleaner%20X/produce');
    expect(posts[0].body).toMatchObject({
      company_id: 7,
      production_qty: 4000,
      material_number: '1001001',
      packing: '30 Kg Drum',
      batch_number: 'B-77',
      invoice_number: 'INV-31',
      invoice_ids: [31],
      sale_ids: [5],
      recipes: [{ recipe_name: 'Cleaner X', qty: 4000 }],
    });
    expect(posts[0].body.production_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(toast.success).toHaveBeenCalled();
  });

  it('accepts a new invoice number typed inline', async () => {
    const posts = installFetch();
    renderModal();

    await pickCompanyAndPi();
    fireEvent.change(screen.getByLabelText('Invoice number'), { target: { value: 'new' } });
    fireEvent.change(await screen.findByLabelText('New invoice number'), { target: { value: 'INV-NEW-7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.invoice_number).toBe('INV-NEW-7');
    expect(posts[0].body.invoice_ids).toBeUndefined();
  });

  it('sends every recipe row and uses the first row for production_qty', async () => {
    const posts = installFetch();
    renderModal();

    await pickCompanyAndPi();
    fireEvent.click(screen.getByRole('button', { name: 'Add recipe' }));
    fireEvent.change(screen.getByLabelText('Recipe 2'), { target: { value: 'Cleaner Y' } });
    fireEvent.change(screen.getByLabelText('Recipe qty 2'), { target: { value: '250' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.production_qty).toBe(5000);
    expect(posts[0].body.recipes).toEqual([
      { recipe_name: 'Cleaner X', qty: 5000 },
      { recipe_name: 'Cleaner Y', qty: 250 },
    ]);
  });

  it('shows the server 400 message inline and does not close', async () => {
    installFetch({ produceStatus: 400, produceBody: { error: 'Recipe has no ingredients' } });
    const { onSaved } = renderModal();

    await pickCompanyAndPi();
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    expect(await screen.findByText('Recipe has no ingredients')).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('shows an empty state when no companies exist', async () => {
    installFetch({ companies: [] });
    renderModal({ recipe: { name: 'Cleaner X', total_quantity: 1500, company_id: null } });

    expect(await screen.findByText('No companies registered yet.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));
    expect(await screen.findByText('Company is required')).toBeTruthy();
  });
});
