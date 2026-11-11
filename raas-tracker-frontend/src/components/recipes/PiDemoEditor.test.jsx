// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    expect(screen.getByText('Automatic water: 40.0%')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Water override for Cleaner'), { target: { value: '15' } });
    fireEvent.change(percentage(), { target: { value: '70' } });
    expect(screen.getByText('Automatic water: 30.0%')).toBeTruthy();
    expect(screen.getByText('Water used: 15.0% (override)')).toBeTruthy();
    expect(screen.getByText(/Total 85.0%/)).toBeTruthy();
    expect(screen.getByLabelText('Water override for Cleaner').value).toBe('15');
  });

  it('keeps empty ingredients unresolved rather than inventing 100% water', async () => {
    setup({ demo: { ...DEMO, products: [{ ...PRODUCT, items: [] }] } });
    await screen.findByText('PI-7');
    expect(screen.getByText('Add an ingredient to resolve automatic water.')).toBeTruthy();
    expect(screen.queryByText('Automatic water: 100.0%')).toBeNull();
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

  it('keeps the edits on a revision conflict and only loses them to an explicit discard', async () => {
    const { replaceDemo } = setup({ writeError: { status: 409, body: { error: 'revision conflict' } } });
    await screen.findByText('PI-7');
    fireEvent.change(percentage(), { target: { value: '75' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
    const banner = await screen.findByText(/Draft was modified elsewhere/);
    // The words must match the button: nothing is thrown away until the
    // operator asks for the reload that replaces their edits.
    expect(banner.textContent).toMatch(/still in this form/);
    expect(banner.textContent).toMatch(/replaces your edits/i);
    expect(percentage().value).toBe('75');
    replaceDemo({ ...DEMO, draft_revision: 8 });
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits and reload' }));
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

  it('never claims a draft changed since an activation that never happened', async () => {
    setup({ demo: { ...DEMO, draft_revision: 1, active_revision: null } });
    await screen.findByText('PI-7');
    expect(screen.getByText('Active revision —')).toBeTruthy();
    expect(screen.queryByText('Draft changes since activation')).toBeNull();
  });

  it('prints percentages to one decimal so float dust cannot fake an illegal plan', async () => {
    // 0.01 + 64.04 + 34.95 adds up to 99.00000000000001 in binary float, and
    // the 1% water puts the total at 100.00000000000001 — inside the activation
    // tolerance, but displayed as raw float dust without rounding.
    setup({ demo: { ...DEMO, products: [{
      product_name: 'Cleaner', unit: 'KG', water_percentage: 1,
      items: [
        { chemical: 'A', chemical_id: 1, percentage: 0.01 },
        { chemical: 'B', chemical_id: 2, percentage: 64.04 },
        { chemical: 'C', chemical_id: 3, percentage: 34.95 },
      ],
    }] } });
    await screen.findByText('PI-7');
    const card = within(screen.getByRole('group', { name: 'Formulation for Cleaner' }));
    expect(card.getByText('Total 100.0%')).toBeTruthy();
    expect(card.queryByText(/100\.00000000000001/)).toBeNull();
    expect(card.getByText('Water used: 1.0% (override)')).toBeTruthy();
  });

  it('rounds the automatic water remainder instead of printing float dust', async () => {
    setup({ demo: { ...DEMO, products: [{
      product_name: 'Cleaner', unit: 'KG', water_percentage: null,
      items: [
        { chemical: 'A', chemical_id: 1, percentage: 33.33 },
        { chemical: 'B', chemical_id: 2, percentage: 33.33 },
        { chemical: 'C', chemical_id: 3, percentage: 33.33 },
      ],
    }] } });
    await screen.findByText('PI-7');
    const card = within(screen.getByRole('group', { name: 'Formulation for Cleaner' }));
    // 100 - 99.99 is 0.010000000000005116 in float.
    expect(card.getByText('Automatic water: 0.0%')).toBeTruthy();
    expect(card.queryByText(/0\.010000000000005116/)).toBeNull();
  });
});

// A response that was superseded — by a later read, another PI, a close, or a
// write that raced a switch — must be a no-op. Writing PI A's formulation into
// PI B's draft is how the next save posts the wrong plan.
const pendingRead = () => {
  let resolve;
  const gate = new Promise((done) => { resolve = done; });
  return { gate, resolve };
};

const productOf = (saleId) => (saleId === 7 ? 'Cleaner' : 'Conditioner');

const demoFor = (saleId) => ({
  id: saleId, sale_id: saleId, pi_number: `PI-${saleId}`, client_name: 'Client Co',
  draft_revision: 1, active_revision: 1, activated_at: null, activated_by: null,
  products: [{
    product_name: productOf(saleId), unit: 'KG', water_percentage: null,
    items: [{ chemical: 'Salt', chemical_id: 1, percentage: 60 }],
  }],
  active_products: [], progress: [],
});

// Every read and every draft save hangs on its own gate, pushed in the order
// the editor asked for it, so a test can resolve them out of order.
function setupRace() {
  const reads = [];
  const drafts = [];
  const saves = [];
  const fetcher = vi.fn(async (url, opts = {}) => {
    const method = opts.method || 'GET';
    if (url === '/api/auth/me') return response({ id: 1, username: 'admin', role: 'admin' });
    if (url === '/api/chemicals/catalog') return response([{ id: 1, name: 'Salt', unit: 'KG' }]);
    const read = /^\/api\/sales\/(\d+)\/demo-recipe$/.exec(url);
    if (read && method === 'GET') {
      const gate = pendingRead();
      reads.push({ saleId: Number(read[1]), gate });
      await gate.gate;
      return response({ demo: demoFor(Number(read[1])) });
    }
    const draft = /^\/api\/sales\/(\d+)\/demo-recipe\/draft$/.exec(url);
    if (draft && method === 'PUT') {
      const gate = pendingRead();
      drafts.push({ saleId: Number(draft[1]), gate });
      saves.push({ saleId: Number(draft[1]), body: JSON.parse(opts.body) });
      await gate.gate;
      return response({ demo: demoFor(Number(draft[1])) });
    }
    throw new Error(`Unexpected request ${method} ${url}`);
  });
  vi.stubGlobal('fetch', fetcher);
  const utils = render(<MemoryRouter><AuthProvider>
    <PiDemoEditor saleId={7} isOpen onClose={() => {}} />
  </AuthProvider></MemoryRouter>);
  // Let every continuation of a settled response run to completion.
  const settle = async () => {
    await act(async () => { await new Promise((done) => { setTimeout(done, 0); }); });
  };
  return { reads, drafts, saves, fetcher, settle, ...utils };
}

const wrapped = (saleId) => (
  <MemoryRouter><AuthProvider>
    <PiDemoEditor saleId={saleId} isOpen onClose={() => {}} />
  </AuthProvider></MemoryRouter>
);

const closedOver = (saleId) => (
  <MemoryRouter><AuthProvider>
    <PiDemoEditor saleId={saleId} isOpen={false} onClose={() => {}} />
  </AuthProvider></MemoryRouter>
);

describe('PI demo editor superseded responses', () => {
  it('drops the older PI response and keeps every action inert while a load runs', async () => {
    const { reads, saves, rerender, fetcher, settle } = setupRace();

    // PI A's first read is in flight: there is no footer to post a wrong baseline.
    await waitFor(() => expect(reads).toHaveLength(1));
    expect(screen.queryByRole('button', { name: 'Save Draft' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Activate' })).toBeNull();

    reads[0].gate.resolve();
    await settle();
    expect(screen.getByText('PI-7')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save Draft' }).disabled).toBe(false);

    // Switch to PI B, then back to A and to B again: the second read for A is
    // still on the wire when B's read is issued, and it answers last.
    rerender(wrapped(8));
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(screen.getByRole('button', { name: 'Save Draft' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Activate' }).disabled).toBe(true);

    rerender(wrapped(7));
    await waitFor(() => expect(reads).toHaveLength(3));
    rerender(wrapped(8));
    await waitFor(() => expect(reads).toHaveLength(4));
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/sales/8/demo-recipe'))
      .toHaveLength(2);

    reads[3].gate.resolve();
    await settle();
    expect(screen.getByText('PI-8')).toBeTruthy();

    // The superseded answer for PI A lands last: it must change nothing at all.
    reads[2].gate.resolve();
    await settle();
    expect(screen.queryByText('PI-7')).toBeNull();
    expect(screen.queryByRole('group', { name: 'Formulation for Cleaner' })).toBeNull();
    expect(screen.getByRole('group', { name: 'Formulation for Conditioner' })).toBeTruthy();

    // And the save that follows can only carry the PI now on screen.
    fireEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].saleId).toBe(8);
    expect(saves[0].body.products.map((p) => p.product_name)).toEqual(['Conditioner']);
  });

  it('drops a draft save that lands after the operator moved to another PI', async () => {
    const { reads, drafts, rerender, settle } = setupRace();

    await waitFor(() => expect(reads).toHaveLength(1));
    reads[0].gate.resolve();
    await settle();
    expect(screen.getByText('PI-7')).toBeTruthy();

    // PI A's save goes out, then the operator switches to PI B mid-flight.
    fireEvent.click(screen.getByRole('button', { name: 'Save Draft' }));
    await waitFor(() => expect(drafts).toHaveLength(1));
    rerender(wrapped(8));
    await waitFor(() => expect(reads).toHaveLength(2));
    reads[1].gate.resolve();
    await settle();
    expect(screen.getByText('PI-8')).toBeTruthy();

    drafts[0].gate.resolve();
    await settle();
    expect(screen.queryByRole('group', { name: 'Formulation for Cleaner' })).toBeNull();
    expect(screen.getByRole('group', { name: 'Formulation for Conditioner' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save Draft' }).disabled).toBe(false);
  });

  it('drops a read that lands after the editor was closed', async () => {
    const { reads, rerender, settle } = setupRace();
    await waitFor(() => expect(reads).toHaveLength(1));

    rerender(closedOver(7));
    reads[0].gate.resolve();
    await settle();

    // Reopened on another PI: the retired read left no draft behind, so there
    // is nothing to post until this PI's own demo arrives.
    rerender(wrapped(8));
    await waitFor(() => expect(reads).toHaveLength(2));
    expect(screen.queryByRole('button', { name: 'Save Draft' })).toBeNull();
    expect(screen.queryByText('Draft revision 1')).toBeNull();

    reads[1].gate.resolve();
    await settle();
    expect(screen.getByText('PI-8')).toBeTruthy();
  });
});
