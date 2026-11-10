// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react';
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

const RECIPE = {
  id: 1, name: 'INV-9 · Cleaner', company_id: 7, invoice_id: 9,
  invoice_number: 'INV-9', pi_number: 'PI-9', client_name: 'Acme LLC',
  company_name: 'Acme',
};

const PRODUCTS = [
  {
    id: 11, on_invoice: true, product_name: 'Cleaner', unit: 'KG',
    invoiced_qty: 5000, produced_qty: 1000, remaining_qty: 4000,
    formulated: true, grand_total: 100,
    items: [{ id: 1, chemical_name: 'NaOH', percentage: 40 }],
  },
  {
    id: 12, on_invoice: true, product_name: 'Polisher', unit: 'KG',
    invoiced_qty: 3000, produced_qty: 3000, remaining_qty: 0,
    formulated: true, grand_total: 100,
    items: [{ id: 2, chemical_name: 'Wax', percentage: 20 }],
  },
  {
    id: 13, on_invoice: true, product_name: 'Degreaser', unit: 'KG',
    invoiced_qty: 900, produced_qty: 0, remaining_qty: 900,
    formulated: false, items: [],
  },
];

function installFetch({
  products = PRODUCTS,
  sourceFails = 0,
  sourceError = 'Invoice service unavailable',
  produceStatus = 201,
  produceBody = { runs: [] },
} = {}) {
  const posts = [];
  let failsLeft = sourceFails;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/produce')) {
      posts.push({ url: u, body: options?.body ? JSON.parse(options.body) : null });
      return jsonResponse(produceBody, produceStatus < 400, produceStatus);
    }
    if (u.includes('/production-source')) {
      if (failsLeft > 0) {
        failsLeft -= 1;
        return jsonResponse({ error: sourceError }, false, 500);
      }
      return jsonResponse({
        invoice_id: 9, invoice_number: 'INV-9', recipe: RECIPE, products,
      });
    }
    return jsonResponse({});
  }));
  return posts;
}

function renderModal({ recipe = RECIPE, onSaved = vi.fn(), onClose = vi.fn() } = {}) {
  render(
    <MemoryRouter>
      <AuthProvider>
        <ProductionRunModal
          isOpen
          onClose={onClose}
          recipe={recipe}
          onSaved={onSaved}
        />
      </AuthProvider>
    </MemoryRouter>
  );
  return { onSaved, onClose };
}

async function waitForSource() {
  return screen.findByLabelText('Quantity for Cleaner');
}

describe('ProductionRunModal invoice scope', () => {
  it('fixes the recipe and its invoice instead of asking for a PI or invoice', async () => {
    const posts = installFetch();
    renderModal();

    // The run can only ever land on the recipe's own invoice, so there is
    // nothing to pick — and nothing to cross-link.
    expect(await screen.findByText('INV-9')).toBeTruthy();
    expect(screen.queryByLabelText('PI / Sale')).toBeNull();
    expect(screen.queryByLabelText('Invoice number')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add recipe' })).toBeNull();

    screen.getByLabelText('Material number');
    screen.getByLabelText('Packing');
    screen.getByLabelText('Batch number');
    screen.getByLabelText('Production date');
    expect(posts).toHaveLength(0);
  });

  it('prefills each product with its remaining invoice quantity', async () => {
    installFetch();
    renderModal();

    const cleaner = await waitForSource();
    expect(cleaner.value).toBe('4000');
    // Fully produced products have nothing left to prefill.
    expect(screen.queryByLabelText('Quantity for Polisher')).toBeNull();
    expect(screen.getByText(/Fully produced/i).textContent).toContain('Polisher');
    // An unformulated product is not silently produced.
    const degreaser = screen.getByLabelText('Quantity for Degreaser');
    expect(degreaser.disabled).toBe(true);
    expect(screen.getByText(/no formulation yet/i)).toBeTruthy();
  });

  it('posts per-product outputs to the recipe id, never legacy fields', async () => {
    const posts = installFetch();
    const { onSaved, onClose } = renderModal();

    await waitForSource();
    fireEvent.change(screen.getByLabelText('Material number'), { target: { value: '1001001' } });
    fireEvent.change(screen.getByLabelText('Packing'), { target: { value: '30 Kg Drum' } });
    fireEvent.change(screen.getByLabelText('Batch number'), { target: { value: 'B-77' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].url).toContain('/api/recipes/by-id/1/produce');
    expect(posts[0].body).toMatchObject({
      company_id: 7,
      material_number: '1001001',
      packing: '30 Kg Drum',
      batch_number: 'B-77',
      outputs: [{ product_name: 'Cleaner', quantity: 4000 }],
    });
    expect(posts[0].body.production_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    // The server derives sale and invoice from the recipe; the client cannot.
    expect(posts[0].body).not.toHaveProperty('production_qty');
    expect(posts[0].body).not.toHaveProperty('sale_ids');
    expect(posts[0].body).not.toHaveProperty('invoice_ids');
    expect(posts[0].body).not.toHaveProperty('recipes');
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith('Production run recorded');
  });

  it('submits an edited quantity and warns above the invoice remainder', async () => {
    const posts = installFetch();
    renderModal();

    const cleaner = await waitForSource();
    fireEvent.change(cleaner, { target: { value: '4500' } });

    expect(screen.getByText(/exceeds the invoice remainder/i).textContent)
      .toContain('4,000.00 KG');

    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    // Over-production is warned, not capped by the client.
    expect(posts[0].body.outputs).toEqual([{ product_name: 'Cleaner', quantity: 4500 }]);
  });

  it('omits zero quantities from the outputs', async () => {
    const posts = installFetch();
    renderModal();

    const cleaner = await waitForSource();
    fireEvent.change(cleaner, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    expect(await screen.findByText('Enter a quantity for at least one product')).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it('never claims the invoice is fully produced when its source failed', async () => {
    installFetch({ sourceFails: 10 });
    renderModal();

    expect(await screen.findByText('Could not load invoice products')).toBeTruthy();
    expect(screen.getByText('Invoice service unavailable')).toBeTruthy();
    expect(screen.queryByText(/fully produced/i)).toBeNull();
    expect(screen.queryByText(/no formulated product/i)).toBeNull();
    expect(screen.getByRole('button', { name: 'Start Production' }).disabled).toBe(true);
    screen.getByRole('button', { name: 'Retry' });
  });

  it('recovers the product list when the failure is retried', async () => {
    installFetch({ sourceFails: 1 });
    renderModal();

    expect(await screen.findByText('Could not load invoice products')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect((await waitForSource()).value).toBe('4000');
    expect(screen.queryByText('Could not load invoice products')).toBeNull();
  });

  it('says every product is fully produced and refuses an empty run', async () => {
    const posts = installFetch({ products: [PRODUCTS[1]] });
    renderModal();

    expect(await screen.findByText(/fully produced/i)).toBeTruthy();
    const start = screen.getByRole('button', { name: 'Start Production' });
    expect(start.disabled).toBe(true);
    fireEvent.click(start);
    expect(posts).toHaveLength(0);
  });

  it('refuses a run when no remaining product has a formulation', async () => {
    const posts = installFetch({ products: [PRODUCTS[2]] });
    renderModal();

    expect(await screen.findByText(/no formulated product/i)).toBeTruthy();
    const start = screen.getByRole('button', { name: 'Start Production' });
    expect(start.disabled).toBe(true);
    fireEvent.click(start);
    expect(posts).toHaveLength(0);
  });

  it('shows the server error inline and does not close', async () => {
    installFetch({
      produceStatus: 400,
      produceBody: { error: 'no unit conversion from KG to L for chemical NaOH' },
    });
    const { onSaved, onClose } = renderModal();

    await waitForSource();
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    expect(await screen.findByText(
      'no unit conversion from KG to L for chemical NaOH',
    )).toBeTruthy();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('blocks an unbalanced product', async () => {
    const posts = installFetch({
      products: [{ ...PRODUCTS[0], grand_total: 95 }],
    });
    renderModal();

    const cleaner = await waitForSource();
    expect(cleaner.disabled).toBe(true);
    expect(screen.getByText(/formulation totals 95.0%/)).toBeTruthy();
    expect(screen.getByText(/must equal 100% before production/)).toBeTruthy();
    expect(screen.getByText(/No product has a formulation totaling 100%/))
      .toBeTruthy();

    const start = screen.getByRole('button', { name: 'Start Production' });
    expect(start.disabled).toBe(true);
    fireEvent.click(start);
    expect(posts).toHaveLength(0);
  });

  it('sends only balanced products', async () => {
    const posts = installFetch({
      products: [
        { ...PRODUCTS[0], grand_total: 100 },
        {
          ...PRODUCTS[0], id: 14, product_name: 'Degreaser', grand_total: 95,
          invoiced_qty: 900, produced_qty: 0, remaining_qty: 900,
        },
      ],
    });
    renderModal();

    await waitForSource();
    expect(screen.getByLabelText('Quantity for Degreaser').disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Start Production' }).disabled)
      .toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.outputs).toEqual([
      { product_name: 'Cleaner', quantity: 4000 },
    ]);
  });

  it.each([99.99, 100.01])(
    'treats a formulation totaling %s as balanced',
    async (total) => {
      const posts = installFetch({
        products: [{ ...PRODUCTS[0], grand_total: total }],
      });
      renderModal();

      const cleaner = await waitForSource();
      expect(cleaner.disabled).toBe(false);
      expect(screen.queryByText(/must equal 100%/)).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Start Production' }));

      await waitFor(() => expect(posts).toHaveLength(1));
      expect(posts[0].body.outputs).toEqual([
        { product_name: 'Cleaner', quantity: 4000 },
      ]);
    },
  );
});

const MIXED_ITEMS = [
  {
    id: 1, chemical_name: 'NaOH', percentage: 40, unit: 'KG',
    required_qty: 1600, stock_remaining: 400, source_type: 'in_house',
    outward_purchase_line_id: null,
  },
  {
    id: 5, chemical_name: 'Wax', percentage: 20, unit: 'KG',
    required_qty: 800, stock_remaining: null, source_type: 'outward',
    outward_purchase_line_id: 71, purchase_number: 'PO-OUT',
    purchase_date: '2026-10-30', supplier_name: 'SupCo',
    client_name: 'Acme LLC', source_qty: 900, source_unit: 'KG',
  },
];

const withItems = (items) => [{ ...PRODUCTS[0], items }];

describe('ProductionRunModal ingredient sourcing', () => {
  it('separates gross use from the stock an in-house row really draws down', async () => {
    installFetch({ products: withItems([MIXED_ITEMS[0]]) });
    renderModal();
    await waitForSource();

    const naoh = within(screen.getByLabelText('Ingredient NaOH in Cleaner'));
    expect(naoh.getByText('In house')).toBeTruthy();
    expect(naoh.getByText('Gross use 1,600.00 KG')).toBeTruthy();
    expect(naoh.getByText('Stock impact 1,600.00 KG')).toBeTruthy();
  });

  it('shows outward evidence and never a balance for what we never held', async () => {
    installFetch({ products: withItems(MIXED_ITEMS) });
    renderModal();
    await waitForSource();

    const wax = within(screen.getByLabelText('Ingredient Wax in Cleaner'));
    expect(wax.getByText('Outward')).toBeTruthy();
    expect(wax.getByText('Gross use 800.00 KG')).toBeTruthy();
    expect(wax.getByText('Deducted 0.00 KG')).toBeTruthy();
    expect(wax.getByText('Not in our stock')).toBeTruthy();

    const evidence = wax.getByLabelText('Outward source for Wax');
    expect(evidence.textContent).toContain('SupCo');
    expect(evidence.textContent).toContain('PO-OUT');
    expect(evidence.textContent).toContain('2026-10-30');
    expect(evidence.textContent).toContain('Acme LLC');
    expect(evidence.textContent).toContain('Received 900.00 KG');

    // No stock number of any kind may sit on an outward row.
    expect(wax.queryByText(/available/i)).toBeNull();
    expect(wax.queryByText(/shortage/i)).toBeNull();
    expect(wax.queryByText(/stock impact/i)).toBeNull();
    // The in-house neighbour keeps its own stock impact line.
    expect(within(screen.getByLabelText('Ingredient NaOH in Cleaner'))
      .getByText('Stock impact 1,600.00 KG')).toBeTruthy();
  });

  it('reads a legacy ingredient with no source field as in house', async () => {
    installFetch({
      products: withItems([{ id: 9, chemical_name: 'Wax', percentage: 20 }]),
    });
    renderModal();
    await waitForSource();

    const wax = within(screen.getByLabelText('Ingredient Wax in Cleaner'));
    expect(wax.getByText('In house')).toBeTruthy();
    // No projected quantities means no invented ones.
    expect(wax.getByText('Gross use —')).toBeTruthy();
  });
});
