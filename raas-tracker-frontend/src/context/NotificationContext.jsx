import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { useAuth } from './AuthContext';
import { useChime } from '../utils/chime';
import { diffNewNotifications, severityMeta } from '../utils/notifications';

const NotificationContext = createContext(null);
const POLL_MS = 30 * 1000;
const NATIVE_KEY = 'raas-notif-native';
const MAX_TOASTS_PER_BATCH = 5;
const EMPTY = [];

export function NotificationProvider({ children }) {
  const { user, apiFetch } = useAuth();
  const chime = useChime();
  const [items, setItems] = useState(EMPTY);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [nativeEnabled, setNativeEnabled] = useState(() => {
    try {
      return window.localStorage.getItem(NATIVE_KEY) === '1';
    } catch {
      return false;
    }
  });
  const seenRef = useRef(null);
  const nativeRef = useRef(nativeEnabled);
  nativeRef.current = nativeEnabled;
  const userRef = useRef(user);
  userRef.current = user;

  // Surface new arrivals: stacked toasts + chime while the tab is visible;
  // browser-native notifications (opt-in) while it is hidden.
  const announce = useCallback((fresh) => {
    if (!fresh.length) return;
    const chronological = [...fresh].reverse();
    if (document.visibilityState === 'visible') {
      chronological.slice(0, MAX_TOASTS_PER_BATCH).forEach((n) => {
        const opts = {
          description: n.body || undefined,
          duration: n.severity === 'critical' ? 10000 : 6000,
        };
        const tone = severityMeta(n.severity).toast;
        if (tone === 'error') toast.error(n.title, opts);
        else if (tone === 'warning') toast.warning(n.title, opts);
        else toast.info(n.title, opts);
      });
      chime.play();
    } else if (
      nativeRef.current &&
      typeof window !== 'undefined' &&
      'Notification' in window &&
      Notification.permission === 'granted'
    ) {
      chronological.forEach((n) => {
        try {
          const note = new Notification(n.title, {
            body: n.body || 'RAAS Tracker notification',
            tag: `raas-${n.id}`,
          });
          note.onclick = () => {
            window.focus();
            note.close();
          };
        } catch {
          /* browser refused */
        }
      });
    }
  }, [chime]);

  const load = useCallback(async ({ initial = false } = {}) => {
    if (!userRef.current) return;
    try {
      const res = await apiFetch('/api/notifications');
      const data = await res.json();
      const next = Array.isArray(data.items) ? data.items : [];
      const prev = seenRef.current;
      seenRef.current = new Set(next.map((n) => n.id));
      setItems(next);
      setUnread(data.unread || 0);
      setError(null);
      // First load only seeds state — never toast a backlog.
      if (initial || prev === null) return;
      const fresh = diffNewNotifications(next, prev);
      if (fresh.length) announce(fresh);
    } catch (err) {
      if (err?.name === 'AbortError') return;
      setError(err?.message || 'Could not load notifications');
    } finally {
      setLoading(false);
    }
  }, [apiFetch, announce]);

  useEffect(() => {
    if (!user) return undefined;
    load({ initial: true });
    const timer = setInterval(() => load(), POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === 'visible') load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [user, load]);

  const postRead = useCallback(async (body) => {
    const res = await apiFetch('/api/notifications/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    setUnread(data.unread || 0);
    return data;
  }, [apiFetch]);

  const markRead = useCallback(async (ids) => {
    if (!ids?.length) return;
    await postRead({ ids });
    setItems((prev) => prev.map((n) => (ids.includes(n.id) ? { ...n, is_read: true } : n)));
  }, [postRead]);

  const markAllRead = useCallback(async () => {
    await postRead({});
    setItems((prev) => prev.map((n) => (n.is_read ? n : { ...n, is_read: true })));
  }, [postRead]);

  const enableNative = useCallback(async () => {
    if (typeof window === 'undefined' || !('Notification' in window)) return false;
    let permission = Notification.permission;
    if (permission === 'default') {
      permission = await Notification.requestPermission();
    }
    const ok = permission === 'granted';
    setNativeEnabled(ok);
    try {
      window.localStorage.setItem(NATIVE_KEY, ok ? '1' : '0');
    } catch {
      /* storage unavailable */
    }
    return ok;
  }, []);

  const disableNative = useCallback(() => {
    setNativeEnabled(false);
    try {
      window.localStorage.setItem(NATIVE_KEY, '0');
    } catch {
      /* storage unavailable */
    }
  }, []);

  const value = {
    items: user ? items : EMPTY,
    unread: user ? unread : 0,
    loading: !!user && loading,
    error: user ? error : null,
    refresh: load,
    markRead,
    markAllRead,
    soundMuted: chime.muted,
    setSoundMuted: chime.setMuted,
    nativeEnabled,
    enableNative,
    disableNative,
  };

  return (
    <NotificationContext.Provider value={value}>
      {children}
    </NotificationContext.Provider>
  );
}

export function useNotifications() {
  const ctx = useContext(NotificationContext);
  if (!ctx) throw new Error('useNotifications must be used inside NotificationProvider');
  return ctx;
}
