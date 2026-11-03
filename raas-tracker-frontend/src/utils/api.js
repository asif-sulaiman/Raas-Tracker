/** Shared API error type (dependency-free so tests need no React). */

// A request that never answers used to leave the app spinning forever: the
// backend's own guards (10s pool acquire, 25s statement timeout) are what
// should end it, and 60s is where the platform already has.
export const DEFAULT_TIMEOUT_MS = 30000;

/**
 * Give fetch() a deadline without taking over the caller's signal.
 *
 * Whichever fires first — the deadline or the caller's abort — aborts the
 * request, and `didTimeOut()` says which one it was so a caller-supplied
 * abort keeps its existing `AbortError` meaning (debounced search, unmount)
 * instead of being reported as a timeout.
 *
 * `done()` must run when the request settles: it clears the timer and the
 * listener on the caller's signal, which is otherwise retained for the life
 * of that signal (a shared controller across many requests would accumulate).
 */
export function withTimeout(options = {}, {
  timeoutMs = DEFAULT_TIMEOUT_MS,
} = {}) {
  const ctrl = new AbortController();
  const caller = options.signal;
  let timedOut = false;

  const onCallerAbort = () => ctrl.abort(caller.reason);
  if (caller) {
    if (caller.aborted) ctrl.abort(caller.reason);
    else caller.addEventListener('abort', onCallerAbort, { once: true });
  }

  const timer = (!timeoutMs || caller?.aborted)
    ? null
    : setTimeout(() => {
      timedOut = true;
      ctrl.abort();
    }, timeoutMs);

  return {
    options: { ...options, signal: ctrl.signal },
    didTimeOut: () => timedOut,
    done() {
      if (timer) clearTimeout(timer);
      if (caller) caller.removeEventListener('abort', onCallerAbort);
    },
  };
}

export class ApiError extends Error {
  constructor(status, message, fields = null, retryAfter = null, code = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fields = fields;
    this.retryAfter = retryAfter;
    // Optional machine-readable discriminator for refusals that share a status
    // but need different recovery (e.g. both 404s on add-line: invoice_gone vs
    // pi_line_gone). Callers branch on this, never on the message text.
    this.code = code;
    this.missing = null;
  }
}
