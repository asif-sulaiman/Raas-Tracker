import { useState, useEffect } from 'react';
import {
  ClipboardList,
  CheckCircle2,
  XCircle,
  UploadCloud,
  ShieldCheck,
  AlertTriangle,
  Clock,
  Filter,
  Download,
  User
} from 'lucide-react';
import clsx from 'clsx';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import { formatDateTime } from '../utils/format';
import { useAuth } from '../context/AuthContext';

const ACTION_CONFIG = {
  APPROVE_ROW: { icon: CheckCircle2, color: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-100 dark:bg-emerald-950/60', label: 'Row Approved', variant: 'success' },
  REJECT_ROW: { icon: XCircle, color: 'text-rose-600 dark:text-rose-400', bg: 'bg-rose-100 dark:bg-rose-950/60', label: 'Row Rejected', variant: 'error' },
  APPROVE_UPLOAD: { icon: ShieldCheck, color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-100 dark:bg-blue-950/60', label: 'Upload Approved', variant: 'info' },
  ADJUST_STOCK: { icon: AlertTriangle, color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-100 dark:bg-amber-950/60', label: 'Stock Adjusted', variant: 'warning' },
  UPLOAD_CREATE: { icon: UploadCloud, color: 'text-indigo-600 dark:text-indigo-400', bg: 'bg-indigo-100 dark:bg-indigo-950/60', label: 'File Uploaded', variant: 'new' },
  LOCK_PERIOD: { icon: ShieldCheck, color: 'text-purple-600 dark:text-purple-400', bg: 'bg-purple-100 dark:bg-purple-950/60', label: 'Period Locked', variant: 'default' },
  TEST_ACTION: { icon: Clock, color: 'text-slate-500', bg: 'bg-slate-100 dark:bg-slate-800', label: 'Test', variant: 'default' }
};

export default function AuditLogs() {
  const { apiFetch } = useAuth();
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterAction, setFilterAction] = useState('all');
  const [filterUser, setFilterUser] = useState('all');

  useEffect(() => {
    (async () => {
      try {
        const res = await apiFetch('/api/audit-logs?limit=100');
        const data = await res.json();
        if (Array.isArray(data)) setLogs(data);
      } catch {
        // Table stays empty on failure.
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const filteredLogs = logs.filter(log => {
    if (filterAction !== 'all' && log.action !== filterAction) return false;
    if (filterUser !== 'all' && log.user_id !== filterUser) return false;
    return true;
  });

  const uniqueActions = [...new Set(logs.map(l => l.action))];
  const uniqueUsers = [...new Set(logs.map(l => l.user_id))];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">Audit Logs</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Track all system changes and user actions</p>
        </div>
        <Button variant="secondary" size="sm" icon={Download}>Export Log</Button>
      </div>

      {/* Filters */}
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-4 shadow-xs">
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <Filter className="h-3.5 w-3.5" /> Filter:
          </span>
          <select
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            className="px-2.5 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20"
          >
            <option value="all">All Actions</option>
            {uniqueActions.map(a => (
              <option key={a} value={a}>{ACTION_CONFIG[a]?.label || a}</option>
            ))}
          </select>
          <select
            value={filterUser}
            onChange={(e) => setFilterUser(e.target.value)}
            className="px-2.5 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20"
          >
            <option value="all">All Users</option>
            {uniqueUsers.map(u => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>
          <span className="text-xs text-slate-400 dark:text-slate-500 ml-auto">
            Showing {filteredLogs.length} of {logs.length} entries
          </span>
        </div>
      </div>

      {/* Timeline */}
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
        {loading ? (
          <div className="p-8 text-center">
            <div className="inline-flex items-center gap-2 text-slate-500 dark:text-slate-400">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />
              Loading audit logs...
            </div>
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="p-8 text-center text-slate-500 dark:text-slate-400 text-sm">
            No audit logs found
          </div>
        ) : (
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {filteredLogs.map((log) => {
            const config = ACTION_CONFIG[log.action] || { icon: Clock, color: 'text-slate-500', bg: 'bg-slate-100', label: log.action, variant: 'default' };
            const Icon = config.icon;

            return (
              <div key={log.id} className="flex items-start gap-4 p-4 hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                <div className={clsx('mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full', config.bg)}>
                  <Icon className={clsx('h-4 w-4', config.color)} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant={config.variant} size="xs">{config.label}</Badge>
                    <span className="text-[10px] text-slate-400 dark:text-slate-500">
                      {log.entity_type} #{log.entity_id}
                    </span>
                  </div>
                  {log.new_value && (
                    <p className="text-xs text-slate-600 dark:text-slate-300 mt-1">
                      {log.new_value}
                    </p>
                  )}
                  <div className="flex items-center gap-3 mt-1.5 text-[10px] text-slate-400 dark:text-slate-500">
                    <span className="flex items-center gap-1"><User className="h-3 w-3" />{log.user_id}</span>
                    <span className="flex items-center gap-1"><Clock className="h-3 w-3" />{formatDateTime(log.timestamp)}</span>
                    {log.ip_address && <span>IP: {log.ip_address}</span>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        )}
      </div>
    </div>
  );
}
