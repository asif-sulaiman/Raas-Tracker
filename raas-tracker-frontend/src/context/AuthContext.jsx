import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ApiError } from '../utils/api';

const AuthContext = createContext(null);
const LOGOUT_EVENT_KEY = 'raas-logout';
const POLL_INTERVAL_MS = 60 * 1000;

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const userRef = useRef(null);
  userRef.current = user;

  const refresh = useCallback(async ({ silent = true } = {}) => {
    try {
      const res = await fetch('/api/auth/me');
      if (res.ok) {
        const data = await res.json();
        // API-key identities carry no user session — treat as logged out for UI.
        if (data && data.username) {
          setUser(data);
        } else {
          setUser(null);
        }
      } else {
        setUser(null);
      }
    } catch {
      if (!silent) setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const redirectToLogin = useCallback((expired) => {
    setUser(null);
    try {
      localStorage.setItem(LOGOUT_EVENT_KEY, String(Date.now()));
    } catch { /* ignore */ }
    navigate('/login', expired ? { state: { expired: true } } : undefined);
  }, [navigate]);

  const login = useCallback(async (username, password) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || 'Login failed');
      err.status = res.status;
      err.retryAfter = res.headers.get('Retry-After');
      throw err;
    }
    setUser(data.user);
    return data.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch { /* ignore */ }
    redirectToLogin(false);
  }, [redirectToLogin]);

  // Central fetch (fail-fast): throws ApiError on any non-2xx.
  // Any 401 while logged in means the session died -> bounce to login.
  // NOTE: do NOT use this for login/status/setup (their 401s are meaningful).
  // Legacy tolerance: endpoints that still return 200 + {success:false}
  // come back as a normal response — callers must check data.success there.
  const apiFetch = useCallback(async (path, options) => {
    let res;
    try {
      res = await fetch(path, options);
    } catch (err) {
      // Aborted requests (debounced search, unmount) propagate unwrapped
      // so callers can silently ignore them.
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError(0, 'Network error — is the server running?');
    }
    if (res.status === 401 && userRef.current) {
      redirectToLogin(true);
    }
    if (!res.ok) {
      let data = {};
      try { data = await res.json(); } catch { /* non-JSON error body */ }
      throw new ApiError(
        res.status,
        data.error || data.message || `Request failed (HTTP ${res.status})`,
        data.fields || data.errors || null,
        res.headers.get('Retry-After')
      );
    }
    return res;
  }, [redirectToLogin]);

  // Initial check.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Active polling while idle (revocation/expiry detection).
  useEffect(() => {
    const t = setInterval(() => {
      if (userRef.current) refresh();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // Closed-laptop case: re-check the instant the tab becomes visible.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && userRef.current) refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refresh]);

  // Multi-tab logout sync.
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === LOGOUT_EVENT_KEY) {
        setUser(null);
        navigate('/login');
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [navigate]);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, apiFetch }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
