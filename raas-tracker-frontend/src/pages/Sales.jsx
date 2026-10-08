import { useState, useEffect, useCallback, useRef } from 'react';
import { UploadCloud, Plus, DollarSign, RefreshCw, Search, Download, ChevronLeft, ChevronRight, Layers, Link2 } from 'lucide-react';
import Button from '../components/ui/Button';
import KpiCard from '../components/cards/KpiCard';
import SaleCard from '../components/sales/SaleCard';
import LcBoardCard from '../components/sales/LcBoardCard';
import MultiPICreateModal from '../components/sales/MultiPICreateModal';
import LinkPIsModal from '../components/sales/LinkPIsModal';
import LcDetailModal from '../components/sales/LcDetailModal';
import UploadPI from '../components/sales/UploadPI';
import ReviewModal from '../components/sales/ReviewModal';
import PaymentModal from '../components/sales/PaymentModal';
import SaleDetailModal from '../components/sales/SaleDetailModal';
import InvoicesRequiredModal from '../components/sales/InvoicesRequiredModal';
import ProductionRequiredModal from '../components/sales/ProductionRequiredModal';
import { STAGES, groupSalesByLc, lcGroupKey, nextStageFor } from '../utils/sales';
import { formatNumber, toCents, fromCents } from '../utils/format';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';

// Header tones mirror PipelineColumn exactly (same classes) so the grouped
// board keeps one rhythm: one column per stage, LC cards + PI cards inside.
const HEADER_STYLES = {
  pi_issued: 'bg-sky-50 dark:bg-sky-950/40 border-sky-200 dark:border-sky-800 text-sky-700 dark:text-sky-300',
  lc_received: 'bg-indigo-50 dark:bg-indigo-950/40 border-indigo-200 dark:border-indigo-800 text-indigo-700 dark:text-indigo-300',
  shipment_ongoing: 'bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300',
  payment_due: 'bg-orange-50 dark:bg-orange-950/40 border-orange-200 dark:border-orange-800 text-orange-700 dark:text-orange-300',
  completed: 'bg-emerald-50 dark:bg-emerald-950/40 border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300',
};

export default function Sales() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const [sales, setSales] = useState([]);
  const [lcs, setLcs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [movingId, setMovingId] = useState(null);

  const [showUpload, setShowUpload] = useState(false);
  const [showMulti, setShowMulti] = useState(false);
  const [showLink, setShowLink] = useState(false);
  const [lcDetailId, setLcDetailId] = useState(null);
  const [reviewData, setReviewData] = useState(null);
  const [paymentSale, setPaymentSale] = useState(null);
  const [detailSaleId, setDetailSaleId] = useState(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [totalSales, setTotalSales] = useState(0);

  // Barrier modals
  const [showInvoicesRequired, setShowInvoicesRequired] = useState(false);
  const [showProductionRequired, setShowProductionRequired] = useState(false);
  const [barrierLcId, setBarrierLcId] = useState(null);
  const [barrierSaleId, setBarrierSaleId] = useState(null);
  // PIs of the LC blocked on the invoice barrier (modal renders one row per
  // PI so an LC can carry a single invoice or one per PI), plus the move to
  // retry once the invoice(s) exist.
  const [barrierPis, setBarrierPis] = useState([]);
  const [barrierMove, setBarrierMove] = useState(null);
  const [barrierDetail, setBarrierDetail] = useState(null);
  const pageSize = 50;

  const abortRef = useRef(null);
  const fetchData = useCallback(async (q, pageNum = 1) => {
    // Abort the in-flight search so out-of-order responses can't win.
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const url = q
      ? `/api/sales?q=${encodeURIComponent(q)}&page=${pageNum}&page_size=${pageSize}`
      : `/api/sales?page=${pageNum}&page_size=${pageSize}`;
    try {
      const [salesRes, summaryRes] = await Promise.all([
        apiFetch(url, { signal: ctrl.signal }),
        apiFetch('/api/sales/summary', { signal: ctrl.signal }),
      ]);
      const [salesData, summaryData] = await Promise.all([salesRes.json(), summaryRes.json()]);
      if (salesData && Array.isArray(salesData.sales)) {
        setSales(salesData.sales);
        setTotalSales(salesData.total || 0);
      } else if (Array.isArray(salesData)) {
        setSales(salesData);
      }
      if (summaryData) setSummary(summaryData);
      // LC list is best-effort enrichment for board grouping (grouping falls
      // back to the lc_number mirror when this read fails).
      try {
        const lcsRes = await apiFetch('/api/lcs', { signal: ctrl.signal });
        const lcsData = await lcsRes.json();
        if (Array.isArray(lcsData)) setLcs(lcsData);
      } catch {
        // Keep the previous LC list; sales still group via the mirror.
      }
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      // List stays as-is on failure; mutations surface their own errors.
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [apiFetch]);

  // loading starts true; the mount fetch only ever clears it (async), and a
  // fetchData identity change means a route change, which remounts this page.
  useEffect(() => {
    (async () => {
      await fetchData();
    })();
  }, [fetchData]);

  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const t = setTimeout(() => {
      setPage(1);
      fetchData(query.trim(), 1);
    }, 300);
    return () => clearTimeout(t);
  }, [query, fetchData]);

  const handleExport = () => {
    window.open('/api/sales/export', '_blank');
  };

  const salesByStage = (stage) => sales.filter((s) => s.stage === stage);

  const handleCardAction = (sale, stageDef) => {
    // Legacy `Enter LC` (PUT /api/sales/<id>/lc) retired: it wrote lc_number
    // with lc_id NULL and stranded PIs. LC creation flows only through
    // LinkPIsModal (POST /api/lcs + POST .../pis).
    if (stageDef.action === 'payment') {
      setPaymentSale(sale);
    } else if (stageDef.action === 'move') {
      handleMove(sale);
    }
  };

  const handleMove = async (sale, notes) => {
    const saleId = sale?.id ?? sale;
    // Reminder target: the lc_received → shipment_ongoing hop is where an
    // invoice number first becomes meaningful.
    const enteringShipment = sale?.stage === 'lc_received';
    setMovingId(saleId);
    try {
      await apiFetch(`/api/sales/${saleId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: notes || '' }),
      });
      toast.success('Sale moved to next stage');
      if (enteringShipment) {
        // Non-blocking reminder only — the move has already succeeded above.
        try {
          const res = await apiFetch(`/api/sales/${saleId}/invoices`);
          const invoices = await res.json();
          if (Array.isArray(invoices) && invoices.length === 0) {
            toast.warning('No invoice yet — add an invoice number so production and shipping can be tracked.');
          }
        } catch {
          // Best effort: never surface an error after a successful move.
        }
      }
    } catch (err) {
      toast.error(err.message || 'Could not move sale');
    } finally {
      setMovingId(null);
      fetchData();
    }
  };

  const handleDelete = async (sale) => {
    const label = sale.pi_number || `#${sale.id}`;
    const ok = await confirm({
      title: `Delete sale "${label}"?`,
      message: `Client: ${sale.client_name || 'unknown'} — the sale and all its items will be removed.`,
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/sales/${sale.id}`, { method: 'DELETE' });
      toast.success(`Sale "${label}" deleted`);
    } catch (err) {
      toast.error(err.message || 'Could not delete sale');
    } finally {
      fetchData();
    }
  };

  // LC lane: one card per LC moves/unlinks as a unit; PIs always travel
  // together. Before attempting a barriered move, probe readiness and, when
  // blocked, do NOT POST — point the operator at the missing precondition
  // (the missing products for recipe/production, or invoices for Barrier 1).
  const handleLcMove = async (lc) => {
    if (typeof lc?.id !== 'number') return;
    const next = nextStageFor(lc.stage);
    if (!next) return;
    setMovingId(`lc-${lc.id}`);
    try {
      const res = await apiFetch(`/api/lcs/${lc.id}/readiness`);
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        toast.error(body?.error || 'Readiness check failed');
        return;
      }
      const blocked =
        next === 'shipment_ongoing' && !body.invoices_ok
          ? { barrier: 'invoices' }
          : (next === 'payment_due' && (!body.recipes_ok || !body.produced_ok))
            ? { barrier: 'production', detail: body.detail }
            : null;
      if (blocked) {
        if (blocked.barrier === 'invoices') {
          const pis = (Array.isArray(lc.pis) ? lc.pis : [])
            .filter((p) => p && typeof p.id === 'number')
            .map((p) => ({ id: p.id, pi_number: p.pi_number }));
          if (pis.length === 0) {
            toast.error('No PIs are linked to this LC, so there is nothing to invoice.');
            return;
          }
          setBarrierLcId(lc.id);
          setBarrierSaleId(null);
          setBarrierPis(pis);
          setBarrierMove({ lcId: lc.id, next });
          setShowInvoicesRequired(true);
        } else if (blocked.barrier === 'production') {
          setBarrierLcId(lc.id);
          setBarrierSaleId(null);
          setBarrierDetail(blocked.detail || {});
          setShowProductionRequired(true);
        } else if (blocked.barrier === 'error') {
          toast.error(blocked.message);
        }
        return;
      }
      await apiFetch(`/api/lcs/${lc.id}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_stage: next }),
      });
      toast.success(`LC "${lc.lc_number || lc.id}" moved`);
    } catch (err) {
      toast.error(err.message || 'Could not move LC');
    } finally {
      setMovingId(null);
      fetchData();
    }
  };

  // The invoice barrier modal calls back here: the blocking invoice(s) now
  // exist, so retry the move the operator originally asked for instead of
  // leaving them to click Move a second time.
  const handleBarrierInvoicesSuccess = async () => {
    const move = barrierMove;
    setShowInvoicesRequired(false);
    setBarrierMove(null);
    setBarrierPis([]);
    if (!move || typeof move.lcId !== 'number' || !move.next) {
      fetchData();
      return;
    }
    setMovingId(`lc-${move.lcId}`);
    try {
      const lc = lcs.find((l) => l.id === move.lcId);
      await apiFetch(`/api/lcs/${move.lcId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_stage: move.next }),
      });
      toast.success(`LC "${lc?.lc_number || move.lcId}" moved`);
    } catch (err) {
      toast.error(err.message || 'Could not move LC');
    } finally {
      setMovingId(null);
      setBarrierLcId(null);
      setBarrierSaleId(null);
      setBarrierDetail({});
      fetchData();
    }
  };

  const handleUnlink = async (lc, pi) => {    if (typeof lc?.id !== 'number' || !pi?.id) return;
    const ok = await confirm({
      title: `Unlink PI "${pi.pi_number || pi.id}"?`,
      message: 'The PI returns to the unlinked list — its data is kept.',
      confirmLabel: 'Unlink',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/lcs/${lc.id}/pis/${pi.id}`, { method: 'DELETE' });
      toast.success(`PI "${pi.pi_number || pi.id}" unlinked`);
    } catch (err) {
      toast.error(err.message || 'Could not unlink PI');
    } finally {
      fetchData();
    }
  };

  const handleLcDelete = async (lc) => {
    if (typeof lc?.id !== 'number') return;
    const ok = await confirm({
      title: `Delete LC "${lc.lc_number || lc.id}"?`,
      message: 'This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/lcs/${lc.id}`, { method: 'DELETE' });
      toast.success(`LC "${lc.lc_number || lc.id}" deleted`);
    } catch (err) {
      toast.error(err.message || 'Could not delete LC');
    } finally {
      fetchData();
    }
  };

  const handleLcOpen = (lc) => {
    if (typeof lc?.id === 'number') {
      setLcDetailId(lc.id);
    } else if (lc?.pis?.[0]?.id) {
      setDetailSaleId(lc.pis[0].id);
    }
  };

  const unlinkedSales = sales.filter((s) => !lcGroupKey(s));

  const handleParsed = (extraction) => {
    setShowUpload(false);
    setReviewData({
      pi_number: extraction.header?.pi_number || '',
      pi_date: extraction.header?.pi_date || '',
      client_name: extraction.header?.client_name || '',
      items: (extraction.items || []).map((i) => ({
        product_name: i.product_name || '',
        quantity: i.quantity ?? 0,
        unit_price: i.unit_price ?? 0,
      })),
      warnings: extraction.warnings || [],
    });
  };

  const handleManualCreate = () => {
    setReviewData({ pi_number: '', pi_date: '', client_name: '', items: [], warnings: [] });
  };

  const handleReviewSave = async (payload) => {
    const res = await apiFetch('/api/sales', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sale: payload.sale, items: payload.items }),
    });
    const data = await res.json().catch(() => ({}));
    setReviewData(null);
    fetchData();
    if (data.warning) toast.warning(data.warning);
    else toast.success('Sale saved');
  };

  return (
    <div className="space-y-6 max-w-[1600px] mx-auto">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <DollarSign className="h-5 w-5 text-emerald-600" />
            Sales Pipeline
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
            PI issued → LC received → shipment → payment → completed
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search PI, client, product, LC…"
              className="rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 pl-8 pr-2.5 py-1.5 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 w-56"
            />
          </div>
          <Button variant="secondary" size="sm" icon={Download} onClick={handleExport}>
            Export CSV
          </Button>
          <Button variant="secondary" size="sm" icon={RefreshCw} onClick={() => { setQuery(''); fetchData(''); }}>
            Refresh
          </Button>
          <Button variant="secondary" size="sm" icon={Plus} onClick={handleManualCreate}>
            Create Manually
          </Button>
          <Button variant="secondary" size="sm" icon={Layers} onClick={() => setShowMulti(true)}>
            Add multiple PIs
          </Button>
          <Button variant="secondary" size="sm" icon={Link2} onClick={() => setShowLink(true)}>
            Link PIs
          </Button>
          <Button variant="primary" size="sm" icon={UploadCloud} onClick={() => setShowUpload(true)}>
            Upload PI
          </Button>
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <KpiCard title="Pipeline Value" value={`$${(summary.total_pipeline_value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`} subvalue={`${summary.total_sales || 0} sales`} icon={DollarSign} color="emerald" />
          {STAGES.map((st) => (
            <KpiCard
              key={st.key}
              title={st.title}
              value={summary[st.key]?.count ?? 0}
              subvalue={`$${(summary[st.key]?.value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
              icon={DollarSign}
              color="blue"
            />
          ))}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-sm text-slate-500">
          Loading sales pipeline…
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5 gap-4 items-start">
            {(() => {
              // F5a invariant: one lc_id split across stages would render 2
              // cards while the backend moves ALL children as a unit — render
              // the LC only in its earliest stage and warn on the split.
              const seenLcKeys = new Set();
              return STAGES.map((st) => {
              const stageSales = salesByStage(st.key);
              const { groups } = groupSalesByLc(stageSales, lcs);
              const byKey = new Map(groups.map((g) => [g.key, g]));
              const rendered = new Set();
              const nodes = [];
              for (const sale of stageSales) {
                const key = lcGroupKey(sale);
                if (key && byKey.has(key)) {
                  if (rendered.has(key)) continue;
                  rendered.add(key);
                  const group = byKey.get(key);
                  const dedupeKey =
                    typeof group.lc.id === 'number' ? `id:${group.lc.id}` : group.key;
                  if (seenLcKeys.has(dedupeKey)) {
                    console.warn(
                      `Split LC ${dedupeKey} appears in multiple stages — rendering only in its earliest stage`
                    );
                    continue;
                  }
                  seenLcKeys.add(dedupeKey);
                  // F6: null-company mirrors carry no meta — render their PIs
                  // as unlinked SaleCards, never as a dead LcBoardCard.
                  if (group.lc._mirror && group.lc.company_id == null) {
                    for (const pi of group.lc.pis) {
                      nodes.push(
                        <SaleCard
                          key={pi.id}
                          sale={pi}
                          actionLabel={movingId || st.action === 'lc' ? null : st.actionLabel}
                          actionVariant={st.actionVariant}
                          onAction={st.action === 'lc' ? undefined : (s) => handleCardAction(s, st)}
                          onView={(s) => setDetailSaleId(s.id)}
                          onDelete={isAdmin ? handleDelete : undefined}
                        />
                      );
                    }
                    continue;
                  }
                  const real = typeof group.lc.id === 'number';
                  nodes.push(
                    <LcBoardCard
                      key={`lc-${group.key}`}
                      lc={group.lc}
                      actionLabel={movingId || !real ? null : st.actionLabel}
                      actionVariant={st.actionVariant}
                      onAction={real ? handleLcMove : undefined}
                      onOpen={handleLcOpen}
                      onViewPI={(pi) => setDetailSaleId(pi.id)}
                      onUnlink={isAdmin && real ? (pi) => handleUnlink(group.lc, pi) : undefined}
                      onManageInvoices={
                        (st.key === 'shipment_ongoing' || st.key === 'payment_due') &&
                        group.lc.pis.length > 0
                          ? () => setDetailSaleId(group.lc.pis[0].id)
                          : undefined
                      }
                      onDelete={isAdmin && real ? handleLcDelete : undefined}
                    />
                  );
                } else {
                  nodes.push(
                    <SaleCard
                      key={sale.id}
                      sale={sale}
                      actionLabel={movingId || st.action === 'lc' ? null : st.actionLabel}
                      actionVariant={st.actionVariant}
                      onAction={st.action === 'lc' ? undefined : (s) => handleCardAction(s, st)}
                      onView={(s) => setDetailSaleId(s.id)}
                      onDelete={isAdmin ? handleDelete : undefined}
                    />
                  );
                }
              }
              // PI total (visible page slice) in integer cents — never an
              // invoice total, never a canonical LC total.
              const columnValue = fromCents(
                stageSales.reduce((sum, s) => sum + toCents(s.total_value), 0)
              );
              return (
                <div key={st.key} className="flex flex-col rounded-xl border border-slate-200 dark:border-slate-700 bg-slate-50/60 dark:bg-slate-900/60 min-h-[200px]">
                  <div className={`px-3.5 py-3 border-b rounded-t-xl ${HEADER_STYLES[st.key] || ''} border-slate-200 dark:border-slate-700`}>
                    <div className="flex items-center justify-between">
                      <h3 className="text-xs font-bold uppercase tracking-wider">{st.title}</h3>
                      <span className="inline-flex items-center justify-center min-w-6 h-6 px-1.5 rounded-full bg-white dark:bg-slate-800 text-xs font-bold shadow-xs">
                        {stageSales.length}
                      </span>
                    </div>
                    <p className="text-xs font-semibold mt-1 opacity-80">
                      PI total (visible) ${formatNumber(columnValue)}
                    </p>
                  </div>
                  <div className="flex flex-col gap-2.5 p-2.5 flex-1">
                    {stageSales.length === 0 ? (
                      <div className="flex-1 flex items-center justify-center py-8 text-xs text-slate-400 dark:text-slate-500">
                        No sales
                      </div>
                    ) : (
                      nodes
                    )}
                  </div>
                </div>
              );
            });
            })()}
          </div>
          {totalSales > pageSize && (
            <div className="flex items-center justify-between mt-4 px-2">
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Showing {Math.min((page - 1) * pageSize + 1, totalSales)}–{Math.min(page * pageSize, totalSales)} of {totalSales} sales
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  icon={ChevronLeft}
                  onClick={() => { setPage(p => Math.max(1, p - 1)); fetchData(query.trim(), page - 1); }}
                  disabled={page === 1}
                  aria-label="Previous page"
                >
                  Previous
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={ChevronRight}
                  onClick={() => { setPage(p => p + 1); fetchData(query.trim(), page + 1); }}
                  disabled={page * pageSize >= totalSales}
                  aria-label="Next page"
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      <UploadPI
        isOpen={showUpload}
        onClose={() => setShowUpload(false)}
        onParsed={handleParsed}
      />

      <ReviewModal
        isOpen={!!reviewData}
        initialData={reviewData}
        onClose={() => setReviewData(null)}
        onSave={handleReviewSave}
      />

      <PaymentModal
        isOpen={!!paymentSale}
        sale={paymentSale}
        onClose={() => setPaymentSale(null)}
        onSaved={fetchData}
      />

      <SaleDetailModal
        saleId={detailSaleId}
        onClose={() => setDetailSaleId(null)}
        onSaved={fetchData}
      />

      <MultiPICreateModal
        isOpen={showMulti}
        onClose={() => setShowMulti(false)}
        onSaved={() => {
          setShowMulti(false);
          fetchData();
        }}
      />

      <LinkPIsModal
        isOpen={showLink}
        unlinkedSales={unlinkedSales}
        existingLcs={lcs}
        onClose={() => setShowLink(false)}
        onLinked={() => {
          setShowLink(false);
          fetchData();
        }}
      />

      <LcDetailModal
        lcId={lcDetailId}
        isOpen={!!lcDetailId}
        onClose={() => setLcDetailId(null)}
        onSaved={() => fetchData()}
        onViewPI={(pi) => {
          setLcDetailId(null);
          setDetailSaleId(pi.id);
        }}
      />

      <InvoicesRequiredModal
        isOpen={showInvoicesRequired}
        onClose={() => {
          setShowInvoicesRequired(false);
          setBarrierLcId(null);
          setBarrierSaleId(null);
          setBarrierPis([]);
          setBarrierMove(null);
          setBarrierDetail({});
        }}
        pis={barrierPis}
        onSuccess={handleBarrierInvoicesSuccess}
      />

      <ProductionRequiredModal
        isOpen={showProductionRequired}
        onClose={() => {
          setShowProductionRequired(false);
          setBarrierLcId(null);
          setBarrierSaleId(null);
          setBarrierDetail({});
        }}
        lcId={barrierLcId}
        saleId={barrierSaleId}
        missingDetail={barrierDetail}
        onSuccess={() => {
          fetchData();
        }}
      />
    </div>
  );
}
