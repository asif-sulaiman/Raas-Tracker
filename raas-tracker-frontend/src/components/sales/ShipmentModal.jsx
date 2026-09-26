import { useState } from 'react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function today() {
  return new Date().toISOString().slice(0, 10);
}

export default function ShipmentModal({ isOpen, saleId, onClose, onSaved }) {
  const { apiFetch } = useAuth();
  const [shipDate, setShipDate] = useState(today());
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [invoiceDate, setInvoiceDate] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const resetAndClose = () => {
    setShipDate(today());
    setInvoiceNumber('');
    setInvoiceDate('');
    setNotes('');
    setError(null);
    onClose?.();
  };

  const handleSave = async () => {
    setError(null);
    if (!shipDate) {
      setError('Ship date is required');
      return;
    }
    setSaving(true);
    try {
      await apiFetch(`/api/sales/${saleId}/shipments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ship_date: shipDate,
          invoice_number: invoiceNumber.trim() || null,
          invoice_date: invoiceDate || null,
          notes: notes.trim() || null,
        }),
      });
      toast.success('Shipment recorded');
      onSaved?.();
      resetAndClose();
    } catch (err) {
      setError(err.message || 'Could not record shipment');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={resetAndClose}
      title="Record Shipment"
      subtitle="Actual shipment event — partial shipments get one row each"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={resetAndClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={handleSave} loading={saving} disabled={saving}>
            Save Shipment
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Ship date *</label>
            <input aria-label="Ship date" type="date" value={shipDate} onChange={(e) => setShipDate(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Invoice date</label>
            <input aria-label="Invoice date" type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} className={inputCls} />
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Invoice number</label>
          <input aria-label="Invoice number" type="text" value={invoiceNumber} onChange={(e) => setInvoiceNumber(e.target.value)} placeholder="INV-2026-001" className={inputCls} />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Notes</label>
          <input aria-label="Notes" type="text" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="partial 1/2, short-shipment reason…" className={inputCls} />
        </div>
        {error && (
          <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
        )}
      </div>
    </Modal>
  );
}
