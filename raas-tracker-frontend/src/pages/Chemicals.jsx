import { useState, useEffect, useCallback } from 'react';
import {
  Package,
  FlaskConical,
  Settings,
  Info,
  AlertTriangle,
  TrendingDown,
  Plus
} from 'lucide-react';
import clsx from 'clsx';
import DataTable from '../components/tables/DataTable';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Modal from '../components/modals/Modal';
import KpiCard from '../components/cards/KpiCard';
import StockHistory from '../components/stock/StockHistory';
import { COMMON_UNITS } from '../utils/units';
import { formatNumber } from '../utils/format';
import { useAuth } from '../context/AuthContext';
import { toast } from 'sonner';

import { stockStatus } from '../utils/stock';

export default function Chemicals() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [chemicals, setChemicals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedChemical, setSelectedChemical] = useState(null);
  const [showAdjustModal, setShowAdjustModal] = useState(false);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [adjustQty, setAdjustQty] = useState('');
  const [adjustReason, setAdjustReason] = useState('Physical count correction');
  const [adjustSaving, setAdjustSaving] = useState(false);
  const [alarmValue, setAlarmValue] = useState('');
  const [alarmSaving, setAlarmSaving] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [addName, setAddName] = useState('');
  const [addQty, setAddQty] = useState('');
  const [addUnit, setAddUnit] = useState('KG');
  const [addReorder, setAddReorder] = useState('');
  const [addSaving, setAddSaving] = useState(false);

  const openAdjust = (row) => {
    setSelectedChemical(row);
    setAdjustQty(String(row.qty ?? ''));
    setAdjustReason('Physical count correction');
    setShowAdjustModal(true);
  };

  const openInfo = (row) => {
    setSelectedChemical(row);
    setAlarmValue(String(row.reorder_level ?? 0));
    setShowDetailModal(true);
  };

  const handleSaveAdjustment = async () => {
    if (!selectedChemical) return;
    const next = parseFloat(adjustQty);
    if (Number.isNaN(next)) {
      toast.error('Enter a valid quantity');
      return;
    }
    const delta = next - (selectedChemical.qty || 0);
    if (delta === 0) {
      setShowAdjustModal(false);
      setSelectedChemical(null);
      return;
    }
    setAdjustSaving(true);
    try {
      await apiFetch('/api/chemicals/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: selectedChemical.name, delta, reason: adjustReason }),
      });
      toast.success(`Stock updated for ${selectedChemical.name}`);
      setShowAdjustModal(false);
      setSelectedChemical(null);
      loadChemicals();
    } catch (err) {
      toast.error(err.message || 'Could not save adjustment');
    } finally {
      setAdjustSaving(false);
    }
  };

  const resetAdd = () => {
    setAddName('');
    setAddQty('');
    setAddUnit('KG');
    setAddReorder('');
  };

  const openAdd = () => {
    resetAdd();
    setShowAddModal(true);
  };

  const handleSaveNew = async () => {
    const name = addName.trim();
    if (!name) {
      toast.error('Enter a chemical name');
      return;
    }
    const qty = addQty === '' ? 0 : parseFloat(addQty);
    if (Number.isNaN(qty) || qty < 0) {
      toast.error('Opening quantity must be 0 or greater');
      return;
    }
    const reorder = addReorder === '' ? 0 : parseFloat(addReorder);
    if (Number.isNaN(reorder) || reorder < 0) {
      toast.error('Low-stock alarm must be 0 or greater');
      return;
    }
    setAddSaving(true);
    try {
      const res = await apiFetch('/api/chemicals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, qty, unit: addUnit, reorder_level: reorder }),
      });
      const data = await res.json().catch(() => ({}));
      toast.success(`Added ${data.name || name} to stock`);
      setShowAddModal(false);
      resetAdd();
      loadChemicals();
    } catch (err) {
      toast.error(err.message || 'Could not add chemical');
    } finally {
      setAddSaving(false);
    }
  };

  const handleSaveAlarm = async () => {
    if (!selectedChemical) return;
    const level = alarmValue === '' ? 0 : parseFloat(alarmValue);
    if (Number.isNaN(level) || level < 0) {
      toast.error('Low-stock alarm must be 0 or greater');
      return;
    }
    setAlarmSaving(true);
    try {
      await apiFetch('/api/chemicals/reorder', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: selectedChemical.name, reorder_level: level }),
      });
      toast.success(`Low-stock alarm set for ${selectedChemical.name}`);
      setShowDetailModal(false);
      setSelectedChemical(null);
      loadChemicals();
    } catch (err) {
      toast.error(err.message || 'Could not save alarm');
    } finally {
      setAlarmSaving(false);
    }
  };

  const loadChemicals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch('/api/chemicals');
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error('Unexpected response from server');
      setChemicals(data.map(c => ({
        ...c,
        balance_this_month: c.qty,
        status: stockStatus(c)
      })));
    } catch (err) {
      setError(err.message || 'Could not load chemical stock');
      setChemicals([]);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadChemicals();
  }, [loadChemicals]);

  const getStockStatus = (chem) => stockStatus(chem);

  const totalChemicals = chemicals.length;
  const activeStock = chemicals.filter(c => c.qty > 0).length;
  const outStock = chemicals.filter(c => c.qty === 0).length;
  const lowStock = chemicals.filter(c => getStockStatus(c) === 'low').length;
  const totalQty = chemicals.reduce((sum, c) => sum + (c.qty || 0), 0);

  const columns = [
    {
      header: 'Chemical Name',
      accessor: 'name',
      render: (val) => (
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
            <FlaskConical className="h-4 w-4" />
          </div>
          <span className="font-medium text-slate-900 dark:text-white truncate max-w-[220px]">{val}</span>
        </div>
      )
    },
    {
      header: 'Current Stock',
      accessor: 'qty',
      render: (val) => (
        <span className="font-mono font-bold text-slate-800 dark:text-slate-100">{formatNumber(val)}</span>
      )
    },
    {
      header: 'Unit',
      accessor: 'unit',
      render: (val) => (
        <span className="px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-[11px] font-mono text-slate-600 dark:text-slate-300">{val}</span>
      )
    },
    {
      header: 'Last Month',
      accessor: 'balance_last_month',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400">{formatNumber(val)}</span>
      )
    },
    {
      header: 'This Month',
      accessor: 'balance_this_month',
      render: (val) => (
        <span className="font-medium text-slate-800 dark:text-slate-200">{formatNumber(val)}</span>
      )
    },
    {
      header: 'Variance',
      accessor: 'variance',
      render: (_val, row) => {
        const diff = (row.qty || 0) - (row.balance_last_month || 0);
        const pct = row.balance_last_month > 0 ? ((diff / row.balance_last_month) * 100).toFixed(1) : '0.0';
        return (
          <span className={clsx(
            'text-xs font-semibold',
            diff > 0 ? 'text-emerald-600 dark:text-emerald-400' : diff < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400'
          )}>
            {diff > 0 ? '+' : ''}{formatNumber(diff)} ({pct}%)
          </span>
        );
      }
    },
    {
      header: 'Status',
      accessor: 'status',
      render: (_val, row) => {
        const status = getStockStatus(row);
        if (status === 'out') return <Badge variant="error" dot>Out of Stock</Badge>;
        if (status === 'low') return <Badge variant="low" dot>Low Stock</Badge>;
        return <Badge variant="matched" dot>Reconciled</Badge>;
      }
    },
    {
      header: 'Actions',
      accessor: 'id',
      sortable: false,
      render: (_val, row) => (
        <div className="flex items-center gap-1.5">
          {isAdmin && (
            <button
              onClick={(e) => { e.stopPropagation(); openAdjust(row); }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/40 dark:text-blue-400 dark:hover:bg-blue-900/60 transition-colors cursor-pointer"
            >
              <Settings className="h-3 w-3" /> Adjust
            </button>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); openInfo(row); }}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 transition-colors cursor-pointer"
          >
            <Info className="h-3 w-3" /> Info
          </button>
        </div>
      )
    }
  ];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">Chemicals Inventory</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Complete catalog of all registered chemicals</p>
        </div>
        <div className="flex items-center gap-2">
          {isAdmin && !loading && !error && (
            <Button variant="primary" size="sm" icon={Plus} onClick={openAdd}>Add Chemical</Button>
          )}
          {loading || error ? (
            <Badge variant="default">Unavailable</Badge>
          ) : (
            <>
              <Badge variant="success">{activeStock} Active</Badge>
              <Badge variant="warning">{lowStock} Low</Badge>
              <Badge variant="error">{outStock} Out</Badge>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard title="Total Chemicals" value={loading || error ? '—' : totalChemicals} icon={FlaskConical} color="blue" trendLabel="registered items" />
        <KpiCard title="Active Stock" value={loading || error ? '—' : activeStock} icon={Package} color="emerald" trendLabel="positive quantity" />
        <KpiCard title="Out of Stock" value={loading || error ? '—' : outStock} icon={TrendingDown} color="rose" trendLabel="zero balance" />
        <KpiCard title="Total Quantity" value={loading || error ? '—' : formatNumber(totalQty)} icon={AlertTriangle} color="amber" trendLabel="across all units" />
      </div>

      {loading ? (
        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs" aria-label="Loading chemical stock">
          <div className="h-5 w-40 rounded bg-slate-200 dark:bg-slate-700 mb-1 animate-pulse" />
          <div className="h-3 w-64 rounded bg-slate-100 dark:bg-slate-800 mb-4 animate-pulse" />
          {Array.from({ length: 8 }).map((_, i) => (
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
          <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">Could not load chemical stock</p>
          <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-1">{error} — no stock figures are shown.</p>
          <Button variant="primary" size="sm" onClick={loadChemicals} className="mt-4">
            Retry
          </Button>
        </div>
      ) : chemicals.length === 0 ? (
        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 dark:bg-slate-800">
            <FlaskConical className="h-6 w-6 text-slate-400" />
          </div>
          <p className="text-sm font-semibold text-slate-800 dark:text-white">No chemicals registered yet</p>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">Upload a monthly stock file or add chemicals to get started.</p>
          {isAdmin && (
            <Button variant="primary" size="sm" icon={Plus} onClick={openAdd} className="mt-4">
              Add your first chemical
            </Button>
          )}
        </div>
      ) : (
        <DataTable
          title="All Chemicals"
          subtitle="Search, filter, and manage chemical stock levels"
          columns={columns}
          data={chemicals}
          searchKey="name"
          searchPlaceholder="Search chemical by name..."
          filterOptions={[{ value: 'matched', label: 'Reconciled' }, { value: 'low', label: 'Low Stock' }, { value: 'out', label: 'Out of Stock' }]}
          filterKey="status"
          initialPageSize={15}
        />
      )}

      {isAdmin && !loading && !error && chemicals.length > 0 && (
        <StockHistory chemicals={chemicals} />
      )}

      <Modal
        isOpen={showAddModal}
        onClose={() => { setShowAddModal(false); resetAdd(); }}
        title="Add Chemical"
        subtitle="Register a new chemical in stock"
        footer={
          <>
            <Button variant="secondary" onClick={() => { setShowAddModal(false); resetAdd(); }} disabled={addSaving}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveNew} loading={addSaving} disabled={addSaving}>Save</Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Chemical name</label>
            <input aria-label="Chemical name" type="text" value={addName} onChange={(e) => setAddName(e.target.value)} placeholder="e.g. Citric Acid" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Opening quantity</label>
              <input aria-label="Opening quantity" type="number" min="0" value={addQty} onChange={(e) => setAddQty(e.target.value)} placeholder="0" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Unit</label>
              <select aria-label="Unit" value={addUnit} onChange={(e) => setAddUnit(e.target.value)} className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500">
                {COMMON_UNITS.map((u) => (
                  <option key={u} value={u}>{u}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Low-stock alarm (0 = off)</label>
              <input aria-label="Low-stock alarm" type="number" min="0" value={addReorder} onChange={(e) => setAddReorder(e.target.value)} placeholder="0" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
          </div>
        </div>
      </Modal>

      <Modal
        isOpen={showAdjustModal}
        onClose={() => { setShowAdjustModal(false); setSelectedChemical(null); }}
        title="Adjust Chemical Stock"
        subtitle={`Update balance for ${selectedChemical?.name || ''}`}
        footer={
          <>
            <Button variant="secondary" onClick={() => { setShowAdjustModal(false); setSelectedChemical(null); }} disabled={adjustSaving}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveAdjustment} loading={adjustSaving} disabled={adjustSaving}>Save Adjustment</Button>
          </>
        }
      >
        {selectedChemical && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-3 bg-slate-50 dark:bg-slate-800/60">
                <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Current Stock</p>
                <p className="text-xl font-bold text-slate-900 dark:text-white mt-0.5">{formatNumber(selectedChemical.qty)} <span className="text-xs font-normal text-slate-500">{selectedChemical.unit}</span></p>
              </div>
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-3 bg-slate-50 dark:bg-slate-800/60">
                <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Last Month</p>
                <p className="text-xl font-bold text-slate-900 dark:text-white mt-0.5">{formatNumber(selectedChemical.balance_last_month)} <span className="text-xs font-normal text-slate-500">{selectedChemical.unit}</span></p>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">New Quantity ({selectedChemical.unit})</label>
              <input aria-label="New quantity" type="number" value={adjustQty} onChange={(e) => setAdjustQty(e.target.value)} className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Reason</label>
              <select aria-label="Reason" value={adjustReason} onChange={(e) => setAdjustReason(e.target.value)} className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500">
                <option>Physical count correction</option>
                <option>Transfer from another warehouse</option>
                <option>Disposal / expiry</option>
                <option>Supplier delivery</option>
              </select>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        isOpen={showDetailModal}
        onClose={() => { setShowDetailModal(false); setSelectedChemical(null); }}
        title="Chemical Information"
        subtitle={selectedChemical?.name || ''}
      >
        {selectedChemical && (
          <>
          <div className="grid grid-cols-2 gap-3 text-xs">
            {[
              ['ID', selectedChemical.id],
              ['Unit', selectedChemical.unit],
              ['Balance Last Month', `${formatNumber(selectedChemical.balance_last_month)} ${selectedChemical.unit}`],
              ['Balance This Month', `${formatNumber(selectedChemical.qty)} ${selectedChemical.unit}`],
              ['Status', getStockStatus(selectedChemical) === 'matched' ? 'Reconciled' : getStockStatus(selectedChemical) === 'low' ? 'Low Stock' : 'Out of Stock'],
              ['Last Updated', selectedChemical.last_updated]
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
                <span className="text-slate-500 dark:text-slate-400 block mb-0.5">{label}</span>
                <span className="font-semibold text-slate-800 dark:text-white">{value}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 rounded-lg border border-slate-200 dark:border-slate-700 p-3">
            <span className="text-slate-500 dark:text-slate-400 block mb-1.5 text-xs">Low-stock alarm</span>
            {isAdmin ? (
              <div className="flex items-center gap-2">
                <input aria-label="Low-stock alarm" type="number" min="0" value={alarmValue} onChange={(e) => setAlarmValue(e.target.value)} placeholder="0" className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
                <span className="text-xs text-slate-500 dark:text-slate-400 shrink-0">{selectedChemical.unit}</span>
                <Button variant="primary" size="sm" onClick={handleSaveAlarm} loading={alarmSaving} disabled={alarmSaving}>Save</Button>
              </div>
            ) : (
              <span className="font-semibold text-slate-800 dark:text-white text-xs">{formatNumber(selectedChemical.reorder_level || 0)} {selectedChemical.unit}</span>
            )}
          </div>
          </>
        )}
      </Modal>
    </div>
  );
}
