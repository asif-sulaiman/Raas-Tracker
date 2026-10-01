import { useState, useEffect, useMemo } from 'react';
import { Search, Loader2, AlertTriangle, Link2 } from 'lucide-react';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import Badge from '../ui/Badge';
import { formatNumber, formatDate } from '../../utils/format';
import { useAuth } from '../../context/AuthContext';
import { toast } from 'sonner';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

/**
 * Phase 3 — link unlinked PIs under one LC.
 *
 * Left: searchable checkbox list of unlinked PIs. Right/below: pick an
 * existing LC or create a new one. Company mismatch (PIs spanning companies,
 * or PIs vs the chosen LC's company) surfaces inline and blocks submit in
 * "existing" mode. Success toasts and refreshes the board via onLinked.
 * The wiring lane owns attach behaviour; preserve layout + copy.
 */
export default function LinkPIsModal({
  isOpen,
  unlinkedSales = [],
  existingLcs = [],
  onClose,
  onLinked,
}) {
  const { apiFetch } = useAuth();
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState([]);
  const [mode, setMode] = useState('existing');
  const [lcId, setLcId] = useState('');
  const [lcNumber, setLcNumber] = useState('');
  const [lcDate, setLcDate] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (isOpen) {
      // Always mounted (isOpen toggles visibility), so selection state must
      // resync whenever it opens — same pattern as ReviewModal.
      // oxlint-disable-next-line react/set-state-in-effect
      setQuery('');
      setSelected([]);
      setMode(existingLcs.length > 0 ? 'existing' : 'new');
      setLcId('');
      setLcNumber('');
      setLcDate('');
      setError(null);
      setSaving(false);
    }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return unlinkedSales;
    return unlinkedSales.filter((s) =>
      [s.pi_number, s.client_name, s.company_name]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q))
    );
  }, [query, unlinkedSales]);

  const selectedSales = useMemo(
    () => unlinkedSales.filter((s) => selected.includes(s.id)),
    [unlinkedSales, selected]
  );

  const selectedCompanies = useMemo(() => {
    const names = new Set(
      selectedSales.map((s) => s.company_name || s.client_name || 'Unknown').filter(Boolean)
    );
    return [...names];
  }, [selectedSales]);

  const chosenLc = useMemo(
    () => existingLcs.find((l) => String(l.id) === String(lcId)) || null,
    [existingLcs, lcId]
  );

  const chosenCompany = chosenLc
    ? chosenLc.company_name || chosenLc.client_name || null
    : null;

  // Company mismatch: selected PIs span >1 company, or differ from the chosen
  // LC's company. Inline only — submit stays enabled for "new" (the new LC
  // inherits the single selected company), blocked for "existing".
  const crossCompany = selectedCompanies.length > 1;
  const lcMismatch =
    mode === 'existing' &&
    chosenCompany &&
    selectedCompanies.length === 1 &&
    !selectedCompanies.includes(chosenCompany);
  const mismatch = crossCompany || lcMismatch;
  // A new LC inherits the selected PI's company — a null company would POST
  // null and 400, so block submit with an inline error instead.
  const nullCompany = mode === 'new' && selectedSales.some((s) => s.company_id == null);
  const noCompanyError =
    nullCompany && selected.length > 0
      ? 'Selected PI has no company — pick an existing LC or set its company first'
      : null;

  const toggle = (id) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const canSubmit =
    selected.length > 0 &&
    !saving &&
    (mode === 'new'
      ? lcNumber.trim() !== '' && !crossCompany && !nullCompany
      : lcId !== '' && !mismatch);

  const handleLink = async () => {
    setError(null);
    if (selected.length === 0) {
      setError('Select at least one PI to link');
      return;
    }
    if (mode === 'new' && !lcNumber.trim()) {
      setError('LC number is required for a new LC');
      return;
    }
    if (mode === 'existing' && !lcId) {
      setError('Pick an existing LC');
      return;
    }
    if (mismatch) {
      setError(
        crossCompany
          ? 'Selected PIs belong to different companies — link them separately'
          : `Company mismatch — these PIs belong to "${selectedCompanies[0]}" but LC "${chosenLc?.lc_number}" belongs to "${chosenCompany}"`
      );
      return;
    }
    if (mode === 'new' && selectedSales.some((s) => s.company_id == null)) {
      setError('Selected PI has no company — pick an existing LC or set its company first');
      return;
    }
    setSaving(true);
    try {
      let targetId = lcId;
      let targetNumber = chosenLc?.lc_number || '';
      if (mode === 'new') {
        const companyId = selectedSales[0]?.company_id;
        if (companyId == null) {
          setError('Selected PI has no company — pick an existing LC or set its company first');
          setSaving(false);
          return;
        }
        const res = await apiFetch('/api/lcs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            company_id: companyId,
            lc_number: lcNumber.trim(),
            lc_date: lcDate || null,
          }),
        });
        const created = await res.json().catch(() => ({}));
        targetId = created.id;
        targetNumber = created.lc_number || lcNumber.trim();
      }
      await apiFetch(`/api/lcs/${targetId}/pis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sale_ids: selected }),
      });
      toast.success(
        `${selected.length} PI${selected.length === 1 ? '' : 's'} linked to LC ${targetNumber}`
      );
      onClose?.();
      onLinked?.();
    } catch (err) {
      setError(err?.message || 'Could not link PIs');
      setSaving(false);
    }
  };

  const selectedTotal = selectedSales.reduce((sum, s) => sum + (s.total_value || 0), 0);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Link PIs to an LC"
      subtitle="Multi-select unlinked PIs, then pick a new or existing LC"
      maxWidth="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            icon={Loader2}
            loading={saving}
            disabled={!canSubmit}
            onClick={handleLink}
          >
            Link {selected.length > 0 ? `${selected.length} PI${selected.length === 1 ? '' : 's'}` : 'PIs'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
          <input
            aria-label="Search unlinked PIs"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search PI number or client…"
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 pl-8 pr-2.5 py-1.5 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          />
        </div>

        {/* PI checkbox list */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-semibold text-slate-800 dark:text-white">
              Unlinked PIs ({filtered.length})
            </h4>
            {selected.length > 0 && (
              <Badge variant="info" size="xs">
                {selected.length} selected · ${formatNumber(selectedTotal)}
              </Badge>
            )}
          </div>
          <div className="max-h-56 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-800">
            {filtered.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-slate-400 dark:text-slate-500">
                {unlinkedSales.length === 0
                  ? 'No unlinked PIs — every PI already sits under an LC.'
                  : 'No PIs match this search.'}
              </p>
            ) : (
              filtered.map((s) => {
                const checked = selected.includes(s.id);
                return (
                  <label
                    key={s.id}
                    className={`flex items-center gap-2.5 px-3 py-2 cursor-pointer transition-colors ${
                      checked
                        ? 'bg-blue-50/70 dark:bg-blue-950/30'
                        : 'hover:bg-slate-50 dark:hover:bg-slate-800/50'
                    }`}
                  >
                    <input
                      type="checkbox"
                      aria-label={`Select PI ${s.pi_number || s.id}`}
                      checked={checked}
                      onChange={() => toggle(s.id)}
                      className="h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500/40"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-900 dark:text-white">
                        {s.pi_number || `#${s.id}`}
                      </span>
                      <span className="block truncate text-[11px] text-slate-500 dark:text-slate-400">
                        {s.company_name || s.client_name || 'Unknown client'}
                        {s.pi_date ? ` • ${formatDate(s.pi_date)}` : ''}
                      </span>
                    </span>
                    <span className="shrink-0 text-xs font-semibold text-slate-700 dark:text-slate-200">
                      ${formatNumber(s.total_value ?? 0)}
                    </span>
                  </label>
                );
              })
            )}
          </div>
        </div>

        {/* Target LC — new or existing */}
        <div className="rounded-xl border border-slate-200 dark:border-slate-700 p-3 space-y-3">
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 cursor-pointer">
              <input
                type="radio"
                aria-label="Use existing LC"
                checked={mode === 'existing'}
                onChange={() => setMode('existing')}
                disabled={existingLcs.length === 0}
                className="h-4 w-4 text-blue-600 focus:ring-blue-500/40 disabled:opacity-40"
              />
              Existing LC
            </label>
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 cursor-pointer">
              <input
                type="radio"
                aria-label="Create new LC"
                checked={mode === 'new'}
                onChange={() => setMode('new')}
                className="h-4 w-4 text-blue-600 focus:ring-blue-500/40"
              />
              New LC
            </label>
          </div>

          {mode === 'existing' ? (
            existingLcs.length > 0 ? (
              <select
                aria-label="Existing LC"
                value={lcId}
                onChange={(e) => setLcId(e.target.value)}
                className={inputCls}
              >
                <option value="">Select LC…</option>
                {existingLcs.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.lc_number} — {l.company_name || l.client_name || ''} ({l.pi_count ?? 0}{' '}
                    PIs)
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-xs text-slate-500 dark:text-slate-400 italic">
                No LCs yet — create a new one below.
              </p>
            )
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
                  LC Number *
                </label>
                <input
                  aria-label="New LC number"
                  value={lcNumber}
                  onChange={(e) => setLcNumber(e.target.value)}
                  className={inputCls}
                  placeholder="LC-123456"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">
                  LC Date
                </label>
                <input
                  aria-label="New LC date"
                  type="date"
                  value={lcDate || ''}
                  onChange={(e) => setLcDate(e.target.value)}
                  className={inputCls}
                />
              </div>
            </div>
          )}
        </div>

        {/* Company-mismatch surfaces inline, never as a toast */}
        {mismatch && (
          <div
            role="alert"
            className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3"
          >
            <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5" /> Company mismatch
            </p>
            <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">
              {crossCompany
                ? `Selected PIs belong to different companies (${selectedCompanies.join(', ')}) — link each company's PIs separately.`
                : `These PIs belong to "${selectedCompanies[0]}" but LC "${chosenLc?.lc_number}" belongs to "${chosenCompany}". Pick a matching LC or create a new one.`}
            </p>
          </div>
        )}

        {noCompanyError && (
          <div
            role="alert"
            className="rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 p-3"
          >
            <p className="mt-1 text-xs text-amber-600 dark:text-amber-400">{noCompanyError}</p>
          </div>
        )}

        {selected.length > 0 && !mismatch && !noCompanyError && (
          <p className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <Link2 className="h-3.5 w-3.5" />
            {selected.length} PI{selected.length === 1 ? '' : 's'} →{' '}
            {mode === 'existing'
              ? chosenLc
                ? `LC ${chosenLc.lc_number}`
                : 'a selected LC'
              : lcNumber.trim()
                ? `new LC ${lcNumber.trim()}`
                : 'a new LC'}
          </p>
        )}

        {error && (
          <p role="alert" className="text-xs font-medium text-rose-600 dark:text-rose-400">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
