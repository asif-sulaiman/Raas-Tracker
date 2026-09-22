import { describe, it, expect } from 'vitest';
import { ApiError } from './api.js';

describe('ApiError', () => {
  it('carries status, message, fields, and retryAfter', () => {
    const fields = [{ field: 'items.1.unit_price', message: 'greater than or equal to 0' }];
    const err = new ApiError(400, 'Invalid payload', fields, '60');
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe('ApiError');
    expect(err.status).toBe(400);
    expect(err.message).toBe('Invalid payload');
    expect(err.fields).toEqual(fields);
    expect(err.retryAfter).toBe('60');
  });

  it('defaults fields and retryAfter to null', () => {
    const err = new ApiError(401, 'authentication required');
    expect(err.fields).toBeNull();
    expect(err.retryAfter).toBeNull();
  });

  it('joins field details the way the sale modal displays them', () => {
    const err = new ApiError(400, 'Invalid payload', [
      { field: 'header.pi_number', message: 'at least 1 character' },
      { field: 'items', message: 'at least 1 item' },
    ]);
    const detail = err.fields.map((d) => `${d.field}: ${d.message}`).join('; ');
    expect(detail).toBe('header.pi_number: at least 1 character; items: at least 1 item');
  });

  it('represents network failures with status 0', () => {
    const err = new ApiError(0, 'Network error');
    expect(err.status).toBe(0);
    expect(err.message).toMatch(/network/i);
  });
});
