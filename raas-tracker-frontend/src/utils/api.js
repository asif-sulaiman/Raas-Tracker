/** Shared API error type (dependency-free so tests need no React). */

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
