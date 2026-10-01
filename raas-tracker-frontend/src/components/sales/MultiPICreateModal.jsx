import { useState, useEffect } from 'react';
import { Plus, Trash2, AlertTriangle, Loader2, FileText } from 'lucide-react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import Badge from '../ui/Badge';
import { formatNumber, sumLineTotals, lineTotal } from '../../utils/format';
import { COMMON_UNITS } from '../../utils/units';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

const rowInputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1 text-xs text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

let seq = 0;
const nextKey = (prefix) => `${prefix}-${Date.now()}-${seq++}`;

function blankItem() {
  return { key: nextKey('item'), product_name: '', quantity: 0, unit_price: 0, unit: 'KG' };
}

function blankDoc() {
  return { key: nextKey('doc'), pi_number: '', pi_date: '', items: [blankItem()] };
}

/**
 * Phase 3 — multi-PI create screen.
 *
 * One form holding N PI documents under a single shared company. A single
 * submit posts the whole batch to POST /api/sales/batch (atomic: any failure
 * rolls back everything, so the error banner always means "nothing saved").
 *
 * Visual rhythm mirrors ReviewModal: same inputs, same items table, same
 * footer — each document is a bordered card so N documents scan as a stack.
 * The wiring lane owns the batch POST behaviour; preserve layout + copy.
 */
export default function MultiPICreateModal({ isOpen, initialDocs, onClose, onSaved }) {
  const { apiFetch } = useAuth();
  const [companies, setCompanies] = useState([]);
  const [companyId, setCompanyId] = useState('');
  const [clientName, setClientName] = useState('');
  const [docs, setDocs] = useState([blankDoc()]);
  const [docErrors, setDocErrors] = useState({});
  const [batchError, setBatchError] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [saving, setSaving] = useState(false);
  const [refsLoading, setRefsLoading] = useState(true);
  const [refsError, setRefsError] = useState(null);
  const [refsAttempt, setRefsAttempt] = useState(0);

  useEffect(() => {
    if (isOpen) {
      // Always mounted (isOpen toggles visibility), so the form must resync
      // whenever it opens — same pattern as ReviewModal.
      // oxlint-disable-next-line react/set-state-in-effect
      setDocs(
        Array.isArray(initialDocs) && initialDocs.length > 0
          ? initialDocs.map((d) => ({
              key: nextKey('doc'),
              pi_number: d.pi_number || '',
              pi_date: d.pi_date || '',
              items:
                (d.items || []).length > 0
                  ? d.items.map((i) => ({
                      key: nextKey('item'),
                      product_name: i.product_name || '',
                      quantity: i.quantity ?? 0,
                      unit_price: i.unit_price ?? 0,
                      unit: (i.unit || 'KG').toUpperCase(),
                    }))
                  : [blankItem()],
            }))
          : [blankDoc()]
      );
      setDocErrors({});
      setBatchError(null);
      setWarnings([]);
      setSaving(false);
      setCompanyId('');
      setClientName('');
      setRefsLoading(true);
      setRefsError(null);
      const loadCompanies = async () => {
        try {
          const res = await apiFetch('/api/companies');
          const data = await res.json();
          setCompanies(Array.isArray(data) ? data : []);
        } catch (err) {
          toast.error('Could not load companies');
          setCompanies([]);
          setRefsError(err?.message || 'Could not load companies');
        } finally {
          setRefsLoading(false);
        }
      };
      loadCompanies();
    }
    // refsAttempt: each retry re-runs the load.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, apiFetch, refsAttempt]);

  const retryRefs = () => setRefsAttempt((n) => n + 1);

  const updateDoc = (docKey, field, value) => {
    setDocs((prev) => prev.map((d) => (d.key === docKey ? { ...d, [field]: value } : d)));
  };

  const updateItem = (docKey, itemKey, field, value) => {
    setDocs((prev) =>
      prev.map((d) =>
        d.key !== docKey
          ? d
          : { ...d, items: d.items.map((i) => (i.key === itemKey ? { ...i, [field]: value } : i)) }
      )
    );
  };

  const addDoc = () => {
    setDocs((prev) => [...prev, blankDoc()]);
  };

  const removeDoc = (docKey) => {
    setDocs((prev) => (prev.length <= 1 ? prev : prev.filter((d) => d.key !== docKey)));
  };

  const addItem = (docKey) => {
    setDocs((prev) =>
      prev.map((d) => (d.key === docKey ? { ...d, items: [...d.items, blankItem()] } : d))
    );
  };

  const removeItem = (docKey, itemKey) => {
    setDocs((prev) =>
      prev.map((d) =>
        d.key !== docKey ? d : { ...d, items: d.items.filter((i) => i.key !== itemKey) }
      )
    );
  };

  const docTotal = (doc) => sumLineTotals(doc.items.filter((i) => i.product_name.trim()));
  const batchTotal = docs.reduce((sum, d) => sum + docTotal(d), 0);
  const batchLines = docs.reduce(
    (sum, d) => sum + d.items.filter((i) => i.product_name.trim()).length,
    0
  );

  // Per-document gates mirror ReviewModal exactly: PI number required, at
  // least one named product row. Company is picked once for the batch.
  const validate = () => {
    const errors = {};
    if (companies.length > 0 && companyId === '') {
      setBatchError({ message: 'Select a company (register it on the Companies page first)' });
      return null;
    }
    docs.forEach((d, idx) => {
      const label = `PI document ${idx + 1}`;
      if (!d.pi_number.trim()) {
        errors[d.key] = 'PI number is required';
        return;
      }
      const clean = d.items.filter((i) => i.product_name.trim());
      if (clean.length === 0) {
        errors[d.key] = `${label}: at least one product item is required`;
      }
    });
    setDocErrors(errors);
    if (Object.keys(errors).length > 0) {
      setBatchError({ message: 'Fix the highlighted documents before saving' });
      return null;
    }
    return docs.map((d) => ({
      sale: {
        pi_number: d.pi_number.trim(),
        pi_date: d.pi_date || null,
        client_name: clientName.trim() || null,
        company_id: companyId === '' ? null : Number(companyId),
      },
      items: d.items
        .filter((i) => i.product_name.trim())
        .map((i) => ({
          product_name: i.product_name.trim(),
          quantity: parseFloat(i.quantity) || 0,
          unit_price: parseFloat(i.unit_price) || 0,
          unit: (i.unit || 'KG').toUpperCase(),
        })),
    }));
  };

  const handleSave = async () => {
    setBatchError(null);
    const payload = validate();
    if (!payload) return;
    setSaving(true);
    try {
      const res = await apiFetch('/api/sales/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sales: payload }),
      });
      const data = await res.json().catch(() => ({}));
      const ids = Array.isArray(data.ids) ? data.ids : [];
      const batchWarnings = Array.isArray(data.warnings) ? data.warnings : [];
      setWarnings(batchWarnings);
      toast.success(
        ids.length > 0 ? `${ids.length} PIs saved to PI Issued` : 'Batch saved'
      );
      onSaved?.(ids, batchWarnings);
    } catch (err) {
      // Atomic contract: any failure rolls back all, so this banner always
      // means nothing was saved. Surface which PI failed when the server
      // names one (400 details), otherwise the raw message.
      const details = Array.isArray(err.fields)
        ? err.fields.map((f) => `${f.field}: ${f.message}`)
        : [];
      setBatchError({
        message: err?.message || 'Failed to save batch — nothing was saved',
        details,
      });
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create multiple PIs"
      subtitle="One company, N PI documents — saved together or not at all"
      maxWidth="max-w-3xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="success"
            size="sm"
            icon={Loader2}
            loading={saving}
            disabled={companies.length === 0}
            onClick={handleSave}
          >
            Save {docs.length} PI{docs.length === 1 ? '' : 's'} to PI Issued
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {warnings.length > 0 && (
          <div className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5" /> Saved with warnings
            </p>
            <ul className="mt-1.5 space-y-0.5 text-xs text-amber-600 dark:text-amber-400 list-disc list-inside">
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Shared company — picked once for the whole batch */}
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-800/40 p-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-end">
            <div>
              <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
                Company * <span className="font-normal text-slate-400">— shared by all PIs</span>
              </label>
              {refsLoading ? (
                <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">Loading…</p>
              ) : refsError ? (
                <div className="flex flex-wrap items-center gap-2 mt-1">
                  <p className="text-xs font-medium text-rose-600 dark:text-rose-400">
                    Could not load companies
                  </p>
                  <Button variant="secondary" size="sm" onClick={retryRefs}>
                    Retry
                  </Button>
                </div>
              ) : companies.length > 0 ? (
                <select
                  aria-label="Company"
                  value={companyId}
                  onChange={(e) => {
                    setCompanyId(e.target.value);
                    const picked = companies.find((c) => String(c.id) === e.target.value);
                    setClientName(picked ? picked.name : '');
                  }}
                  className={inputCls}
                >
                  <option value="">Select company…</option>
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              ) : (
                <p className="text-xs text-slate-500 dark:text-slate-400 italic mt-1">
                  No companies registered
                </p>
              )}
            </div>
            <div className="flex items-center gap-2 sm:justify-end">
              <Badge variant="info" size="xs">
                {docs.length} document{docs.length === 1 ? '' : 's'}
              </Badge>
              <Badge variant="success" size="xs">
                {batchLines} lines · ${formatNumber(batchTotal)}
              </Badge>
            </div>
          </div>
        </div>

        {/* Document stack */}
        <div className="space-y-3">
          {docs.map((doc, docIdx) => (
            <section
              key={doc.key}
              aria-label={`PI document ${docIdx + 1}`}
              className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden"
            >
              <div className="flex items-center justify-between gap-2 px-3.5 py-2.5 border-b border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-800/40">
                <h4 className="flex items-center gap-1.5 text-sm font-semibold text-slate-800 dark:text-white">
                  <FileText className="h-4 w-4 text-slate-400" />
                  PI document {docIdx + 1}
                  <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    ${formatNumber(docTotal(doc))}
                  </span>
                </h4>
                <button
                  type="button"
                  onClick={() => removeDoc(doc.key)}
                  disabled={docs.length <= 1}
                  aria-label={`Remove document ${docIdx + 1}`}
                  title={docs.length <= 1 ? 'A batch needs at least one document' : 'Remove document'}
                  className="p-1.5 rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>

              <div className="p-3.5 space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
                      PI Number *
                    </label>
                    <input
                      aria-label={`PI number document ${docIdx + 1}`}
                      value={doc.pi_number}
                      onChange={(e) => updateDoc(doc.key, 'pi_number', e.target.value)}
                      className={inputCls}
                      placeholder="PI-2026-001"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
                      PI Date
                    </label>
                    <input
                      aria-label={`PI date document ${docIdx + 1}`}
                      type="date"
                      value={doc.pi_date || ''}
                      onChange={(e) => updateDoc(doc.key, 'pi_date', e.target.value)}
                      className={inputCls}
                    />
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-xs font-semibold text-slate-600 dark:text-slate-300">
                      Products ({doc.items.length})
                    </p>
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={Plus}
                      onClick={() => addItem(doc.key)}
                      aria-label={`Add row document ${docIdx + 1}`}
                    >
                      Add Row
                    </Button>
                  </div>
                  <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        <tr>
                          <th className="px-2.5 py-2">Product</th>
                          <th className="px-2.5 py-2 w-24">Qty</th>
                          <th className="px-2.5 py-2 w-20">Unit</th>
                          <th className="px-2.5 py-2 w-28">Unit Price ($)</th>
                          <th className="px-2.5 py-2 w-24 text-right">Total</th>
                          <th className="px-2.5 py-2 w-10"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                        {doc.items.map((item, rowIdx) => (
                          <tr key={item.key}>
                            <td className="px-2.5 py-1.5">
                              <input
                                aria-label={`Product name document ${docIdx + 1} row ${rowIdx + 1}`}
                                value={item.product_name}
                                onChange={(e) =>
                                  updateItem(doc.key, item.key, 'product_name', e.target.value)
                                }
                                className={rowInputCls}
                                placeholder="Product name"
                              />
                            </td>
                            <td className="px-2.5 py-1.5">
                              <input
                                aria-label={`Quantity document ${docIdx + 1} row ${rowIdx + 1}`}
                                type="number"
                                min="0"
                                step="any"
                                value={item.quantity}
                                onChange={(e) =>
                                  updateItem(doc.key, item.key, 'quantity', e.target.value)
                                }
                                className={rowInputCls}
                              />
                            </td>
                            <td className="px-2.5 py-1.5">
                              <select
                                aria-label={`Unit document ${docIdx + 1} row ${rowIdx + 1}`}
                                value={item.unit || 'KG'}
                                onChange={(e) =>
                                  updateItem(doc.key, item.key, 'unit', e.target.value)
                                }
                                className={rowInputCls}
                              >
                                {COMMON_UNITS.map((u) => (
                                  <option key={u} value={u}>
                                    {u}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td className="px-2.5 py-1.5">
                              <input
                                aria-label={`Unit price document ${docIdx + 1} row ${rowIdx + 1}`}
                                type="number"
                                min="0"
                                step="any"
                                value={item.unit_price}
                                onChange={(e) =>
                                  updateItem(doc.key, item.key, 'unit_price', e.target.value)
                                }
                                className={rowInputCls}
                              />
                            </td>
                            <td className="px-2.5 py-1.5 text-right font-semibold text-slate-800 dark:text-slate-100">
                              ${formatNumber(lineTotal(item.quantity, item.unit_price))}
                            </td>
                            <td className="px-2.5 py-1.5">
                              <button
                                type="button"
                                onClick={() => removeItem(doc.key, item.key)}
                                aria-label={`Remove row ${rowIdx + 1} document ${docIdx + 1}`}
                                title="Remove row"
                                className="p-1.5 rounded-lg text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 cursor-pointer"
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {docErrors[doc.key] && (
                  <p className="text-xs font-medium text-rose-600 dark:text-rose-400">
                    {docErrors[doc.key]}
                  </p>
                )}
              </div>
            </section>
          ))}
        </div>

        <Button variant="secondary" size="sm" icon={Plus} onClick={addDoc} className="w-full justify-center">
          Add PI document
        </Button>

        {/* Atomic-failure banner: the batch is all-or-nothing, so any error
            here means nothing was saved. Name the failing PI when known. */}
        {batchError && (
          <div
            role="alert"
            className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/30 p-3"
          >
            <p className="flex items-center gap-1.5 text-xs font-semibold text-rose-700 dark:text-rose-300">
              <AlertTriangle className="h-3.5 w-3.5" /> Nothing was saved — the whole batch was
              rolled back
            </p>
            <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{batchError.message}</p>
            {Array.isArray(batchError.details) && batchError.details.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 text-xs text-rose-600 dark:text-rose-400 list-disc list-inside">
                {batchError.details.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
