import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { KeyRound, AlertTriangle, ArrowLeft } from 'lucide-react';
import Button from '../components/ui/Button';
import { useAuth } from '../context/AuthContext';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function ChangePassword() {
  // NOTE: plain fetch, not apiFetch — a 401 here means "wrong current
  // password" and must not trigger the session-death redirect to login.
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const forced = Boolean(user?.must_change_password) || location.state?.forced === true;

  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError('New password must be at least 8 characters');
      return;
    }
    if (next !== confirm) {
      setError('New passwords do not match');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/auth/password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(
          res.status === 401
            ? 'Current password is incorrect'
            : (data.error || 'Could not change password')
        );
        setBusy(false);
        return;
      }
      // Clear the flag in context BEFORE navigating, otherwise the
      // password gate still sees must_change_password and bounces back.
      if (data && data.user) setUser(data.user);
      navigate('/', { replace: true });
    } catch {
      setError('Network error — is the server running?');
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-100 dark:bg-slate-950 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-xl">
        <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-blue-600" /> Change password
        </h2>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 mb-4">
          {forced
            ? 'Your password must be changed before you can continue.'
            : 'Set a new password for your account.'}
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Current password</label>
            <input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              className={inputCls}
              autoComplete="current-password"
              autoFocus
            />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">New password (min 8 characters)</label>
            <input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              className={inputCls}
              autoComplete="new-password"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Confirm new password</label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className={inputCls}
              autoComplete="new-password"
            />
          </div>
          {error && (
            <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
              <AlertTriangle className="h-3.5 w-3.5" /> {error}
            </p>
          )}
          <Button variant="primary" size="md" icon={KeyRound} loading={busy} className="w-full justify-center" type="submit">
            Set new password
          </Button>
        </form>

        {!forced && (
          <p className="mt-4 text-center">
            <Link to="/" className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline">
              <ArrowLeft className="h-3.5 w-3.5" /> Back to dashboard
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
