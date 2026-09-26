// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../context/AuthContext';
import ShipmentModal from './ShipmentModal';

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

function jsonResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data, headers: { get: () => null } };
}

function installFetch(posts) {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url);
    if (u.includes('/api/auth/me')) {
      return jsonResponse({ id: 1, username: 'admin', role: 'admin' });
    }
    if (u.includes('/api/sales/5/shipments') && options && options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      return { ok: true, status: 201, json: async () => ({ id: 11 }), headers: { get: () => null } };
    }
    return jsonResponse({});
  }));
}

function renderShipment(posts, onSaved = vi.fn()) {
  installFetch(posts);
  return render(
    <MemoryRouter>
      <AuthProvider>
        <ShipmentModal isOpen saleId={5} onClose={vi.fn()} onSaved={onSaved} />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('ShipmentModal', () => {
  it('requires a ship date before posting', async () => {
    const posts = [];
    renderShipment(posts);
    const dateInput = await screen.findByLabelText('Ship date');
    fireEvent.change(dateInput, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Shipment' }));
    expect(await screen.findByText(/ship date is required/i)).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it('posts the shipment with invoice data', async () => {
    const posts = [];
    const onSaved = vi.fn();
    renderShipment(posts, onSaved);
    fireEvent.change(await screen.findByLabelText('Ship date'), { target: { value: '2026-09-10' } });
    fireEvent.change(screen.getByLabelText('Invoice number'), { target: { value: 'INV-9' } });
    fireEvent.change(screen.getByLabelText('Invoice date'), { target: { value: '2026-09-09' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Shipment' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({
      ship_date: '2026-09-10', invoice_number: 'INV-9', invoice_date: '2026-09-09',
    });
    expect(onSaved).toHaveBeenCalled();
  });
});
