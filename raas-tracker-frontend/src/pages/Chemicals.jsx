import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Package,
  FlaskConical,
  Boxes,
  Truck,
  Settings,
  Info,
  AlertTriangle,
  TrendingDown,
  Plus
} from 'lucide-react';
import clsx from 'clsx';
import DataTable from '../components/tables/DataTable';
import PackagingPanel from '../components/packaging/PackagingPanel';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Modal from '../components/modals/Modal';
import KpiCard from '../components/cards/KpiCard';
import StockHistory from '../components/stock/StockHistory';
import ChemicalPurchaseModal from '../components/stock/ChemicalPurchaseModal';
import PurchaseHistory from '../components/stock/PurchaseHistory';
import SuggestionInput from '../components/ui/SuggestionInput';
import { formatNumber } from '../utils/format';
import { useAuth } from '../context/AuthContext';
import { toast } from 'sonner';

import { stockStatus } from '../utils/stock';

const REASON_SUGGESTIONS = [
  'Physical count correction',
  'Transfer from another warehouse',
  'Disposal / expiry',
  'Supplier delivery',
];

const PROJECTED_TOOLTIP =
  'Additional quantity to purchase for active PI plans';

const fieldCls =
  'w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500';

export default function Chemicals() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('chemicals');
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
  const [showPurchaseModal, setShowPurchaseModal] = useState(false);
  const [outwardRows, setOutwardRows] = useState([]);
  const [outwardLoading, setOutwardLoading] = useState(false);
  const [outwardError, setOutwardError] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [unstocked, setUnstocked] = useState([]);
  const [unstockedOpen, setUnstockedOpen] = useState(false);

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
      const res = await apiFetch('/api/chemicals/inventory');
      const data = await res.json();
      const materials = Array.isArray(data.materials) ? data.materials : [];
      setChemicals(materials.map(c => ({
        ...c,
        status: stockStatus(c)
      })));
      setCoverage(data.coverage ?? null);
      setUnstocked(Array.isArray(data.unstocked_requirements) ? data.unstocked_requirements : []);
    } catch (err) {
      setError(err.message || 'Could not load chemical stock');
      setChemicals([]);
      setCoverage(null);
      setUnstocked([]);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    loadChemicals();
  }, [loadChemicals]);

  const loadOutward = useCallback(async () => {
    setOutwardLoading(true);
    setOutwardError(null);
    try {
      const res = await apiFetch('/api/chemical-purchases?delivery_type=outward&per_page=50&include_cancelled=true');
      const data = await res.json();
      const purchases = Array.isArray(data.purchases) ? data.purchases : [];
      setOutwardRows(purchases.flatMap((p) => (p.items || []).map((it) => ({
        id: it.id,
        purchase_id: p.id,
        material: it.chemical_name,
        chemical_id: it.chemical_id,
        quantity: it.quantity,
        unit: it.unit,
        supplier: p.supplier_name,
        purchase_number: p.purchase_number,
        date: p.purchase_date,
        client: p.client_name || '—',
        client_id: p.client_id ?? null,
        client_name: p.client_name ?? null,
        cancelled: p.cancelled,
      }))));
    } catch (err) {
      setOutwardError(err.message || 'Could not load outward purchases');
      setOutwardRows([]);
    } finally {
      setOutwardLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    // Outward receipts load lazily when the tab opens — an external-system sync.
    // loadOutward flips loading up front and wraps the fetch in try/catch;
    // oxlint's set-state-in-effect flags that residual, suppressed here exactly
    // as StockHistory does.
    // oxlint-disable-next-line react/set-state-in-effect
    if (activeTab === 'outward') loadOutward();
  }, [activeTab, loadOutward]);

  const getStockStatus = (chem) => stockStatus(chem);

  const totalChemicals = chemicals.length;
  const activeStock = chemicals.filter(c => c.qty > 0).length;
  const outStock = chemicals.filter(c => c.qty === 0).length;
  const lowStock = chemicals.filter(c => getStockStatus(c) === 'low').length;
  const totalQty = chemicals.reduce((sum, c) => sum + (c.qty || 0), 0);

  const columns = [
    {
      header: 'Raw Material',
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
      header: 'Last Purchase Date',
      accessor: 'last_purchase_date',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400">{val || '—'}</span>
      )
    },
    {
      header: 'Last Purchase Quantity',
      accessor: 'last_purchase_quantity',
      render: (val) => (
        <span className="font-mono text-slate-700 dark:text-slate-200">{val != null ? formatNumber(val) : '—'}</span>
      )
    },
    {
      header: 'Supplier Name',
      accessor: 'supplier_name',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400">{val || '—'}</span>
      )
    },
    {
      header: 'Projected Quantity',
      accessor: 'projected_quantity',
      sortable: false,
      render: (val, row) => {
        const unknown = val == null;
        const text = unknown ? '—' : val === 0 ? '0' : `${formatNumber(val)} ${row.unit}`;
        return (
          <span
            title={PROJECTED_TOOLTIP}
            aria-label={unknown
              ? 'Projected quantity unknown: missing unit conversion or no active plan'
              : `Projected purchase ${text}`}
            className={unknown ? 'text-slate-400 dark:text-slate-500 cursor-help' : 'font-mono text-slate-700 dark:text-slate-200 cursor-help'}
          >
            {text}
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

  const outwardColumns = [
    {
      header: 'Material',
      accessor: 'material',
      render: (val) => (
        <span className="font-medium text-slate-900 dark:text-white">{val}</span>
      )
    },
    {
      header: 'Quantity',
      accessor: 'quantity',
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
      header: 'Supplier',
      accessor: 'supplier',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400">{val || '—'}</span>
      )
    },
    {
      header: 'Purchase #',
      accessor: 'purchase_number',
      render: (val) => (
        <span className="font-mono text-slate-700 dark:text-slate-200">{val}</span>
      )
    },
    {
      header: 'Date',
      accessor: 'date',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400">{val || '—'}</span>
      )
    },
    {
      header: 'Client',
      accessor: 'client',
      render: (val) => (
        <span className="text-slate-600 dark:text-slate-400">{val || '—'}</span>
      )
    },
    {
      header: 'Status',
      accessor: 'cancelled',
      sortable: false,
      render: (val) => (
        val ? <Badge variant="default">Cancelled</Badge> : <Badge variant="success">Active</Badge>
      )
    },
    {
      // Entry point into the recipe workflow: the client and the line travel
      // with it, so nothing has to be re-picked there. A cancelled delivery
      // can back no recipe, so it offers no button.
      header: 'Actions',
      accessor: 'id',
      sortable: false,
      render: (_, row) => (row.cancelled ? null : (
        <Button
          variant="secondary"
          size="sm"
          onClick={() => navigate('/recipes', {
            state: {
              useOutwardLine: {
                line_id: row.id,
                purchase_id: row.purchase_id,
                client_id: row.client_id,
                client_name: row.client,
                chemical_id: row.chemical_id,
                chemical_name: row.material,
                quantity: row.quantity,
                unit: row.unit,
                supplier_name: row.supplier,
                purchase_number: row.purchase_number,
                purchase_date: row.date,
              },
            },
          })}
        >
          Use in invoice recipe
        </Button>
      ))
    }
  ];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">
            {activeTab === 'chemicals' ? 'Chemicals Inventory' : activeTab === 'outward' ? 'Outward Purchases' : 'Packaging Register'}
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            {activeTab === 'chemicals'
              ? 'Complete catalog of all registered chemicals'
              : activeTab === 'outward'
                ? 'Materials purchased for delivery to client sites'
                : 'Bags, jars and drums counted as objects — not chemicals, never a recipe ingredient'}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {activeTab === 'chemicals' && (
            <>
              {isAdmin && !loading && !error && (
                <Button variant="primary" size="sm" icon={Plus} onClick={() => setShowPurchaseModal(true)}>Add Chemical</Button>
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
              <span className="w-px h-6 bg-slate-200 dark:bg-slate-700" aria-hidden="true" />
            </>
          )}
          <button
            onClick={() => setActiveTab('chemicals')}
            aria-pressed={activeTab === 'chemicals'}
            className={clsx(
              'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer',
              activeTab === 'chemicals'
                ? 'bg-blue-600 text-white'
                : 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700'
            )}
          >
            <FlaskConical className="h-4 w-4" /> Chemicals
          </button>
          <button
            onClick={() => setActiveTab('packaging')}
            aria-pressed={activeTab === 'packaging'}
            className={clsx(
              'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer',
              activeTab === 'packaging'
                ? 'bg-blue-600 text-white'
                : 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700'
            )}
          >
            <Boxes className="h-4 w-4" /> Packaging
          </button>
          <button
            onClick={() => setActiveTab('outward')}
            aria-pressed={activeTab === 'outward'}
            className={clsx(
              'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors cursor-pointer',
              activeTab === 'outward'
                ? 'bg-blue-600 text-white'
                : 'bg-white dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700'
            )}
          >
            <Truck className="h-4 w-4" /> Outward
          </button>
        </div>
      </div>

      {activeTab === 'chemicals' && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <KpiCard title="Total Chemicals" value={loading || error ? '—' : totalChemicals} icon={FlaskConical} color="blue" trendLabel="registered items" />
          <KpiCard title="Active Stock" value={loading || error ? '—' : activeStock} icon={Package} color="emerald" trendLabel="positive quantity" />
          <KpiCard title="Out of Stock" value={loading || error ? '—' : outStock} icon={TrendingDown} color="rose" trendLabel="zero balance" />
          <KpiCard title="Total Quantity" value={loading || error ? '—' : formatNumber(totalQty)} icon={AlertTriangle} color="amber" trendLabel="across all units" />
        </div>
      )}

      {activeTab === 'packaging' ? (
        <PackagingPanel isAdmin={isAdmin} />
      ) : activeTab === 'outward' ? (
        outwardLoading ? (
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs" aria-label="Loading outward purchases">
            <div className="h-5 w-40 rounded bg-slate-200 dark:bg-slate-700 mb-1 animate-pulse" />
            <div className="h-3 w-64 rounded bg-slate-100 dark:bg-slate-800 mb-4 animate-pulse" />
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 py-3 border-t border-slate-100 dark:border-slate-800/60 animate-pulse">
                <div className="h-4 flex-1 rounded bg-slate-200 dark:bg-slate-700" />
                <div className="h-4 w-20 rounded bg-slate-100 dark:bg-slate-800" />
                <div className="h-4 w-16 rounded bg-slate-100 dark:bg-slate-800" />
              </div>
            ))}
          </div>
        ) : outwardError ? (
          <div className="rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-8 text-center" role="alert">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-rose-100 dark:bg-rose-950/60">
              <AlertTriangle className="h-6 w-6 text-rose-600 dark:text-rose-400" />
            </div>
            <p className="text-sm font-semibold text-rose-700 dark:text-rose-300">Could not load outward purchases</p>
            <p className="text-xs text-rose-600/80 dark:text-rose-400/80 mt-1">{outwardError}</p>
            <Button variant="primary" size="sm" onClick={loadOutward} className="mt-4">
              Retry
            </Button>
          </div>
        ) : outwardRows.length === 0 ? (
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-slate-100 dark:bg-slate-800">
              <Truck className="h-6 w-6 text-slate-400" />
            </div>
            <p className="text-sm font-semibold text-slate-800 dark:text-white">No outward purchases recorded yet</p>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">Materials bought for delivery to a client will appear here.</p>
          </div>
        ) : (
          <DataTable
            title="Outward Purchases"
            subtitle="Materials purchased for delivery to client sites"
            columns={outwardColumns}
            data={outwardRows}
            searchKey="material"
            searchPlaceholder="Search by material..."
            initialPageSize={15}
          />
        )
      ) : loading ? (
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
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">Record a purchase to add chemicals to stock.</p>
          {isAdmin && (
            <Button variant="primary" size="sm" icon={Plus} onClick={() => setShowPurchaseModal(true)} className="mt-4">
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

      {activeTab === 'chemicals' && !loading && !error && coverage && (
        <>
          {(coverage.open_pis_without_plan?.length ?? 0) > 0 && (
            <div role="alert" className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-2">
              <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
                {coverage.open_pis_without_plan.length} open PIs lack an active formulation
              </p>
              <ul className="text-xs text-amber-700 dark:text-amber-400 list-disc pl-5">
                {coverage.open_pis_without_plan.map((pi) => (
                  <li key={pi.sale_id}>{pi.pi_number} ({(pi.products || []).join(', ')})</li>
                ))}
              </ul>
              <p className="text-xs text-amber-700 dark:text-amber-400">
                Their purchasing forecasts stay incomplete until a demo recipe is activated.
              </p>
            </div>
          )}
          {(coverage.incomplete_plans?.length ?? 0) > 0 && (
            <div role="alert" className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-4 space-y-2">
              <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">Active plans are incomplete</p>
              <ul className="text-xs text-amber-700 dark:text-amber-400 list-disc pl-5">
                {coverage.incomplete_plans.map((pi) => (
                  <li key={pi.sale_id}>{pi.pi_number} ({(pi.products || []).join(', ')})</li>
                ))}
              </ul>
            </div>
          )}
          {coverage.state === 'no_active_plans' && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              No active PI plans — projected quantities are unknown until a demo recipe is activated.
            </p>
          )}
        </>
      )}

      {activeTab === 'chemicals' && !loading && !error && unstocked.length > 0 && (
        <details
          open={unstockedOpen}
          className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-4"
        >
          <summary
            onClick={(e) => { e.preventDefault(); setUnstockedOpen((o) => !o); }}
            className="text-sm font-semibold text-slate-800 dark:text-white cursor-pointer"
          >
            Unstocked requirements ({unstocked.length})
          </summary>
          <ul className="mt-3 space-y-2">
            {unstocked.map((u) => (
              <li key={u.name} className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
                <span className="font-medium text-slate-900 dark:text-white">{u.name}</span>
                <span>Required: {u.required_quantity == null ? '—' : `${formatNumber(u.required_quantity)} ${u.unit}`}</span>
                <span>Projected purchase: {u.projected_quantity == null ? '—' : `${formatNumber(u.projected_quantity)} ${u.unit}`}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {activeTab === 'chemicals' && isAdmin && !loading && !error && chemicals.length > 0 && (
        <StockHistory chemicals={chemicals} />
      )}

      {activeTab === 'chemicals' && isAdmin && !loading && !error && (
        <PurchaseHistory onChanged={loadChemicals} />
      )}

      <ChemicalPurchaseModal
        isOpen={showPurchaseModal}
        onClose={() => setShowPurchaseModal(false)}
        onSuccess={loadChemicals}
      />

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
                <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">Last Purchase</p>
                <p className="text-xl font-bold text-slate-900 dark:text-white mt-0.5">{selectedChemical.last_purchase_date || '—'}</p>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">New Quantity ({selectedChemical.unit})</label>
              <input aria-label="New quantity" inputMode="decimal" type="number" value={adjustQty} onChange={(e) => setAdjustQty(e.target.value)} className="w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Reason</label>
              <SuggestionInput aria-label="Reason" options={REASON_SUGGESTIONS} value={adjustReason} onChange={(e) => setAdjustReason(e.target.value)} maxLength={120} className={fieldCls} />
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
              ['Current Stock', `${formatNumber(selectedChemical.qty)} ${selectedChemical.unit}`],
              ['Last Purchase Date', selectedChemical.last_purchase_date || '—'],
              ['Supplier Name', selectedChemical.supplier_name || '—'],
              ['Status', getStockStatus(selectedChemical) === 'matched' ? 'Reconciled' : getStockStatus(selectedChemical) === 'low' ? 'Low Stock' : 'Out of Stock'],
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
                <input aria-label="Low-stock alarm" inputMode="numeric" type="number" min="0" value={alarmValue} onChange={(e) => setAlarmValue(e.target.value)} placeholder="0" className="w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500" />
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
