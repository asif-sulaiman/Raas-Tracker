import { useState, useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { Plus, Minus, AlertTriangle, Loader2 } from 'lucide-react';
import { formatNumber } from '../../utils/format';

/**
 * Invoice Builder Modal for LC Shipment Barrier
 * 
 * Allows creating one or multiple invoices with explicit line quantities.
 * Quantities can exceed PI quantities (shipment advantage).
 * Unit prices are server-enforced from PI lines.
 */
export default function InvoiceBuilderModal({
  isOpen,
  onClose,
  lcId,
  pis = [],
  onSuccess,
}) {
  const { apiFetch } = useAuth();
  
  // State for each invoice being built
  const [invoices, setInvoices] = useState([{ id: 1, lines: [], number: '' }]);
  const [activeInvoiceIndex, setActiveInvoiceIndex] = useState(0);
  const [formError, setFormError] = useState(null);
  const [creating, setCreating] = useState(false);
  
  // Fetch PI line details for each PI
  const [piLineDetails, setPiLineDetails] = useState({});
  const [loadingDetails, setLoadingDetails] = useState(false);
  
  // Fetch PI line details when modal opens
  useEffect(() => {
    if (!isOpen) return;
    
    const fetchDetails = async () => {
      setLoadingDetails(true);
      try {
        const details = {};
        for (const pi of pis) {
          if (!pi.id) continue;
          const res = await apiFetch(`/api/sales/${pi.id}/invoices`);
          const existingInvoices = await res.json();
          const completionRes = await apiFetch(`/api/sales/${pi.id}/completion`);
          const completion = await completionRes.json();
          
          details[pi.id] = {
            piNumber: pi.pi_number,
            saleId: pi.id,
            lines: completion?.items?.map(item => ({
              saleItemId: item.sale_item_id,
              productName: item.product_name,
              piQuantity: item.pi_quantity || item.quantity || 0,
              invoicedQuantity: item.invoiced_quantity || 0,
              unitPrice: item.unit_price || 0,
              unit: item.unit || 'KG',
              remaining: Math.max(0, (item.pi_quantity || item.quantity || 0) - (item.invoiced_quantity || 0))
            })) || [],
            totalValue: completion?.total_amount || 0,
          };
        }
        setPiLineDetails(details);
      } catch (err) {
        console.error('Failed to fetch PI details:', err);
      } finally {
        setLoadingDetails(false);
      }
    };
    fetchDetails();
  }, [isOpen, pis, apiFetch]);

  const activeInvoice = invoices[activeInvoiceIndex];
  const activePiId = activeInvoice?.piId || (pis[0]?.id);
  const activePiDetails = piLineDetails[activePiId] || { lines: [] };
  const piLines = activePiDetails.lines || [];
  const hasLines = piLines.length > 0;
  const totalAmount = activeInvoice.lines.reduce(
    (sum, line) => sum + (line.quantity * line.unitPrice), 0
  );

  // Invoice lines content (extracted to avoid parser issues with nested ternary)
  const invoiceLinesContent = activeInvoice.lines.length === 0 ? (
    <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-4 text-center text-sm text-slate-500 dark:text-slate-400">
      <p>No lines added yet. Add lines from the PI reference below.</p>
    </div>
  ) : (
    <div className="space-y-2">
      <div className="grid grid-cols-12 gap-2 text-xs text-slate-500 dark:text-slate-400 px-1">
        <div className="col-span-5 font-medium">Product</div>
        <div className="col-span-2 text-right">Qty</div>
        <div className="col-span-2 text-right">Unit Price</div>
        <div className="col-span-2 text-right">Line Total</div>
        <div className="col-span-1"></div>
      </div>
      <div>
        {activeInvoice.lines.map((line, lineIdx) => (
          <div key={line.id} className="grid grid-cols-12 gap-2 items-center px-1">
            <div className="col-span-5 text-sm text-slate-900 dark:text-white truncate">
              {line.productName}
            </div>
            <div className="col-span-2">
              <input
                type="number"
                step="0.001"
                min="0"
                value={line.quantity}
                onChange={(e) => updateLineQuantity(activeInvoiceIndex, lineIdx, e.target.value)}
                className="w-full px-2 py-1 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-right"
              />
            </div>
            <div className="col-span-2 text-right text-sm text-slate-600 dark:text-slate-400 pr-2">
              ${formatNumber(line.unitPrice)}
            </div>
            <div className="col-span-2 text-right text-sm font-medium text-slate-900 dark:text-white pr-2">
              ${formatNumber(line.quantity * line.unitPrice)}
            </div>
            <div className="col-span-1">
              <button
                onClick={() => removeLine(activeInvoiceIndex, lineIdx)}
                className="text-rose-500 hover:text-rose-700 p-1"
                aria-label="Remove line"
              >
                ×
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  // PI Lines Reference content (extracted to avoid parser issues with ternary)
  const piLinesReference = hasLines ? (
    <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
      <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">
        PI Lines (Reference — quantities can exceed these)
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-200 dark:border-slate-700">
              <th className="text-left py-1 px-2 text-slate-500">Product</th>
              <th className="text-right py-1 px-2 text-slate-500">PI Qty</th>
              <th className="text-right py-1 px-2 text-slate-500">Invoiced</th>
              <th className="text-right py-1 px-2 text-slate-500">Remaining</th>
              <th className="text-right py-1 px-2 text-slate-500">Unit Price</th>
            </tr>
          </thead>
          <tbody>
            {piLines.map(line => (
              <tr key={line.saleItemId} className="border-b border-slate-100 dark:border-slate-800">
                <td className="py-1 px-2 text-slate-900 dark:text-white">{line.productName}</td>
                <td className="text-right py-1 px-2 text-slate-600 dark:text-slate-400">
                  {formatNumber(line.piQuantity)} {line.unit}
                </td>
                <td className="text-right py-1 px-2 text-slate-600 dark:text-slate-400">
                  {formatNumber(line.invoicedQuantity)} {line.unit}
                </td>
                <td className="text-right py-1 px-2 text-slate-600 dark:text-slate-400">
                  {formatNumber(line.remaining)} {line.unit}
                </td>
                <td className="text-right py-1 px-2 text-slate-600 dark:text-slate-400">
                  ${formatNumber(line.unitPrice)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  ) : null;

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

  const addInvoice = () => {
    const newInvoice = {
      id: Date.now(),
      lines: [],
      number: '',
      piId: pis[0]?.id,
    };
    setInvoices(prev => [...prev, newInvoice]);
    setActiveInvoiceIndex(prev => prev + 1);
  };

  const removeInvoice = (index) => {
    if (invoices.length <= 1) return;
    setInvoices(prev => prev.filter((_, i) => i !== index));
    setActiveInvoiceIndex(prev => Math.min(prev, invoices.length - 2));
  };

  const addLine = (invoiceIndex, line) => {
    setInvoices(prev => prev.map((inv, i) => 
      i === index ? { ...inv, lines: [...inv.lines, line] } : inv
    ));
  };

  const removeLine = (invoiceIndex, lineIndex) => {
    setInvoices(prev => prev.map((inv, i) => 
      i === index ? { ...inv, lines: inv.lines.filter((_, j) => j !== lineIndex) } : inv
    ));
  };

  const updateLineQuantity = (invoiceIndex, lineIndex, quantity) => {
    setInvoices(prev => prev.map((inv, i) => 
      i === index ? { 
        ...inv, 
        lines: inv.lines.map((line, j) => 
          j === lineIdx ? { ...line, quantity: Math.max(0, parseFloat(quantity) || 0) } : line
        )
      } : inv
    ));
  };

  const handleCreate = async () => {
    const filled = invoices
      .map((inv, idx) => ({
        index: idx,
        saleId: inv.piId,
        invoiceNumber: inv.number.trim(),
        lines: inv.lines
          .filter(l => l.quantity > 0)
          .map(l => ({ sale_item_id: l.saleItemId, quantity: l.quantity }))
      }))
      .filter(inv => inv.invoiceNumber && inv.lines.length > 0);

    if (filled.length === 0) {
      setFormError('Enter at least one invoice number with at least one line.');
      return;
    }
    setCreating(true);
    setFormError(null);
    setRowErrors({});
    try {
      for (const inv of filled) {
        try {
          await apiFetch(`/api/sales/${inv.saleId}/invoices`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
              invoice_number: inv.invoiceNumber,
              lines: inv.lines
            }),
          });
        } catch (err) {
          throw new Error(`Invoice ${inv.invoiceNumber}: ${err?.message || 'Failed'}`);
        }
      }
      
      toast.success(
        filled.length === 1 ? 'Invoice created' : `${filled.length} invoices created`,
      );
      if (onSuccess) await onSuccess();
      onClose();
    } catch (err) {
      setFormError(err.message || 'Failed to create invoices');
    } finally {
      setCreating(false);
    }
  };

  if (!isOpen) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create Invoice(s)"
      description="Build one or more invoices for this LC. Quantities can exceed PI quantities for shipment advantage."
      size="lg"
    >
      <div className="space-y-4">
        {/* LC Info */}
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3 text-sm">
          <p className="font-medium text-slate-900 dark:text-white">
            LC: {lcId} — {pis.length} PI(s) linked
          </p>
        </div>

        {/* Invoice Tabs */}
        {invoices.length > 1 && (
          <div className="border-b border-slate-200 dark:border-slate-700">
            <nav className="flex gap-1 px-1" aria-label="Invoices">
              {invoices.map((inv, idx) => (
                <button
                  key={inv.id}
                  onClick={() => setActiveInvoiceIndex(idx)}
                  className={`px-3 py-1.5 text-sm rounded-t-md transition-colors ${
                    idx === activeInvoiceIndex
                      ? 'bg-white dark:bg-slate-100 border-b-2 border-blue-500 font-medium text-blue-600'
                      : 'text-slate-500 hover:text-slate-700 dark:text-slate-400'
                  }`}
                >
                  {inv.number || `Invoice ${idx + 1}`}
                  {invoices.length > 1 && (
                    <button
                      onClick={(e) => { e.stopPropagation(); removeInvoice(idx); }}
                      className="ml-1 text-rose-500 hover:text-rose-700"
                      aria-label="Remove invoice"
                    >
                      ×
                    </button>
                  )}
                </button>
              ))}
              <button
                onClick={addInvoice}
                className="px-3 py-1.5 text-sm text-slate-500 hover:text-blue-600 dark:hover:text-blue-400 flex items-center gap-1"
              >
                <Plus className="h-4 w-4" /> Add Invoice
              </button>
            </nav>
          </div>
        )}

        const loadingModal = loadingDetails ? (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create Invoice(s)"
      description="Build one or more invoices for this LC. Quantities can exceed PI quantities for shipment advantage."
      size="lg"
    >
      <div className="space-y-4">
        <div className="flex items-center justify-center py-8 text-slate-500">
          <Loader2 className="h-5 w-5 animate-spin mr-2" />
          Loading PI details...
        </div>
      </div>
    </Modal>
  ) : null;

  return (
          <div className="space-y-4">
            {/* PI Lines Reference */}
            {piLinesReference}

            {/* Invoice Lines Editor */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="font-medium text-slate-900 dark:text-white">
                  Invoice Lines {invoices.length > 1 && `for ${activeInvoice.number || `Invoice ${activeInvoiceIndex + 1}`}`}
                </h4>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  Total: ${formatNumber(totalAmount)}
                </span>
              </div>

              {invoiceLinesContent}

              {/* Add Line from PI */}
              {hasLines && (
                <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
                  <h5 className="text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">
                    Add Line from PI
                  </h5>
                  <div className="grid grid-cols-12 gap-2 items-end">
                    <div className="col-span-5">
                      <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                        Product (from PI)
                      </label>
                      <select
                        value={activeInvoice.newLineSaleItemId || ''}
                        onChange={(e) => {
                          const selected = piLines.find(l => l.saleItemId === parseInt(e.target.value));
                          setInvoices(prev => prev.map((inv, i) => 
                            i === activeInvoiceIndex ? { 
                              ...inv, 
                              newLineSaleItemId: e.target.value,
                              newLineUnitPrice: selected?.unitPrice || 0,
                              newLineProductName: selected?.productName,
                              newLineUnit: selected?.unit || 'KG',
                              newLinePiQty: selected?.piQuantity || 0,
                              newLineInvoicedQty: selected?.invoicedQuantity || 0,
                            } : inv
                          ));
                        }}
                        className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800"
                      >
                        <option value="">Select a PI line...</option>
                        {piLines.map(line => (
                          <option key={line.saleItemId} value={line.saleItemId}>
                            {line.productName} (Remaining: {formatNumber(line.remaining)} {line.unit}, ${formatNumber(line.unitPrice)}/{line.unit})
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="col-span-2">
                      <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                        Invoice Qty
                      </label>
                      <input
                        type="number"
                        step="0.001"
                        min="0.001"
                        value={activeInvoice.newLineQty || ''}
                        onChange={(e) => setInvoices(prev => prev.map((inv, i) => 
                          i === activeInvoiceIndex ? { ...inv, newLineQty: e.target.value } : inv
                        ))}
                        placeholder="e.g. 25"
                        className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800"
                      />
                    </div>
                    <div className="col-span-2 text-right">
                      <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                        Unit Price (from PI)
                      </label>
                      <input
                        type="text"
                        value={activeInvoice.newLineUnitPrice ? formatNumber(activeInvoice.newLineUnitPrice) : ''}
                        readOnly
                        className="w-full px-2 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-slate-100 dark:bg-slate-700 text-right"
                      />
                    </div>
                    <div className="col-span-1">
                      <button
                        onClick={() => {
                          if (!activeInvoice.newLineSaleItemId || !activeInvoice.newLineQty) return;
                          const selected = piLines.find(l => l.saleItemId === parseInt(activeInvoice.newLineSaleItemId));
                          if (!selected) return;
                          const newLine = {
                            id: Date.now() + Math.random(),
                            saleItemId: parseInt(activeInvoice.newLineSaleItemId),
                            productName: selected.productName,
                            unit: selected.unit || 'KG',
                            quantity: parseFloat(activeInvoice.newLineQty),
                            unitPrice: selected.unitPrice,
                          };
                          setInvoices(prev => prev.map((inv, i) => 
                            i === activeInvoiceIndex ? { 
                              ...inv, 
                              lines: [...inv.lines, newLine], 
                              newLineSaleItemId: '', 
                              newLineQty: '', 
                              newLineUnitPrice: 0 
                            } : inv
                          ));
                        }}
                        disabled={!activeInvoice.newLineSaleItemId || !activeInvoice.newLineQty}
                        className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        Add Line
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Form Error */}
              {formError && <p className="text-xs text-rose-600 dark:text-rose-400">{formError}</p>}

              {/* Invoice Number Inputs */}
              <div className="space-y-3">
                {invoices.map((inv, idx) => (
                  <div key={inv.id} className="flex items-center gap-2">
                    <label className="w-24 text-xs font-medium text-slate-700 dark:text-slate-300">
                      {invoices.length > 1 ? `Invoice ${idx + 1}` : 'Invoice'}
                    </label>
                    <input
                      type="text"
                      value={inv.number}
                      onChange={(e) => setInvoices(prev => prev.map((inv, i) => 
                        i === idx ? { ...inv, number: e.target.value } : inv
                      ))}
                      placeholder="e.g. INV-2024-001"
                      className="flex-1 px-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50"
                      disabled={creating}
                    />
                  </div>
                ))}
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="secondary" size="sm" onClick={onClose} disabled={creating}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleCreate}
                  disabled={creating || invoices.some(inv => !inv.number.trim() || inv.lines.length === 0)}
                  loading={creating}
                >
                  <span className="flex items-center gap-1.5">
                    <Plus className="h-3.5 w-3.5" />
                    Create Invoices
                  </span>
                </Button>
</div>
</div>
      </div>
    </div>
    </Modal>
  );
}