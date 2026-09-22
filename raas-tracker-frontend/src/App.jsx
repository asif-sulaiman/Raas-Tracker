import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Toaster } from 'sonner';
import Layout from './components/layout/Layout';
import ProtectedRoute from './components/auth/ProtectedRoute';
import { AuthProvider } from './context/AuthContext';
import { NotificationProvider } from './context/NotificationContext';
import { ConfirmProvider } from './context/ConfirmContext';
import ErrorBoundary from './components/ui/ErrorBoundary';
import Home from './pages/Home';
import Upload from './pages/Upload';
import Chemicals from './pages/Chemicals';
import Recipes from './pages/Recipes';
import Reports from './pages/Reports';
import AuditLogs from './pages/AuditLogs';
import Sales from './pages/Sales';
import Users from './pages/Users';
import Login from './pages/Login';
import Setup from './pages/Setup';

function App() {
  return (
    <BrowserRouter>
      <Toaster position="bottom-right" richColors closeButton />
      <AuthProvider>
        <NotificationProvider>
          <ConfirmProvider>
          <ErrorBoundary>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/setup" element={<Setup />} />
              <Route element={<ProtectedRoute />}>
                <Route path="/" element={<Layout />}>
                  <Route index element={<ErrorBoundary><Home /></ErrorBoundary>} />
                  <Route path="upload" element={<ErrorBoundary><Upload /></ErrorBoundary>} />
                  <Route path="chemicals" element={<ErrorBoundary><Chemicals /></ErrorBoundary>} />
                  <Route path="recipes" element={<ErrorBoundary><Recipes /></ErrorBoundary>} />
                  <Route path="reports" element={<ErrorBoundary><Reports /></ErrorBoundary>} />
                  <Route path="audit-logs" element={<ErrorBoundary><AuditLogs /></ErrorBoundary>} />
                  <Route path="sales" element={<ErrorBoundary><Sales /></ErrorBoundary>} />
                  <Route path="users" element={<ErrorBoundary><Users /></ErrorBoundary>} />
                </Route>
              </Route>
            </Routes>
          </ErrorBoundary>
          </ConfirmProvider>
        </NotificationProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
