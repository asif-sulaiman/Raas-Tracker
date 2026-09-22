import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function LCModal({ isOpen, sale, onClose, onSaved }) {
  const { apiFetch } = useAuth();
  const [lcNumber, setLcNumber] = useState('');
  const [lcDate, setLcDate] = useState('');
  const [shipmentDate, setShipmentDate] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen && sale) {
      setLcNumber(sale.lc_number || '');
      setLcDate(sale.lc_date || '');
      setShipmentDate(sale.shipment_date || '');
      setError(null);
      setSaving(false);
    }
  }, [isOpen, sale]);

  const handleSave = async () => {
    setError(null);
    if (!lcNumber.trim()) {
      setError('LC number is required');
      return;
    }
    setSaving(true);
    try {
      await apiFetch(`/api/sales/${sale.id}/lc`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lc_number: lcNumber.trim(),
          lc_date: lcDate || null,
          shipment_date: shipmentDate || null,
        }),
      });
      onClose?.();
      onSaved?.();
    } catch (err) {
      setError(err.message || 'Failed to save LC details');
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Enter LC — ${sale?.pi_number || ''}`}
      subtitle={`${sale?.client_name || ''} • moves to LC Received on save`}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" icon={Loader2} loading={saving} onClick={handleSave}>
            Save & Move to LC Received
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <label className="text-xs font-medium text-slate-600 dark:text-slate-300">LC Number *</label>
          <input value={lcNumber} onChange={(e) => setLcNumber(e.target.value)} className={inputCls} placeholder="LC-123456" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">LC Date</label>
            <input type="date" value={lcDate || ''} onChange={(e) => setLcDate(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Latest Shipment Date</label>
            <input type="date" value={shipmentDate || ''} onChange={(e) => setShipmentDate(e.target.value)} className={inputCls} />
          </div>
        </div>
        {error && <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>}
      </div>
    </Modal>
  );
}
