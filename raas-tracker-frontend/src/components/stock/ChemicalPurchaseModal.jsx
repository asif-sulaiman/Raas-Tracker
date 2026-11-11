import { useState, useEffect } from 'react';
import { Plus, X } from 'lucide-react';
import clsx from 'clsx';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import SuggestionInput from '../ui/SuggestionInput';
import { COMMON_UNITS } from '../../utils/units';
import { todayLocalISO } from '../../utils/format';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';

const fieldCls =
  'w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500';

const labelCls = 'block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5';

// Stable keys across add/remove so React never reuses a row's DOM for a
// different material (which would leak typed values between rows).
let nextRowKey = 1;
function makeRow() {
  return { key: nextRowKey++, mode: 'new', name: '', unit: 'KG', chemicalId: '', quantity: '', reorder: '' };
}

function segCls(active) {
  return clsx(
    'px-3 py-1.5 text-xs font-semibold transition-colors cursor-pointer',
    active
      ? 'bg-blue-600 text-white'
      : 'bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700'
  );
}

function quantityValid(line) {
  if (line.quantity === '' || line.quantity == null) return false;
  const n = Number(line.quantity);
  return Number.isFinite(n) && n > 0;
}

/**
 * Multi-material purchase entry. One shared header (supplier, number, date,
 * delivery type) above repeatable New/Existing material rows. In-house receipts
 * stock the material; outward receipts name a client and never touch stock.
 * Entered values survive a server refusal so the operator can fix and retry.
 */
export default function ChemicalPurchaseModal({ isOpen, onClose, onSuccess }) {
  const { apiFetch } = useAuth();
  const [supplierName, setSupplierName] = useState('');
  const [purchaseNumber, setPurchaseNumber] = useState('');
  const [purchaseDate, setPurchaseDate] = useState(todayLocalISO);
  const [deliveryType, setDeliveryType] = useState('in_house');
  const [clientId, setClientId] = useState('');
  const [lines, setLines] = useState(() => [makeRow()]);
  const [saving, setSaving] = useState(false);
  const [catalog, setCatalog] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [fieldErrors, setFieldErrors] = useState({});

  // Load the entry selectors once per open. Every setState lands after an
  // await, and a close mid-flight is ignored so a stale response cannot
  // repopulate a form the operator already dismissed.
  useEffect(() => {
    if (!isOpen) return undefined;
    let cancelled = false;
    const loadOptions = async () => {
      try {
        const [catRes, compRes] = await Promise.all([
          apiFetch('/api/chemicals/catalog'),
          apiFetch('/api/companies'),
        ]);
        const cat = await catRes.json();
        const comp = await compRes.json();
        if (cancelled) return;
        setCatalog(Array.isArray(cat) ? cat : []);
        setCompanies(Array.isArray(comp) ? comp : []);
      } catch (err) {
        if (!cancelled) toast.error(err.message || 'Could not load purchase options');
      }
    };
    loadOptions();
    return () => { cancelled = true; };
  }, [isOpen, apiFetch]);

  const resetForm = () => {
    setSupplierName('');
    setPurchaseNumber('');
    setPurchaseDate(todayLocalISO());
    setDeliveryType('in_house');
    setClientId('');
    setLines([makeRow()]);
  };

  const handleClose = () => {
    if (saving) return;
    resetForm();
    onClose();
  };

  const updateLine = (key, patch) => {
    clearFieldError(key);
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  };

  // A row's own field error is cleared as soon as the operator touches it.
  const clearFieldError = (key) => setFieldErrors((f) => { const n = { ...f }; delete n[key]; return n; });

  const addRow = () => setLines((ls) => [...ls, makeRow()]);

  const removeRow = (key) =>
    setLines((ls) => (ls.length > 1 ? ls.filter((l) => l.key !== key) : ls));

  const selectExisting = (key, name) => {
    const match = catalog.find((c) => c.name.toLowerCase() === name.trim().toLowerCase());
    updateLine(key, {
      name,
      chemicalId: match ? String(match.id) : '',
      unit: match ? match.unit : '',
    });
  };

  const quantitiesOk = lines.length > 0 && lines.every(quantityValid);

  const handleSave = async () => {
    if (saving) return;
    const supplier = supplierName.trim();
    if (!supplier) { toast.error('Enter a supplier name'); return; }
    const number = purchaseNumber.trim();
    if (!number) { toast.error('Enter a purchase number'); return; }
    if (!purchaseDate) { toast.error('Select a purchase date'); return; }
    if (deliveryType === 'outward' && !clientId) {
      toast.error('Select a client for the outward purchase');
      return;
    }
    for (let i = 0; i < lines.length; i += 1) {
      const l = lines[i];
      const label = `material ${i + 1}`;
      if (!quantityValid(l)) { toast.error(`Enter a quantity greater than 0 for ${label}`); return; }
      if (l.mode === 'new') {
        if (!l.name.trim()) { toast.error(`Enter a chemical name for ${label}`); return; }
        if (!l.unit.trim()) { toast.error(`Enter a unit for ${label}`); return; }
        if (l.reorder !== '') {
          const r = Number(l.reorder);
          if (!Number.isFinite(r) || r < 0) {
            toast.error(`Low-stock alarm must be 0 or greater for ${label}`);
            return;
          }
        }
      } else if (!l.chemicalId) {
        toast.error(`Select an existing chemical for ${label}`);
        return;
      }
    }

    setSaving(true);
    setFieldErrors({});
    try {
      const body = {
        supplier_name: supplier,
        purchase_number: number,
        purchase_date: purchaseDate,
        delivery_type: deliveryType,
        client_id: deliveryType === 'outward' ? Number(clientId) : null,
        lines: lines.map((l) => {
          const quantity = Number(l.quantity);
          if (l.mode === 'new') {
            return {
              chemical_name: l.name.trim(),
              chemical_id: null,
              quantity,
              unit: l.unit.trim(),
              is_new: true,
              reorder_level: l.reorder === '' ? null : Number(l.reorder),
            };
          }
          const match = catalog.find((c) => String(c.id) === l.chemicalId);
          return {
            chemical_name: match ? match.name : l.name.trim(),
            chemical_id: Number(l.chemicalId),
            quantity,
            unit: l.unit,
            is_new: false,
            reorder_level: null,
          };
        }),
      };
      await apiFetch('/api/chemical-purchases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      toast.success(`Purchase ${number} recorded`);
      onSuccess?.();
      resetForm();
      onClose();
    } catch (err) {
      // Preserve every entered value so the operator can correct and retry.
      // Attach server field errors (lines.N.<field>) to their own material row
      // instead of one generic banner, so each correction is next to its field.
      const details = err?.fields;
      let attached = false;
      if (Array.isArray(details) && details.length > 0) {
        const next = {};
        for (const d of details) {
          const m = String(d.field || '').match(/^lines\.(\d+)\./);
          if (m && lines[Number(m[1])]) {
            next[lines[Number(m[1])].key] = d.message;
            attached = true;
          }
        }
        setFieldErrors(next);
      }
      if (!attached) toast.error(err.message || 'Could not save the purchase');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Add Chemical"
      subtitle="Record a multi-material purchase"
      maxWidth="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={saving}>Cancel</Button>
          <Button variant="primary" onClick={handleSave} loading={saving} disabled={saving || !quantitiesOk}>Save</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className={labelCls}>Supplier name</label>
            <input aria-label="Supplier name" type="text" value={supplierName} onChange={(e) => setSupplierName(e.target.value)} placeholder="e.g. Acme Chemicals" className={fieldCls} />
          </div>
          <div>
            <label className={labelCls}>Purchase number</label>
            <input aria-label="Purchase number" type="text" value={purchaseNumber} onChange={(e) => setPurchaseNumber(e.target.value)} placeholder="e.g. PO-1042" className={fieldCls} />
          </div>
          <div>
            <label className={labelCls}>Purchase date</label>
            <input aria-label="Purchase date" type="date" value={purchaseDate} onChange={(e) => setPurchaseDate(e.target.value)} className={fieldCls} />
          </div>
          <div>
            <label className={labelCls}>Delivery type</label>
            <select aria-label="Delivery type" value={deliveryType} onChange={(e) => setDeliveryType(e.target.value)} className={fieldCls}>
              <option value="in_house">In house</option>
              <option value="outward">Outward</option>
            </select>
          </div>
        </div>

        {deliveryType === 'outward' && (
          <div>
            <label className={labelCls}>Client</label>
            <select aria-label="Client" value={clientId} onChange={(e) => setClientId(e.target.value)} className={fieldCls}>
              <option value="">Select client…</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">Outward purchases go straight to the client and never enter in-house stock.</p>
          </div>
        )}

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">Materials</span>
            <Button variant="secondary" size="sm" icon={Plus} onClick={addRow}>Add material</Button>
          </div>

          {lines.map((l, idx) => (
            <div key={l.key} role="group" aria-label={`Material ${idx + 1}`} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/40 p-3 space-y-3">
              {fieldErrors[l.key] && (
                <p role="alert" className="text-[11px] text-red-600 dark:text-red-400 font-medium leading-none">{fieldErrors[l.key]}</p>
              )}
              <div className="flex items-center justify-between gap-2">
                <div className="flex rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
                  <button type="button" onClick={() => updateLine(l.key, { mode: 'new' })} aria-pressed={l.mode === 'new'} className={segCls(l.mode === 'new')}>New Chemical</button>
                  <button type="button" onClick={() => updateLine(l.key, { mode: 'existing' })} aria-pressed={l.mode === 'existing'} className={segCls(l.mode === 'existing')}>Existing Chemical</button>
                </div>
                <button
                  type="button"
                  onClick={() => removeRow(l.key)}
                  disabled={lines.length === 1}
                  aria-label={`Remove material ${idx + 1}`}
                  className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-700 hover:text-slate-600 dark:hover:text-slate-200 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {l.mode === 'new' ? (
                <>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className={labelCls}>Chemical name</label>
                      <input aria-label="Chemical name" type="text" value={l.name} onChange={(e) => updateLine(l.key, { name: e.target.value })} placeholder="e.g. Citric Acid" className={fieldCls} />
                    </div>
                    <div>
                      <label className={labelCls}>Unit</label>
                      <SuggestionInput aria-label="Unit" options={COMMON_UNITS} value={l.unit} onChange={(e) => updateLine(l.key, { unit: e.target.value })} className={fieldCls} />
                    </div>
                  </div>
                  <div>
                    <label className={labelCls}>Low-stock alarm (optional)</label>
                    <input aria-label="Low-stock alarm" inputMode="numeric" type="number" min="0" value={l.reorder} onChange={(e) => updateLine(l.key, { reorder: e.target.value })} placeholder="0" className={fieldCls} />
                  </div>
                </>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className={labelCls}>Existing chemical</label>
                    <SuggestionInput aria-label="Existing chemical" options={catalog.map((c) => c.name)} value={l.name} onChange={(e) => selectExisting(l.key, e.target.value)} placeholder="Search chemicals…" className={fieldCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Unit</label>
                    <input aria-label="Unit" type="text" readOnly value={l.chemicalId ? l.unit : ''} placeholder="—" className={clsx(fieldCls, 'bg-slate-100 dark:bg-slate-700/60 cursor-not-allowed')} />
                  </div>
                </div>
              )}

              <div>
                <label className={labelCls}>Quantity</label>
                <input aria-label="Quantity" inputMode="decimal" type="number" min="0" step="any" value={l.quantity} onChange={(e) => updateLine(l.key, { quantity: e.target.value })} placeholder="0" className={fieldCls} />
              </div>
            </div>
          ))}

          {!quantitiesOk && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">Enter a quantity greater than 0 for each material to save.</p>
          )}
        </div>
      </div>
    </Modal>
  );
}