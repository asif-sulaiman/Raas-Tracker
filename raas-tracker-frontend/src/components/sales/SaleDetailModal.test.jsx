// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import { ConfirmProvider } from '../../context/ConfirmContext';
import SaleDetailModal from './SaleDetailModal';

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

const DETAIL = {
  id: 5, stage: 'shipment_ongoing', pi_number: 'PI-5', pi_date: '2026-09-01',
  client_name: 'Acme', pi_file_path: null, lc_number: 'LC-1', lc_date: '2026-09-02',
  shipment_date: '2026-09-15', payment_date: null, payment_amount: 0,
  created_at: '2026-09-01 10:00:00', updated_at: '2026-09-02 10:00:00',
  company_id: 7, maturity_date: null, comments: null, company_name: 'Acme',
  items: [{ id: 21, sale_id: 5, product_name: 'Cotton', quantity: 100, unit_price: 3.25, unit: 'KG' }],
  history: [],
  payments: [],
  shipments: [
    { id: 9, sale_id: 5, ship_date: '2026-09-10', invoice_number: 'INV-9', invoice_date: '2026-09-09', notes: 'partial 1/2', created_at: '2026-09-10 10:00:00' },
  ],
  invoice_total: 325, total_paid: 0, balance: 325,
};

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch({
  role = 'admin', deletes = [], failGetsAfterPut = false, notFound = false,
  detail = DETAIL, invoices = [],
} = {}) {
  let putSeen = false;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: role, role });
    }
    if (u.includes('/invoice-lines') && method === 'GET') {
      return jsonResponse({ lines: [], has_line_less_invoices: false });
    }
    if (u.match(/\/api\/sales\/5\/invoices$/) && method === 'GET') {
      return jsonResponse(invoices);
    }
    if (u.includes('/completion') && method === 'GET') {
      return jsonResponse({ status: 'none', paid: 0, total: invoices.length, invoices });
    }
    if (u.match(/\/api\/sales\/5\/shipments\/\d+/) && method === 'DELETE') {
      deletes.push(u);
      return jsonResponse({ message: 'Shipment deleted' });
    }
    if (u.match(/\/api\/sales\/5$/) && method === 'PUT') {
      putSeen = true;
      return jsonResponse({ comments: 'ok' });
    }
    if (u.match(/\/api\/sales\/5$/)) {
      if (notFound) return jsonResponse({ error: 'Sale not found' }, false, 404);
      if (failGetsAfterPut && putSeen) return jsonResponse({ error: 'Bad gateway' }, false, 502);
      return jsonResponse(detail);
    }
    if (u.includes('/api/sales/5')) {
      return jsonResponse(detail);
    }
    return jsonResponse({});
  }));
}

function renderDetail(opts = {}) {
  installFetch(opts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ConfirmProvider>
          <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={vi.fn()} />
        </ConfirmProvider>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('SaleDetailModal shipments', () => {
  it('lists shipments without offering legacy recording', async () => {
    renderDetail({ role: 'admin' });
    expect(await screen.findByText('INV-9')).toBeTruthy();
    expect(screen.getByText('partial 1/2')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Record shipment' })).toBeNull();
  });

  it('hides shipment actions from non-admins', async () => {
    renderDetail({ role: 'user' });
    expect(await screen.findByText('INV-9')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Record shipment' })).toBeNull();
    expect(screen.queryByRole('button', { name: /delete shipment/i })).toBeNull();
  });

  it('deletes a shipment after confirm', async () => {
    const deletes = [];
    renderDetail({ role: 'admin', deletes });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete shipment INV-9' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deletes).toHaveLength(1));
    expect(deletes[0]).toMatch(/\/api\/sales\/5\/shipments\/9/);
  });

  it('keeps the sale on screen when a post-mutation refresh fails', async () => {
    // The save succeeds, then the follow-up GET 502s.
    renderDetail({ role: 'admin', failGetsAfterPut: true });
    expect(await screen.findByText('INV-9')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Sale comments'), { target: { value: 'check the balance' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    // A failed refresh is not evidence the sale is gone.
    expect(await screen.findByText(/could not refresh/i)).toBeTruthy();
    expect(screen.queryByText('Sale not found')).toBeNull();
    // The already-loaded content stays readable and is flagged as stale.
    expect(screen.getByText('INV-9')).toBeTruthy();
    expect(screen.getByText('partial 1/2')).toBeTruthy();
    expect(screen.getByText('Cotton')).toBeTruthy();
    screen.getByRole('button', { name: 'Retry' });
  });

  it('recovers the sale when the refresh retry succeeds', async () => {
    // The initial load succeeds; every later refresh 502s until the retry.
    let gets = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      const method = options?.method || 'GET';
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.match(/\/api\/sales\/5$/) && method === 'PUT') return jsonResponse({ comments: 'ok' });
      if (u.match(/\/api\/sales\/5$/)) {
        gets += 1;
        if (gets === 1) return jsonResponse(DETAIL);
        if (gets < 3) return jsonResponse({ error: 'Bad gateway' }, false, 502);
        return jsonResponse(DETAIL);
      }
      if (u.includes('/api/sales/5')) return jsonResponse(DETAIL);
      return jsonResponse({});
    }));
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={vi.fn()} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('INV-9')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Sale comments'), { target: { value: 'note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/could not refresh/i)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/could not refresh/i)).toBeNull());
    expect(screen.getByText('INV-9')).toBeTruthy();
  });

  it('reports a missing sale only on an actual not-found response', async () => {
    renderDetail({ notFound: true });
    expect(await screen.findByText('Sale not found')).toBeTruthy();
    // Nothing was loaded, so there is no content to preserve and no retry.
    expect(screen.queryByText('INV-9')).toBeNull();
  });

  it('F8 renders no add-line section when there is no open invoice', async () => {
    const noInv = { ...DETAIL, invoices: [], stage: 'shipment_ongoing' };
    vi.stubGlobal('fetch', vi.fn(async (url) => {
      const u = String(url);
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.match(/\/api\/sales\/5$/)) return jsonResponse(noInv);
      if (u.includes('/api/sales/5')) return jsonResponse(noInv);
      return jsonResponse({});
    }));
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={vi.fn()} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('Cotton')).toBeTruthy();
    // Nothing can receive a line, so the composer is not offered at all.
    expect(screen.queryByLabelText('Target invoice')).toBeNull();
    expect(screen.queryByText(/add lines to invoice/i)).toBeNull();
  });

  it('F10 reads the add-line budget from the sale endpoint and surfaces its 404', async () => {
    // The per-invoice items read is gone: one call now feeds every row of the
    // composer, and a failure must be reported rather than rendered as an
    // empty PI (which would read as "nothing left to invoice").
    const withInv = {
      ...DETAIL,
      invoices: [{ invoice_id: 101, invoice_number: 'INV-101', status: 'planned' }],
    };
    let budgetCalls = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      const method = options?.method || 'GET';
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.includes('/invoice-lines') && method === 'GET') {
        budgetCalls += 1;
        return jsonResponse({ error: 'Sale not found' }, false, 404);
      }
      if (u.match(/\/api\/sales\/5$/)) return jsonResponse(withInv);
      if (u.includes('/api/sales/5')) return jsonResponse(withInv);
      return jsonResponse({});
    }));
    const onSaved = vi.fn();
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={onSaved} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('Cotton')).toBeTruthy();
    // An open invoice makes the composer available; its budget read is the
    // sale-level endpoint, not one call per invoice.
    expect(await screen.findByLabelText('Target invoice')).toBeTruthy();
    await waitFor(() => expect(budgetCalls).toBeGreaterThanOrEqual(1));
    expect(await screen.findByText(/sale not found/i)).toBeTruthy();
  });
});

describe('SaleDetailModal invoice lifecycle by stage', () => {
  it('mounts invoice management in Payment Due', async () => {
    renderDetail({ detail: { ...DETAIL, stage: 'payment_due' } });
    // Invoice management follows the sale past Shipment Ongoing.
    expect(await screen.findByRole('button', { name: 'Add invoice' })).toBeTruthy();
    expect(screen.getByText(/^Invoices \(/)).toBeTruthy();
  });

  it('shows completed invoices read-only while payments stay editable', async () => {
    const invoices = [{
      invoice_id: 101, invoice_number: 'INV-101', status: 'paid',
      approx_ship_date: '2026-09-01', actual_ship_date: '2026-09-02', notes: null,
      created_at: '2026-09-01 10:00:00', amount: 325, paid_amount: 325,
    }];
    const detail = {
      ...DETAIL,
      stage: 'completed',
      invoices,
      payments: [{ id: 3, payment_date: '2026-10-01', payment_amount: 325, notes: null }],
      total_paid: 325, balance: 0,
    };
    renderDetail({ detail, invoices });

    expect(await screen.findByText('INV-101')).toBeTruthy();
    // No invoice or line may be added, advanced, or voided on a completed sale.
    expect(screen.queryByRole('button', { name: 'Add invoice' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Book shipment' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Ship' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Record payment' })).toBeNull();
    expect(screen.queryByRole('button', { name: /void invoice/i })).toBeNull();
    expect(screen.queryByText(/add lines to invoice/i)).toBeNull();
    // The payment ledger stays correctable — that is the report's edit path.
    expect(screen.getByTitle('Edit payment')).toBeTruthy();
  });

  it('targets only planned or produced invoices for new lines', async () => {
    const invoices = [
      { invoice_id: 101, invoice_number: 'INV-PLAN', status: 'planned' },
      { invoice_id: 102, invoice_number: 'INV-BOOK', status: 'booked' },
    ];
    renderDetail({ detail: { ...DETAIL, invoices }, invoices });

    const select = await screen.findByLabelText('Target invoice');
    const options = Array.from(select.querySelectorAll('option'));
    // A booked invoice is frozen: lines may not be added behind its back.
    expect(options).toHaveLength(1);
    expect(select.textContent).toContain('INV-PLAN');
    expect(select.textContent).not.toContain('INV-BOOK');
  });
});

// The server enforces gt=0 on sale_items.quantity / unit_price. parse_sales.py
// emits unit_price 0.0 when the PI has no recognisable price column, so a zero
// is a realistic operator-facing value here — it must be stopped in the browser
// with a clear reason instead of being POSTed and coming back as a bare 400.
describe('SaleDetailModal item sign guard', () => {
  // Self-contained fetch: the shared installFetch answers PUT /api/sales/5 with a
  // body that has no `items`, which is fine for the comment-save tests but makes
  // a successful item save render `sale.items.length` on undefined.
  let puts = [];
  function installEditorFetch() {
    puts = [];
    vi.stubGlobal('fetch', vi.fn(async (url, options) => {
      const u = String(url);
      const method = options?.method || 'GET';
      if (u.includes('/api/auth/me')) return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
      if (u.match(/\/api\/sales\/5$/) && method === 'PUT') {
        puts.push(JSON.parse(options.body));
        return jsonResponse(DETAIL);
      }
      if (u.match(/\/api\/sales\/5$/)) return jsonResponse(DETAIL);
      if (u.includes('/api/sales/5')) return jsonResponse(DETAIL);
      return jsonResponse({});
    }));
  }

  async function openEditor(onSaved) {
    installEditorFetch();
    render(
      <MemoryRouter>
        <AuthProvider>
          <ConfirmProvider>
            <SaleDetailModal saleId={5} onClose={vi.fn()} onSaved={onSaved} />
          </ConfirmProvider>
        </AuthProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText('Cotton')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Sale' }));
    // In edit mode each row renders qty and unit price as number inputs
    // (role=spinbutton), in that DOM order.
    const spinbuttons = await screen.findAllByRole('spinbutton');
    return { quantity: spinbuttons[0], unitPrice: spinbuttons[1] };
  }

  it('blocks a zero quantity client-side instead of posting it', async () => {
    const onSaved = vi.fn();
    const { quantity } = await openEditor(onSaved);
    fireEvent.change(quantity, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    expect(await screen.findByText(/must be greater than zero/i)).toBeTruthy();
    // No PUT may be attempted, and no success callback may fire.
    expect(puts).toHaveLength(0);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('blocks a zero unit price client-side instead of posting it', async () => {
    const onSaved = vi.fn();
    const { unitPrice } = await openEditor(onSaved);
    fireEvent.change(unitPrice, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    expect(await screen.findByText(/must be greater than zero/i)).toBeTruthy();
    expect(puts).toHaveLength(0);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('still blocks negatives, and still saves a valid line', async () => {
    const onSaved = vi.fn();
    const { quantity } = await openEditor(onSaved);
    fireEvent.change(quantity, { target: { value: '-5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    expect(await screen.findByText(/must be greater than zero/i)).toBeTruthy();
    expect(puts).toHaveLength(0);
    expect(onSaved).not.toHaveBeenCalled();

    fireEvent.change(quantity, { target: { value: '100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].items[0]).toMatchObject({ product_name: 'Cotton', quantity: 100, unit_price: 3.25 });
    expect(onSaved).toHaveBeenCalledTimes(1);
  });
});
