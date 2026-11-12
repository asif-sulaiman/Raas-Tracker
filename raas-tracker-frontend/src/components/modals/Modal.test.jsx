// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import Modal from './Modal.jsx';
import { ICON_HIT_AREA } from '../ui/Button.jsx';

afterEach(() => {
  cleanup();
  document.body.style.overflow = '';
});

function Harness({ initialOpen = false }) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <>
      <button>Outside</button>
      <button onClick={() => setOpen(true)}>Open dialog</button>
      <Modal
        isOpen={open}
        onClose={() => setOpen(false)}
        title="Test dialog"
        subtitle="A subtitle"
        footer={<button>Save</button>}
      >
        <button>Body action</button>
      </Modal>
    </>
  );
}

function openFromTrigger() {
  const trigger = screen.getByRole('button', { name: 'Open dialog' });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}

function currentCloseButton() {
  const dialog = screen.getByRole('dialog', { name: 'Test dialog' });
  return within(dialog).getByRole('button', { name: 'Close' });
}

describe('dialog semantics', () => {
  it('exposes a labelled modal dialog with an accessible close control', () => {
    render(<Harness initialOpen />);
    const dialog = screen.getByRole('dialog', { name: 'Test dialog' });
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    const labelId = dialog.getAttribute('aria-labelledby');
    expect(labelId).toBeTruthy();
    expect(document.getElementById(labelId)?.textContent).toBe('Test dialog');
    expect(currentCloseButton()).toBeTruthy();
  });

  it('gives the close control the shared 44px hit area', () => {
    render(<Harness initialOpen />);
    expect(currentCloseButton().className).toContain(ICON_HIT_AREA);
  });

  it('reserves the safe-area inset inside the sheet, not on the overlay', () => {
    render(<Harness initialOpen />);
    const sheet = screen.getByRole('dialog', { name: 'Test dialog' });
    // Padding the overlay would lift the sheet off the bottom edge on a
    // notched phone; the inset belongs to the sheet's own bottom.
    expect(sheet.className).toContain('pb-[env(safe-area-inset-bottom)]');
    expect(sheet.className).toContain('md:pb-0');
    expect(sheet.parentElement.className).not.toContain('env(safe-area-inset-bottom)');
  });

  it('moves focus into the dialog when it opens', () => {
    render(<Harness />);
    openFromTrigger();
    expect(document.activeElement).toBe(currentCloseButton());
  });
});

describe('dismissal', () => {
  it('closes on Escape and returns focus to the trigger', () => {
    render(<Harness />);
    const trigger = openFromTrigger();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('closes from the close button and returns focus to the trigger', () => {
    render(<Harness />);
    const trigger = openFromTrigger();
    fireEvent.click(currentCloseButton());
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe('focus trap', () => {
  it('wraps Shift+Tab from the first control to the last', () => {
    render(<Harness initialOpen />);
    const dialog = screen.getByRole('dialog', { name: 'Test dialog' });
    const save = within(dialog).getByRole('button', { name: 'Save' });
    expect(document.activeElement).toBe(currentCloseButton());
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(save);
  });

  it('wraps Tab from the last control to the first', () => {
    render(<Harness initialOpen />);
    const dialog = screen.getByRole('dialog', { name: 'Test dialog' });
    const save = within(dialog).getByRole('button', { name: 'Save' });
    save.focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(currentCloseButton());
  });

  it('pulls focus back inside when Tab starts outside the dialog', () => {
    render(<Harness initialOpen />);
    screen.getByRole('button', { name: 'Outside' }).focus();
    fireEvent.keyDown(window, { key: 'Tab' });
    expect(document.activeElement).toBe(currentCloseButton());
  });
});

describe('stacked dialogs', () => {
  function StackHarness() {
    const [outerOpen, setOuterOpen] = useState(true);
    const [innerOpen, setInnerOpen] = useState(false);
    return (
      <>
        <Modal isOpen={outerOpen} onClose={() => setOuterOpen(false)} title="Outer dialog">
          <button onClick={() => setInnerOpen(true)}>Open inner</button>
        </Modal>
        <Modal isOpen={innerOpen} onClose={() => setInnerOpen(false)} title="Inner dialog">
          <button>Inner action</button>
        </Modal>
      </>
    );
  }

  it('lets only the topmost dialog handle Escape', () => {
    render(<StackHarness />);
    const openInner = screen.getByRole('button', { name: 'Open inner' });
    openInner.focus();
    fireEvent.click(openInner);
    expect(screen.getByRole('dialog', { name: 'Inner dialog' })).toBeTruthy();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Inner dialog' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Outer dialog' })).toBeTruthy();
    expect(document.activeElement).toBe(openInner);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Outer dialog' })).toBeNull();
  });

  it('keeps the body locked until the last dialog closes', () => {
    render(<StackHarness />);
    expect(document.body.style.overflow).toBe('hidden');

    const openInner = screen.getByRole('button', { name: 'Open inner' });
    openInner.focus();
    fireEvent.click(openInner);
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.body.style.overflow).toBe('');
  });
});
