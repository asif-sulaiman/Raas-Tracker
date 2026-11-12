import { useEffect, useId, useRef } from 'react';

// One module-level stack for every dialog-like layer (Modal, mobile drawer).
// Equal z-index means DOM order decides what paints on top, and that is also
// the order in which these effects run, so the last entry is always the layer
// the user is looking at. Without the stack every open dialog answered Escape
// at once, so confirming a delete also closed the detail modal underneath.
const layers = [];
let bodyOverflowBeforeLock = null;

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableIn(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
}

function trapTab(event, container) {
  const items = focusableIn(container);
  if (items.length === 0) {
    event.preventDefault();
    container.focus();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  // Focus can be outside the layer (programmatic focus, a covered dialog, the
  // browser resetting focus after a removal). Pull it back in on the next Tab.
  if (event.shiftKey) {
    if (active === first || !container.contains(active)) {
      event.preventDefault();
      last.focus();
    }
  } else if (active === last || !container.contains(active)) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Shared behaviour for an open dialog layer.
 *
 * - Escape closes only the topmost layer.
 * - Tab/Shift+Tab cycle inside `containerRef`.
 * - Focus returns to whatever was focused before the layer opened.
 * - Body scroll locks on the first layer and unlocks when the last one closes.
 *
 * `onClose` is read through a ref so an inline closure (the common call site)
 * does not re-run the effect and bounce focus on every parent render.
 */
export function useDialogLayer({ isOpen, onClose, containerRef, initialFocusRef }) {
  const layerId = useId();
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const container = containerRef.current;
    const returnFocus = document.activeElement;

    if (layers.length === 0) {
      bodyOverflowBeforeLock = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    }
    layers.push(layerId);

    const initial = initialFocusRef?.current || focusableIn(container)[0] || container;
    initial?.focus?.();

    const handleKeyDown = (event) => {
      if (layers[layers.length - 1] !== layerId) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key === 'Tab') trapTab(event, container);
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      const index = layers.indexOf(layerId);
      if (index !== -1) layers.splice(index, 1);
      if (layers.length === 0) {
        document.body.style.overflow = bodyOverflowBeforeLock ?? '';
        bodyOverflowBeforeLock = null;
      }
      if (returnFocus?.isConnected) returnFocus.focus?.();
    };
  }, [isOpen, layerId, containerRef, initialFocusRef]);
}
