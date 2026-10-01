import { useState, useEffect } from 'react';
import { Pencil, Loader2, Trash2, Unlink, AlertTriangle, ChevronRight } from 'lucide-react';
import Modal from '../modals/Modal';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { formatNumber, formatDate } from '../../utils/format';
import { STAGE_LABELS, STAGE_BADGE } from '../../utils/sales';
import { useAuth } from '../../context/AuthContext';
import { useConfirm } from '../../context/ConfirmContext';
import { toast } from 'sonner';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

function Field({ label, value, mono }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wider text-slate-400 dark:text-slate-500">{label}</p>
      <p className={`text-sm font-medium text-slate-900 dark:text-white mt-0.5 ${mono ? 'font-mono' : ''}`}>
        {value || '—'}
      </p>
    </div>
  );
}

/**
 * Phase 3 — LC-level modal.
 *
 * View/edit the LC's mutable fields (dates, bank ref, notes — the LC number
 * and company are immutable, enforced server-side), manage the attached-PI
 * list with detach buttons, and a danger-zone delete that stays disabled
 * with an explanation while PIs are attached (the API answers 409).
 * The wiring lane owns fetch/save behaviour; preserve layout + copy.
 */
export default function LcDetailModal({ lcId, isOpen, onClose, onSaved, onViewPI }) {
  const { apiFetch } = useAuth();
  const { confirm } = useConfirm();
  const [lc, setLc] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [lcDate, setLcDate] = useState('');
  const [expiryDate, setExpiryDate] = useState('');
  const [bankRef, setBankRef] = useState('');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [detachingId, setDetachingId] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const refresh = async (id) => {
    try {
      const res = await apiFetch(`/api/lcs/${id}`);
      const data = await res.json();
      setLc(data.error ? null : data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err?.message || 'Could not load this LC');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen && lcId) {
      // Always mounted (isOpen toggles visibility), so state must resync here.
      // oxlint-disable-next-line react/set-state-in-effect
      setLoading(true);
      setEditing(false);
      setError(null);
      setLoadError(null);
      setLc(null);
      refresh(lcId);
    } else {
      setLc(null);
    }
    // `refresh` is a plain per-render function — adding it would refetch on
    // every render.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, lcId]);

  const startEditing = () => {
    if (!lc) return;
    setLcDate(lc.lc_date || '');
    setExpiryDate(lc.expiry_date || '');
    setBankRef(lc.bank_ref || '');
    setNotes(lc.notes || '');
    setError(null);
    setEditing(true);
  };

  // PUT accepts dates/bank_ref/notes only — lc_number + company_id are
  // deliberately absent so the form cannot offer immutable fields.
  const handleSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const res = await apiFetch(`/api/lcs/${lcId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lc_date: lcDate || null,
          expiry_date: expiryDate || null,
          bank_ref: bankRef.trim() || null,
          notes: notes.trim() || null,
        }),
      });
      const saved = await res.json();
      setLc(saved.error ? lc : saved);
      setEditing(false);
      toast.success('LC updated');
      onSaved?.();
    } catch (err) {
      setError(err?.message || 'Failed to save LC');
    } finally {
      setSaving(false);
    }
  };

  const handleDetach = async (pi) => {
    const ok = await confirm({
      title: `Unlink PI "${pi.pi_number || pi.id}"?`,
      message: `The PI returns to the unlinked list — its data is kept.`,
      confirmLabel: 'Unlink',
      danger: true,
    });
    if (!ok) return;
    setDetachingId(pi.id);
    try {
      await apiFetch(`/api/lcs/${lcId}/pis/${pi.id}`, { method: 'DELETE' });
      toast.success(`PI "${pi.pi_number || pi.id}" unlinked`);
      await refresh(lcId);
      onSaved?.();
    } catch (err) {
      toast.error(err.message || 'Could not unlink PI');
    } finally {
      setDetachingId(null);
    }
  };

  const handleDelete = async () => {
    const ok = await confirm({
      title: `Delete LC "${lc?.lc_number}"?`,
      message: 'This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      await apiFetch(`/api/lcs/${lcId}`, { method: 'DELETE' });
      toast.success(`LC "${lc?.lc_number}" deleted`);
      onClose?.();
      onSaved?.();
    } catch (err) {
      toast.error(err.message || 'Could not delete LC');
      setDeleting(false);
    }
  };

  const pis = Array.isArray(lc?.pis) ? lc.pis : [];
  const total = lc?.total_value ?? pis.reduce((sum, p) => sum + (p.total_value || 0), 0);
  const blocked = pis.length > 0;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        lc ? `LC ${lc.lc_number} — ${lc.company_name || lc.client_name || 'Unknown client'}` : 'LC Details'
      }
      subtitle={lc ? `Stage: ${STAGE_LABELS[lc.stage] || lc.stage} · ${pis.length} PI${pis.length === 1 ? '' : 's'} · $${formatNumber(total)}` : ''}
      maxWidth="max-w-2xl"
      footer={
        lc && !loading ? (
          editing ? (
            <>
              <Button variant="secondary" size="sm" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </Button>
              <Button variant="success" size="sm" icon={Loader2} loading={saving} onClick={handleSave}>
                Save Changes
              </Button>
            </>
          ) : (
            <Button variant="secondary" size="sm" icon={Pencil} onClick={startEditing}>
              Edit LC
            </Button>
          )
        ) : null
      }
    >
      {loading ? (
        <p className="text-sm text-slate-500 py-6 text-center">Loading…</p>
      ) : loadError && !lc ? (
        <div className="py-6 text-center">
          <p className="text-sm font-semibold text-rose-600 dark:text-rose-400">Could not load this LC</p>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">{loadError}</p>
          <Button variant="secondary" size="sm" className="mt-4" onClick={() => { setLoading(true); refresh(lcId); }}>
            Retry
          </Button>
        </div>
      ) : !lc ? (
        <p className="text-sm text-rose-500 py-6 text-center">LC not found</p>
      ) : (
        <div className="space-y-5">
          <div className="flex items-center gap-2">
            <Badge variant={STAGE_BADGE[lc.stage] || 'default'}>
              {STAGE_LABELS[lc.stage] || lc.stage}
            </Badge>
            <span className="font-mono text-xs text-slate-500 dark:text-slate-400">
              {lc.lc_number}
            </span>
          </div>

          {editing ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">LC Date</label>
                <input
                  aria-label="LC date"
                  type="date"
                  value={lcDate || ''}
                  onChange={(e) => setLcDate(e.target.value)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Expiry Date</label>
                <input
                  aria-label="Expiry date"
                  type="date"
                  value={expiryDate || ''}
                  onChange={(e) => setExpiryDate(e.target.value)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Bank Reference</label>
                <input
                  aria-label="Bank reference"
                  value={bankRef}
                  onChange={(e) => setBankRef(e.target.value)}
                  className={inputCls}
                  placeholder="Bank ref…"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-slate-600 dark:text-slate-300">Notes</label>
                <input
                  aria-label="Notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  className={inputCls}
                  placeholder="Free-form notes…"
                />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Field label="LC Date" value={formatDate(lc.lc_date)} />
              <Field label="Expiry Date" value={formatDate(lc.expiry_date)} />
              <Field label="Bank Ref" value={lc.bank_ref} mono />
              <Field label="Notes" value={lc.notes} />
            </div>
          )}
          {editing && error && (
            <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
          )}

          {/* Attached PIs */}
          <div>
            <h4 className="text-sm font-semibold text-slate-800 dark:text-white mb-2">
              Attached PIs ({pis.length}) — ${formatNumber(total)}
            </h4>
            {pis.length === 0 ? (
              <p className="text-xs text-slate-400 dark:text-slate-500">
                No PIs attached yet — use “Link PIs” on the board.
              </p>
            ) : (
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 divide-y divide-slate-100 dark:divide-slate-800">
                {pis.map((pi) => (
                  <div key={pi.id} className="flex items-center gap-1.5 px-2.5 py-2">
                    <button
                      type="button"
                      onClick={() => onViewPI?.(pi)}
                      aria-label={`View PI ${pi.pi_number || pi.id}`}
                      title={`View PI ${pi.pi_number || pi.id}`}
                      className="flex-1 min-w-0 flex items-center gap-2 text-left cursor-pointer rounded focus:outline-none focus:ring-2 focus:ring-blue-500/40"
                    >
                      <span className="truncate text-xs font-medium text-slate-900 dark:text-white">
                        {pi.pi_number || `#${pi.id}`}
                      </span>
                      <span className="shrink-0 text-[11px] text-slate-400">
                        ${formatNumber(pi.total_value ?? 0)} · {pi.item_count ?? 0} items
                      </span>
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-300 dark:text-slate-600" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDetach(pi)}
                      disabled={detachingId === pi.id}
                      aria-label={`Detach PI ${pi.pi_number || pi.id}`}
                      title={`Unlink PI ${pi.pi_number || pi.id}`}
                      className="p-1.5 rounded-lg text-slate-400 hover:text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-950/40 disabled:opacity-40 cursor-pointer"
                    >
                      <Unlink className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Danger zone — delete stays disabled while PIs are attached */}
          <div className="rounded-xl border border-rose-200 dark:border-rose-800 bg-rose-50/50 dark:bg-rose-950/20 p-3.5">
            <h4 className="text-sm font-semibold text-rose-700 dark:text-rose-300">Danger zone</h4>
            {blocked ? (
              <p className="mt-1 flex items-start gap-1.5 text-xs text-rose-600 dark:text-rose-400">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                This LC still has {pis.length} attached PI{pis.length === 1 ? '' : 's'} — unlink
                {pis.length === 1 ? ' it' : ' them'} above before deleting. Deleting an LC with
                PIs attached is refused (409).
              </p>
            ) : (
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                No PIs attached — this LC can be deleted safely.
              </p>
            )}
            <Button
              variant="danger"
              size="sm"
              icon={Trash2}
              loading={deleting}
              disabled={blocked || deleting}
              onClick={handleDelete}
              aria-label="Delete LC"
              className="mt-2.5"
            >
              Delete LC
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
