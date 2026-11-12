import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  BookOpen,
  FlaskConical,
  Plus,
  Trash2,
  Eye,
  ArrowLeft,
  Edit3,
  Check,
  AlertTriangle,
  Factory
} from 'lucide-react';
import clsx from 'clsx';
import Button, { ICON_HIT_AREA } from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import EntityCombobox from '../components/ui/EntityCombobox';
import Modal from '../components/modals/Modal';
import KpiCard from '../components/cards/KpiCard';
import { formatNumber } from '../utils/format';
import { isBalanced } from '../utils/formulation';
import { INVOICE_STATUS_LABELS } from '../utils/invoices';
import {
  SOURCE_IN_HOUSE,
  SOURCE_OUTWARD,
  NO_STOCK_LABEL,
  isOutward,
  sourceLabel,
  outwardEvidence,
} from '../utils/ingredientSource';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';
import ProductionRunModal from '../components/modals/ProductionRunModal';

const inputCls =
  'w-full px-3 py-2 text-base sm:text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent';

// recipe_products.water_percentage is a float4, so a stored 33.33 reads back as
// 33.332998… — round to the input's own step for display. An explicit 0 stays
// "0", never blank.
const waterDraftFromServer = (value) => (
  value == null ? '' : String(Math.round(Number(value) * 100) / 100)
);

// A 400 answers with details:[{field,message}]. The field that broke the save
// keeps its own message here instead of being flattened into a generic toast.
const fieldErrorOf = (err, field) => (
  (Array.isArray(err?.fields) ? err.fields : [])
    .find((entry) => entry?.field === field)?.message || null
);

const messagesOf = (err) => (Array.isArray(err?.fields)
  ? err.fields.map((entry) => entry?.message).filter(Boolean)
  : []);

// One receipt line names the material, what arrived, who sent it and when. A
// line another recipe or run already references says so — and stays pickable.
const sourceOptionLabel = (source) => [
  source.chemical_name,
  `${formatNumber(source.quantity)} ${source.unit}`,
  source.supplier_name,
  source.purchase_number,
  source.purchase_date,
  source.frozen ? 'referenced — usable, not editable' : null,
].filter(Boolean).join(' · ');

// What the client delivered, and the fact that none of our stock moved.
const evidenceText = (row) => [
  ...outwardEvidence(row),
  `Received ${formatNumber(row.source_qty)} ${row.source_unit || row.unit || ''}`.trim(),
  `Deducted ${formatNumber(0)} ${row.unit || row.source_unit || ''}`.trim(),
].join(' · ');

/**
 * Receipt picker for one outward ingredient: only lines this client delivered
 * are listed, and only lines of this material when a row is being switched.
 */
function OutwardSourcePicker({
  ariaLabel, id, label, sources, state, value, onChange, chemicalId, onRetry,
  className = inputCls,
}) {
  const list = chemicalId == null
    ? sources
    : sources.filter((s) => String(s.chemical_id) === String(chemicalId));
  const picked = list.find((s) => String(s.line_id) === String(value));
  return (
    <div>
      {label && (
        <label
          htmlFor={id}
          className="block text-xs text-slate-500 dark:text-slate-400 mb-1"
        >
          {label}
        </label>
      )}
      <EntityCombobox
        id={id}
        aria-label={ariaLabel}
        options={list.map((s) => ({ value: s.line_id, label: sourceOptionLabel(s) }))}
        value={value}
        onChange={onChange}
        disabled={state.loading}
        placeholder={state.loading ? 'Loading client receipts…' : 'Search client receipts…'}
        emptyMessage="This client has no outward receipt recorded"
        hint="Pick the receipt line the client delivered"
        className={className}
      />
      {state.noClient && (
        <p
          role="alert"
          className="mt-1 text-[11px] font-medium text-amber-700 dark:text-amber-300"
        >
          This recipe has no client on its PI, so outward receipts cannot be listed.
        </p>
      )}
      {state.error && (
        <div role="alert" className="mt-1 space-y-0.5">
          <p className="text-[11px] font-medium text-rose-600 dark:text-rose-400">
            Could not list the outward receipts for this client
          </p>
          <p className="text-[11px] text-rose-600 dark:text-rose-400">{state.error}</p>
          {state.fieldError && (
            <p className="text-[11px] font-medium text-rose-600 dark:text-rose-400">
              {state.fieldError}
            </p>
          )}
          <Button variant="secondary" size="sm" onClick={onRetry}>Try again</Button>
        </div>
      )}
      {picked?.frozen && (
        <div className="mt-1 flex items-start gap-1.5">
          <Badge variant="warning">Referenced</Badge>
          <p className="text-[11px] text-slate-500 dark:text-slate-400">
            Already used by a recipe or a run: it cannot be edited here, but it is usable.
          </p>
        </div>
      )}
    </div>
  );
}

const OUTWARD_SOURCES_EMPTY = {
  loading: false, error: null, fieldError: null, noClient: false,
};

// The API refuses this pairing, so it is refused here first and said in the
// same words the operator sees on the field.
const NEED_RECEIPT_MESSAGE = 'An outward ingredient needs a receipt line — pick the receipt line the client delivered.';

/**
 * One product's formulation. The invoice defines the product and its
 * quantities; this card only owns the chemicals and their percentages, so a
 * product added to the invoice later appears as an empty card on reload.
 */
function ProductFormulationCard({
  product, chemicals, isAdmin, onAdd, onUpdate, onDelete, onSetWater,
  onUpdateSource, outward, onLoadSources, onRetrySources, presetSource,
}) {
  const [chemical, setChemical] = useState(presetSource?.chemical || '');
  const [pct, setPct] = useState('');
  const [editItemId, setEditItemId] = useState(null);
  const [editPct, setEditPct] = useState('');
  const [saving, setSaving] = useState(false);
  const [savingWater, setSavingWater] = useState(false);

  // The add footer holds its own source draft — what the operator is about to
  // write, not what is already stored. A receipt carried in from the outward
  // register pre-fills it, so the entry point is never a dead end.
  const [addSource, setAddSource] = useState(
    presetSource?.sourceType || SOURCE_IN_HOUSE,
  );
  const [addLine, setAddLine] = useState(presetSource?.lineId || '');
  const [addSourceError, setAddSourceError] = useState(null);

  // One row's source is edited at a time, so the table can never end up with
  // two half-written sources.
  const [sourceItemId, setSourceItemId] = useState(null);
  const [sourceDraft, setSourceDraft] = useState(
    { sourceType: SOURCE_IN_HOUSE, lineId: '' },
  );
  const [sourceError, setSourceError] = useState(null);
  const [savingSource, setSavingSource] = useState(false);

  // The water input is a draft the operator types; the server wins whenever it
  // reports a different value (save, add/remove item, dialog re-open). Syncing
  // during render — not in an effect — means the stored value is on screen at
  // the first paint instead of one commit later, so it can never clobber an
  // edit typed in between.
  const serverWater = product.water_percentage;
  const waterKey = `${product.product_name}\u0000${serverWater}`;
  const [waterDraft, setWaterDraft] = useState(
    () => waterDraftFromServer(serverWater));
  const [syncedWaterKey, setSyncedWaterKey] = useState(waterKey);
  if (syncedWaterKey !== waterKey) {
    setSyncedWaterKey(waterKey);
    setWaterDraft(waterDraftFromServer(serverWater));
  }

  const ingredientPct = product.percentage;
  const trimmedWater = waterDraft.trim();
  const draftWater = trimmedWater === '' ? null : Number(trimmedWater);
  // A draft that is neither blank nor a finite number keeps the remainder
  // preview (the save below refuses it); it must never render "NaN%".
  const usableDraft = draftWater != null && Number.isFinite(draftWater);
  const waterPct = usableDraft
    ? draftWater
    : (product.items.length ? 100 - ingredientPct : null);
  const grandTotal = waterPct == null ? null : ingredientPct + waterPct;
  const offBalance = grandTotal != null && !isBalanced(grandTotal);

  const available = chemicals.filter(
    (c) => !product.items.some((item) => item.chemical_name === c.name)
  );

  const submitAdd = async () => {
    if (!chemical || pct === '') {
      toast.error('Select chemical and enter percentage');
      return;
    }
    if (addSource === SOURCE_OUTWARD && !addLine) {
      setAddSourceError(NEED_RECEIPT_MESSAGE);
      return;
    }
    setSaving(true);
    try {
      const ok = await onAdd(product, chemical, parseFloat(pct), {
        sourceType: addSource,
        lineId: addLine ? Number(addLine) : null,
      });
      if (ok) {
        setChemical('');
        setPct('');
        // The delivery on that line is now committed to this formulation; the
        // next ingredient needs its own receipt.
        if (addSource === SOURCE_OUTWARD) setAddLine('');
        setAddSourceError(null);
      }
    } finally {
      setSaving(false);
    }
  };

  const changeAddSource = (event) => {
    const next = event.target.value;
    setAddSource(next);
    if (next === SOURCE_IN_HOUSE) setAddLine('');
    else onLoadSources();
    setAddSourceError(null);
  };

  const pickReceiptForAdd = (lineId) => {
    setAddLine(lineId);
    const source = outward.sources.find((s) => String(s.line_id) === String(lineId));
    // The receipt names the material. A different chemical typed into the next
    // field could only be refused, so the field follows the line.
    if (source?.chemical_name) setChemical(source.chemical_name);
    setAddSourceError(null);
  };

  const openSourceEditor = (item) => {
    setEditItemId(null);
    setSourceItemId(item.id);
    setSourceDraft({
      sourceType: isOutward(item) ? SOURCE_OUTWARD : SOURCE_IN_HOUSE,
      lineId: item.outward_purchase_line_id == null
        ? ''
        : String(item.outward_purchase_line_id),
    });
    setSourceError(null);
    // The stored line can only be named once its client's receipts are back.
    if (isOutward(item)) onLoadSources();
  };

  const changeSourceDraft = (event) => {
    const next = event.target.value;
    setSourceDraft((draft) => ({
      sourceType: next,
      lineId: next === SOURCE_OUTWARD ? draft.lineId : '',
    }));
    setSourceError(null);
    if (next === SOURCE_OUTWARD) onLoadSources();
  };

  const closeSourceEditor = () => {
    setSourceItemId(null);
    setSourceError(null);
  };

  const submitSource = async (item) => {
    if (sourceDraft.sourceType === SOURCE_OUTWARD && !sourceDraft.lineId) {
      setSourceError(NEED_RECEIPT_MESSAGE);
      return;
    }
    setSavingSource(true);
    try {
      const result = await onUpdateSource(
        product,
        item,
        sourceDraft.sourceType,
        sourceDraft.lineId ? Number(sourceDraft.lineId) : null,
      );
      if (result?.ok) {
        closeSourceEditor();
      } else {
        setSourceError(
          (result?.messages || []).join(' — ')
          || 'Could not change where this ingredient comes from',
        );
      }
    } finally {
      setSavingSource(false);
    }
  };

  const submitEdit = async (item) => {
    setSaving(true);
    try {
      const ok = await onUpdate(product, item, parseFloat(editPct));
      if (ok) {
        setEditItemId(null);
        setEditPct('');
      }
    } finally {
      setSaving(false);
    }
  };

  const submitWater = async () => {
    const trimmed = waterDraft.trim();
    const value = trimmed === '' ? null : Number(trimmed);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 100)) {
      toast.error('Water must be between 0 and 100');
      return;
    }
    setSavingWater(true);
    try {
      await onSetWater(product, value);
    } finally {
      setSavingWater(false);
    }
  };

  return (
    <section
      aria-label={`${product.product_name} formulation`}
      className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden"
    >
      <div className="p-4 border-b border-slate-100 dark:border-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400">
              <FlaskConical className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
                {product.product_name}
              </h3>
              <p className="text-[11px] text-slate-500 dark:text-slate-400">
                {product.items.length} ingredient{product.items.length === 1 ? '' : 's'}
                {' '}&middot;{' '}
                {product.percentage.toFixed(1)}% raw materials
              </p>
            </div>
          </div>
          <Badge
            variant={
              product.formulated && isBalanced(grandTotal) ? 'success' : 'warning'
            }
          >
            {!product.formulated
              ? 'Empty formulation'
              : isBalanced(grandTotal) ? 'Ready' : 'Not balanced'}
          </Badge>
        </div>

        <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs text-slate-600 dark:text-slate-400">
          <p>Invoiced {formatNumber(product.invoiced_qty)} {product.unit}</p>
          <p>Produced {formatNumber(product.produced_qty)} {product.unit}</p>
          <p className="font-semibold text-slate-800 dark:text-slate-200">
            Remaining {formatNumber(product.remaining_qty)} {product.unit}
          </p>
        </div>
        <p className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">
          Quantities are for the {formatNumber(product.remaining_qty)} {product.unit} still to produce
        </p>

        {product.line_count > 1 && (
          <p className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">
            {product.line_count} lines merged · {formatNumber(product.invoiced_qty)} {product.unit} on the invoice
          </p>
        )}
        {product.unconverted && product.unconverted.length > 0 && (
          <p className="mt-1.5 flex items-start gap-1 text-[11px] text-rose-600 dark:text-rose-400">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              Some invoice lines need a unit conversion (
              {product.unconverted.map((u) => `${u.from_unit} → ${u.to_unit}`).join(', ')}
              ) — add it in Unit Conversions before producing.
            </span>
          </p>
        )}
      </div>

      <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-100 dark:border-slate-800">
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">#</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Chemical</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Percentage</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Source</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Chemical needed</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Current Stock</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Stock remaining</th>
            <th className="px-4 py-2.5 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
          {product.items.map((item, idx) => (
            <tr key={item.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
              <td className="px-4 py-2.5 text-slate-400">{idx + 1}</td>
              <td className="px-4 py-2.5 font-medium text-slate-900 dark:text-white">
                {item.chemical_name}
              </td>
              <td className="px-4 py-2.5">
                {editItemId === item.id ? (
                  <div className="flex items-center gap-1.5">
                    <input
                      inputMode="decimal" type="number"
                      step="0.01"
                      min="0"
                      max="100"
                      aria-label={`Percentage for ${item.chemical_name} in ${product.product_name}`}
                      value={editPct}
                      onChange={(e) => setEditPct(e.target.value)}
                      className="w-20 px-2 py-1 text-base sm:text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white"
                      autoFocus
                    />
                    <button
                      aria-label={`Save ${item.chemical_name} percentage in ${product.product_name}`}
                      onClick={() => submitEdit(item)}
                      disabled={saving}
                      className="p-1 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 rounded cursor-pointer"
                    >
                      <Check className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  <button
                    aria-label={`Edit ${item.chemical_name} percentage in ${product.product_name}`}
                    onClick={() => { setSourceItemId(null); setEditItemId(item.id); setEditPct(String(item.percentage)); }}
                    className="px-2 py-1 text-sm rounded-md bg-slate-50 dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 font-mono transition-colors cursor-pointer"
                  >
                    {item.percentage}%
                  </button>
                )}
              </td>
              <td className="px-4 py-2.5 align-top">
                {sourceItemId === item.id ? (
                  <div className="space-y-2">
                    <div>
                      <label
                        htmlFor={`row-source-${item.id}`}
                        className="block text-[11px] text-slate-500 dark:text-slate-400 mb-1"
                      >
                        Where this comes from
                      </label>
                      <select
                        id={`row-source-${item.id}`}
                        aria-label={`Source for ${item.chemical_name} in ${product.product_name}`}
                        value={sourceDraft.sourceType}
                        onChange={changeSourceDraft}
                        className="w-full px-2 py-1 text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      >
                        <option value={SOURCE_IN_HOUSE}>In house — from our stock</option>
                        <option value={SOURCE_OUTWARD}>Outward — the client delivered it</option>
                      </select>
                    </div>
                    {sourceDraft.sourceType === SOURCE_OUTWARD && (
                      <OutwardSourcePicker
                        ariaLabel={`Outward receipt for ${item.chemical_name} in ${product.product_name}`}
                        id={`row-receipt-${item.id}`}
                        label="Client receipt line"
                        sources={outward.sources}
                        state={outward}
                        value={sourceDraft.lineId}
                        onChange={(lineId) => {
                          setSourceDraft((draft) => ({ ...draft, lineId }));
                          setSourceError(null);
                        }}
                        chemicalId={item.chemical_id}
                        onRetry={onRetrySources}
                      />
                    )}
                    {sourceError && (
                      <p
                        role="alert"
                        className="text-[11px] font-medium text-rose-600 dark:text-rose-400"
                      >
                        {sourceError}
                      </p>
                    )}
                    <div className="flex items-center gap-1.5">
                      <Button
                        variant="primary"
                        size="sm"
                        icon={Check}
                        onClick={() => submitSource(item)}
                        loading={savingSource}
                        aria-label={`Save source for ${item.chemical_name} in ${product.product_name}`}
                      >
                        Save source
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={closeSourceEditor}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <button
                      aria-label={`Change source for ${item.chemical_name} in ${product.product_name}`}
                      onClick={() => openSourceEditor(item)}
                      className="px-2 py-1 text-sm rounded-md bg-slate-50 dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 transition-colors cursor-pointer"
                    >
                      {sourceLabel(item)}
                    </button>
                    {isOutward(item) && (
                      <p
                        aria-label={`Outward source for ${item.chemical_name} in ${product.product_name}`}
                        className="mt-1 text-[11px] text-slate-500 dark:text-slate-400"
                      >
                        {evidenceText(item)}
                      </p>
                    )}
                  </>
                )}
              </td>
              <td className="px-4 py-2.5">
                <span className={clsx(
                  'font-mono',
                  item.required_qty == null
                    ? 'text-slate-400 dark:text-slate-500'
                    : 'text-slate-700 dark:text-slate-300',
                )}>
                  {item.required_qty == null
                    ? '—'
                    : `${formatNumber(item.required_qty)} ${item.unit}`}
                </span>
              </td>
              <td className="px-4 py-2.5">
                {isOutward(item) ? (
                  // What the client delivered never stood on our shelf, so no
                  // balance and no shortage may be printed for it.
                  <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
                    {NO_STOCK_LABEL}
                  </span>
                ) : (
                  <span className={clsx('font-mono', item.current_stock > 0 ? 'text-slate-700 dark:text-slate-300' : 'text-rose-500')}>
                    {formatNumber(item.current_stock)} {item.unit}
                  </span>
                )}
              </td>
              <td className="px-4 py-2.5">
                <span className={clsx(
                  'font-mono',
                  item.stock_remaining == null
                    ? 'text-slate-400 dark:text-slate-500'
                    : item.stock_remaining < 0
                      ? 'text-rose-500'
                      : 'text-slate-700 dark:text-slate-300',
                )}>
                  {item.stock_remaining == null
                    ? '—'
                    : `${formatNumber(item.stock_remaining)} ${item.unit}`}
                </span>
              </td>
              <td className="px-4 py-2.5 text-right">
                {isAdmin && (
                  <button
                    aria-label={`Remove ${item.chemical_name} from ${product.product_name}`}
                    onClick={() => onDelete(product, item)}
                    className={`p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer ${ICON_HIT_AREA}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </td>
            </tr>
          ))}
          {product.items.length === 0 && (
            <tr>
              <td colSpan="8" className="px-4 py-6 text-center text-slate-400 dark:text-slate-500">
                No ingredients yet. Add this product&apos;s chemicals below.
              </td>
            </tr>
          )}
        </tbody>
      </table>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 dark:border-slate-800 px-4 py-3">
        <label
          htmlFor={`water-${product.product_name}`}
          className="text-xs font-medium text-slate-500 dark:text-slate-400"
        >
          Water %
        </label>
        <input
          id={`water-${product.product_name}`}
          aria-label={`Water percentage for ${product.product_name}`}
          inputMode="decimal" type="number"
          step="0.01"
          min="0"
          max="100"
          value={waterDraft}
          onChange={(e) => setWaterDraft(e.target.value)}
          placeholder="auto"
          className="w-24 px-2 py-1 text-base sm:text-sm rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-slate-900 dark:text-white"
        />
        <Button
          variant="secondary"
          size="sm"
          onClick={submitWater}
          loading={savingWater}
          aria-label={`Save water for ${product.product_name}`}
        >
          Save
        </Button>
        <span className="text-[11px] text-slate-400 dark:text-slate-500">
          {trimmedWater === '' ? 'blank = the unformulated remainder' : 'stored percentage'}
        </span>
        {product.water_qty != null && (
          <span
            aria-label={`Water quantity for ${product.product_name}`}
            className="text-xs font-medium text-slate-500 dark:text-slate-400"
          >
            Water {formatNumber(product.water_qty)} {product.unit}
          </span>
        )}
        <span className="ml-auto text-sm font-semibold text-slate-700 dark:text-slate-200">
          {grandTotal == null
            ? 'Grand total —'
            : `Grand total ${grandTotal.toFixed(1)}%`}
        </span>
        {offBalance && (
          <span className="flex items-center gap-1 text-xs font-medium text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            ingredients + water do not add up to 100% — production is blocked until they do
          </span>
        )}
      </div>

      <div className="p-4 border-t border-slate-100 dark:border-slate-800 flex items-end gap-3 flex-wrap">
        <div className="flex-1 min-w-[180px]">
          <label
            htmlFor={`chem-${product.product_name}`}
            className="block text-xs text-slate-500 dark:text-slate-400 mb-1"
          >
            Chemical
          </label>
          <EntityCombobox
            id={`chem-${product.product_name}`}
            aria-label={`Chemical for ${product.product_name}`}
            options={available.map((c) => ({ value: c.name, label: c.name }))}
            value={chemical}
            onChange={setChemical}
            placeholder="Search chemicals…"
            emptyMessage="No matching chemical — check the spelling or pick from the list"
            hint="No chemical by that name — pick one from the list"
            className={inputCls}
          />
        </div>
        <div className="w-56">
          <label
            htmlFor={`source-${product.product_name}`}
            className="block text-xs text-slate-500 dark:text-slate-400 mb-1"
          >
            Source
          </label>
          <select
            id={`source-${product.product_name}`}
            aria-label={`Source for ${product.product_name}`}
            value={addSource}
            onChange={changeAddSource}
            className={inputCls}
          >
            <option value={SOURCE_IN_HOUSE}>In house — from our stock</option>
            <option value={SOURCE_OUTWARD}>Outward — the client delivered it</option>
          </select>
        </div>
        {addSource === SOURCE_OUTWARD && (
          <div className="flex-1 min-w-[220px]">
            <OutwardSourcePicker
              ariaLabel={`Outward receipt for ${product.product_name}`}
              id={`receipt-${product.product_name}`}
              label="Client receipt line"
              sources={outward.sources}
              state={outward}
              value={addLine}
              onChange={pickReceiptForAdd}
              onRetry={onRetrySources}
            />
          </div>
        )}
        <div className="w-32">
          <label
            htmlFor={`pct-${product.product_name}`}
            className="block text-xs text-slate-500 dark:text-slate-400 mb-1"
          >
            Percentage %
          </label>
          <input
            id={`pct-${product.product_name}`}
            aria-label={`Percentage for ${product.product_name}`}
            inputMode="decimal" type="number"
            step="0.01"
            min="0"
            max="100"
            value={pct}
            onChange={(e) => setPct(e.target.value)}
            placeholder="0"
            className={inputCls}
          />
        </div>
        <div className="flex flex-col items-start gap-1">
          <Button
            variant="primary"
            size="sm"
            icon={Plus}
            onClick={submitAdd}
            loading={saving}
            aria-label={`Add ingredient to ${product.product_name}`}
          >
            Add
          </Button>
          {addSourceError && (
            <p
              role="alert"
              className="text-[11px] font-medium text-rose-600 dark:text-rose-400"
            >
              {addSourceError}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

export default function Recipes() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const location = useLocation();
  // The outward register hands a receipt line over through location state:
  // arriving here means "build the recipe that uses this delivery".
  const [carriedLine, setCarriedLine] = useState(
    () => location.state?.useOutwardLine || null,
  );
  const [recipes, setRecipes] = useState([]);
  const [chemicals, setChemicals] = useState([]);
  const [loading, setLoading] = useState(true);

  const [selectedRecipe, setSelectedRecipe] = useState(null);
  const [detailProducts, setDetailProducts] = useState([]);

  const [showCreateModal, setShowCreateModal] = useState(Boolean(carriedLine));
  const [showDetail, setShowDetail] = useState(false);
  const [showEditMeta, setShowEditMeta] = useState(false);
  const [showProductionRun, setShowProductionRun] = useState(false);

  const [newName, setNewName] = useState('');
  const [companies, setCompanies] = useState([]);
  const [allLcs, setAllLcs] = useState([]);
  const [companyId, setCompanyId] = useState(
    carriedLine?.client_id == null ? '' : String(carriedLine.client_id),
  );
  const [lcId, setLcId] = useState('');
  const [lcPis, setLcPis] = useState([]);
  const [saleId, setSaleId] = useState('');
  const [invoices, setInvoices] = useState([]);
  const [invoiceId, setInvoiceId] = useState('');
  const [invoiceItems, setInvoiceItems] = useState([]);
  const [loadingRefs, setLoadingRefs] = useState(false);
  const [loadingPis, setLoadingPis] = useState(false);
  const [loadingInvoiceItems, setLoadingInvoiceItems] = useState(false);

  const [editName, setEditName] = useState('');
  const [editError, setEditError] = useState(null);
  const [savingMeta, setSavingMeta] = useState(false);

  // Outward receipts are read once per client: every picker on the page lists
  // the same rows, so switching a source never fires a second fetch.
  const [outward, setOutward] = useState(
    { ...OUTWARD_SOURCES_EMPTY, sources: [] },
  );
  const sourcesKey = useRef('');
  const outwardToken = useRef(0);

  const loadOutwardSources = useCallback(async (clientId) => {
    const key = clientId == null || clientId === '' ? '' : String(clientId);
    sourcesKey.current = key;
    // Whoever fires last owns the picker. Two reads can be in flight at once
    // (the operator moved to another client before the first answered), and the
    // older one resolving last must not pin the wrong client's receipts — so a
    // response whose token is no longer current is a no-op.
    const token = outwardToken.current + 1;
    outwardToken.current = token;
    const isCurrent = () => token === outwardToken.current;
    if (!key) {
      // No client on the PI means no delivery to point at — say that, rather
      // than sending a client_id the API must refuse.
      setOutward({ ...OUTWARD_SOURCES_EMPTY, sources: [], noClient: true });
      return;
    }
    // Drop the previous client's rows the moment a new load starts: they are
    // another client's receipts and must not stay pickable while this reads.
    setOutward({ ...OUTWARD_SOURCES_EMPTY, sources: [], loading: true });
    try {
      const res = await apiFetch(
        `/api/chemical-purchases/outward-sources?client_id=${encodeURIComponent(key)}`,
      );
      const data = await res.json();
      if (!isCurrent()) return;
      setOutward({
        ...OUTWARD_SOURCES_EMPTY,
        sources: Array.isArray(data?.sources) ? data.sources : [],
      });
    } catch (err) {
      if (!isCurrent()) return;
      // A failed read must stay retryable, so the key is dropped with it.
      sourcesKey.current = '';
      setOutward({
        ...OUTWARD_SOURCES_EMPTY,
        sources: [],
        error: err?.message || 'The server refused this request',
        fieldError: fieldErrorOf(err, 'client_id'),
      });
    }
  }, [apiFetch]);

  // The client behind the receipts is the recipe's own — never picked by hand.
  const outwardClientId = selectedRecipe?.company_id
    ?? (showCreateModal ? carriedLine?.client_id : null) ?? null;
  const requestOutwardSources = useCallback(() => {
    const key = outwardClientId == null ? '' : String(outwardClientId);
    if (key && sourcesKey.current === key) return;
    loadOutwardSources(outwardClientId);
  }, [loadOutwardSources, outwardClientId]);

  const presetSource = useMemo(() => (carriedLine ? {
    sourceType: SOURCE_OUTWARD,
    lineId: String(carriedLine.line_id),
    chemical: carriedLine.chemical_name,
  } : null), [carriedLine]);

  // Fetch only — loading is already true on mount; event-handler refreshers
  // flip the spinner on themselves before calling this.
  const fetchData = useCallback(async () => {
    try {
      const [recsRes, chemsRes] = await Promise.all([
        apiFetch('/api/recipes'),
        apiFetch('/api/chemicals')
      ]);
      const [recs, chems] = await Promise.all([recsRes.json(), chemsRes.json()]);
      if (Array.isArray(recs)) setRecipes(recs);
      if (Array.isArray(chems)) setChemicals(chems);
    } catch {
      // List stays as-is on failure; mutations surface their own errors.
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    (async () => {
      await fetchData();
    })();
  }, [fetchData]);

  useEffect(() => {
    if (!showCreateModal) return;
    (async () => {
      setLoadingRefs(true);
      try {
        // Only LCs with an open child PI — a closed LC has nothing left to
        // produce, so it must never be an option here.
        const [coRes, lcRes] = await Promise.all([
          apiFetch('/api/companies'),
          apiFetch('/api/lcs?open_only=true'),
        ]);
        const [cos, lcs] = await Promise.all([coRes.json(), lcRes.json()]);
        setCompanies(Array.isArray(cos) ? cos : []);
        setAllLcs(Array.isArray(lcs) ? lcs : []);
      } catch (err) {
        toast.error(err?.message || 'Could not load running LCs');
      } finally {
        setLoadingRefs(false);
      }
    })();
  }, [showCreateModal, apiFetch]);

  const companyLcs = useMemo(
    () => allLcs.filter(l => String(l.company_id) === String(companyId)),
    [allLcs, companyId]
  );

  // Distinct products on the selected invoice, with the quantity each line
  // carries. Read-only: the invoice, not the operator, defines the products.
  const invoiceProducts = useMemo(() => {
    const byName = new Map();
    for (const item of invoiceItems) {
      const name = (item.product_name || '').trim();
      if (!name) continue;
      const key = name.toLowerCase();
      const entry = byName.get(key) || { name, unit: item.unit || 'KG', quantity: 0 };
      entry.quantity += Number(item.quantity) || 0;
      byName.set(key, entry);
    }
    return [...byName.values()];
  }, [invoiceItems]);

  const invoiceRecipe = useMemo(
    () => recipes.find(r => String(r.invoice_id) === String(invoiceId)) || null,
    [recipes, invoiceId]
  );

  const selectedPi = lcPis.find(p => String(p.id) === String(saleId));
  const piLabel = selectedPi?.pi_number || (saleId ? `PI #${saleId}` : '');
  const selectedInvoice = invoices.find(
    (inv) => String(inv.invoice_id ?? inv.id) === String(invoiceId)
  );
  const namePlaceholder = piLabel && selectedInvoice
    ? `e.g. ${piLabel} · ${selectedInvoice.invoice_number}`
    : 'e.g. PI-2026-014 · INV-014';

  const handleCompanyChange = (value) => {
    // A receipt carried from the register belongs to one client only: once the
    // company moves, that line is no longer a source this recipe may use.
    if (carriedLine && String(value) !== String(carriedLine.client_id)) {
      setCarriedLine(null);
      sourcesKey.current = '';
    }
    setCompanyId(value);
    setLcId('');
    setLcPis([]);
    setSaleId('');
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
  };

  const handleLcChange = async (value) => {
    setLcId(value);
    setLcPis([]);
    setSaleId('');
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
    if (!value) return;
    setLoadingPis(true);
    try {
      // open_only drops completed PIs; a PI with no producible invoice stays
      // visible with a count of 0 so it can be shown disabled, not hidden.
      const res = await apiFetch(`/api/lcs/${encodeURIComponent(value)}?open_only=true`);
      const data = await res.json();
      setLcPis(Array.isArray(data?.pis) ? data.pis : []);
    } catch (err) {
      setLcPis([]);
      toast.error(err?.message || 'Could not load PIs for this LC');
    } finally {
      setLoadingPis(false);
    }
  };

  const handleSaleChange = async (value) => {
    setSaleId(value);
    setInvoices([]);
    setInvoiceId('');
    setInvoiceItems([]);
    if (!value) return;
    try {
      // booked/shipped/paid invoices are past production.
      const res = await apiFetch(
        `/api/sales/${encodeURIComponent(value)}/invoices?producible=true`
      );
      const invs = await res.json();
      setInvoices(Array.isArray(invs) ? invs : []);
    } catch {
      setInvoices([]);
    }
  };

  const handleInvoiceChange = async (value) => {
    setInvoiceId(value);
    setInvoiceItems([]);
    if (!value) return;
    setLoadingInvoiceItems(true);
    try {
      const res = await apiFetch(`/api/invoices/${encodeURIComponent(value)}/items`);
      const items = await res.json();
      setInvoiceItems(Array.isArray(items) ? items : []);
    } catch (err) {
      toast.error(err?.message || 'Could not load invoice items');
    } finally {
      setLoadingInvoiceItems(false);
    }
  };

  // The list hands out the row id, so detail reads key off it: a (company,
  // name) pair that drifted would 404 on a recipe the list is showing. Older
  // payloads without an id still resolve by name.
  const fetchRecipeDetail = async (recipe) => {
    const path = recipe?.id != null
      ? '/api/recipes/by-id/' + encodeURIComponent(recipe.id)
      : '/api/recipes/' + encodeURIComponent(recipe.name) +
        '?company_id=' + encodeURIComponent(recipe.company_id);
    try {
      const res = await apiFetch(path);
      const data = await res.json();
      if (data.recipe) {
        setSelectedRecipe(data.recipe);
        setDetailProducts(Array.isArray(data.products) ? data.products : []);
        setShowDetail(true);
      }
    } catch (err) {
      if (err.status === 404) {
        // Someone deleted it while the list still had the row.
        toast.error('That recipe no longer exists — the list has been refreshed');
        setLoading(true);
        fetchData();
        return;
      }
      toast.error(err.message || 'Could not load recipe');
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return toast.error('Recipe name required');
    if (!companyId) return toast.error('Company is required');
    if (!lcId) return toast.error('LC is required');
    if (!saleId) return toast.error('PI / Sale is required');
    if (!invoiceId) return toast.error('Invoice is required');
    if (invoiceRecipe) {
      return toast.error(`${invoiceRecipe.invoice_number || 'This invoice'} already has a recipe`);
    }
    try {
      const name = newName.trim();
      const res = await apiFetch('/api/recipes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          company_id: Number(companyId),
          invoice_id: Number(invoiceId),
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `Failed to create recipe (${res.status})`);
      toast.success(`Recipe "${name}" created`);
      setNewName('');
      setShowCreateModal(false);
      setLoading(true);
      await fetchData();
      // The new recipe is empty until its products are formulated, so open it
      // instead of dropping the operator back on the list.
      const created = data.recipe || {};
      // A receipt carried in from the register must be nameable in the very
      // next ingredient row, so its client's lines are read before the detail.
      if (carriedLine?.client_id != null) {
        await loadOutwardSources(carriedLine.client_id);
      }
      if (created.id != null) {
        await fetchRecipeDetail(created);
      }
    } catch (err) {
      toast.error(err.message || 'Failed to create recipe');
    }
  };

  const handleDelete = async (recipe) => {
    const ok = await confirm({
      title: `Delete recipe "${recipe.name}"?`,
      message: 'The recipe and all its formulations will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(recipe.id),
        { method: 'DELETE' }
      );
      const data = await res.json();
      if (data.success) {
        if (showDetail && selectedRecipe?.id === recipe.id) setShowDetail(false);
        toast.success(`Recipe "${recipe.name}" deleted`);
        setLoading(true);
        fetchData();
      }
    } catch (err) {
      toast.error(err.message || 'Could not delete recipe');
    }
  };

  const openEditMeta = () => {
    if (!selectedRecipe) return;
    setEditName(selectedRecipe.name);
    setEditError(null);
    setShowEditMeta(true);
  };

  const handleUpdateMeta = async () => {
    if (!selectedRecipe) return;
    const trimmedName = editName.trim();
    if (!trimmedName) {
      setEditError('Recipe name is required');
      return;
    }
    setSavingMeta(true);
    setEditError(null);
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmedName }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        // A name clash (409) stays here: the form is the only place the user
        // can correct it.
        setEditError(data?.error || `Could not update recipe (${res.status})`);
        return;
      }
      const finalName = data.name || trimmedName;
      const renamed = finalName !== selectedRecipe.name;
      setRecipes(prev => prev.map(r => (
        r.id === selectedRecipe.id ? { ...r, name: finalName } : r
      )));
      setSelectedRecipe(prev => (prev ? { ...prev, name: finalName } : prev));
      setShowEditMeta(false);
      toast.success(renamed ? `Recipe renamed to "${finalName}"` : 'Recipe updated');
      // The invoice and its products follow the row id, so a rename cannot
      // detach the detail from what it is showing.
      fetchRecipeDetail({ ...selectedRecipe, name: finalName });
      setLoading(true);
      fetchData();
    } catch (err) {
      setEditError(err.message || 'Could not update recipe');
    } finally {
      setSavingMeta(false);
    }
  };

  // Formulation writes key off the product, not the recipe name: the invoice
  // defines which slot an ingredient belongs to.
  const handleAddItem = async (product, chemical, percentage, source) => {
    if (!selectedRecipe) return false;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/products/' + encodeURIComponent(product.product_name) + '/items',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chemical,
            percentage,
            source_type: source?.sourceType || SOURCE_IN_HOUSE,
            outward_purchase_line_id: source?.lineId ?? null,
          }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Failed to add ingredient');
      toast.success('Ingredient added');
      await fetchRecipeDetail(selectedRecipe);
      return true;
    } catch (err) {
      toast.error(err.message || 'Failed to add ingredient');
      return false;
    }
  };

  // Changing a source writes the whole pairing at once: the stored percentage
  // rides along because the API requires it, and the line goes null the moment
  // the ingredient comes back from our own stock.
  const handleUpdateItemSource = async (product, item, sourceType, lineId) => {
    if (!selectedRecipe) return { ok: false, messages: [] };
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/items/' + encodeURIComponent(item.id),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            percentage: Number(item.percentage),
            source_type: sourceType,
            outward_purchase_line_id: sourceType === SOURCE_OUTWARD ? lineId : null,
          }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Could not change the source');
      toast.success(sourceType === SOURCE_OUTWARD
        ? 'Ingredient now draws on the client delivery'
        : 'Ingredient draws on our stock again');
      await fetchRecipeDetail(selectedRecipe);
      return { ok: true, messages: [] };
    } catch (err) {
      // Field refusals stay on the field that caused them.
      const messages = messagesOf(err);
      if (!messages.length) toast.error(err.message || 'Could not change the source');
      return { ok: false, messages };
    }
  };

  const handleUpdateItem = async (product, item, percentage) => {
    if (!selectedRecipe) return false;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/items/' + encodeURIComponent(item.id),
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ percentage }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Could not update ingredient');
      toast.success('Ingredient updated');
      await fetchRecipeDetail(selectedRecipe);
      return true;
    } catch (err) {
      toast.error(err.message || 'Could not update ingredient');
      return false;
    }
  };

  // Water is display-only: the server stores an override, or NULL for the
  // unformulated remainder. Nothing is deducted and no cap reads it, so a
  // total that is not 100 stays a warning.
  const handleSetWater = async (product, percentage) => {
    if (!selectedRecipe) return false;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/products/' + encodeURIComponent(product.product_name) + '/water',
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ percentage }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || 'Could not save water percentage');
      }
      toast.success(
        percentage == null ? 'Water reset to the remainder' : 'Water saved');
      await fetchRecipeDetail(selectedRecipe);
      return true;
    } catch (err) {
      toast.error(err.message || 'Could not save water percentage');
      return false;
    }
  };

  const handleDeleteItem = async (product, item) => {
    if (!selectedRecipe) return;
    const ok = await confirm({
      title: `Remove "${item.chemical_name}" from ${product.product_name}?`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await apiFetch(
        '/api/recipes/by-id/' + encodeURIComponent(selectedRecipe.id) +
          '/items/' + encodeURIComponent(item.id),
        { method: 'DELETE' }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || 'Could not remove ingredient');
      toast.success('Ingredient removed');
      await fetchRecipeDetail(selectedRecipe);
    } catch (err) {
      toast.error(err.message || 'Could not remove ingredient');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="inline-flex items-center gap-2 text-slate-500 dark:text-slate-400">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" />
          Loading recipes...
        </div>
      </div>
    );
  }

  if (showDetail && selectedRecipe) {
    const productCount = detailProducts.length;
    const formulatedCount = detailProducts.filter(p => p.formulated).length;
    const hasFormulated = formulatedCount > 0;

    return (
      <div className="space-y-6 max-w-7xl mx-auto">
        <div className="flex flex-wrap items-center gap-4">
          <button
            aria-label="Back to recipes"
            onClick={() => { setShowDetail(false); setSelectedRecipe(null); setDetailProducts([]); setCarriedLine(null); setLoading(true); fetchData(); }}
            className={`p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer ${ICON_HIT_AREA}`}
          >
            <ArrowLeft className="h-5 w-5 text-slate-600 dark:text-slate-400" />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{selectedRecipe.name}</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              {selectedRecipe.invoice_number || '—'}
              {selectedRecipe.pi_number ? ` · ${selectedRecipe.pi_number}` : ''}
              {selectedRecipe.client_name ? ` · ${selectedRecipe.client_name}` : ''}
              {' '}&middot;{' '}
              <span>{formulatedCount} of {productCount} products formulated</span>
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" icon={Edit3} onClick={openEditMeta}>Edit</Button>
            {isAdmin && (
              <Button variant="danger" size="sm" icon={Trash2} onClick={() => handleDelete(selectedRecipe)}>Delete</Button>
            )}
            {isAdmin && hasFormulated && (
              <Button variant="primary" size="sm" icon={Factory} onClick={() => setShowProductionRun(true)}>
                Go for Production
              </Button>
            )}
          </div>
        </div>

        {!hasFormulated && productCount > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-800 dark:bg-amber-950/40">
            <p className="text-sm font-medium text-amber-700 dark:text-amber-300">
              Formulate at least one product before producing this invoice.
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              Booking stays blocked until every product is formulated and covered by a production run.
            </p>
          </div>
        )}

        {productCount === 0 && (
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-6 text-center text-sm text-slate-500 dark:text-slate-400">
            This invoice has no lines, so there is nothing to formulate.
          </div>
        )}

        <div className="space-y-4">
          {detailProducts.map((product) => (
            <ProductFormulationCard
              key={product.id ?? product.product_name}
              product={product}
              chemicals={chemicals}
              isAdmin={isAdmin}
              onAdd={handleAddItem}
              onUpdate={handleUpdateItem}
              onDelete={handleDeleteItem}
              onSetWater={handleSetWater}
              onUpdateSource={handleUpdateItemSource}
              outward={outward}
              onLoadSources={requestOutwardSources}
              onRetrySources={requestOutwardSources}
              presetSource={presetSource}
            />
          ))}
        </div>

        <Modal
          isOpen={showEditMeta}
          onClose={() => setShowEditMeta(false)}
          title="Edit Recipe"
          footer={
            <>
              <Button variant="secondary" size="sm" onClick={() => setShowEditMeta(false)}>Cancel</Button>
              <Button variant="primary" size="sm" onClick={handleUpdateMeta} loading={savingMeta} disabled={savingMeta}>Save</Button>
            </>
          }
        >
          <div className="space-y-4">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Renaming keeps the invoice, the formulations and the run history — they
              follow the recipe, not its label.
            </p>
            <div>
              <label htmlFor="edit-recipe-name" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Recipe Name *</label>
              <input
                id="edit-recipe-name"
                type="text"
                value={editName}
                onChange={e => setEditName(e.target.value)}
                className={inputCls}
              />
            </div>
            <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
              <p className="text-xs text-slate-500 dark:text-slate-400">Invoice</p>
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                {selectedRecipe.invoice_number || '—'}
              </p>
              <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">
                The invoice and its products are the recipe&apos;s identity and cannot be changed.
              </p>
            </div>
            {editError && (
              <p role="alert" className="text-xs font-medium text-rose-600 dark:text-rose-400">
                {editError}
              </p>
            )}
          </div>
        </Modal>

        <ProductionRunModal
          key={selectedRecipe.id ?? selectedRecipe.name}
          isOpen={showProductionRun}
          onClose={() => setShowProductionRun(false)}
          recipe={selectedRecipe}
          onSaved={() => {
            setShowProductionRun(false);
            // The run changes produced/remaining, which the cards show, so the
            // detail is re-read instead of trusting the pre-run rows.
            if (selectedRecipe) {
              fetchRecipeDetail(selectedRecipe);
            }
          }}
        />
      </div>
    );
  }

  const awaitingFormulation = recipes.reduce(
    (sum, r) => sum + Math.max((r.product_count || 0) - (r.formulated_count || 0), 0),
    0
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white">Recipes</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            {recipes.length} recipe{recipes.length !== 1 ? 's' : ''} in database
          </p>
        </div>
        <Button variant="primary" size="sm" icon={Plus} onClick={() => setShowCreateModal(true)}>
          New Recipe
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <KpiCard title="Total Recipes" value={recipes.length} icon={BookOpen} color="blue" />
        <KpiCard title="Total Chemicals" value={chemicals.length} icon={FlaskConical} color="purple" />
        <KpiCard
          title="Products awaiting formulation"
          value={awaitingFormulation}
          icon={AlertTriangle}
          color={awaitingFormulation > 0 ? 'amber' : 'emerald'}
        />
      </div>

      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">#</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Recipe Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Invoice</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Products</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Formulated</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Created</th>
              <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
            {recipes.map((recipe, idx) => (
              <tr key={recipe.id ?? recipe.name} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                <td className="px-4 py-3 text-slate-400">{idx + 1}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-950/40 text-blue-600 dark:text-blue-400">
                      <BookOpen className="h-4 w-4" />
                    </div>
                    <span className="font-medium text-slate-900 dark:text-white">{recipe.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-slate-700 dark:text-slate-300 font-mono">
                  {recipe.invoice_number || '—'}
                </td>
                <td className="px-4 py-3 text-slate-700 dark:text-slate-300">
                  {recipe.product_count ?? 0}
                </td>
                <td className="px-4 py-3">
                  <span className={clsx(
                    'text-xs font-medium',
                    (recipe.formulated_count || 0) === (recipe.product_count || 0)
                      ? 'text-emerald-600 dark:text-emerald-400'
                      : 'text-amber-600 dark:text-amber-400'
                  )}>
                    {recipe.formulated_count || 0} of {recipe.product_count || 0} formulated
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs">{recipe.created || '—'}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-3">
                    <Button variant="ghost" size="sm" icon={Eye} onClick={() => fetchRecipeDetail(recipe)}>
                      View
                    </Button>
                    {isAdmin && (
                      <button
                        aria-label={`Delete recipe ${recipe.name}`}
                        onClick={() => handleDelete(recipe)}
                        className={`p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer ${ICON_HIT_AREA}`}
                        title="Delete recipe"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {recipes.length === 0 && (
              <tr>
                <td colSpan="7" className="px-4 py-8 text-center text-slate-400 dark:text-slate-500">
                  No recipes found. Create your first recipe to get started.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </div>

      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Create New Recipe"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setShowCreateModal(false)}>Cancel</Button>
            <Button
              variant="primary"
              size="sm"
              icon={Plus}
              onClick={handleCreate}
              disabled={Boolean(invoiceRecipe)}
            >
              Create
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            One recipe covers every product on one invoice. Only LCs with open
            PIs, and invoices still in production, are listed.
          </p>
          {carriedLine && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 dark:border-blue-800 dark:bg-blue-950/40">
              <p
                aria-label="Outward receipt carried from the register"
                className="text-xs font-medium text-blue-800 dark:text-blue-200"
              >
                {[
                  `Outward receipt ${carriedLine.purchase_number || `line #${carriedLine.line_id}`}`,
                  carriedLine.chemical_name,
                  `${formatNumber(carriedLine.quantity)} ${carriedLine.unit}`,
                  carriedLine.client_name,
                ].filter(Boolean).join(' · ')}
                {' — this client is already selected, and a new ingredient starts on this line.'}
              </p>
              <Button
                variant="secondary"
                size="sm"
                className="mt-2"
                onClick={() => { setCarriedLine(null); sourcesKey.current = ''; }}
              >
                Don&apos;t use this receipt
              </Button>
            </div>
          )}
          <div className="grid grid-cols-1 gap-3">
            <div>
              <label htmlFor="recipe-company" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Company *</label>
              <EntityCombobox
                id="recipe-company"
                options={companies.map(c => ({ value: c.id, label: c.name }))}
                value={companyId}
                onChange={handleCompanyChange}
                placeholder={loadingRefs ? 'Loading companies…' : 'Search companies…'}
                className={inputCls}
              />
            </div>
            <div>
              <label htmlFor="recipe-lc" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">LC *</label>
              <EntityCombobox
                id="recipe-lc"
                options={companyLcs.map(l => {
                  const count = l.producible_pi_count ?? 0;
                  return {
                    value: l.id,
                    label: `${l.lc_number} · ${count === 0
                      ? 'no invoices in production'
                      : `${count} PI${count === 1 ? '' : 's'} in production`}`,
                    disabled: count === 0,
                  };
                })}
                value={lcId}
                onChange={handleLcChange}
                disabled={!companyId}
                aria-describedby="recipe-lc-help"
                placeholder={companyId ? 'Search LCs…' : 'Select a company first'}
                className={clsx(inputCls, 'disabled:opacity-60')}
              />
              <p id="recipe-lc-help" aria-live="polite" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                {companyId && !loadingRefs && companyLcs.length === 0
                  ? (
                    <>No running LCs for this company. PIs without a linked LC are not shown here — link them from <Link to="/sales" className="text-blue-600 dark:text-blue-400 underline">Sales</Link>.</>
                  )
                  : 'Only LCs with open PIs are listed.'}
              </p>
            </div>
            <div>
              <label htmlFor="recipe-pi" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">PI / Sale *</label>
              <EntityCombobox
                id="recipe-pi"
                options={lcPis.map(s => {
                  const count = s.producible_invoice_count ?? 0;
                  return {
                    value: s.id,
                    label: `${s.pi_number || `PI #${s.id}`}${s.client_name ? ` — ${s.client_name}` : ''} · ${count === 0
                      ? 'no invoices in production'
                      : `${count} invoice${count === 1 ? '' : 's'} in production`}`,
                    disabled: count === 0,
                  };
                })}
                value={saleId}
                onChange={handleSaleChange}
                disabled={!lcId || loadingPis}
                placeholder={!lcId ? 'Select an LC first' : loadingPis ? 'Loading PIs…' : 'Search PIs…'}
                className={clsx(inputCls, 'disabled:opacity-60')}
              />
            </div>
            <div>
              <label htmlFor="recipe-invoice" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Invoice *</label>
              <EntityCombobox
                id="recipe-invoice"
                options={invoices.map(inv => {
                  const id = inv.invoice_id ?? inv.id;
                  return {
                    value: id,
                    label: `${inv.invoice_number || `Invoice #${id}`} · ${INVOICE_STATUS_LABELS[inv.status] || inv.status}`,
                  };
                })}
                value={invoiceId}
                onChange={handleInvoiceChange}
                disabled={!saleId}
                placeholder={saleId ? 'Search invoices…' : 'Select a PI first'}
                className={clsx(inputCls, 'disabled:opacity-60')}
              />
            </div>
            {invoiceId && (
              <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-3 py-2">
                <p className="text-xs font-medium text-slate-600 dark:text-slate-300">
                  Invoice products
                </p>
                {loadingInvoiceItems ? (
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">Loading items…</p>
                ) : invoiceProducts.length === 0 ? (
                  <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                    This invoice has no lines.
                  </p>
                ) : (
                  <ul className="mt-1.5 space-y-1">
                    {invoiceProducts.map(p => (
                      <li key={p.name} className="flex items-center justify-between gap-2 text-xs">
                        <span className="text-slate-700 dark:text-slate-300">{p.name}</span>
                        <span className="font-mono text-slate-500 dark:text-slate-400">
                          {formatNumber(p.quantity)} {p.unit}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-1.5 text-[11px] text-slate-400 dark:text-slate-500">
                  {invoiceProducts.length} products on this invoice
                </p>
                {invoiceRecipe && (
                  <p className="mt-1.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                    {invoiceRecipe.invoice_number || 'This invoice'} already has a recipe
                    ({invoiceRecipe.name}). Open it to formulate its products — one recipe per invoice.
                  </p>
                )}
              </div>
            )}
          </div>
          <div>
            <label htmlFor="recipe-name" className="block text-xs text-slate-500 dark:text-slate-400 mb-1">Recipe Name *</label>
            <input
              id="recipe-name"
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder={namePlaceholder}
              aria-describedby="recipe-name-help"
              className={inputCls}
            />
            <p id="recipe-name-help" className="mt-1 text-xs text-slate-400 dark:text-slate-500">
              {piLabel && selectedInvoice
                ? <>Selected: {piLabel} · {selectedInvoice.invoice_number}</>
                : 'Name it yourself — the PI and invoice appear here once chosen.'}
            </p>
          </div>
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Formulate each invoice product after creating the recipe.
          </p>
        </div>
      </Modal>
    </div>
  );
}
