import { useState } from 'react';
import { toast } from 'sonner';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import SuggestionInput from '../ui/SuggestionInput';
import { useAuth } from '../../context/AuthContext';
import { formatNumber } from '../../utils/format';
import {
  MOVEMENT_KINDS,
  COUNT_KIND,
  kindOf,
  buildMovementBody,
  piecesError,
} from '../../utils/packaging';

const REASON_SUGGESTIONS = [
  'Supplier delivery',
  'Physical count',
  'Filled on the line',
  'Returned by customer',
  'Damaged in handling',
  'Discarded / unusable',
];

const fieldCls =
  'w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:outline-hidden focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500';

/**
 * One ledger row for one packaging type.
 *
 * The operator states what happened — a movement of N pieces, or how many they
 * counted — and never a delta they subtracted themselves. The server turns a
 * count into a delta inside its own transaction, so two people counting the
 * same stack cannot lose one of the two edits (the chemical adjust modal does,
 * because it subtracts in the browser).
 *
 * Mounted per opening (the parent renders it only while a row is selected), so
 * there is no reset-on-open effect to write and no stale field to carry over.
 */
export default function PackagingMovementModal({ item, onClose, onSaved }) {
  const { apiFetch } = useAuth();
  const [kind, setKind] = useState('RECEIVED');
  const [pieces, setPieces] = useState('');
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState(null);

  const isCount = kind === COUNT_KIND;
  const number = Math.trunc(Number(pieces) || 0);
  const result = isCount ? number : (item.qty || 0) + number * (kindOf(kind)?.sign ?? 1);

  const handleSave = async () => {
    let invalid = piecesError(pieces, kind);
    if (!invalid && result < 0) {
      invalid = `Only ${item.qty || 0} ${item.label} on site — cannot remove ${number}`;
    }
    if (invalid) {
      setFormError(invalid);
      return;
    }
    setSaving(true);
    try {
      await apiFetch('/api/packaging/movements', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildMovementBody(kind, pieces, { label: item.label, reason })),
      });
      toast.success(isCount
        ? `Counted ${number} ${item.label}`
        : `${kindOf(kind)?.label || kind}: ${number} ${item.label}`);
      onSaved();
    } catch (err) {
      setFormError(err.message || 'Could not record the movement');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      isOpen
      onClose={onClose}
      title="Record Packaging Movement"
      subtitle={`${item.label} — ${formatNumber(item.qty || 0, 0)} ${item.unit || 'PCS'} on site`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="primary" onClick={handleSave} loading={saving} disabled={saving}>
            Save Movement
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5" htmlFor="packaging-kind">
            What happened
          </label>
          <select
            id="packaging-kind"
            aria-label="Movement type"
            value={kind}
            onChange={(e) => { setKind(e.target.value); setFormError(null); }}
            className={fieldCls}
          >
            {MOVEMENT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
            {isCount ? 'Counted on site' : 'Pieces'}
          </label>
          <input
            aria-label={isCount ? 'Counted on site' : 'Pieces'}
            inputMode="numeric" type="number"
            min="0"
            step="1"
            value={pieces}
            onChange={(e) => { setPieces(e.target.value); setFormError(null); }}
            placeholder={isCount ? String(item.qty || 0) : '0'}
            className={fieldCls}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">Reason</label>
          <SuggestionInput
            aria-label="Reason"
            options={REASON_SUGGESTIONS}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Optional"
            className={fieldCls}
          />
        </div>

        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-3 bg-slate-50 dark:bg-slate-800/60 flex items-center justify-between">
          <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            Count after saving
          </span>
          <span className="text-lg font-bold text-slate-900 dark:text-white font-mono">
            {pieces === '' || result < 0 ? '—' : formatNumber(result, 0)} {item.unit || 'PCS'}
          </span>
        </div>

        {formError && (
          <p className="text-xs font-medium text-rose-600 dark:text-rose-400" role="alert">{formError}</p>
        )}
      </div>
    </Modal>
  );
}
