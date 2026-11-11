import { useState, useEffect, useCallback } from 'react';
import {
  Boxes,
  Package,
  Scale,
  Info,
  AlertTriangle,
  Settings,
  Plus,
  History,
} from 'lucide-react';
import clsx from 'clsx';
import DataTable from '../tables/DataTable';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Modal from '../modals/Modal';
import KpiCard from '../cards/KpiCard';
import PackagingMovementModal from './PackagingMovementModal';
import { useAuth } from '../../context/AuthContext';
import { formatNumber, formatDateTime } from '../../utils/format';
import { packagingStatus, packagingTotals, packableKg, kindOf } from '../../utils/packaging';
import { toast } from 'sonner';

const fieldCls =
  'w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500';

const KIND_VARIANT = {
  RECEIVED: 'success',
  RETURNED: 'info',
  ISSUED: 'default',
  DAMAGED: 'warning',
  SCRAPPED: 'error',
  COUNT: 'uploaded',
};

function StatusBadge({ item }) {
  const status = packagingStatus(item);
  if (status === 'out') return <Badge variant="error" dot>None on site</Badge>;
  if (status === 'low') return <Badge variant="low" dot>Low</Badge>;
  return <Badge variant="matched" dot>Counted</Badge>;
}

/**
 * The packaging register: how many bags, jars and drums are physically here.
 *
 * Read-only for anyone signed in; a movement (including a physical count) is a
 * floor-level action, while naming a new container type and setting its buy
 * alarm stay admin-only — the same split the API enforces.
 */
export default function PackagingPanel({ isAdmin }) {
  const { apiFetch } = useAuth();
  const [items, setItems] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [movementId, setMovementId] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [alarmItem, setAlarmItem] = useState(null);
  const [addLabel, setAddLabel] = useState('');
  const [addCapacity, setAddCapacity] = useState('');
  const [addSaving, setAddSaving] = useState(false);
  const [alarmValue, setAlarmValue] = useState('');
  const [alarmSaving, setAlarmSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/api/packaging');
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error('Unexpected response from server');
      setItems(data.map((row) => ({ ...row, status: packagingStatus(row) })));
    } catch (err) {
      setError(err.message || 'Could not load packaging stock');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await apiFetch('/api/packaging/history?limit=10');
      const data = await res.json();
      setHistory(Array.isArray(data) ? data : []);
    } catch {
      // A missing history feed is a degraded panel, not a missing stock figure:
      // the counts above come from the same ledger and are already on screen.
      setHistory([]);
    }
  }, [apiFetch]);

  useEffect(() => {
    // Same residual as components/stock/StockHistory.jsx: the loader opens with
    // setLoading(true) and oxlint reads that as a synchronous setState in the
    // effect. Mount-time fetch of a server list is the effect's whole purpose.
    // oxlint-disable-next-line react/set-state-in-effect
    load();
    loadHistory();
  }, [load, loadHistory]);

  const refresh = () => {
    load();
    loadHistory();
  };

  const openAdd = () => {
    setAddLabel('');
    setAddCapacity('');
    setShowAdd(true);
  };

  const handleSaveNew = async () => {
    const label = addLabel.trim();
    if (!label) {
      toast.error('Enter a name for the container type');
      return;
    }
    const capacity = addCapacity === '' ? 0 : Number(addCapacity);
    if (!Number.isFinite(capacity) || capacity < 0) {
      toast.error('Capacity must be 0 kg or greater');
      return;
    }
    setAddSaving(true);
    try {
      await apiFetch('/api/packaging/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label, capacity_kg: capacity }),
      });
      toast.success(`Added ${label} to the register`);
      setShowAdd(false);
      refresh();
    } catch (err) {
      toast.error(err.message || 'Could not add the packaging type');
    } finally {
      setAddSaving(false);
    }
  };

  const openAlarm = (row) => {
    setAlarmItem(row);
    setAlarmValue(String(row.reorder_level ?? 0));
  };

  const handleSaveAlarm = async () => {
    if (!alarmItem) return;
    const level = alarmValue === '' ? 0 : Number(alarmValue);
    if (!Number.isFinite(level) || level < 0 || level !== Math.trunc(level)) {
      toast.error('Alarm level must be a whole number of pieces');
      return;
    }
    setAlarmSaving(true);
    try {
      await apiFetch('/api/packaging/reorder', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: alarmItem.label, reorder_level: level }),
      });
      toast.success(`Alarm set for ${alarmItem.label}`);
      setAlarmItem(null);
      refresh();
    } catch (err) {
      toast.error(err.message || 'Could not save the alarm');
    } finally {
      setAlarmSaving(false);
    }
  };

  const totals = packagingTotals(items);
  const movementItem = items.find((i) => i.id === movementId) || null;

  const columns = [
    {
      header: 'Container Type',
      accessor: 'label',
      render: (val) => (
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
            <Boxes className="h-4 w-4" />
          </div>
          <span className="font-medium text-slate-900 dark:text-white truncate max-w-[220px]">{val}</span>
        </div>
      ),
    },
    {
      header: 'On Site',
      accessor: 'qty',
      render: (val, row) => (
        <span className="font-mono font-bold text-slate-800 dark:text-slate-100">
          {formatNumber(val, 0)} <span className="text-[11px] font-normal text-slate-500">{row.unit}</span>
        </span>
      ),
    },
    {
      header: 'Capacity',
      accessor: 'capacity_kg',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400 font-mono">{formatNumber(val)}</span>
      ),
    },
    {
      header: 'Packable KG',
      accessor: 'packable_kg',
      sortable: false,
      render: (_val, row) => (
        <span className="text-slate-600 dark:text-slate-400 font-mono">
          {row.qty > 0 ? formatNumber(packableKg([row])) : '—'}
        </span>
      ),
    },
    {
      header: 'Alarm',
      accessor: 'reorder_level',
      render: (val) => (
        <span className={clsx('font-mono', val > 0 ? 'text-slate-600 dark:text-slate-400' : 'text-slate-400 dark:text-slate-500')}>
          {val > 0 ? `${formatNumber(val, 0)} pcs` : 'Off'}
        </span>
      ),
    },
    {
      header: 'Last Moved',
      accessor: 'last_moved_at',
      render: (val) => (
        <span className="text-slate-500 dark:text-slate-400">{val ? formatDateTime(val) : 'Never counted'}</span>
      ),
    },
    {
      header: 'Status',
      accessor: 'status',
      render: (_val, row) => <StatusBadge item={row} />,
    },
    {
      header: 'Actions',
      accessor: 'id',
      sortable: false,
      render: (_val, row) => (
        <div className="flex items-center gap-1.5">
          <button
            onClick={(e) => { e.stopPropagation(); setMovementId(row.id); }}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/40 dark:text-blue-400 dark:hover:bg-blue-900/60 transition-colors cursor-pointer"
          >
            <Settings className="h-3 w-3" /> Record
          </button>
          {isAdmin && (
            <button
              onClick={(e) => { e.stopPropagation(); openAlarm(row); }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 transition-colors cursor-pointer"
            >
              <Info className="h-3 w-3" /> Alarm
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard title="Types Tracked" value={loading || error ? '—' : totals.types} icon={Boxes} color="blue" trendLabel="container types" />
        <KpiCard title="Total Pieces" value={loading || error ? '—' : formatNumber(totals.pieces, 0)} icon={Package} color="emerald" trendLabel="counted on site" />
        <KpiCard title="Need Reordering" value={loading || error ? '—' : totals.low + totals.out} icon={AlertTriangle} color="amber" trendLabel={`${totals.out} empty, ${totals.low} low`} />
        <KpiCard title="Packable KG" value={loading || error ? '—' : formatNumber(totals.packableKg)} icon={Scale} color="purple" trendLabel="pieces x capacity" />
      </div>

      {loading ? (
        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs" aria-label="Loading packaging stock">
          <div className="h-5 w-40 rounded bg-slate-200 dark:bg-slate-700 mb-1 animate-pulse" />
          <div className="h-3 w-64 rounded bg-slate-100 dark:bg-slate-800 mb-4 animate-pulse" />
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 py-3 border-t border-slate-100 dark:border-slate-800/60 animate-pulse">
              <div className="h-8 w-8 rounded-lg bg-slate-200 dark:bg-slate-700 shrink-0" />
              <div className="h-4 flex-1 rounded bg-slate-200 dark:bg-slate-700" />
              <div className="h-4 w-20 rounded bg-slate-100 dark:bg-slate-800" />
              <div className="h-4 w-16 rounded bg-slate-100 dark:bg-slate-800" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-8 text-center" role="alert">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-rose-100 dark:bg-rose-950/60">
            <AlertTriangle className="h-6 w-6 text-rose-600 dark:text-rose-400" />
          </div>
          <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">Could not load packaging stock</p>
          <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-1">{error} — no container counts are shown.</p>
          <Button variant="primary" size="sm" onClick={load} className="mt-4">Retry</Button>
        </div>
      ) : (
        <DataTable
          title="Packaging Register"
          subtitle="Counted containers — bags, jars and drums. These are not chemicals and never enter a recipe."
          columns={columns}
          data={items}
          searchKey="label"
          searchPlaceholder="Search container type..."
          filterOptions={[
            { value: 'ok', label: 'Counted' },
            { value: 'low', label: 'Low' },
            { value: 'out', label: 'None on site' },
          ]}
          filterKey="status"
          initialPageSize={15}
          actions={isAdmin && (
            <Button variant="primary" size="sm" icon={Plus} onClick={openAdd}>Add Type</Button>
          )}
        />
      )}

      {!loading && !error && history.length > 0 && (
        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
          <div className="flex items-center gap-2 mb-1">
            <History className="h-4 w-4 text-blue-600 dark:text-blue-400" />
            <h3 className="text-base font-semibold text-slate-900 dark:text-white">Recent Movements</h3>
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
            Every count and every in/out, newest first — read from the ledger itself.
          </p>
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {history.map((m) => {
              const kind = kindOf(m.kind);
              return (
                <div key={m.id} className="flex items-center gap-3 py-2 text-xs">
                  <Badge variant={KIND_VARIANT[m.kind] || 'default'} size="xs">{kind?.label || m.kind}</Badge>
                  <span className="font-medium text-slate-800 dark:text-white shrink-0">{m.label}</span>
                  <span className="font-mono text-slate-600 dark:text-slate-300">
                    {formatNumber(m.before_qty, 0)} → {formatNumber(m.after_qty, 0)}
                  </span>
                  <span
                    className={clsx(
                      'font-mono font-semibold shrink-0',
                      m.delta > 0 ? 'text-emerald-600 dark:text-emerald-400'
                        : m.delta < 0 ? 'text-rose-600 dark:text-rose-400'
                          : 'text-slate-400 dark:text-slate-500',
                    )}
                  >
                    {m.delta > 0 ? '+' : ''}{m.delta}
                  </span>
                  {m.reason && <span className="text-slate-500 dark:text-slate-400 truncate">{m.reason}</span>}
                  <span className="ml-auto text-[10px] text-slate-400 dark:text-slate-500 shrink-0">
                    {m.actor} · {formatDateTime(m.created_at)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <Modal
        isOpen={showAdd}
        onClose={() => setShowAdd(false)}
        title="Add Packaging Type"
        subtitle="Register a container your team counts"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowAdd(false)} disabled={addSaving}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveNew} loading={addSaving} disabled={addSaving}>Save</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Name</label>
            <input aria-label="Container name" type="text" value={addLabel} onChange={(e) => setAddLabel(e.target.value)} placeholder="e.g. 200 kg drum" className={fieldCls} />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Capacity (KG)</label>
            <input aria-label="Capacity in KG" inputMode="decimal" type="number" min="0" step="0.001" value={addCapacity} onChange={(e) => setAddCapacity(e.target.value)} placeholder="0" className={fieldCls} />
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
              How much one of these holds, used for the packable-KG total.
            </p>
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={alarmItem !== null}
        onClose={() => setAlarmItem(null)}
        title="Packaging Alarm"
        subtitle={alarmItem ? `${alarmItem.label} — ${formatNumber(alarmItem.qty, 0)} on site` : ''}
        footer={
          <>
            <Button variant="secondary" onClick={() => setAlarmItem(null)} disabled={alarmSaving}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveAlarm} loading={alarmSaving} disabled={alarmSaving}>Save</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Alert me at (pieces)</label>
            <input aria-label="Alarm level" inputMode="numeric" type="number" min="0" step="1" value={alarmValue} onChange={(e) => setAlarmValue(e.target.value)} placeholder="0" className={fieldCls} />
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
              0 turns the alarm off — an empty stack still reports, and nothing else does.
            </p>
          </div>
        </div>
      </Modal>

      {movementItem && (
        <PackagingMovementModal
          item={movementItem}
          onClose={() => setMovementId(null)}
          onSaved={() => { setMovementId(null); refresh(); }}
        />
      )}
    </div>
  );
}
