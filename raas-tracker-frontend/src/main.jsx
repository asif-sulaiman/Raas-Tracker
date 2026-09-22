import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ui/ErrorBoundary.jsx'

function GlobalFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-950 p-6">
      <div className="max-w-md w-full rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center shadow-xl" role="alert">
        <p className="text-lg font-bold text-slate-900 dark:text-white">RAAS Tracker ran into a problem</p>
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2">
          The application crashed. Your saved data is safe — reloading usually fixes this.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="mt-6 px-4 py-2 text-sm font-semibold rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors cursor-pointer"
        >
          Reload application
        </button>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary fallback={<GlobalFallback />}>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
