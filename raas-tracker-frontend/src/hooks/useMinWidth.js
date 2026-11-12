import { useCallback, useSyncExternalStore } from 'react';

// Reads the same width the CSS breakpoints do when the browser supports it.
// jsdom has no matchMedia, so tests fall back to its innerWidth (1024 by
// default) and can opt into the narrow branch by setting innerWidth first.
function matches(minWidth) {
  if (typeof window === 'undefined') return true;
  if (typeof window.matchMedia === 'function') {
    return window.matchMedia(`(min-width: ${minWidth}px)`).matches;
  }
  return window.innerWidth >= minWidth;
}

/**
 * Tracks whether the viewport is at least `minWidth` CSS pixels wide.
 *
 * Rotation, browser zoom and window resizing all fire `resize`, so no
 * separate media-query listener is needed. The server snapshot is the wide
 * branch: nothing server-renders this SPA, and a table is the safe default if
 * that ever changes.
 */
export function useMinWidth(minWidth) {
  const subscribe = useCallback((onChange) => {
    window.addEventListener('resize', onChange);
    return () => window.removeEventListener('resize', onChange);
  }, []);

  const getSnapshot = useCallback(() => matches(minWidth), [minWidth]);

  return useSyncExternalStore(subscribe, getSnapshot, () => true);
}
