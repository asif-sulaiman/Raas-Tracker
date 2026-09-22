import { useState, useEffect } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';

export default function ProtectedRoute() {
  const { user, loading } = useAuth();
  const [setupNeeded, setSetupNeeded] = useState(null);
  const location = useLocation();

  useEffect(() => {
    if (!loading && !user) {
      fetch('/api/auth/status')
        .then((r) => r.json())
        .then((data) => setSetupNeeded(!!data.setup_needed))
        .catch(() => setSetupNeeded(false));
    }
  }, [loading, user]);

  if (loading || (!user && setupNeeded === null)) {
    return (
      <div className="flex items-center justify-center h-screen text-sm text-slate-500">
        Loading…
      </div>
    );
  }
  if (!user) {
    if (setupNeeded) return <Navigate to="/setup" replace />;
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
