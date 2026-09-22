import { useState, useEffect } from 'react';
import { Plus, Trash2, AlertTriangle, Loader2 } from 'lucide-react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { formatNumber, sumLineTotals, lineTotal } from '../../utils/format';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function ReviewModal({ isOpen, initialData, onClose, onSave }) {
  const [piNumber, setPiNumber] = useState('');
  const [piDate, setPiDate] = useState('');
  const [clientName, setClientName] = useState('');
  const [items, setItems] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen && initialData) {
      setPiNumber(initialData.pi_number || '');
      setPiDate(initialData.pi_date || '');
      setClientName(initialData.client_name || '');
      setItems(
        (initialData.items || []).map((i, idx) => ({
          key: `${Date.now()}-${idx}`,
          product_name: i.product_name || '',
          quantity: i.quantity ?? 0,
          unit_price: i.unit_price ?? 0,
        }))
      );
      setWarnings(initialData.warnings || []);
      setError(null);
      setSaving(false);
    }
  }, [isOpen, initialData]);

  const updateItem = (key, field, value) => {
    setItems((prev) => prev.map((i) => (i.key === key ? { ...i, [field]: value } : i)));
  };

  const removeItem = (key) => {
    setItems((prev) => prev.filter((i) => i.key !== key));
  };

  const addItem = () => {
    setItems((prev) => [
      ...prev,
      { key: `${Date.now()}-${prev.length}`, product_name: '', quantity: 0, unit_price: 0 },
    ]);
  };

  const totalValue = sumLineTotals(items);

  const handleSave = async () => {
    setError(null);
    if (!piNumber.trim()) {
      setError('PI number is required');
      return;
    }
    const cleanItems = items
      .filter((i) => i.product_name.trim())
      .map((i) => ({
        product_name: i.product_name.trim(),
        quantity: parseFloat(i.quantity) || 0,
        unit_price: parseFloat(i.unit_price) || 0,
      }));
    if (cleanItems.length === 0) {
      setError('At least one product item is required');
      return;
    }
    setSaving(true);
    try {
      await onSave?.({
        sale: { pi_number: piNumber.trim(), pi_date: piDate || null, client_name: clientName.trim() || null },
        items: cleanItems,
      });
    } catch {
      setError('Failed to save sale');
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Review & Confirm PI"
      subtitle="Verify extracted data before saving to the pipeline"
      maxWidth="max-w-3xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="success" size="sm" icon={Loader2} loading={saving} onClick={handleSave}>
            Save to PI Issued
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {warnings.length > 0 && (
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5" /> Extraction warnings — please verify
            </p>
            <ul className="mt-1.5 space-y-0.5 text-xs text-amber-600 dark:text-amber-400 list-disc list-inside">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">PI Number *</label>
            <input value={piNumber} onChange={(e) => setPiNumber(e.target.value)} className={inputCls} placeholder="PI-2026-001" />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">PI Date</label>
            <input type="date" value={piDate || ''} onChange={(e) => setPiDate(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Client Name</label>
            <input value={clientName} onChange={(e) => setClientName(e.target.value)} className={inputCls} placeholder="Client company" />
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-slate-800 dark:text-white">
              Products ({items.length})
            </h4>
            <Button variant="secondary" size="sm" icon={Plus} onClick={addItem}>
              Add Row
            </Button>
          </div>

          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                <tr>
                  <th className="px-2.5 py-2">Product</th>
                  <th className="px-2.5 py-2 w-24">Qty</th>
                  <th className="px-2.5 py-2 w-28">Unit Price ($)</th>
                  <th className="px-2.5 py-2 w-24 text-right">Total</th>
                  <th className="px-2.5 py-2 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-2.5 py-4 text-center text-slate-400">
                      No items — click “Add Row” to add products manually
                    </td>
                  </tr>
                ) : (
                  items.map((item) => (
                    <tr key={item.key}>
                      <td className="px-2.5 py-1.5">
                        <input
                          value={item.product_name}
                          onChange={(e) => updateItem(item.key, 'product_name', e.target.value)}
                          className={inputCls}
                          placeholder="Product name"
                        />
                      </td>
                      <td className="px-2.5 py-1.5">
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={item.quantity}
                          onChange={(e) => updateItem(item.key, 'quantity', e.target.value)}
                          className={inputCls}
                        />
                      </td>
                      <td className="px-2.5 py-1.5">
                        <input
                          type="number"
                          min="0"
                          step="any"
                          value={item.unit_price}
                          onChange={(e) => updateItem(item.key, 'unit_price', e.target.value)}
                          className={inputCls}
                        />
                      </td>
                      <td className="px-2.5 py-1.5 text-right font-semibold text-slate-800 dark:text-slate-100">
                        ${formatNumber(lineTotal(item.quantity, item.unit_price))}
                      </td>
                      <td className="px-2.5 py-1.5">
                        <button
                          onClick={() => removeItem(item.key)}
                          className="p-1.5 rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 cursor-pointer"
                          title="Remove row"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
              {items.length > 0 && (
                <tfoot className="bg-slate-50 dark:bg-slate-800/60">
                  <tr>
                    <td colSpan={3} className="px-2.5 py-2 text-right text-xs font-semibold text-slate-600 dark:text-slate-300">
                      Total Value (USD)
                    </td>
                    <td className="px-2.5 py-2 text-right text-sm font-bold text-emerald-600 dark:text-emerald-400">
                      ${formatNumber(totalValue)}
                    </td>
                    <td />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        </div>

        {error && (
          <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
        )}
      </div>
    </Modal>
  );
}
