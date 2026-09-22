import React from 'react';

/**
 * Catches render-time crashes below it and shows a fallback instead of
 * white-screening. Two usages:
 * - per-route: <ErrorBoundary><Page /></ErrorBoundary> (inline panel + retry)
 * - global: <ErrorBoundary fallback={<GlobalFallback />}> in main.jsx
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('UI crash intercepted:', error, info?.componentStack);
    this.props.onError?.(error, info);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    if (this.props.fallback) return this.props.fallback;
    return (
      <div
        className="rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-8 text-center"
        role="alert"
      >
        <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">
          Something went wrong rendering this page
        </p>
        <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-1">
          {this.state.error?.message || 'Unexpected error'} — your data is safe.
        </p>
        <div className="mt-4 flex items-center justify-center gap-2">
          <button
            onClick={this.handleRetry}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-600 hover:bg-rose-700 text-white transition-colors cursor-pointer"
          >
            Try again
          </button>
          <button
            onClick={() => window.location.reload()}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-200 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/60 transition-colors cursor-pointer"
          >
            Reload app
          </button>
        </div>
      </div>
    );
  }
}
