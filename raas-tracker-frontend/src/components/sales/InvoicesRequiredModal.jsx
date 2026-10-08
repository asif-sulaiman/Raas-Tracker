import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { Plus, X, AlertCircle, Loader2 } from 'lucide-react';

export default function InvoicesRequiredModal({
  isOpen,
  onClose,
  lcId,
  saleId,
  onSuccess,
}) {
  const { apiFetch } = useAuth();
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState(null);
  const [invoiceNumber, setInvoiceNumber] = useState('');
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!invoiceNumber.trim()) {
      setFormError('Invoice number is required');
      return;
    }
    setCreating(true);
    setFormError(null);
    try {
      const res = await fetch(`/api/sales/${saleId}/invoices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoice_number: invoiceNumber.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || 'Failed to create invoice');
      }
      toast.success('Invoice created');
      if (onSuccess) onSuccess();
      onClose();
    } catch (err) {
      setFormError(err.message || 'Failed to create invoice');
    } finally {
      setCreating(false);
    }
  };

  if (!isOpen) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Invoices Required"
      description="You must create at least one invoice before moving this LC to Shipment Ongoing."
      size="md"
    >
      <div className="space-y-4">
        <div className="space-y-4">
          <div className="text-sm text-slate-600 dark:text-slate-400">
            <p className="flex items-center gap-2">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-amber-100 dark:bg-amber-900/50 text-amber-600 dark:text-amber-400">
                <span className="text-[10px] font-bold">!</span>
              </span>
              <span>At least one invoice is required to move this LC to <strong>Shipment Ongoing</strong>.</span>
            </p>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              Create an invoice number below. The amount and lines will be derived automatically from the remaining PI quantities.
            </p>
          </div>

          <div className="space-y-3">
            <div>
              <label htmlFor="invoiceNumber" className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                Invoice Number *
              </label>
              <input
                id="invoiceNumber"
                type="text"
                value={invoiceNumber}
                onChange={(e) => setInvoiceNumber(e.target.value)}
                placeholder="e.g. INV-2024-001"
                className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
                disabled={creating}
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              />
              {formError && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{formError}</p>}
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" size="sm" onClick={onClose} disabled={creating}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleCreate}
              disabled={creating || !invoiceNumber.trim()}
              loading={creating}
            >
              <span className="flex items-center gap-1.5">
                <Plus className="h-3.5 w-3.5" />
                Create Invoice
              </span>
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
