// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import InvoiceEditor from './InvoiceEditor';

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

const SALE_ID = 7;
const INVOICE_ID = 55;

// Line 101: PI 10, 2 already invoiced -> remaining 8, ceiling 10.5 (max add 8.5).
// Line 102: fully invoiced -> remaining 0, still listed.
function budget(overrides = {}) {
  return {
    lines: [
      {
        sale_id: SALE_ID, sale_item_id: 101, product_name: 'Alpha', unit: 'KG',
        pi_quantity: 10, invoiced_quantity: 2, remaining: 8, ceiling: 10.5,
        unit_price: 5, invoice_ids: [5],
      },
      {
        sale_id: SALE_ID, sale_item_id: 102, product_name: 'Beta', unit: 'KG',
        pi_quantity: 4, invoiced_quantity: 4, remaining: 0, ceiling: 4.2,
        unit_price: 3, invoice_ids: [5],
      },
    ],
    has_line_less_invoices: false,
    ...overrides,
  };
}

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

/**
 * Stubs the API surface the editor talks to.
 * `createReply`/`addReply` let a test fail a write once and answer the next
 * budget read with changed figures (the 409 resync contract).
 */
function installFetch({
  budgets = [budget()],
  createReply = null,
  addReply = null,
} = {}) {
  const calls = { budgets: 0, creates: [], adds: [] };
  let budgetIndex = 0;
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    const method = options?.method || 'GET';
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/invoice-lines') && method === 'GET') {
      const body = budgets[Math.min(budgetIndex, budgets.length - 1)];
      budgetIndex += 1;
      calls.budgets += 1;
      return jsonResponse(body);
    }
    if (/\/api\/sales\/\d+\/invoices$/.test(u) && method === 'POST') {
      calls.creates.push(JSON.parse(options.body));
      if (createReply) return createReply();
      return jsonResponse(
        { invoice_id: 900, invoice_number: 'INV-NEW', status: 'planned', amount: 40 },
        true, 201,
      );
    }
    if (/\/api\/invoices\/\d+\/items$/.test(u) && method === 'POST') {
      calls.adds.push({ url: u, body: JSON.parse(options.body) });
      if (addReply) return addReply();
      return jsonResponse({ id: 5, sale_item_id: 101, quantity: 1, unit_price: 5 }, true, 201);
    }
    return jsonResponse({});
  }));
  return calls;
}

function renderEditor(props = {}) {
  const onDone = vi.fn();
  const onCancel = vi.fn();
  render(
    <MemoryRouter>
      <AuthProvider>
        <InvoiceEditor
          mode="create"
          saleId={SALE_ID}
          onDone={onDone}
          onCancel={onCancel}
          {...props}
        />
      </AuthProvider>
    </MemoryRouter>,
  );
  return { onDone, onCancel };
}

const qtyFor = (product) =>
  screen.getByLabelText(`Invoice qty for ${product}`);
const priceFor = (product) =>
  screen.getByLabelText(`Unit price for ${product}`);
const totalFor = (product) =>
  screen.getByLabelText(`Total price for ${product}`);
const saveButton = () => screen.getByRole('button', { name: /save invoice/i });

beforeEach(() => {
  cleanup();
});

describe('InvoiceEditor — create mode', () => {
  it('renders the header fields, one row per PI line and a read-only price', async () => {
    installFetch();
    renderEditor();

    expect(await screen.findByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.getByLabelText('Invoice number')).toBeTruthy();
    expect(screen.getByLabelText('Invoice date').value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(priceFor('Alpha').value).toContain('5');
    expect(priceFor('Alpha').readOnly).toBe(true);
    // The ceiling is a hard stop, not advertised headroom.
    expect(screen.queryByText(/ceiling/i)).toBeNull();
  });

  it('shows the live remaining under PI qty and no invoiced column', async () => {
    installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    const alphaRow = screen.getByText('Alpha').closest('tr');
    // Column order is part of the contract; the input is the only quantity.
    expect(
      screen.getAllByRole('columnheader').map((h) => h.textContent.trim()),
    ).toEqual(['Product', 'PI qty', 'Invoice qty', 'Unit price', 'Total price']);
    expect(qtyFor('Alpha').value).toBe('');
    expect(qtyFor('Alpha').getAttribute('placeholder')).toBeNull();
    // PI 10 with 2 invoiced: the server's remaining is what the row reports.
    expect(alphaRow.textContent).toContain('10.00 KG');
    expect(alphaRow.textContent).toContain('Remaining 8.00 KG');
    expect(alphaRow.textContent).not.toMatch(/Invoiced/);

    fireEvent.change(qtyFor('Alpha'), { target: { value: '3' } });
    expect(alphaRow.textContent).toContain('Remaining 5.00 KG');

    fireEvent.change(qtyFor('Alpha'), { target: { value: '' } });
    expect(alphaRow.textContent).toContain('Remaining 8.00 KG');

    // The column model no longer restates what is already invoiced.
    expect(screen.queryByRole('columnheader', { name: /invoiced/i })).toBeNull();
    expect(screen.queryByRole('columnheader', { name: /remaining/i })).toBeNull();
  });

  it('prices the line as quantity times unit price', async () => {
    installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    expect(totalFor('Alpha').value).toBe('$0.00');
    expect(totalFor('Alpha').readOnly).toBe(true);

    fireEvent.change(qtyFor('Alpha'), { target: { value: '3' } });
    expect(totalFor('Alpha').value).toBe('$15.00'); // unit price 5

    fireEvent.change(qtyFor('Alpha'), { target: { value: '' } });
    expect(totalFor('Alpha').value).toBe('$0.00');
  });

  it('floors remaining at zero while the 5% slack stays usable', async () => {
    installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    const betaRow = screen.getByText('Beta').closest('tr'); // PI 4, 4 invoiced
    expect(betaRow.textContent).toContain('Remaining 0.00 KG');

    fireEvent.change(qtyFor('Beta'), { target: { value: '0.2' } }); // inside ceiling 4.2
    expect(betaRow.textContent).toContain('Remaining 0.00 KG');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-1' },
    });
    expect(saveButton().disabled).toBe(false);
  });

  it('keeps Save disabled until a number and one quantity exist', async () => {
    installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    expect(saveButton().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-1' },
    });
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(qtyFor('Alpha'), { target: { value: '1' } });
    expect(saveButton().disabled).toBe(false);
  });

  it('posts exactly the server contract and never a price', async () => {
    const calls = installFetch();
    const { onDone } = renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-77' },
    });
    fireEvent.change(qtyFor('Alpha'), { target: { value: '3' } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(calls.creates).toHaveLength(1));
    const body = calls.creates[0];
    expect(body.invoice_number).toBe('INV-77');
    expect(body.invoice_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(body.lines).toEqual([{ sale_item_id: 101, quantity: 3 }]);
    expect(JSON.stringify(body)).not.toContain('unit_price');
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(onDone.mock.calls[0][0].invoice_id).toBe(900);
  });

  it('stops at the 5% ceiling before posting', async () => {
    const calls = installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-1' },
    });
    fireEvent.change(qtyFor('Alpha'), { target: { value: '8.6' } }); // 2 + 8.6 > 10.5

    expect(screen.getByText(/over the invoicing limit — this line can add at most 8\.50/i)).toBeTruthy();
    // The over-limit row still reports the money it would commit and floors
    // the PI remaining; only Save refuses it.
    const alphaRow = screen.getByText('Alpha').closest('tr');
    expect(alphaRow.textContent).toContain('Remaining 0.00 KG');
    expect(totalFor('Alpha').value).toBe('$43.00');
    expect(saveButton().disabled).toBe(true);
    fireEvent.click(saveButton());
    expect(calls.creates).toHaveLength(0);
  });

  it('allows a quantity above remaining while it stays under the ceiling', async () => {
    const calls = installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-1' },
    });
    fireEvent.change(qtyFor('Alpha'), { target: { value: '8.4' } }); // > 8, <= 8.5

    // Above the PI remaining but inside the +5% cap: no refusal copy.
    expect(screen.queryByText(/over the invoicing limit/i)).toBeNull();
    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.creates).toHaveLength(1));
  });

  it('accepts a quantity exactly at the ceiling', async () => {
    const calls = installFetch();
    renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-1' },
    });
    fireEvent.change(qtyFor('Alpha'), { target: { value: '8.5' } }); // 2 + 8.5 === 10.5

    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.creates).toHaveLength(1));
  });

  it('takes the maximum from the payload, not from pi * 1.05', async () => {
    // A future tolerance change must reach the UI without a code edit. If the
    // editor recomputed pi * 1.05 it would cap at 10.5 and refuse 11 here.
    const calls = installFetch({
      budgets: [{
        lines: [{
          sale_id: SALE_ID, sale_item_id: 101, product_name: 'Alpha', unit: 'KG',
          pi_quantity: 10, invoiced_quantity: 0, remaining: 10, ceiling: 12,
          unit_price: 5, invoice_ids: [],
        }],
        has_line_less_invoices: false,
      }],
    });
    renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-1' },
    });
    fireEvent.change(qtyFor('Alpha'), { target: { value: '11' } });

    expect(saveButton().disabled).toBe(false);
    fireEvent.click(saveButton());
    await waitFor(() => expect(calls.creates).toHaveLength(1));
  });

  it('resyncs and preserves the form on a 409', async () => {
    // Second read reports more invoiced, so the same quantity is now over.
    const calls = installFetch({
      budgets: [
        budget(),
        budget({
          lines: [
            { ...budget().lines[0], invoiced_quantity: 6, remaining: 4, ceiling: 10.5 },
            budget().lines[1],
          ],
        }),
      ],
      createReply: () => jsonResponse(
        { error: 'PI line 101: invoiced 12 exceeds 10.5 (PI qty 10 + 5%)' },
        false, 409,
      ),
    });
    renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-409' },
    });
    // Locally fine against the stale budget (2 invoiced, 8.5 still addable)
    // but over once the server reports 6 invoiced: the write is refused.
    fireEvent.change(qtyFor('Alpha'), { target: { value: '6' } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(calls.budgets).toBeGreaterThanOrEqual(2));
    // The reloaded figures are the ones that count: 6 invoiced leaves 4.5.
    expect(
      await screen.findByText(/this line can add at most 4\.50/i),
    ).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
    expect(screen.getByLabelText('Invoice number').value).toBe('INV-409');
    expect(qtyFor('Alpha').value).toBe('6');
  });

  it('marks a vanished PI line superseded on a 400 and keeps the header', async () => {
    const calls = installFetch({
      budgets: [
        budget(),
        budget({ lines: [budget().lines[1]] }), // 101 disappeared
      ],
      createReply: () => jsonResponse(
        { error: 'sale item 101 not found in this sale' }, false, 400,
      ),
    });
    renderEditor();
    await screen.findByText('Alpha');

    fireEvent.change(screen.getByLabelText('Invoice number'), {
      target: { value: 'INV-400' },
    });
    fireEvent.change(qtyFor('Alpha'), { target: { value: '1' } });
    fireEvent.click(saveButton());

    await waitFor(() => expect(calls.budgets).toBeGreaterThanOrEqual(2));
    const notice = await screen.findByText(/no longer on this PI/i);
    // A dropped row cannot carry its own mark, so the notice must name it.
    expect(notice.textContent).toContain('Alpha');
    expect(screen.getByLabelText('Invoice number').value).toBe('INV-400');
  });
});

describe('InvoiceEditor — add-line mode', () => {
  it('posts one line to the chosen invoice and echoes it to onDone', async () => {
    const calls = installFetch();
    const { onDone } = renderEditor({
      mode: 'add-line',
      targetInvoiceId: INVOICE_ID,
      invoiceLabel: 'INV-55',
    });
    await screen.findByText('Alpha');

    expect(screen.queryByLabelText('Invoice number')).toBeNull();
    fireEvent.change(qtyFor('Alpha'), { target: { value: '1.5' } });
    // The same computed column is available in add-line mode.
    expect(totalFor('Alpha').value).toBe('$7.50');
    fireEvent.click(screen.getByRole('button', { name: /add line/i }));

    await waitFor(() => expect(calls.adds).toHaveLength(1));
    expect(calls.adds[0].url).toContain(`/api/invoices/${INVOICE_ID}/items`);
    expect(calls.adds[0].body).toEqual({ sale_item_id: 101, quantity: 1.5 });
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('posts every staged row, one request each, and reports once', async () => {
    // add-line mode still shows the whole table, so more than one row can be
    // staged; silently dropping all but the first would lose the operator's
    // input without saying so.
    const calls = installFetch();
    const { onDone } = renderEditor({
      mode: 'add-line',
      targetInvoiceId: INVOICE_ID,
      invoiceLabel: 'INV-55',
    });
    await screen.findByText('Alpha');

    fireEvent.change(qtyFor('Alpha'), { target: { value: '2' } });
    fireEvent.change(qtyFor('Beta'), { target: { value: '0.2' } });
    fireEvent.click(screen.getByRole('button', { name: /add line/i }));

    await waitFor(() => expect(calls.adds).toHaveLength(2));
    expect(calls.adds.map((a) => a.body)).toEqual([
      { sale_item_id: 101, quantity: 2 },
      { sale_item_id: 102, quantity: 0.2 },
    ]);
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
  });

  it('flags the failing line on a 409, resyncs and keeps its quantity', async () => {
    const calls = installFetch({
      budgets: [
        budget(),
        // A concurrent add landed: 8 invoiced now, so 8 + 4 > 10.5.
        budget({
          lines: [
            { ...budget().lines[0], invoiced_quantity: 8, remaining: 2 },
            budget().lines[1],
          ],
        }),
      ],
      addReply: () => jsonResponse(
        { error: 'PI line 101: invoiced 12 exceeds 10.5 (PI qty 10 + 5%)' },
        false, 409,
      ),
    });
    renderEditor({
      mode: 'add-line',
      targetInvoiceId: INVOICE_ID,
      invoiceLabel: 'INV-55',
    });
    await screen.findByText('Alpha');

    fireEvent.change(qtyFor('Alpha'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /add line/i }));

    await waitFor(() => expect(calls.adds).toHaveLength(1));
    expect(calls.budgets).toBeGreaterThanOrEqual(2);
    expect(
      await screen.findByText(/this line can add at most 2\.50/i),
    ).toBeTruthy();
    expect(qtyFor('Alpha').value).toBe('4');
  });

  it('marks the target superseded when the invoice itself is gone', async () => {
    installFetch({
      addReply: () => jsonResponse(
        { error: 'Invoice not found', code: 'invoice_gone' }, false, 404,
      ),
    });
    renderEditor({
      mode: 'add-line',
      targetInvoiceId: INVOICE_ID,
      invoiceLabel: 'INV-55',
    });
    await screen.findByText('Alpha');

    fireEvent.change(qtyFor('Alpha'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: /add line/i }));

    expect(await screen.findByText(/no longer available/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /add line/i }).disabled).toBe(true);
  });

  it('reports the rows that landed when a later row is refused', async () => {
    // Each POST is its own transaction, so stopping half-way still invoiced the
    // rows before it. The parent must hear about them, or the sale keeps
    // showing the pre-submit totals until something unrelated refreshes.
    let attempt = 0;
    const calls = installFetch({
      budgets: [
        budget(),
        budget({
          lines: [
            { ...budget().lines[0], invoiced_quantity: 4, remaining: 6 },
            budget().lines[1],
          ],
        }),
      ],
      addReply: () => {
        attempt += 1;
        if (attempt === 1) {
          return jsonResponse(
            { id: 5, sale_item_id: 101, quantity: 2, unit_price: 5 }, true, 201,
          );
        }
        return jsonResponse(
          { error: 'PI line 102: invoiced 4.2 exceeds 4.2 (PI qty 4 + 5%)' },
          false, 409,
        );
      },
    });
    const { onDone } = renderEditor({
      mode: 'add-line',
      targetInvoiceId: INVOICE_ID,
      invoiceLabel: 'INV-55',
    });
    await screen.findByText('Alpha');

    // Beta is fully invoiced, so only the 0.2 left inside its 5% ceiling is
    // submittable - anything more would be blocked by the hard stop, not the
    // server, and this test is about a refusal that arrives mid-sequence.
    fireEvent.change(qtyFor('Alpha'), { target: { value: '2' } });
    fireEvent.change(qtyFor('Beta'), { target: { value: '0.2' } });
    fireEvent.click(screen.getByRole('button', { name: /add line/i }));

    await waitFor(() => expect(calls.adds).toHaveLength(2));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    // The landed row is cleared; the refused one keeps what was typed.
    await waitFor(() => expect(qtyFor('Alpha').value).toBe(''));
    expect(qtyFor('Beta').value).toBe('0.2');
  });

  it('drops a PI line that vanished under add-line and says so', async () => {
    // An add-line 404 is ambiguous, so the body carries a code. A vanished PI
    // line is not a dead target invoice: resync, name it, keep editing.
    let attempt = 0;
    const calls = installFetch({
      budgets: [budget(), budget({ lines: [budget().lines[0]] })],
      addReply: () => {
        attempt += 1;
        if (attempt === 1) {
          return jsonResponse(
            { id: 6, sale_item_id: 101, quantity: 1, unit_price: 5 }, true, 201,
          );
        }
        return jsonResponse(
          { error: 'sale item not found', code: 'pi_line_gone' }, false, 404,
        );
      },
    });
    const { onDone } = renderEditor({
      mode: 'add-line',
      targetInvoiceId: INVOICE_ID,
      invoiceLabel: 'INV-55',
    });
    await screen.findByText('Alpha');

    fireEvent.change(qtyFor('Alpha'), { target: { value: '1' } });
    fireEvent.change(qtyFor('Beta'), { target: { value: '0.2' } });
    fireEvent.click(screen.getByRole('button', { name: /add line/i }));

    await waitFor(() => expect(calls.adds).toHaveLength(2));
    expect(
      await screen.findByText(/1 PI line\(s\) are no longer on this PI/i),
    ).toBeTruthy();
    // The vanished row is gone, not left as a permanent refusal.
    await waitFor(() => expect(screen.queryByLabelText('Invoice qty for Beta')).toBeNull());
    expect(screen.queryByText(/sale item not found/i)).toBeNull();
    expect(screen.queryByText(/no longer available/i)).toBeNull();
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});
