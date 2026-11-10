// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import PiDemoEditor from './PiDemoEditor';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const PRODUCT = {
  product_name: 'Cleaner', unit: 'KG', water_percentage: null,
  items: [{ chemical: 'Salt', chemical_id: 1, percentage: 60 }],
};
const DEMO = {
  id: 4, sale_id: 7, pi_number: 'PI-7', client_name: 'Client Co',
  draft_revision: 3, active_revision: 2, activated_at: '2026-11-09T09:00:00', activated_by: 'admin',
  products: [PRODUCT], active_products: [{ ...PRODUCT, water_percentage: 40 }],
  progress: [{ product_name: 'Cleaner', unit: 'KG', ordered_quantity: 100,
    target_quantity: 60, produced_quantity: 20, remaining_quantity: 40 }],
};
const response = (data, status = 200) => ({
  ok: status < 400, status, json: async () => data, headers: { get: () => null },
});

function setup({ demo = DEMO, readError, writeError } = {}) {
  let current = structuredClone(demo);
  const requests = [];
  const fetcher = vi.fn(async (url, opts = {}) => {
    const method = opts.method || 'GET';
    if (url === '/api/auth/me') return response({ id: 1, username: 'admin', role: 'admin' });
    if (url === '/api/chemicals/catalog') return response([{ id: 1, name: 'Salt', unit: 'KG' }]);
    if (url === '/api/sales/7/demo-recipe' && method === 'GET') {
      if (readError) return response({ error: 'Temporarily unavailable' }, readError);
      return current ? response({ demo: current }) : response({ error: 'Demo not found' }, 404);
    }
    requests.push({ url, method, body: JSON.parse(opts.body || '{}') });
    if (writeError) return response(writeError.body, writeError.status);
    if (url === '/api/sales/7/demo-recipe' && method === 'POST') {
      current = structuredClone(DEMO);
    } else if (url === '/api/sales/7/demo-recipe/draft' && method === 'PUT') {
      current.products = JSON.parse(opts.body).products;
      current.draft_revision += 1;
    } else if (url === '/api/sales/7/demo-recipe/activate' && method === 'POST') {
      current.active_revision = current.draft_revision;
    } else {
      throw new Error(`Unexpected request ${method} ${url}`);
    }
    return response({ demo: structuredClone(current) });
  });
  vi.stubGlobal('fetch', fetcher);
  const onClose = vi.fn();
  const view = render(<MemoryRouter><AuthProvider>
    <PiDemoEditor saleId={7} isOpen onClose={onClose} />
  </AuthProvider></MemoryRouter>);
  return { requests, view, fetcher, onClose, replaceDemo: (value) => { current = value; } };
}

const percentage = () => screen.getByLabelText('Percentage for Salt in Cleaner');

 describe('PI demo editor', () => {
  it('shows server quantities without treating ordered quantity as remaining work', async () => {
    setup();
    expect(await screen.findByText('PI-7')).toBeTruthy();
    const card = within(screen.getByRole('group', { name: 'Formulation for Cleaner' }));
    expect(card.getByText('Ordered 100 KG')).toBeTruthy();
    expect(card.getByText('Retained target 60 KG')).toBeTruthy();
    expect(card.getByText('Produced 20 KG')).toBeTruthy();
    expect(card.getByText('Remaining 40 KG')).toBeTruthy();
    expect(screen.getByText('Draft changes since activation')).toBeTruthy();
  });

  it('creates only after the explicit create action, never during a read', async () => {
    const { requests } = setup({ demo: null });
    const create = await screen.findByRole('button', { name: 'Create Demo Recipe' });
    expect(requests).toEqual([]);
    fireEvent.click(create);
    expect(await screen.findByText('PI-7')).toBeTruthy();
    expect(requests).toEqual([{ url: '/api/sales/7/demo-recipe', method: 'POST', body: {} }]);
  });

  it('does not mistake a failed read for a nonexistent demo', async () => {
    const { requests } = setup({ readError: 503 });
    expect(await screen.findByRole('alert')).toHaveTextContent('Temporarily unavailable');
    expect(screen.queryByRole('button', { name: 'Create Demo Recipe' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(requests).toEqual([]);
  });

  it('saves changed draft percentages with the revision that was read', async () => {
    const { requests } = setup();
    await screen.findByText('PI-7');
    fireEvent.change(percentage(), { target: { value: '70' } });
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Activate' }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
    await screen.findByText('Draft revision 4');
    expect(requests).toEqual([{ url: '/api/sales/7/demo-recipe/draft', method: 'PUT', body: {
      expected_revision: 3,
      products: [{ product_name: 'Cleaner', unit: 'KG', water_percentage: null,
        items: [{ chemical: 'Salt', percentage: 70 }] }],
    } }]);
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    expect(screen.getByText('Active revision 2')).toBeTruthy();
  });

  it('activates the saved revision without running production or changing stock', async () => {
    const { requests } = setup();
    await screen.findByText('PI-7');
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }));
    await screen.findByText('Active revision 3');
    expect(requests).toEqual([{ url: '/api/sales/7/demo-recipe/activate', method: 'POST',
      body: { expected_revision: 3 } }]);
  });

  it('recomputes automatic water and preserves an explicit override when ingredients change', async () => {
    setup();
    await screen.findByText('PI-7');
    expect(screen.getByText('Automatic water: 40%')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Water override for Cleaner'), { target: { value: '15' } });
    fireEvent.change(percentage(), { target: { value: '70' } });
    expect(screen.getByText('Automatic water: 30%')).toBeTruthy();
    expect(screen.getByText('Water used: 15% (override)')).toBeTruthy();
    expect(screen.getByText(/Total 85%/)).toBeTruthy();
    expect(screen.getByLabelText('Water override for Cleaner').value).toBe('15');
  });

  it('keeps empty ingredients unresolved rather than inventing 100% water', async () => {
    setup({ demo: { ...DEMO, products: [{ ...PRODUCT, items: [] }] } });
    await screen.findByText('PI-7');
    expect(screen.getByText('Add an ingredient to resolve automatic water.')).toBeTruthy();
    expect(screen.queryByText('Automatic water: 100%')).toBeNull();
    expect(screen.getByRole('button', { name: 'Activate' }).disabled).toBe(true);
  });

  it('adds a formulation for a newly added PI product missing from the saved draft', async () => {
    setup({ demo: { ...DEMO, products: [], progress: DEMO.progress } });
    await screen.findByText('PI-7');
    expect(screen.getByText(/Cleaner needs a formulation/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add formulation for Cleaner' }));
    expect(screen.getByRole('group', { name: 'Formulation for Cleaner' })).toBeTruthy();
    expect(screen.getByText('Unsaved changes')).toBeTruthy();
  });

  it('retains edits on revision conflict and reloads only when requested', async () => {
    const { replaceDemo } = setup({ writeError: { status: 409, body: { error: 'revision conflict' } } });
    await screen.findByText('PI-7');
    fireEvent.change(percentage(), { target: { value: '75' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
    await screen.findByText(/Draft was modified elsewhere/);
    expect(percentage().value).toBe('75');
    replaceDemo({ ...DEMO, draft_revision: 8 });
    fireEvent.click(screen.getByRole('button', { name: 'Reload draft' }));
    await screen.findByText('Draft revision 8');
    expect(percentage().value).toBe('60');
  });

  it('preserves the active forecast and shows product-specific activation refusals inline', async () => {
    setup({ writeError: { status: 400, body: { error: 'Cannot activate',
      details: [{ field: 'products["Cleaner"].unit', message: 'No conversion to L' }] } } });
    await screen.findByText('PI-7');
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }));
    await screen.findByText('No conversion to L');
    expect(screen.getByText('Active revision 2')).toBeTruthy();
    expect(screen.getByText(/Previous active forecast is unchanged/)).toBeTruthy();
    const card = within(screen.getByRole('group', { name: 'Formulation for Cleaner' }));
    expect(card.getByText('No conversion to L')).toBeTruthy();
  });

  it('rejects a blank percentage rather than submitting an accidental zero', async () => {
    const { requests } = setup();
    await screen.findByText('PI-7');
    fireEvent.change(percentage(), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
    expect(await screen.findByText(/Enter a percentage between 0 and 100/)).toBeTruthy();
    expect(requests).toEqual([]);
  });

  it('shows unknown progress as unknown rather than zero', async () => {
    setup({ demo: { ...DEMO, progress: [{ ...DEMO.progress[0], produced_quantity: null, remaining_quantity: null }] } });
    await screen.findByText('PI-7');
    expect(screen.getByText('Produced unknown')).toBeTruthy();
    expect(screen.getByText('Remaining unknown')).toBeTruthy();
  });

  it('does not load or write when closed', async () => {
    const { view, fetcher, requests } = setup();
    await screen.findByText('PI-7');
    view.rerender(<MemoryRouter><AuthProvider>
      <PiDemoEditor saleId={7} isOpen={false} onClose={() => {}} />
    </AuthProvider></MemoryRouter>);
    fetcher.mockClear();
    await waitFor(() => expect(screen.queryByText('PI-7')).toBeNull());
    expect(fetcher).not.toHaveBeenCalled();
    expect(requests).toEqual([]);
  });
});
