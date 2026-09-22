/** Shared API error type (dependency-free so tests need no React). */

export class ApiError extends Error {
  constructor(status, message, fields = null, retryAfter = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fields = fields;
    this.retryAfter = retryAfter;
  }
}
