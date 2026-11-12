import { useId, useRef } from 'react';
import { X } from 'lucide-react';
import { useDialogLayer } from '../../hooks/useDialogLayer';
import { ICON_HIT_AREA } from '../ui/Button';

export default function Modal({
  isOpen,
  onClose,
  title,
  subtitle,
  children,
  footer,
  maxWidth = 'max-w-lg'
}) {
  const titleId = useId();
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);

  useDialogLayer({
    isOpen,
    onClose,
    containerRef: dialogRef,
    initialFocusRef: closeButtonRef,
  });

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center pb-[env(safe-area-inset-bottom)] md:items-center md:p-4">
      {/* Backdrop */}
      <div
        aria-hidden="true"
        className="fixed inset-0 bg-slate-900/60 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      {/* Dialog: a bottom sheet below md, a centred card above it. */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : 'Dialog'}
        tabIndex={-1}
        className={`relative z-10 flex max-h-[85dvh] w-full flex-col ${maxWidth} rounded-t-2xl border border-slate-200 bg-white shadow-2xl transition-all md:max-h-[calc(100dvh-4rem)] md:rounded-2xl dark:border-slate-800 dark:bg-slate-900`}
      >
        {/* Header */}
        <div className="flex shrink-0 items-start justify-between gap-4 p-5 border-b border-slate-100 dark:border-slate-800">
          <div className="min-w-0">
            {title && (
              <h3
                id={titleId}
                className="text-base font-semibold text-slate-900 dark:text-white"
              >
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className={`shrink-0 rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-600 dark:hover:text-slate-200 cursor-pointer ${ICON_HIT_AREA}`}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>

        {/* Footer */}
        {footer && (
          <div className="flex shrink-0 items-center justify-end gap-2.5 px-5 py-4 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/30 md:rounded-b-2xl">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
