import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, Loader2 } from 'lucide-react';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';
import { formatNumber } from '../../utils/format';
import { COMMON_UNITS } from '../../utils/units';
import { toast } from 'sonner';

function parseFactor(raw) {
  const v = parseFloat(raw);
  return Number.isFinite(v) && v > 0 ? v : null;
}

/**
 * Inline unit mapping for flagged upload rows.
 *
 * Props:
 * - rows: flagged comparison rows [{name, upload_this, upload_last, upload_unit, db_unit}]
 * - onMappingsSaved(units: string[]): called with the unit strings now mapped
 *
 * Rows are grouped by their unmapped unit string, so one mapping covers
 * every identical row (apply-to-all by construction). Each mapping is
 * persisted as a unit conversion; the parent hides the group once saved
 * and enables Approve & Import when nothing remains.
 */
export default function UnitMapping({ rows = [], onMappingsSaved }) {
  const { apiFetch } = useAuth();
  const groups = useMemo(() => {
    const map = new Map();
    for (const row of rows) {
      const unit = (row.upload_unit || '').toUpperCase();
      if (!unit) continue;
      if (!map.has(unit)) map.set(unit, { unit, rows: [], dbUnits: new Set() });
      const g = map.get(unit);
      g.rows.push(row);
      if (row.db_unit) g.dbUnits.add(String(row.db_unit).toUpperCase());
    }
    return [...map.values()];
  }, [rows]);
  const [knownUnits, setKnownUnits] = useState([]);
  // Per-unit forms persist across renders; the parent remounts (via key)
  // whenever the flagged set changes, so no effect-driven reset is needed.
  const [forms, setForms] = useState(() => {
    const init = {};
    for (const g of groups) {
      init[g.unit] = { target: [...g.dbUnits][0] || '', factor: '' };
    }
    return init;
  });
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch('/api/unit-conversions');
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) {
          const units = new Set(COMMON_UNITS);
          for (const c of data) {
            if (c.from_unit) units.add(String(c.from_unit).toUpperCase());
            if (c.to_unit) units.add(String(c.to_unit).toUpperCase());
          }
          setKnownUnits([...units].sort());
        }
      } catch {
        if (!cancelled) setKnownUnits([...COMMON_UNITS]);
      }
    })();
    return () => { cancelled = true; };
  }, [apiFetch]);

  const setForm = (unit, patch) =>
    setForms((prev) => ({ ...prev, [unit]: { ...prev[unit], ...patch } }));

  const groupValid = (g) => {
    const f = forms[g.unit];
    return !!(f && f.target && parseFactor(f.factor) !== null);
  };

  const allValid = groups.length > 0 && groups.every(groupValid);

  const rowQty = (row) => row.upload_this ?? row.upload_last ?? 0;

  const handleSave = async () => {
    if (!allValid || saving) return;
    setSaving(true);
    const saved = [];
    const nextErrors = {};
    try {
      for (const g of groups) {
        const f = forms[g.unit];
        try {
          const res = await apiFetch('/api/unit-conversions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              from_unit: g.unit,
              to_unit: f.target,
              factor: parseFactor(f.factor),
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
          saved.push(g.unit);
        } catch (err) {
          nextErrors[g.unit] = err.message || 'Could not save mapping';
        }
      }
      setErrors(nextErrors);
      if (saved.length > 0) {
        toast.success(`Saved ${saved.length} unit mapping${saved.length === 1 ? '' : 's'}`);
        onMappingsSaved?.(saved);
      }
      if (Object.keys(nextErrors).length > 0) {
        toast.error('Some mappings failed to save');
      }
    } finally {
      setSaving(false);
    }
  };

  if (groups.length === 0) return null;

  return (
    <div className="rounded-xl border border-amber-300 dark:border-amber-700 bg-amber-50/60 dark:bg-amber-950/20 p-5 shadow-xs">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-100 dark:bg-amber-900/40 shrink-0">
          <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-400" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
            {groups.length} unmapped unit{groups.length === 1 ? '' : 's'} — approval blocked
          </h3>
          <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">
            Map each unit to a base unit to proceed. Mappings are saved for future uploads.
          </p>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {groups.map((g) => {
          const f = forms[g.unit] || { target: '', factor: '', save: true };
          const factor = parseFactor(f.factor);
          return (
            <div
              key={g.unit}
              className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-3.5"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <span className="text-xs font-semibold text-slate-800 dark:text-slate-100">
                  {g.rows.length} row{g.rows.length === 1 ? '' : 's'} × {g.unit}
                </span>
                <span className="text-[11px] text-slate-500 dark:text-slate-400 truncate max-w-55">
                  {g.rows.map((r) => r.name).join(', ')}
                </span>
              </div>
              <div className="mt-2.5 flex flex-wrap items-end gap-3">
                <label className="text-xs text-slate-600 dark:text-slate-400">
                  Target unit
                  <select
                    value={f.target}
                    onChange={(e) => setForm(g.unit, { target: e.target.value })}
                    className="ml-2 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm text-slate-900 dark:text-white"
                  >
                    <option value="">Select…</option>
                    {[...new Set([...(knownUnits.length ? knownUnits : COMMON_UNITS), ...g.dbUnits])].map((u) => (
                      <option key={u} value={u}>{u}</option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-slate-600 dark:text-slate-400">
                  1 {g.unit} = 
                  <input
                    value={f.factor}
                    onChange={(e) => setForm(g.unit, { factor: e.target.value })}
                    placeholder="200"
                    inputMode="decimal"
                    className="ml-2 w-24 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2 py-1.5 text-sm text-slate-900 dark:text-white"
                  />
                  <span className="ml-1">{f.target || '…'}</span>
                </label>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {factor !== null && f.target ? (
                    <>Preview: <strong className="text-slate-800 dark:text-slate-100">{formatNumber(g.rows.reduce((s, r) => s + rowQty(r) * factor, 0))} {f.target}</strong></>
                  ) : (
                    'Enter a positive factor to preview'
                  )}
                </span>
              </div>
              {errors[g.unit] && (
                <p className="mt-1.5 text-xs text-rose-600 dark:text-rose-400">{errors[g.unit]}</p>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-4 flex items-center justify-end">
        <Button
          variant="primary"
          size="sm"
          icon={saving ? Loader2 : allValid ? CheckCircle2 : ArrowRight}
          loading={saving}
          disabled={!allValid || saving}
          onClick={handleSave}
          title={allValid ? 'Save unit mappings' : 'Fill in every target unit and factor first'}
        >
          Save mappings
        </Button>
      </div>
    </div>
  );
}
