import { useState, useEffect, useCallback } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { History } from 'lucide-react';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';
import { formatNumber, formatDateTime } from '../../utils/format';
import { bucketKey, bucketLabel, rollupByBucket, defaultRange } from '../../utils/history';

const GRANULARITIES = ['day', 'week', 'month'];
const FEED_CAP = 50;

function signed(n) {
  const v = Number(n) || 0;
  return `${v > 0 ? '+' : ''}${formatNumber(v)}`;
}

function HistoryTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white/95 dark:bg-slate-900/95 p-3 shadow-lg text-xs">
      <p className="font-semibold text-slate-800 dark:text-slate-200 mb-1">{label}</p>
      <p className="font-mono font-bold text-slate-800 dark:text-slate-100">
        Net {signed(payload[0].value)}
      </p>
    </div>
  );
}

function purposeVariant(source) {
  if (source === 'upload') return 'info';
  if (source === 'registration') return 'success';
  return 'default';
}

export default function StockHistory({ chemicals = [] }) {
  const { apiFetch } = useAuth();
  const [range, setRange] = useState(defaultRange);
  const [granularity, setGranularity] = useState('week');
  const [chemicalId, setChemicalId] = useState('all');
  const [movements, setMovements] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadHistory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        since: range.since,
        until: range.until,
        limit: '500',
      });
      if (chemicalId !== 'all') params.set('chemical_id', chemicalId);
      const res = await apiFetch(`/api/chemicals/history?${params}`);
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error('Unexpected response from server');
      setMovements(data);
    } catch (err) {
      setError(err.message || 'Could not load stock history');
      setMovements([]);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, range, chemicalId]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const rollup = rollupByBucket(movements, granularity);
  const net = movements.reduce((sum, m) => sum + (Number(m.delta) || 0), 0);

  const groups = [];
  const byKey = new Map();
  for (const m of movements) {
    const key = bucketKey(m.timestamp, granularity);
    if (!key) continue;
    if (!byKey.has(key)) {
      const group = { key, label: bucketLabel(key, granularity), rows: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    byKey.get(key).rows.push(m);
  }
  const visible = groups.slice(0, FEED_CAP);
  const shown = visible.reduce((n, g) => n + g.rows.length, 0);

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
      <div className="flex items-center gap-2 mb-1">
        <History className="h-4 w-4 text-blue-600 dark:text-blue-400" />
        <h3 className="text-base font-semibold text-slate-900 dark:text-white">Stock History</h3>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
        Every inventory change, datewise — Net {signed(net)} across {movements.length} movement{movements.length === 1 ? '' : 's'} in range
      </p>

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div>
          <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">From date</label>
          <input aria-label="From date" type="date" value={range.since} onChange={(e) => setRange((r) => ({ ...r, since: e.target.value }))} className="px-2.5 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20" />
        </div>
        <div>
          <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">To date</label>
          <input aria-label="To date" type="date" value={range.until} onChange={(e) => setRange((r) => ({ ...r, until: e.target.value }))} className="px-2.5 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20" />
        </div>
        <div>
          <span className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">Group by</span>
          <div className="flex rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden" role="group" aria-label="Granularity">
            {GRANULARITIES.map((g) => (
              <button
                key={g}
                onClick={() => setGranularity(g)}
                aria-pressed={granularity === g}
                className={`px-3 py-1.5 text-xs font-semibold capitalize transition-colors cursor-pointer ${granularity === g ? 'bg-blue-600 text-white' : 'bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700'}`}
              >
                {g === 'day' ? 'Day' : g === 'week' ? 'Week' : 'Month'}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-1">Chemical</label>
          <select aria-label="Chemical" value={chemicalId} onChange={(e) => setChemicalId(e.target.value)} className="px-2.5 py-1.5 text-xs rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20">
            <option value="all">All chemicals</option>
            {chemicals.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="py-8 text-center text-xs text-slate-500 dark:text-slate-400">
          <div className="inline-flex items-center gap-2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />
            Loading history…
          </div>
        </div>
      ) : error ? (
        <div className="py-6 text-center">
          <p className="text-xs text-rose-600 dark:text-rose-400 mb-3">{error}</p>
          <Button variant="secondary" size="sm" onClick={loadHistory}>Retry</Button>
        </div>
      ) : movements.length === 0 ? (
        <p className="py-8 text-center text-xs text-slate-500 dark:text-slate-400">
          No movements in this range — history accumulates from here on.
        </p>
      ) : (
        <>
          {rollup.length > 0 && (
            <div className="h-56 w-full mb-2">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={rollup} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#94a3b8" opacity={0.2} vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: '#64748b', fontSize: 11 }} />
                  <YAxis tickLine={false} axisLine={false} tick={{ fill: '#64748b', fontSize: 11 }} />
                  <Tooltip content={<HistoryTooltip />} />
                  <Bar name="Net change" dataKey="net" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {visible.map((group) => (
              <div key={group.key} className="py-2">
                <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500 px-1 mb-1">
                  {group.label}
                </p>
                {group.rows.map((m) => (
                  <div key={m.id} className="flex items-start gap-3 px-1 py-1.5">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-xs font-semibold text-slate-800 dark:text-white">{m.chemical}</span>
                        <Badge variant={purposeVariant(m.source)} size="xs">{m.purpose}</Badge>
                      </div>
                      <p className="text-xs text-slate-600 dark:text-slate-300 mt-0.5 font-mono">
                        {m.old == null ? '—' : formatNumber(m.old)} → {formatNumber(m.new)} {m.unit}
                        {' '}({signed(m.delta)} {m.unit})
                      </p>
                      <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-0.5">
                        {m.actor} · {formatDateTime(m.timestamp)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
          {movements.length > shown && (
            <p className="text-[11px] text-slate-400 dark:text-slate-500 mt-2">
              Showing latest {shown} of {movements.length} movements — narrow the range to see more.
            </p>
          )}
        </>
      )}
    </div>
  );
}
