import { useCallback, useEffect, useRef, useState } from 'react';

// Module-level cache: survives route unmounts, so returning to a page shows
// its last data instantly instead of a skeleton. Keyed by request path.
const cache = new Map();

export function clearCachedFetch(key) {
  if (key === undefined) cache.clear();
  else cache.delete(key);
}

/**
 * Fetch JSON once per key and reuse it while `staleTime` has not elapsed.
 *
 * With a cached entry the data renders immediately and a stale entry refreshes
 * in the background; `loading` only tracks a cold cache, so callers can keep
 * showing content while a refresh runs. A failed background refresh leaves the
 * cached data in place (the server response is short-lived anyway).
 */
export default function useCachedFetch(key, fetcher, { staleTime = 60_000 } = {}) {
  const entry = cache.get(key);
  const [data, setData] = useState(entry ? entry.data : null);
  const [loading, setLoading] = useState(!entry);
  const [error, setError] = useState(null);
  const fetcherRef = useRef(fetcher);
  const mountedRef = useRef(false);
  useEffect(() => { fetcherRef.current = fetcher; }, [fetcher]);

  const load = useCallback(async ({ force = false } = {}) => {
    const current = cache.get(key);
    if (!force && current && Date.now() - current.ts < staleTime) {
      setData(current.data);
      setLoading(false);
      return current.data;
    }
    if (!cache.has(key)) setLoading(true);
    try {
      const result = await fetcherRef.current();
      cache.set(key, { data: result, ts: Date.now() });
      if (mountedRef.current) {
        setData(result);
        setError(null);
      }
      return result;
    } catch (err) {
      // Only surface the failure when there is nothing to fall back on;
      // a refresh failure must not blank an already-rendered page.
      if (mountedRef.current && !cache.has(key)) setError(err);
      return null;
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [key, staleTime]);

  useEffect(() => {
    mountedRef.current = true;
    // Same loader pattern the Auth provider uses: the synchronous loading flag
    // is intentional, and the fetch itself is external.
    // oxlint-disable-next-line react/set-state-in-effect
    load();
    return () => { mountedRef.current = false; };
  }, [load]);

  const refresh = useCallback(() => load({ force: true }), [load]);

  return { data, loading, error, refresh };
}
