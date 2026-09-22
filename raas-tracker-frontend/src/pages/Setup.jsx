import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ShieldCheck, AlertTriangle } from 'lucide-react';
import Button from '../components/ui/Button';
import { useAuth } from '../context/AuthContext';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function Setup() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [setupToken, setSetupToken] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [alreadyDone, setAlreadyDone] = useState(false);
  const { refresh } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    fetch('/api/auth/status')
      .then((r) => r.json())
      .then((data) => {
        if (!data.setup_needed) setAlreadyDone(true);
      })
      .catch(() => {});
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/auth/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Setup-Token': setupToken.trim() },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 403 || res.status === 404) {
        navigate('/login', { replace: true });
        return;
      }
      if (!res.ok) {
        setError(data.error || 'Setup failed');
        setBusy(false);
        return;
      }
      await refresh();
      navigate('/', { replace: true });
    } catch {
      setError('Network error');
      setBusy(false);
    }
  };

  if (alreadyDone) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-slate-100 dark:bg-slate-950 p-4">
        <div className="text-center space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">Setup already completed.</p>
          <Link to="/login" className="text-sm font-semibold text-blue-600 dark:text-blue-400 hover:underline">
            Go to login
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-100 dark:bg-slate-950 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-xl">
        <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-emerald-600" /> Initial Setup
        </h2>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 mb-4">
          Create the first administrator account. This page works only once.
          Paste the setup token printed in the server terminal.
        </p>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Setup token (from server console)</label>
            <input value={setupToken} onChange={(e) => setSetupToken(e.target.value)} className={inputCls} placeholder="Paste one-time token" />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Admin username</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} className={inputCls} autoComplete="username" autoFocus placeholder="e.g. admin" />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Password (min 8 characters)</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputCls} autoComplete="new-password" />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Confirm password</label>
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} className={inputCls} autoComplete="new-password" />
          </div>
          {error && (
            <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
              <AlertTriangle className="h-3.5 w-3.5" /> {error}
            </p>
          )}
          <Button variant="success" size="md" loading={busy} className="w-full justify-center" type="submit">
            Create Admin & Sign In
          </Button>
        </form>
      </div>
    </div>
  );
}
