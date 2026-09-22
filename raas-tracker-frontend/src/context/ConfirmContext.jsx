import { createContext, useContext, useState, useCallback, useRef } from 'react';
import Modal from '../components/modals/Modal';
import Button from '../components/ui/Button';

const ConfirmContext = createContext(null);

/**
 * Promise-based replacement for window.confirm.
 *   const { confirm } = useConfirm();
 *   if (!(await confirm({ title, message, danger: true }))) return;
 * Resolves false on cancel / backdrop / Escape (Modal's onClose).
 */
export function ConfirmProvider({ children }) {
  const [req, setReq] = useState(null);
  const resolver = useRef(null);

  const confirm = useCallback((opts) => {
    const o = typeof opts === 'string' ? { message: opts } : opts || {};
    return new Promise((resolve) => {
      resolver.current = resolve;
      setReq({
        title: 'Please confirm',
        confirmLabel: 'Confirm',
        danger: false,
        ...o,
      });
    });
  }, []);

  const settle = useCallback((value) => {
    resolver.current?.(value);
    resolver.current = null;
    setReq(null);
  }, []);

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      <Modal
        isOpen={!!req}
        onClose={() => settle(false)}
        title={req?.title}
        subtitle={req?.message}
        maxWidth="max-w-sm"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => settle(false)}>
              {req?.cancelLabel || 'Cancel'}
            </Button>
            <Button
              variant={req?.danger ? 'danger' : 'primary'}
              size="sm"
              onClick={() => settle(true)}
            >
              {req?.confirmLabel || 'Confirm'}
            </Button>
          </>
        }
      >
        {req?.detail && (
          <p className="text-xs text-slate-500 dark:text-slate-400">{req.detail}</p>
        )}
      </Modal>
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used inside ConfirmProvider');
  return ctx;
}
