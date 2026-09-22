// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { ConfirmProvider, useConfirm } from './ConfirmContext.jsx';

afterEach(() => cleanup());

function Probe({ onResult }) {
  const { confirm } = useConfirm();
  return (
    <button
      onClick={async () => {
        const v = await confirm({ title: 'Delete it?', message: 'Are you sure?' });
        onResult(v);
      }}
    >
      ask
    </button>
  );
}

function setup() {
  let result = 'pending';
  render(
    <ConfirmProvider>
      <Probe onResult={(v) => { result = v; }} />
    </ConfirmProvider>
  );
  fireEvent.click(screen.getByText('ask'));
  expect(screen.getByText('Delete it?')).toBeTruthy();
  return () => result;
}

describe('ConfirmProvider', () => {
  it('resolves true when the confirm button is clicked', async () => {
    const getResult = setup();
    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(getResult()).toBe(true));
  });

  it('resolves false on Cancel', async () => {
    const getResult = setup();
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(getResult()).toBe(false));
  });

  it('throws outside the provider', async () => {
    const { useConfirm: uc } = await import('./ConfirmContext.jsx');
    expect(() => uc()).toThrow();
  });
});

describe('sonner Toaster', () => {
  it('renders an error toast', async () => {
    const { Toaster, toast } = await import('sonner');
    render(<Toaster />);
    toast.error('Hello toast');
    await waitFor(() => expect(screen.getByText('Hello toast')).toBeTruthy());
  });
});
