import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { KeyRound, AlertTriangle, CheckCircle2, ArrowLeft } from 'lucide-react';
import Button from '../components/ui/Button';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function Shell({ children }) {
  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-100 dark:bg-slate-950 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-xl">
        {children}
      </div>
    </div>
  );
}

export default function ResetPassword() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const navigate = useNavigate();

  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!done) return undefined;
    const t = setTimeout(() => navigate('/login', { replace: true }), 3000);
    return () => clearTimeout(t);
  }, [done, navigate]);

  if (!token) {
    return (
      <Shell>
        <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
          <KeyRound className="h-5 w-5 text-blue-600" /> Reset password
        </h2>
        <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400 mt-3">
          <AlertTriangle className="h-3.5 w-3.5" /> This reset link is missing its token.
        </p>
        <p className="mt-4 text-center">
          <Link to="/login" className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline">
            <ArrowLeft className="h-3.5 w-3.5" /> Back to login
          </Link>
        </p>
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell>
        <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-emerald-600" /> Password reset
        </h2>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-2">
          Your password is set. Redirecting to login…
        </p>
        <p className="mt-4 text-center">
          <Link to="/login" className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline">
            Go to login now
          </Link>
        </p>
      </Shell>
    );
  }

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError('New password must be at least 8 characters');
      return;
    }
    if (next !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(
          res.status === 400
            ? 'This reset link is invalid or has expired. Ask your admin for a new one.'
            : (data.error || 'Could not reset password')
        );
        setBusy(false);
        return;
      }
      setDone(true);
    } catch {
      setError('Network error — is the server running?');
      setBusy(false);
    }
  };

  return (
    <Shell>
      <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
        <KeyRound className="h-5 w-5 text-blue-600" /> Reset password
      </h2>
      <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 mb-4">
        Choose a new password for your account.
      </p>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-300">New password (min 8 characters)</label>
          <input
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            className={inputCls}
            autoComplete="new-password"
            autoFocus
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
      <p className="mt-4 text-center">
        <Link to="/login" className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline">
          <ArrowLeft className="h-3.5 w-3.5" /> Back to login
        </Link>
      </p>
    </Shell>
  );
}
