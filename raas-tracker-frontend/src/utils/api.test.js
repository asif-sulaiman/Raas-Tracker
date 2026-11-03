import { describe, it, expect, vi, afterEach } from 'vitest';
import { ApiError, withTimeout, DEFAULT_TIMEOUT_MS } from './api.js';

afterEach(() => {
  vi.useRealTimers();
});

describe('withTimeout', () => {
  it('replaces the signal and keeps every other option', () => {
    const req = withTimeout({ method: 'POST', headers: { A: 'b' }, body: '{}' });
    expect(req.options.method).toBe('POST');
    expect(req.options.headers).toEqual({ A: 'b' });
    expect(req.options.body).toBe('{}');
    expect(typeof req.options.signal.addEventListener).toBe('function');
    req.done();
  });

  it('aborts at the deadline and says it was the deadline', () => {
    vi.useFakeTimers();
    const req = withTimeout({}, { timeoutMs: 1000 });
    expect(req.options.signal.aborted).toBe(false);
    vi.advanceTimersByTime(1000);
    expect(req.options.signal.aborted).toBe(true);
    expect(req.didTimeOut()).toBe(true);
    req.done();
  });

  it('treats a caller abort as the caller’s, not a timeout', () => {
    vi.useFakeTimers();
    const caller = new AbortController();
    const req = withTimeout({ signal: caller.signal }, { timeoutMs: 5000 });
    const reason = new DOMException('Aborted', 'AbortError');
    caller.abort(reason);
    expect(req.options.signal.aborted).toBe(true);
    expect(req.didTimeOut()).toBe(false);
    req.done();
  });

  it('honours a caller signal that was already aborted', () => {
    const caller = new AbortController();
    caller.abort();
    const req = withTimeout({ signal: caller.signal });
    expect(req.options.signal.aborted).toBe(true);
    expect(req.didTimeOut()).toBe(false);
    req.done();
  });

  it('stops the deadline once the request is finished', () => {
    vi.useFakeTimers();
    const req = withTimeout({}, { timeoutMs: 1000 });
    req.done();
    vi.advanceTimersByTime(10000);
    expect(req.options.signal.aborted).toBe(false);
    expect(req.didTimeOut()).toBe(false);
  });

  it('removes its caller listener on done, so a shared signal does not accumulate', () => {
    const caller = new AbortController();
    const spy = vi.spyOn(caller.signal, 'removeEventListener');
    const req = withTimeout({ signal: caller.signal });
    req.done();
    expect(spy).toHaveBeenCalledWith('abort', expect.any(Function));
  });

  it('can be disabled for a request that must not be cut off', () => {
    vi.useFakeTimers();
    const req = withTimeout({}, { timeoutMs: 0 });
    vi.advanceTimersByTime(DEFAULT_TIMEOUT_MS * 10);
    expect(req.options.signal.aborted).toBe(false);
    req.done();
  });

  it('sits between the server’s own guard and the platform function limit', () => {
    // Above the backend's 25s statement_timeout and 10s pool-acquire timeout,
    // so the server's own 503/500 is what the user normally sees; below the
    // 60s maxDuration in vercel.json, so the browser never waits for a request
    // the platform has already killed.
    expect(DEFAULT_TIMEOUT_MS).toBeGreaterThan(25000);
    expect(DEFAULT_TIMEOUT_MS).toBeLessThan(60000);
  });
});

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

  it('F1 carries backend details and missing without dropping fields', () => {
    const details = [{ field: 'company_id', message: 'required' }];
    const err = new ApiError(400, 'Invalid payload', details, null);
    err.missing = ['company_id'];
    expect(err.fields).toEqual(details);
    expect(err.missing).toEqual(['company_id']);
    // Consumers stay on err.fields; details alias must map to fields.
    const backendBody = { error: 'Invalid payload', details, missing: ['company_id'] };
    const mappedFields = backendBody.fields || backendBody.details || backendBody.errors || null;
    expect(mappedFields).toEqual(details);
    expect(backendBody.missing).toEqual(['company_id']);
  });
});
