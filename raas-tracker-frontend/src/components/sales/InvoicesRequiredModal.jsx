import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { Plus } from 'lucide-react';

/**
 * Shipment barrier: an LC needs >= 1 invoice before moving to
 * Shipment Ongoing (the gate counts invoices across ALL of the LC's sales).
 * One invoice number per PI here — a single invoice or one per PI, so an LC
 * with several PIs can carry several invoices. Splitting ONE PI across
 * several invoices stays in the PI detail view (InvoiceLineEditor), because
 * each create auto-seeds the PI's whole remainder: a second blind create on
 * the same PI would only mint a zero-amount row.
 */
export default function InvoicesRequiredModal({
  isOpen,
  onClose,
  pis = [],
  onSuccess,
}) {
  const { apiFetch } = useAuth();
  const [numbers, setNumbers] = useState({});
  const [rowErrors, setRowErrors] = useState({});
  const [formError, setFormError] = useState(null);
  const [creating, setCreating] = useState(false);

  const rows = (Array.isArray(pis) ? pis : []).filter(
    (p) => p && typeof p.id === 'number',
  );

  const setNumber = (saleId, value) => {
    setNumbers((prev) => ({ ...prev, [saleId]: value }));
    setRowErrors((prev) => {
      if (!prev[saleId]) return prev;
      const next = { ...prev };
      delete next[saleId];
      return next;
    });
  };

  const handleCreate = async () => {
    const filled = rows
      .map((p) => ({ saleId: p.id, invoiceNumber: (numbers[p.id] || '').trim() }))
      .filter((r) => r.invoiceNumber);
    if (filled.length === 0) {
      setFormError('Enter at least one invoice number below.');
      return;
    }
    setCreating(true);
    setFormError(null);
    setRowErrors({});
    try {
      const failures = {};
      for (const row of filled) {
        try {
          await apiFetch(`/api/sales/${row.saleId}/invoices`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ invoice_number: row.invoiceNumber }),
          });
        } catch (err) {
          failures[row.saleId] = err?.message || 'Failed to create invoice';
        }
      }
      if (Object.keys(failures).length > 0) {
        setRowErrors(failures);
        setFormError('Some invoices failed — fix the highlighted rows and retry.');
        return;
      }
      toast.success(
        filled.length === 1 ? 'Invoice created' : `${filled.length} invoices created`,
      );
      if (onSuccess) await onSuccess();
      onClose();
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
        <div className="text-sm text-slate-600 dark:text-slate-400">
          <p className="flex items-center gap-2">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-amber-100 dark:bg-amber-900/50 text-amber-600 dark:text-amber-400">
              <span className="text-[10px] font-bold">!</span>
            </span>
            <span>At least one invoice is required to move this LC to <strong>Shipment Ongoing</strong>.</span>
          </p>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
            Enter one invoice number per PI — a single invoice or one for each PI.
            To split one PI across several invoices, use its detail view afterwards.
          </p>
        </div>

        {rows.length === 0 ? (
          <p className="text-sm text-rose-600 dark:text-rose-400">
            No PIs are linked to this LC, so there is nothing to invoice.
          </p>
        ) : (
          <div className="space-y-3">
            {rows.map((p) => (
              <div key={p.id}>
                <label
                  htmlFor={`invoiceNumber-${p.id}`}
                  className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
                >
                  {p.pi_number || `PI #${p.id}`} — Invoice Number
                </label>
                <input
                  id={`invoiceNumber-${p.id}`}
                  type="text"
                  value={numbers[p.id] || ''}
                  onChange={(e) => setNumber(p.id, e.target.value)}
                  placeholder="e.g. INV-2024-001"
                  className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
                  disabled={creating}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                />
                {rowErrors[p.id] && (
                  <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{rowErrors[p.id]}</p>
                )}
              </div>
            ))}
          </div>
        )}

        {formError && <p className="text-xs text-rose-600 dark:text-rose-400">{formError}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={creating}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleCreate}
            disabled={creating || rows.length === 0}
            loading={creating}
          >
            <span className="flex items-center gap-1.5">
              <Plus className="h-3.5 w-3.5" />
              Create Invoices
            </span>
          </Button>
        </div>
      </div>
    </Modal>
  );
}
