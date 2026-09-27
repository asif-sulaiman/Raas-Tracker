import { useState } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { LogIn, AlertTriangle, Clock, KeyRound } from 'lucide-react';
import Button from '../components/ui/Button';
import Modal from '../components/modals/Modal';
import { useAuth } from '../context/AuthContext';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [forgotOpen, setForgotOpen] = useState(false);
  const [forgotName, setForgotName] = useState('');
  const [forgotBusy, setForgotBusy] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);
  const [forgotError, setForgotError] = useState(null);
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const expired = location.state?.expired;
  const from = location.state?.from || '/';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError('Enter your username and password');
      return;
    }
    setBusy(true);
    try {
      await login(username.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      if (err.status === 429) {
        setError(`Too many failed attempts. Try again in ${err.retryAfter ? Math.ceil(err.retryAfter / 60) + ' min' : 'a while'}.`);
      } else {
        setError(err.message || 'Login failed');
      }
      setBusy(false);
    }
  };

  const openForgot = () => {
    setForgotName(username.trim());
    setForgotError(null);
    setForgotSent(false);
    setForgotBusy(false);
    setForgotOpen(true);
  };

  const closeForgot = () => {
    setForgotOpen(false);
    setForgotError(null);
    setForgotSent(false);
  };

  const handleForgot = async (e) => {
    e.preventDefault();
    setForgotError(null);
    if (!forgotName.trim()) {
      setForgotError('Enter your username');
      return;
    }
    setForgotBusy(true);
    try {
      // Any HTTP response means "accepted" — the server never reveals
      // whether the account exists, and neither do we.
      await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: forgotName.trim() }),
      });
      setForgotSent(true);
    } catch {
      setForgotError('Network error — is the server running?');
    } finally {
      setForgotBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-100 dark:bg-slate-950 p-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 shadow-xl">
        <h2 className="text-xl font-bold text-slate-900 dark:text-white">RAAS Tracker Login</h2>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 mb-4">
          Sign in to access the stock tracker
        </p>

        {expired && (
          <p className="flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2 mb-3">
            <Clock className="h-3.5 w-3.5" /> Session expired — please sign in again
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Username</label>
            <input value={username} onChange={(e) => setUsername(e.target.value)} className={inputCls} autoComplete="username" autoFocus />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Password</label>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputCls} autoComplete="current-password" />
          </div>
          {error && (
            <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
              <AlertTriangle className="h-3.5 w-3.5" /> {error}
            </p>
          )}
          <Button variant="primary" size="md" icon={LogIn} loading={busy} className="w-full justify-center" type="submit">
            Sign In
          </Button>
        </form>

        <p className="mt-3 text-center">
          <button type="button" onClick={openForgot} className="text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline cursor-pointer">
            Forgot password?
          </button>
        </p>

        <p className="text-xs text-slate-500 dark:text-slate-400 mt-2 text-center">
          First run? <Link to="/setup" className="text-blue-600 dark:text-blue-400 font-semibold hover:underline">Create admin account</Link>
        </p>
      </div>

      <Modal
        isOpen={forgotOpen}
        onClose={closeForgot}
        title="Forgot password?"
        subtitle="Enter your username to request a reset"
        maxWidth="max-w-sm"
      >
        {forgotSent ? (
          <div className="space-y-3">
            <p className="text-sm text-slate-600 dark:text-slate-300">
              If an account with that username exists, reset instructions are on their way.
              Contact your admin if you need help.
            </p>
            <Button variant="secondary" size="sm" onClick={closeForgot} className="w-full justify-center">
              Back to login
            </Button>
          </div>
        ) : (
          <form onSubmit={handleForgot} className="space-y-3">
            <div>
              <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Username</label>
              <input
                value={forgotName}
                onChange={(e) => setForgotName(e.target.value)}
                className={inputCls}
                autoComplete="username"
                autoFocus
                placeholder="e.g. storekeeper"
              />
            </div>
            {forgotError && (
              <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
                <AlertTriangle className="h-3.5 w-3.5" /> {forgotError}
              </p>
            )}
            <Button variant="primary" size="sm" icon={KeyRound} loading={forgotBusy} className="w-full justify-center" type="submit">
              Send reset request
            </Button>
          </form>
        )}
      </Modal>
    </div>
  );
}
