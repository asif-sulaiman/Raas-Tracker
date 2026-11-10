import { useState, useEffect, useCallback } from 'react';
import { ReceiptText, ChevronDown, ChevronUp, ChevronLeft, ChevronRight, Eye, Pencil, Ban } from 'lucide-react';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import Modal from '../modals/Modal';
import SuggestionInput from '../ui/SuggestionInput';
import { useAuth } from '../../context/AuthContext';
import { formatNumber } from '../../utils/format';
import { toast } from 'sonner';

const PER_PAGE = 10;

const fieldCls =
  'w-full px-3 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500';
const labelCls = 'block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5';
const viewBtnCls =
  'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 transition-colors cursor-pointer';
const editBtnCls =
  'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/40 dark:text-blue-400 dark:hover:bg-blue-900/60 transition-colors cursor-pointer';
const cancelBtnCls =
  'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-rose-50 text-rose-700 hover:bg-rose-100 dark:bg-rose-950/40 dark:text-rose-400 dark:hover:bg-rose-900/60 transition-colors cursor-pointer';

const CORRECTION_REASONS = ['Quantity typo', 'Supplier invoice correction', 'Delivery shortfall'];
const CANCEL_REASONS = ['Duplicate entry', 'Supplier credit note', 'Incorrect client', 'Entered in error'];

const EMPTY_PAGE = { purchases: [], total: 0, page: 1, per_page: PER_PAGE };

/**
 * Admin receipt register, distinct from stock movement history. Lists complete
 * purchases (active and cancelled) with server-side pagination, a read-only
 * detail, and admin-only correction/cancellation under an optimistic version
 * lock. onChanged lets the parent refresh stock after an in-house mutation.
 */
export default function PurchaseHistory({ onChanged }) {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [open, setOpen] = useState(true);
  const [page, setPage] = useState(1);
  const [data, setData] = useState(EMPTY_PAGE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [viewPurchase, setViewPurchase] = useState(null);
  const [editPurchase, setEditPurchase] = useState(null);
  const [editQuantities, setEditQuantities] = useState({});
  const [editReason, setEditReason] = useState('');
  const [editSaving, setEditSaving] = useState(false);
  const [cancelPurchase, setCancelPurchase] = useState(null);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelSaving, setCancelSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/chemical-purchases?per_page=${PER_PAGE}&include_cancelled=true&page=${page}`);
      const body = await res.json();
      setData({
        purchases: Array.isArray(body.purchases) ? body.purchases : [],
        total: Number(body.total) || 0,
        page: Number(body.page) || page,
        per_page: Number(body.per_page) || PER_PAGE,
      });
    } catch (err) {
      setError(err.message || 'Could not load purchase history');
      setData(EMPTY_PAGE);
    } finally {
      setLoading(false);
    }
  }, [apiFetch, page]);

  useEffect(() => {
    // Fetch-on-mount / page-change syncs an external system. load() flips loading
    // up front for retries and post-mutation refreshes and wraps the fetch in
    // try/catch; oxlint's set-state-in-effect flags that residual, suppressed here
    // exactly as StockHistory does.
    // oxlint-disable-next-line react/set-state-in-effect
    load();
  }, [load]);

  const totalPages = Math.max(1, Math.ceil(data.total / data.per_page));

  const openEdit = (p) => {
    setEditPurchase(p);
    setEditQuantities(Object.fromEntries((p.items || []).map((i) => [i.id, String(i.quantity)])));
    setEditReason('');
  };

  const openCancel = (p) => {
    setCancelPurchase(p);
    setCancelReason('');
  };

  const handleSaveEdit = async () => {
    if (editSaving || !editPurchase) return;
    const reason = editReason.trim();
    if (!reason) { toast.error('Enter a correction reason'); return; }
    const patches = [];
    for (const i of editPurchase.items || []) {
      const raw = editQuantities[i.id];
      const q = Number(raw);
      if (raw === '' || raw == null || !Number.isFinite(q) || q <= 0) {
        toast.error(`Enter a quantity greater than 0 for ${i.chemical_name}`);
        return;
      }
      if (Number(i.quantity) === q) continue;
      patches.push({ id: i.id, quantity: q });
    }
    setEditSaving(true);
    try {
      await apiFetch(`/api/chemical-purchases/${editPurchase.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_version: editPurchase.version, reason, line_patches: patches }),
      });
      toast.success(`Purchase ${editPurchase.purchase_number} corrected`);
      setEditPurchase(null);
      load();
      onChanged?.();
    } catch (err) {
      toast.error(err.message || 'Could not save the correction');
    } finally {
      setEditSaving(false);
    }
  };

  const handleConfirmCancel = async () => {
    if (cancelSaving || !cancelPurchase) return;
    const reason = cancelReason.trim();
    if (!reason) { toast.error('Enter a cancellation reason'); return; }
    setCancelSaving(true);
    try {
      await apiFetch(`/api/chemical-purchases/${cancelPurchase.id}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_version: cancelPurchase.version, reason }),
      });
      toast.success(`Purchase ${cancelPurchase.purchase_number} cancelled`);
      setCancelPurchase(null);
      load();
      onChanged?.();
    } catch (err) {
      toast.error(err.message || 'Could not cancel the purchase');
    } finally {
      setCancelSaving(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
      <div className="flex items-center gap-2 mb-1">
        <ReceiptText className="h-4 w-4 text-blue-600 dark:text-blue-400" />
        <h3 className="text-base font-semibold text-slate-900 dark:text-white">Purchase History</h3>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? 'Collapse purchase history' : 'Expand purchase history'}
          className="ml-auto rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-600 dark:hover:text-slate-200 transition-colors cursor-pointer"
        >
          {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
        Posted receipts — corrections and cancellations apply only the stock difference.
      </p>

      {open && (loading ? (
        <div className="py-8 text-center text-xs text-slate-500 dark:text-slate-400">
          <div className="inline-flex items-center gap-2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />
            Loading purchases…
          </div>
        </div>
      ) : error ? (
        <div className="py-6 text-center">
          <p className="text-xs text-rose-600 dark:text-rose-400 mb-3">{error}</p>
          <Button variant="secondary" size="sm" onClick={load}>Retry</Button>
        </div>
      ) : data.purchases.length === 0 ? (
        <p className="py-8 text-center text-xs text-slate-500 dark:text-slate-400">No purchases recorded yet.</p>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200/80 dark:border-slate-800">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50/80 dark:bg-slate-800/60 border-b border-slate-200/80 dark:border-slate-800 text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wider">
                <tr>
                  {['Date', 'Purchase #', 'Supplier', 'Type', 'Client', 'Materials', 'Status', 'Actions'].map((h) => (
                    <th key={h} className="px-4 py-3">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
                {data.purchases.map((p) => (
                  <tr key={p.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200 whitespace-nowrap">{p.purchase_date || '—'}</td>
                    <td className="px-4 py-3 font-medium text-slate-900 dark:text-white whitespace-nowrap">{p.purchase_number}</td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{p.supplier_name}</td>
                    <td className="px-4 py-3">
                      <Badge variant={p.delivery_type === 'outward' ? 'warning' : 'info'}>
                        {p.delivery_type === 'outward' ? 'Outward' : 'In house'}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200">{p.client_name || '—'}</td>
                    <td className="px-4 py-3 text-slate-700 dark:text-slate-200" title={(p.items || []).map((i) => i.chemical_name).join(', ')}>
                      {(p.items || []).length}
                    </td>
                    <td className="px-4 py-3">
                      {p.cancelled ? <Badge variant="default">Cancelled</Badge> : <Badge variant="success">Active</Badge>}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <button type="button" onClick={() => setViewPurchase(p)} className={viewBtnCls}>
                          <Eye className="h-3 w-3" /> View
                        </button>
                        {isAdmin && !p.cancelled && (
                          <button type="button" onClick={() => openEdit(p)} className={editBtnCls}>
                            <Pencil className="h-3 w-3" /> Edit
                          </button>
                        )}
                        {isAdmin && !p.cancelled && (
                          <button type="button" onClick={() => openCancel(p)} className={cancelBtnCls}>
                            <Ban className="h-3 w-3" /> Cancel
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pt-4 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-slate-500 dark:text-slate-400">
            <span>Page {data.page} of {totalPages} · {data.total} purchase{data.total === 1 ? '' : 's'}</span>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" icon={ChevronLeft} onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || loading}>Previous</Button>
              <Button variant="secondary" size="sm" icon={ChevronRight} onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages || loading}>Next</Button>
            </div>
          </div>
        </>
      ))}

      <Modal
        isOpen={!!viewPurchase}
        onClose={() => setViewPurchase(null)}
        title="Purchase Details"
        subtitle={viewPurchase?.purchase_number || ''}
      >
        {viewPurchase && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-xs">
              {[
                ['Supplier', viewPurchase.supplier_name],
                ['Date', viewPurchase.purchase_date],
                ['Type', viewPurchase.delivery_type === 'outward' ? 'Outward' : 'In house'],
                ['Client', viewPurchase.client_name || '—'],
                ['Status', viewPurchase.cancelled ? 'Cancelled' : 'Active'],
                ['Version', viewPurchase.version],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
                  <span className="text-slate-500 dark:text-slate-400 block mb-0.5">{label}</span>
                  <span className="font-semibold text-slate-800 dark:text-white">{value}</span>
                </div>
              ))}
            </div>
            {viewPurchase.cancelled && viewPurchase.cancellation_reason && (
              <p className="text-xs text-rose-600 dark:text-rose-400">Cancelled: {viewPurchase.cancellation_reason}</p>
            )}
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50/80 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wider">
                  <tr>
                    <th className="px-3 py-2">Material</th>
                    <th className="px-3 py-2">Quantity</th>
                    <th className="px-3 py-2">Unit</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
                  {(viewPurchase.items || []).map((i) => (
                    <tr key={i.id}>
                      <td className="px-3 py-2 font-medium text-slate-800 dark:text-white">{i.chemical_name}</td>
                      <td className="px-3 py-2 font-mono text-slate-700 dark:text-slate-200">{formatNumber(i.quantity)}</td>
                      <td className="px-3 py-2 text-slate-700 dark:text-slate-200">{i.unit}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Modal>

      <Modal
        isOpen={!!editPurchase}
        onClose={() => { if (!editSaving) setEditPurchase(null); }}
        title="Correct Purchase"
        subtitle={editPurchase?.purchase_number || ''}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditPurchase(null)} disabled={editSaving}>Cancel</Button>
            <Button variant="primary" onClick={handleSaveEdit} loading={editSaving} disabled={editSaving}>Save Correction</Button>
          </>
        }
      >
        {editPurchase && (
          <div className="space-y-4">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Adjust the posted quantity per material. In-house stock moves by the difference only.
            </p>
            {(editPurchase.items || []).map((i) => (
              <div key={i.id}>
                <label className={labelCls}>{i.chemical_name} ({i.unit})</label>
                <input
                  aria-label={`Quantity for ${i.chemical_name}`}
                  type="number"
                  min="0"
                  step="any"
                  value={editQuantities[i.id] ?? ''}
                  onChange={(e) => setEditQuantities((q) => ({ ...q, [i.id]: e.target.value }))}
                  className={fieldCls}
                />
              </div>
            ))}
            <div>
              <label className={labelCls}>Correction reason</label>
              <SuggestionInput aria-label="Correction reason" options={CORRECTION_REASONS} value={editReason} onChange={(e) => setEditReason(e.target.value)} maxLength={200} className={fieldCls} />
            </div>
          </div>
        )}
      </Modal>

      <Modal
        isOpen={!!cancelPurchase}
        onClose={() => { if (!cancelSaving) setCancelPurchase(null); }}
        title="Cancel Purchase"
        subtitle={cancelPurchase?.purchase_number || ''}
        footer={
          <>
            <Button variant="secondary" onClick={() => setCancelPurchase(null)} disabled={cancelSaving}>Keep Purchase</Button>
            <Button variant="danger" onClick={handleConfirmCancel} loading={cancelSaving} disabled={cancelSaving}>Cancel Purchase</Button>
          </>
        }
      >
        {cancelPurchase && (
          <div className="space-y-4">
            <div className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-3">
              <p className="text-xs text-rose-700 dark:text-rose-300">
                {cancelPurchase.delivery_type === 'in_house'
                  ? "Cancelling reverses each material's posted quantity from in-house stock. This cannot be undone."
                  : 'Cancelling voids this outward receipt. It never affected in-house stock.'}
              </p>
            </div>
            <div>
              <label className={labelCls}>Cancellation reason</label>
              <SuggestionInput aria-label="Cancellation reason" options={CANCEL_REASONS} value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} maxLength={200} className={fieldCls} />
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}