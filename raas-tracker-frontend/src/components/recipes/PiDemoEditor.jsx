import { useCallback, useEffect, useRef, useState } from 'react';
import { Plus, X } from 'lucide-react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import EntityCombobox from '../ui/EntityCombobox';
import { useAuth } from '../../context/AuthContext';
import { formatNumber } from '../../utils/format';
import { toast } from 'sonner';

const fieldCls =
  'w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500';
const labelCls = 'block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5';

const clone = (value) => structuredClone(value ?? []);

// Percentages are float arithmetic on this screen (100 - 99.99 is
// 0.010000000000005116), and the activation gate reads "ingredients + water =
// 100 within tolerance" — so the operator gets the same one-decimal figure the
// recipe cards show, never the float dust that makes a legal plan look illegal.
const pct = (value) => formatNumber(value, 1);

function quantityLine(label, value, unit) {
  return value == null ? `${label} unknown` : `${label} ${value} ${unit}`;
}

/**
 * Draft/activation editor for a PI's demo recipe. The server owns every
 * quantity (ordered, retained target, produced, remaining); this screen only
 * edits formulation percentages and an optional water override. A read never
 * creates a demo — creation is an explicit action. Activation is refused while
 * edits are unsaved or a product has no resolved formulation, and a refusal
 * leaves the previous active forecast untouched.
 */
export default function PiDemoEditor({ saleId, isOpen, onClose }) {
  const { apiFetch } = useAuth();
  const [loading, setLoading] = useState(true);
  const [demo, setDemo] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [readError, setReadError] = useState(null);
  const [products, setProducts] = useState([]);
  const [baseline, setBaseline] = useState([]);
  const [readRevision, setReadRevision] = useState(0);
  const [catalog, setCatalog] = useState([]);
  const [pending, setPending] = useState({});
  const [saving, setSaving] = useState(false);
  const [activating, setActivating] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [activationNotice, setActivationNotice] = useState(false);
  const [validationError, setValidationError] = useState(null);

  const applyDemo = useCallback((d) => {
    setDemo(d);
    setProducts(clone(d.products));
    setBaseline(clone(d.products));
    setReadRevision(d.draft_revision);
    setConflict(false);
    setFieldErrors({});
    setActivationNotice(false);
    setValidationError(null);
  }, []);

  // SaleDetailModal keys its late-response guard on the PI; here the same job
  // needs a counter, because the retired read and the one replacing it can
  // belong to the same PI (close and reopen, retry twice). Whoever claims the
  // newest token owns the screen; every older answer is a no-op.
  const readToken = useRef(0);

  const loadDemo = useCallback(async () => {
    const token = readToken.current + 1;
    readToken.current = token;
    const isCurrent = () => token === readToken.current;
    setLoading(true);
    setReadError(null);
    setNotFound(false);
    try {
      const res = await apiFetch(`/api/sales/${saleId}/demo-recipe`);
      const data = await res.json();
      if (isCurrent()) applyDemo(data.demo);
    } catch (err) {
      if (!isCurrent()) return;
      if (err.status === 404) {
        setNotFound(true);
        setDemo(null);
      } else {
        setReadError(err.message || 'Could not load the demo recipe');
      }
    } finally {
      // A retired read must not clear the spinner its replacement raised.
      if (isCurrent()) setLoading(false);
    }
  }, [apiFetch, saleId, applyDemo]);

  useEffect(() => {
    if (!isOpen) return undefined;
    let cancelled = false;
    apiFetch('/api/chemicals/catalog')
      .then((res) => res.json())
      .then((cat) => { if (!cancelled) setCatalog(Array.isArray(cat) ? cat : []); })
      .catch(() => { /* the picker degrades to empty; percentages still edit */ });
    // oxlint-disable-next-line react/set-state-in-effect
    loadDemo();
    return () => {
      cancelled = true;
      // Retire whatever is in flight — a switching PI or a closing editor
      // leaves no response that is allowed to write to this screen.
      readToken.current += 1;
    };
  }, [isOpen, loadDemo, apiFetch]);

  const dirty = JSON.stringify(products) !== JSON.stringify(baseline);
  const progress = demo?.progress ?? [];
  const missing = progress.filter(
    (pr) => !products.some((p) => p.product_name === pr.product_name),
  );
  const unresolved = products.some((p) => p.items.length === 0);
  const canActivate = !dirty && !unresolved && missing.length === 0 && products.length > 0;

  const updateItem = (pIdx, iIdx, patch) =>
    setProducts((ps) => ps.map((p, pi) => (pi === pIdx
      ? { ...p, items: p.items.map((it, ii) => (ii === iIdx ? { ...it, ...patch } : it)) }
      : p)));

  const removeItem = (pIdx, iIdx) =>
    setProducts((ps) => ps.map((p, pi) => (pi === pIdx
      ? { ...p, items: p.items.filter((_, ii) => ii !== iIdx) }
      : p)));

  const setWater = (pIdx, value) =>
    setProducts((ps) => ps.map((p, pi) => (pi === pIdx ? { ...p, water_percentage: value } : p)));

  const addFormulation = (pr) =>
    setProducts((ps) => [...ps, {
      product_name: pr.product_name, unit: pr.unit, water_percentage: null, items: [],
    }]);

  const addIngredient = (productName) => {
    const id = pending[productName];
    const match = catalog.find((c) => String(c.id) === String(id));
    if (!match) return;
    setProducts((ps) => ps.map((p) => (p.product_name === productName
      ? { ...p, items: [...p.items, { chemical: match.name, chemical_id: match.id, percentage: '' }] }
      : p)));
    setPending((s) => ({ ...s, [productName]: '' }));
  };

  const handleCreate = async () => {
    const token = readToken.current;
    const isCurrent = () => token === readToken.current;
    setSaving(true);
    try {
      const res = await apiFetch(`/api/sales/${saleId}/demo-recipe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!isCurrent()) return;
      applyDemo(data.demo);
      setNotFound(false);
    } catch (err) {
      if (isCurrent()) toast.error(err.message || 'Could not create the demo recipe');
    } finally {
      // The busy flag is this screen's, not the PI's: clearing it unconditionally
      // is what stops a retired write from leaving the next PI's buttons dead.
      setSaving(false);
    }
  };

  const handleSave = async () => {
    for (const p of products) {
      for (const it of p.items) {
        const n = Number(it.percentage);
        if (it.percentage === '' || it.percentage == null || !Number.isFinite(n) || n < 0 || n > 100) {
          setValidationError('Enter a percentage between 0 and 100');
          return;
        }
      }
    }
    setValidationError(null);
    setConflict(false);
    const token = readToken.current;
    const isCurrent = () => token === readToken.current;
    setSaving(true);
    const body = {
      expected_revision: readRevision,
      products: products.map((p) => ({
        product_name: p.product_name,
        unit: p.unit,
        water_percentage: p.water_percentage === '' || p.water_percentage == null
          ? null : Number(p.water_percentage),
        items: p.items.map((it) => ({ chemical: it.chemical, percentage: Number(it.percentage) })),
      })),
    };
    try {
      const res = await apiFetch(`/api/sales/${saleId}/demo-recipe/draft`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (isCurrent()) applyDemo(data.demo);
    } catch (err) {
      if (!isCurrent()) return;
      if (err.status === 409) {
        setConflict(true);
      } else {
        setValidationError(err.message || 'Could not save the draft');
      }
    } finally {
      setSaving(false);
    }
  };

  const handleActivate = async () => {
    const token = readToken.current;
    const isCurrent = () => token === readToken.current;
    setActivating(true);
    setFieldErrors({});
    setActivationNotice(false);
    try {
      const res = await apiFetch(`/api/sales/${saleId}/demo-recipe/activate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_revision: readRevision }),
      });
      const data = await res.json();
      if (isCurrent()) applyDemo(data.demo);
    } catch (err) {
      if (!isCurrent()) return;
      const details = Array.isArray(err.fields) ? err.fields : [];
      const mapped = {};
      details.forEach((d) => {
        const m = /products\["([^"]+)"\]/.exec(d.field || '');
        if (m) mapped[m[1]] = d.message;
      });
      setFieldErrors(mapped);
      setActivationNotice(true);
      if (Object.keys(mapped).length === 0) toast.error(err.message || 'Could not activate');
    } finally {
      setActivating(false);
    }
  };

  const footer = demo && !notFound && !readError ? (
    <>
      <Button variant="secondary" onClick={onClose} disabled={saving || activating}>Close</Button>
      <Button variant="secondary" onClick={handleSave} loading={saving} disabled={saving || activating || loading}>
        Save Draft
      </Button>
      <Button variant="success" onClick={handleActivate} loading={activating} disabled={!canActivate || saving || activating || loading}>
        Activate
      </Button>
    </>
  ) : null;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Demo Recipe" subtitle="Plan a PI's formulation before production" maxWidth="max-w-3xl" footer={footer}>
      {loading ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">Loading…</p>
      ) : readError ? (
        <div className="space-y-3">
          <div role="alert" className="rounded-lg border border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/30 p-3 text-sm text-rose-700 dark:text-rose-300">
            {readError}
          </div>
          <Button variant="secondary" onClick={loadDemo}>Retry</Button>
        </div>
      ) : notFound ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-600 dark:text-slate-300">This PI has no demo recipe yet.</p>
          <Button variant="primary" onClick={handleCreate} loading={saving}>Create Demo Recipe</Button>
        </div>
      ) : demo ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-base font-semibold text-slate-900 dark:text-white">{demo.pi_number}</h4>
              <p className="text-xs text-slate-500 dark:text-slate-400">{demo.client_name}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-slate-600 dark:text-slate-300">Draft revision {demo.draft_revision}</span>
              <span className="text-slate-600 dark:text-slate-300">Active revision {demo.active_revision ?? '—'}</span>
              {demo.active_revision != null && demo.draft_revision > demo.active_revision && (
                <span className="rounded-full bg-amber-100 dark:bg-amber-900/40 px-2 py-0.5 font-medium text-amber-700 dark:text-amber-300">
                  Draft changes since activation
                </span>
              )}
              {dirty && (
                <span className="rounded-full bg-blue-100 dark:bg-blue-900/40 px-2 py-0.5 font-medium text-blue-700 dark:text-blue-300">
                  Unsaved changes
                </span>
              )}
            </div>
          </div>

          {conflict && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/30 p-3">
              <p className="text-sm text-amber-700 dark:text-amber-300">
                Draft was modified elsewhere. Your edits are still in this form and nothing has
                been saved — reloading picks up the latest revision and replaces your edits.
              </p>
              <Button variant="secondary" size="sm" onClick={loadDemo}>Discard edits and reload</Button>
            </div>
          )}

          {activationNotice && (
            <p className="text-sm text-slate-600 dark:text-slate-300">Previous active forecast is unchanged.</p>
          )}

          {validationError && (
            <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">{validationError}</p>
          )}

          {missing.map((pr) => (
            <div key={pr.product_name} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/40 p-3">
              <p className="text-sm text-slate-700 dark:text-slate-300">{pr.product_name} needs a formulation</p>
              <Button variant="secondary" size="sm" icon={Plus} onClick={() => addFormulation(pr)}>
                Add formulation for {pr.product_name}
              </Button>
            </div>
          ))}

          {products.map((p, pIdx) => {
            const prog = progress.find((x) => x.product_name === p.product_name);
            const sum = p.items.reduce((acc, it) => acc + (Number(it.percentage) || 0), 0);
            const resolved = p.items.length > 0;
            const automatic = resolved ? 100 - sum : null;
            const hasOverride = p.water_percentage !== null && p.water_percentage !== '' && p.water_percentage !== undefined;
            const waterUsed = hasOverride ? Number(p.water_percentage) : automatic;
            const total = resolved ? sum + (Number(waterUsed) || 0) : null;
            return (
              <div key={p.product_name} role="group" aria-label={`Formulation for ${p.product_name}`} className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-3 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-900 dark:text-white">{p.product_name}</span>
                  <span className="text-xs text-slate-500 dark:text-slate-400">{p.unit}</span>
                </div>

                {prog && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
                    <span>{quantityLine('Ordered', prog.ordered_quantity, prog.unit)}</span>
                    <span>{quantityLine('Retained target', prog.target_quantity, prog.unit)}</span>
                    <span>{quantityLine('Produced', prog.produced_quantity, prog.unit)}</span>
                    <span>{quantityLine('Remaining', prog.remaining_quantity, prog.unit)}</span>
                  </div>
                )}

                {fieldErrors[p.product_name] && (
                  <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">{fieldErrors[p.product_name]}</p>
                )}

                <div className="space-y-2">
                  {p.items.map((it, iIdx) => (
                    <div key={`${it.chemical}-${iIdx}`} className="flex items-end gap-2">
                      <div className="flex-1">
                        <span className={labelCls}>{it.chemical}</span>
                      </div>
                      <div className="w-28">
                        <input
                          aria-label={`Percentage for ${it.chemical} in ${p.product_name}`}
                          inputMode="decimal" type="number" min="0" max="100" step="any"
                          value={it.percentage ?? ''}
                          onChange={(e) => updateItem(pIdx, iIdx, { percentage: e.target.value })}
                          className={fieldCls}
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => removeItem(pIdx, iIdx)}
                        aria-label={`Remove ${it.chemical} from ${p.product_name}`}
                        className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 hover:text-slate-600 dark:hover:text-slate-200 transition-colors cursor-pointer"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                </div>

                <div className="flex items-end gap-2">
                  <div className="flex-1">
                    <EntityCombobox
                      options={catalog.map((c) => ({ value: String(c.id), label: c.name }))}
                      value={pending[p.product_name] || ''}
                      onChange={(v) => setPending((s) => ({ ...s, [p.product_name]: v }))}
                      placeholder="Add ingredient…"
                    />
                  </div>
                  <Button variant="secondary" size="sm" icon={Plus} onClick={() => addIngredient(p.product_name)} disabled={!pending[p.product_name]}>
                    Add
                  </Button>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className={labelCls}>Water override (optional)</label>
                    <input
                      aria-label={`Water override for ${p.product_name}`}
                      inputMode="decimal" type="number" min="0" max="100" step="any"
                      value={p.water_percentage ?? ''}
                      onChange={(e) => setWater(pIdx, e.target.value)}
                      placeholder="Automatic"
                      className={fieldCls}
                    />
                  </div>
                  <div className="text-xs text-slate-600 dark:text-slate-300 space-y-1">
                    {!resolved ? (
                      <p className="text-amber-600 dark:text-amber-400">Add an ingredient to resolve automatic water.</p>
                    ) : (
                      <>
                        <p>Automatic water: {pct(automatic)}%</p>
                        <p>Water used: {pct(waterUsed)}%{hasOverride ? ' (override)' : ''}</p>
                        <p>Total {pct(total)}%</p>
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </Modal>
  );
}