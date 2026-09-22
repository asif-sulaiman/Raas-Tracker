import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import {
  Bell, Volume2, VolumeX, CheckCheck, AlertTriangle, Info, RefreshCw, Monitor,
} from 'lucide-react';
import { useNotifications } from '../../context/NotificationContext';
import { timeAgo, entityRoute, severityMeta } from '../../utils/notifications';

function SeverityIcon({ severity }) {
  const meta = severityMeta(severity);
  const Icon = meta.icon === 'alert' ? AlertTriangle : Info;
  return (
    <span className={clsx('mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full', meta.iconWrap)}>
      <Icon size={14} />
    </span>
  );
}

export default function NotificationBell() {
  const {
    items, unread, loading, error, refresh, markRead, markAllRead,
    soundMuted, setSoundMuted, nativeEnabled, enableNative, disableNative,
  } = useNotifications();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const navigate = useNavigate();
  const supportsNative = typeof window !== 'undefined' && 'Notification' in window;

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const handleItemClick = async (n) => {
    if (!n.is_read) await markRead([n.id]);
    const route = entityRoute(n);
    setOpen(false);
    if (route) navigate(route);
  };

  return (
    <div className="relative" ref={wrapRef}>
      <button
        type="button"
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="relative p-2 rounded-lg hover:bg-gray-100 text-gray-500 transition-colors cursor-pointer"
      >
        <Bell size={20} />
        {unread > 0 && (
          <span
            data-testid="unread-badge"
            className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full bg-rose-500 text-white text-[10px] font-bold flex items-center justify-center"
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          className="absolute right-0 mt-2 w-96 max-w-[calc(100vw-2rem)] rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-lg z-50 overflow-hidden"
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 dark:border-slate-800">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                Notifications
              </h3>
              {unread > 0 && (
                <span className="inline-flex items-center rounded-full bg-rose-50 dark:bg-rose-950/50 px-2 py-0.5 text-[10px] font-semibold text-rose-600 dark:text-rose-400">
                  {unread} new
                </span>
              )}
            </div>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => markAllRead()}
                className="flex items-center gap-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
              >
                <CheckCheck size={14} /> Mark all read
              </button>
            )}
          </div>

          <div className="max-h-96 overflow-y-auto">
            {loading && items.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-slate-400">
                Loading…
              </div>
            )}
            {error && (
              <div className="px-4 py-6 text-center">
                <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>
                <button
                  type="button"
                  onClick={() => refresh()}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 cursor-pointer"
                >
                  <RefreshCw size={13} /> Retry
                </button>
              </div>
            )}
            {!loading && !error && items.length === 0 && (
              <div className="px-4 py-8 text-center text-sm text-slate-400">
                You&apos;re all caught up.
              </div>
            )}
            {!error && items.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => handleItemClick(n)}
                className={clsx(
                  'w-full text-left flex gap-3 px-4 py-3 border-b border-slate-50 dark:border-slate-800/80 last:border-0 transition-colors cursor-pointer',
                  'hover:bg-slate-50 dark:hover:bg-slate-800/60',
                  !n.is_read && 'bg-blue-50/60 dark:bg-blue-950/20'
                )}
              >
                <SeverityIcon severity={n.severity} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span
                      className={clsx(
                        'text-sm truncate',
                        n.is_read
                          ? 'font-medium text-slate-600 dark:text-slate-300'
                          : 'font-semibold text-slate-900 dark:text-white'
                      )}
                    >
                      {n.title}
                    </span>
                    <span className="text-[10px] text-slate-400 shrink-0">
                      {timeAgo(n.created_at)}
                    </span>
                  </span>
                  {n.body && (
                    <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400 truncate">
                      {n.body}
                    </span>
                  )}
                </span>
                {!n.is_read && (
                  <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-blue-500" />
                )}
              </button>
            ))}
          </div>

          <div className="flex items-center justify-between border-t border-slate-100 dark:border-slate-800 px-4 py-2">
            <button
              type="button"
              onClick={() => setSoundMuted(!soundMuted)}
              title={soundMuted ? 'Unmute notification sounds' : 'Mute notification sounds'}
              className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 cursor-pointer"
            >
              {soundMuted ? <VolumeX size={14} /> : <Volume2 size={14} />}
              Sound {soundMuted ? 'off' : 'on'}
            </button>
            {supportsNative && (
              <button
                type="button"
                onClick={() => (nativeEnabled ? disableNative() : enableNative())}
                title="Browser notifications while this tab is hidden"
                className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 cursor-pointer"
              >
                <Monitor size={14} />
                Browser alerts {nativeEnabled ? 'on' : 'off'}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
