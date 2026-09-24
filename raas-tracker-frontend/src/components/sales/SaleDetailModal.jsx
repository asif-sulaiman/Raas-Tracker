import { useState, useEffect } from 'react';
import { Pencil, Trash2, Check, X, Plus, Loader2 } from 'lucide-react';
import Modal from '../modals/Modal';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { formatNumber, formatDate, formatDateTime, sumLineTotals, lineTotal } from '../../utils/format';
import { STAGE_LABELS, STAGE_BADGE } from '../../utils/sales';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { toast } from 'sonner';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function Field({ label, value, mono }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wider text-slate-400 dark:text-slate-500">{label}</p>
      <p className={`text-sm font-medium text-slate-900 dark:text-white mt-0.5 ${mono ? 'font-mono' : ''}`}>
        {value || '—'}
      </p>
    </div>
  );
}

export default function SaleDetailModal({ saleId, onClose, onSaved }) {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const [sale, setSale] = useState(null);
  const [loading, setLoading] = useState(false);
  const [editingPaymentId, setEditingPaymentId] = useState(null);
  const [editPayDate, setEditPayDate] = useState('');
  const [editPayAmount, setEditPayAmount] = useState('');
  const [editing, setEditing] = useState(false);
  const [editHeader, setEditHeader] = useState({ pi_number: '', pi_date: '', client_name: '' });
  const [editItems, setEditItems] = useState([]);
  const [removedIds, setRemovedIds] = useState([]);
  const [savingSale, setSavingSale] = useState(false);
  const [saleError, setSaleError] = useState(null);

  const refresh = async (id) => {
    try {
      const res = await apiFetch(`/api/sales/${id}`);
      const data = await res.json();
      setSale(data.error ? null : data);
    } catch {
      setSale(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (saleId) {
      setLoading(true);
      setEditingPaymentId(null);
      setEditing(false);
      setSaleError(null);
      refresh(saleId);
    } else {
      setSale(null);
    }
  }, [saleId]);

  const startEditing = () => {
    if (!sale) return;
    setEditHeader({
      pi_number: sale.pi_number || '',
      pi_date: sale.pi_date || '',
      client_name: sale.client_name || '',
    });
    setEditItems(sale.items.map((i) => ({
      key: `db-${i.id}`, id: i.id,
      product_name: i.product_name, quantity: i.quantity, unit_price: i.unit_price,
    })));
    setRemovedIds([]);
    setSaleError(null);
    setEditing(true);
  };

  const updateEditItem = (key, field, value) => {
    setEditItems((prev) => prev.map((i) => (i.key === key ? { ...i, [field]: value } : i)));
  };

  const removeEditItem = (key) => {
    if (editItems.length <= 1) {
      setSaleError('A sale must keep at least one product item.');
      return;
    }
    const target = editItems.find((i) => i.key === key);
    if (target?.id) setRemovedIds((prev) => [...prev, target.id]);
    setEditItems((prev) => prev.filter((i) => i.key !== key));
  };

  const handleSaveSale = async () => {
    setSaleError(null);
    if (!editHeader.pi_number.trim()) {
      setSaleError('PI number is required');
      return;
    }
    const clean = editItems.filter((i) => i.product_name.trim());
    if (clean.length === 0) {
      setSaleError('At least one product item is required');
      return;
    }
    for (const i of clean) {
      if ((parseFloat(i.quantity) || 0) < 0 || (parseFloat(i.unit_price) || 0) < 0) {
        setSaleError('Quantity and price cannot be negative');
        return;
      }
    }
    setSavingSale(true);
    try {
      // Single atomic request: header + items + removals commit together or not at all.
      const res = await apiFetch(`/api/sales/${saleId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          header: {
            pi_number: editHeader.pi_number.trim(),
            pi_date: editHeader.pi_date || null,
            client_name: editHeader.client_name.trim() || null,
          },
          items: clean.map((i) => ({
            ...(i.id != null ? { id: i.id } : {}),
            product_name: i.product_name.trim(),
            quantity: parseFloat(i.quantity) || 0,
            unit_price: parseFloat(i.unit_price) || 0,
          })),
          removedIds,
        }),
      });
      const saved = await res.json();
      setSale(saved.error ? sale : saved);
      setEditing(false);
      toast.success('Sale saved');
      onSaved?.();
    } catch (err) {
      const detail = err.fields?.map((d) => `${d.field}: ${d.message}`).join('; ');
      setSaleError(detail || err.message || 'Failed to save changes');
    } finally {
      setSavingSale(false);
    }
  };

  const handleDeletePayment = async (paymentId) => {
    const ok = await confirm({
      title: 'Delete this payment record?',
      message: 'Totals will be recalculated.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/sales/${saleId}/payments/${paymentId}`, { method: 'DELETE' });
      toast.success('Payment deleted');
      refresh(saleId);
      onSaved?.();
    } catch (err) {
      toast.error(err.message || 'Could not delete payment');
    }
  };

  const handleSavePaymentEdit = async (paymentId) => {
    const amount = parseFloat(editPayAmount);
    if (!amount || amount <= 0) {
      toast.error('Enter a valid amount');
      return;
    }
    try {
      await apiFetch(`/api/sales/${saleId}/payments/${paymentId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payment_date: editPayDate || null, payment_amount: amount }),
      });
      setEditingPaymentId(null);
      toast.success('Payment updated');
      refresh(saleId);
      onSaved?.();
    } catch (err) {
      toast.error(err.message || 'Could not update payment');
    }
  };

  const total = sumLineTotals(sale?.items);

  return (
    <Modal
      isOpen={!!saleId}
      onClose={onClose}
      title={sale ? `${sale.pi_number || `#${sale.id}`} — ${sale.client_name || 'Unknown client'}` : 'Sale Details'}
      subtitle={sale ? `Stage: ${STAGE_LABELS[sale.stage] || sale.stage}` : ''}
      maxWidth="max-w-2xl"
      footer={
        sale && !loading ? (
          editing ? (
            <>
              <Button variant="secondary" size="sm" onClick={() => setEditing(false)} disabled={savingSale}>
                Cancel
              </Button>
              <Button variant="success" size="sm" icon={Loader2} loading={savingSale} onClick={handleSaveSale}>
                Save Changes
              </Button>
            </>
          ) : (
            <Button variant="secondary" size="sm" icon={Pencil} onClick={startEditing}>
              Edit Sale
            </Button>
          )
        ) : null
      }
    >
      {loading ? (
        <p className="text-sm text-slate-500 py-6 text-center">Loading…</p>
      ) : !sale ? (
        <p className="text-sm text-rose-500 py-6 text-center">Sale not found</p>
      ) : (
        <div className="space-y-5">
          <div className="flex items-center gap-2">
            <Badge variant={STAGE_BADGE[sale.stage] || 'default'}>
              {STAGE_LABELS[sale.stage] || sale.stage}
            </Badge>
            <span className="text-xs text-slate-400">
              Created {formatDateTime(sale.created_at)}
            </span>
          </div>

          {editing ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">PI Number *</label>
                <input value={editHeader.pi_number} onChange={(e) => setEditHeader({ ...editHeader, pi_number: e.target.value })} className={inputCls} />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">PI Date</label>
                <input type="date" value={editHeader.pi_date || ''} onChange={(e) => setEditHeader({ ...editHeader, pi_date: e.target.value })} className={inputCls} />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Client Name</label>
                <input value={editHeader.client_name} onChange={(e) => setEditHeader({ ...editHeader, client_name: e.target.value })} className={inputCls} />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Field label="PI Date" value={formatDate(sale.pi_date)} />
              <Field label="LC Number" value={sale.lc_number} mono />
              <Field label="LC Date" value={formatDate(sale.lc_date)} />
              <Field label="Shipment Date" value={formatDate(sale.shipment_date)} />
              <Field label="Payment Date" value={formatDate(sale.payment_date)} />
              <Field label="Payment (USD)" value={sale.payment_amount ? `$${formatNumber(sale.payment_amount)}` : null} />
            </div>
          )}

          <div>
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-sm font-semibold text-slate-800 dark:text-white">
                Products ({editing ? editItems.length : sale.items.length}) — ${formatNumber(total)}
              </h4>
              {editing && (
                <Button
                  variant="secondary"
                  size="sm"
                  icon={Plus}
                  onClick={() => setEditItems((prev) => [...prev, { key: `new-${Date.now()}`, id: null, product_name: '', quantity: 0, unit_price: 0 }])}
                >
                  Add Row
                </Button>
              )}
            </div>
            <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 uppercase tracking-wider">
                  <tr>
                    <th className="px-2.5 py-2">Product</th>
                    <th className="px-2.5 py-2 text-right">Qty</th>
                    <th className="px-2.5 py-2 text-right">Unit Price</th>
                    <th className="px-2.5 py-2 text-right">Total</th>
                    {editing && <th className="px-2.5 py-2 w-10"></th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {editing ? (
                    editItems.map((i) => (
                      <tr key={i.key}>
                        <td className="px-2.5 py-1.5">
                          <input value={i.product_name} onChange={(e) => updateEditItem(i.key, 'product_name', e.target.value)} className={inputCls} />
                        </td>
                        <td className="px-2.5 py-1.5">
                          <input type="number" min="0" step="any" value={i.quantity} onChange={(e) => updateEditItem(i.key, 'quantity', e.target.value)} className={inputCls} />
                        </td>
                        <td className="px-2.5 py-1.5">
                          <input type="number" min="0" step="any" value={i.unit_price} onChange={(e) => updateEditItem(i.key, 'unit_price', e.target.value)} className={inputCls} />
                        </td>
                        <td className="px-2.5 py-1.5 text-right font-semibold">
                          ${formatNumber(lineTotal(i.quantity, i.unit_price))}
                        </td>
                        <td className="px-2.5 py-1.5">
                          <button onClick={() => removeEditItem(i.key)} className="p-1 text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 rounded-lg cursor-pointer" title="Remove row">
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    sale.items.map((i) => (
                      <tr key={i.id}>
                        <td className="px-2.5 py-2 font-medium text-slate-900 dark:text-white">{i.product_name}</td>
                        <td className="px-2.5 py-2 text-right">{formatNumber(i.quantity)}</td>
                        <td className="px-2.5 py-2 text-right">${formatNumber(i.unit_price)}</td>
                        <td className="px-2.5 py-2 text-right font-semibold">
                          ${formatNumber((i.quantity || 0) * (i.unit_price || 0))}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          {editing && saleError && (
            <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{saleError}</p>
          )}

          {(sale.payments?.length ?? 0) > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-slate-800 dark:text-white mb-2">
                Payments — paid ${formatNumber(sale.total_paid || 0)} of ${formatNumber(sale.invoice_total || 0)}
                {(sale.balance ?? 0) > 0 && (
                  <span className="ml-2 text-xs font-medium text-amber-600 dark:text-amber-400">
                    (balance ${formatNumber(sale.balance)})
                  </span>
                )}
              </h4>
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-800 text-xs">
                {sale.payments.map((p) => (
                  <div key={p.id} className="flex items-center gap-2 px-2.5 py-1.5">
                    {editingPaymentId === p.id ? (
                      <>
                        <input
                          type="date"
                          value={editPayDate}
                          onChange={(e) => setEditPayDate(e.target.value)}
                          className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs"
                        />
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={editPayAmount}
                          onChange={(e) => setEditPayAmount(e.target.value)}
                          className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs w-24"
                        />
                        <button onClick={() => handleSavePaymentEdit(p.id)} className="p-1 text-emerald-600 cursor-pointer" title="Save">
                          <Check className="h-4 w-4" />
                        </button>
                        <button onClick={() => setEditingPaymentId(null)} className="p-1 text-slate-400 cursor-pointer" title="Cancel">
                          <X className="h-4 w-4" />
                        </button>
                      </>
                    ) : (
                      <>
                        <span className="text-slate-500 dark:text-slate-400">{formatDate(p.payment_date)}</span>
                        <span className="font-semibold text-slate-800 dark:text-slate-100">${formatNumber(p.payment_amount)}</span>
                        {p.notes && <span className="text-slate-400 truncate flex-1">{p.notes}</span>}
                        <span className="flex-1" />
                        <button
                          onClick={() => { setEditingPaymentId(p.id); setEditPayDate(p.payment_date || ''); setEditPayAmount(p.payment_amount); }}
                          className="p-1 text-slate-400 hover:text-blue-600 cursor-pointer"
                          title="Edit payment"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        {isAdmin && (
                          <button onClick={() => handleDeletePayment(p.id)} className="p-1 text-slate-400 hover:text-rose-600 cursor-pointer" title="Delete payment">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {sale.history && sale.history.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-slate-800 dark:text-white mb-2">Stage History</h4>
              <ol className="relative border-l border-slate-200 dark:border-slate-700 ml-1.5 space-y-3">
                {sale.history.map((h) => (
                  <li key={h.id} className="ml-4">
                    <span className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full bg-blue-500 border-2 border-white dark:border-slate-900" />
                    <p className="text-xs font-medium text-slate-800 dark:text-slate-200">
                      {h.from_stage ? `${STAGE_LABELS[h.from_stage] || h.from_stage} → ` : 'Created in '}
                      <span className="font-bold">{STAGE_LABELS[h.to_stage] || h.to_stage}</span>
                    </p>
                    <p className="text-[11px] text-slate-400">
                      {formatDateTime(h.changed_at)}{h.notes ? ` • ${h.notes}` : ''}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
