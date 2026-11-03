import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'sonner';
import Layout from './components/layout/Layout';
import ProtectedRoute from './components/auth/ProtectedRoute';
import { AuthProvider } from './context/AuthContext';
import { NotificationProvider } from './context/NotificationContext';
import { ConfirmProvider } from './context/ConfirmContext';
import ErrorBoundary from './components/ui/ErrorBoundary';

// Route-level splitting keeps Recharts and the heavier editors out of the
// first payload; each page (and its vendors) downloads on first visit.
const Home = lazy(() => import('./pages/Home'));
const Upload = lazy(() => import('./pages/Upload'));
const Chemicals = lazy(() => import('./pages/Chemicals'));
const Recipes = lazy(() => import('./pages/Recipes'));
const Reports = lazy(() => import('./pages/Reports'));
const AuditLogs = lazy(() => import('./pages/AuditLogs'));
const Sales = lazy(() => import('./pages/Sales'));
const Users = lazy(() => import('./pages/Users'));
const Companies = lazy(() => import('./pages/Companies'));
const Login = lazy(() => import('./pages/Login'));
const Setup = lazy(() => import('./pages/Setup'));
const ResetPassword = lazy(() => import('./pages/ResetPassword'));
const ChangePassword = lazy(() => import('./pages/ChangePassword'));

function RouteFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950" role="status" aria-label="Loading page">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-700 dark:border-t-blue-400" />
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Toaster position="bottom-right" richColors closeButton />
      <AuthProvider>
        <NotificationProvider>
          <ConfirmProvider>
          <ErrorBoundary>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/login" element={<Login />} />
                <Route path="/setup" element={<Setup />} />
                <Route path="/reset" element={<ResetPassword />} />
                <Route element={<ProtectedRoute />}>
                  <Route path="/change-password" element={<ErrorBoundary><ChangePassword /></ErrorBoundary>} />
                  <Route path="/" element={<Layout />}>
                    <Route index element={<ErrorBoundary><Home /></ErrorBoundary>} />
                    <Route path="upload" element={<ErrorBoundary><Upload /></ErrorBoundary>} />
                    <Route path="chemicals" element={<ErrorBoundary><Chemicals /></ErrorBoundary>} />
                    <Route path="recipes" element={<ErrorBoundary><Recipes /></ErrorBoundary>} />
                    <Route path="reports" element={<ErrorBoundary><Reports /></ErrorBoundary>} />
                    <Route path="audit-logs" element={<ErrorBoundary><AuditLogs /></ErrorBoundary>} />
                    <Route path="sales" element={<ErrorBoundary><Sales /></ErrorBoundary>} />
                    <Route path="users" element={<ErrorBoundary><Users /></ErrorBoundary>} />
                    <Route path="companies" element={<ErrorBoundary><Companies /></ErrorBoundary>} />
                  </Route>
                </Route>
              </Routes>
            </Suspense>
          </ErrorBoundary>
          </ConfirmProvider>
        </NotificationProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
