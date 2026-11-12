import { useState, useEffect } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { withTimeout } from '../../utils/api';

export default function ProtectedRoute() {
  const { user, loading } = useAuth();
  const [setupNeeded, setSetupNeeded] = useState(null);
  const location = useLocation();

  useEffect(() => {
    if (!loading && !user) {
      // Same deadline as every other request: a status check that never
      // answers would leave this spinner on screen forever, which is the
      // symptom the session check fix exists to remove.
      const req = withTimeout({});
      fetch('/api/auth/status', req.options)
        .then((r) => r.json())
        .then((data) => {
          // Defensive check: ensure data is an object and has setup_needed property
          if (data && typeof data === 'object' && 'setup_needed' in data) {
            setSetupNeeded(!!data.setup_needed);
          } else {
            setSetupNeeded(false);
          }
        })
        .catch(() => setSetupNeeded(false))
        .finally(() => req.done());
    }
  }, [loading, user]);

  if (loading || (!user && setupNeeded === null)) {
    return (
      <div className="flex items-center justify-center h-dvh text-sm text-slate-500">
        Loading…
      </div>
    );
  }
  if (!user) {
    if (setupNeeded) return <Navigate to="/setup" replace />;
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  // Forced password change: the backend 403s everything else until the
  // flag clears, so hold the user here until they set a new password.
  if (user.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace state={{ forced: true }} />;
  }
  return <Outlet />;
}
