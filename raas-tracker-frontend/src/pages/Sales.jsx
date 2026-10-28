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
import SaleDetailModal from '../components/sales/SaleDetailModal';
import { BOARD_STAGES, STAGES, groupSalesByLc, lcGroupKey, nextStageFor } from '../utils/sales';
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
  const [detailSaleId, setDetailSaleId] = useState(null);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [totalSales, setTotalSales] = useState(0);

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
    if (stageDef.action === 'detail') {
      // Payment Due is invoice territory now: the sale-level payment form is
      // gone, and the card opens the editor that owns the invoices.
      setDetailSaleId(sale.id);
    } else if (stageDef.action === 'move') {
      handleMove(sale);
    }
  };

  const handleMove = async (sale, notes) => {
    const saleId = sale?.id ?? sale;
    setMovingId(saleId);
    try {
      await apiFetch(`/api/sales/${saleId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: notes || '' }),
      });
      toast.success('Sale moved to next stage');
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

  // LC lane: one card per LC moves/unlinks as a unit; PIs travel with the LC.
  // Shipment Ongoing needs no invoice, so that crossing posts straight through.
  // The Payment Due hop is not offered here: the LC rolls up server-side when
  // its last invoice gets an actual ship date (`_sync_lc_payment_due`).
  const handleLcMove = async (lc) => {
    if (typeof lc?.id !== 'number') return;
    const next = nextStageFor(lc.stage);
    if (!next) return;
    setMovingId(`lc-${lc.id}`);
    try {
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

  // Payment Due LCs open invoice management on their first PI; the stage move
  // is not offered from that column (payment accrues per invoice).
  const handleLcManage = (lc) => {
    const first = Array.isArray(lc?.pis) ? lc.pis[0] : null;
    if (first?.id) setDetailSaleId(first.id);
  };

  // One invoice step straight from the board. Shipping posts the actual date,
  // which advances the sale (and possibly the LC) server-side — so the board
  // is refreshed after every step. When the PI still has uninvoiced quantity,
  // the operator is asked whether more invoices are coming.
  const handleInvoiceStep = async (sale, invoice, kind, payload) => {
    const res = await apiFetch(`/api/sales/${sale.id}/invoices/${invoice.invoice_id}/${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (kind === 'book') toast.success('Shipment booked');
    else if (kind === 'ship') toast.success('Invoice shipped');
    else toast.success(data?.status === 'paid' ? 'Invoice paid' : 'Payment recorded');
    fetchData();
    if (kind === 'ship' && sale.has_uninvoiced_remainder) {
      const more = await confirm({
        title: 'Create more invoices?',
        message: `${sale.pi_number || `PI #${sale.id}`} still has uninvoiced quantity. Will you invoice the rest of the PI?`,
        confirmLabel: 'Add invoice',
        cancelLabel: 'No more invoices',
      });
      if (more) setDetailSaleId(sale.id);
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
          <div
            role="region"
            aria-label="Sales pipeline board"
            className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 items-start"
          >
            {(() => {
              // Group once across every stage: an invoice can advance a PI
              // ahead of its LC (a sibling invoice has not shipped yet), so a
              // lagging LC must still render in its own column while the
              // advanced PI renders on its own in the later column.
              const { groups: allGroups } = groupSalesByLc(sales, lcs);
              const groupByKey = new Map(allGroups.map((g) => [g.key, g]));
              return BOARD_STAGES.map((st) => {
              const stageSales = salesByStage(st.key);
              const rendered = new Set();
              const nodes = [];

              const saleCard = (sale) => (
                <SaleCard
                  key={sale.id}
                  sale={sale}
                  actionLabel={movingId || st.action === 'lc' ? null : st.actionLabel}
                  actionVariant={st.actionVariant}
                  onAction={st.action === 'lc' ? undefined : (s) => handleCardAction(s, st)}
                  onView={(s) => setDetailSaleId(s.id)}
                  onDelete={isAdmin ? handleDelete : undefined}
                  onInvoiceStep={handleInvoiceStep}
                  canEdit={isAdmin}
                />
              );

              const lcCard = (group) => {
                const real = typeof group.lc.id === 'number';
                return (
                  <LcBoardCard
                    key={`lc-${group.key}`}
                    lc={group.lc}
                    actionLabel={movingId || !real ? null : st.actionLabel}
                    actionVariant={st.actionVariant}
                    onAction={
                      real
                        ? (st.action === 'detail' ? handleLcManage : handleLcMove)
                        : undefined
                    }
                    onOpen={handleLcOpen}
                    onViewPI={(pi) => setDetailSaleId(pi.id)}
                    onUnlink={isAdmin && real ? (pi) => handleUnlink(group.lc, pi) : undefined}
                    onManageInvoices={
                      st.key === 'shipment_ongoing' && group.lc.pis.length > 0
                        ? () => setDetailSaleId(group.lc.pis[0].id)
                        : undefined
                    }
                    onDelete={isAdmin && real ? handleLcDelete : undefined}
                  />
                );
              };

              // F6: null-company mirrors carry no meta — render their PIs as
              // unlinked SaleCards, never as a dead LcBoardCard.
              const isDeadMirror = (group) => group.lc._mirror && group.lc.company_id == null;

              for (const sale of stageSales) {
                const key = lcGroupKey(sale);
                const group = key ? groupByKey.get(key) : null;
                if (!group || isDeadMirror(group)) {
                  nodes.push(saleCard(sale));
                  continue;
                }
                // The PI advanced ahead of its LC: it keeps its own card here
                // (with its invoice rows) and the LC card stays behind.
                if (group.lc.stage !== st.key) {
                  nodes.push(saleCard(sale));
                  continue;
                }
                if (rendered.has(group.key)) continue;
                rendered.add(group.key);
                nodes.push(lcCard(group));
              }
              // An LC whose PIs all sit in other columns still shows in its
              // own column, so it never disappears from the board.
              for (const group of allGroups) {
                if (group.lc.stage !== st.key || rendered.has(group.key)) continue;
                if (isDeadMirror(group)) continue;
                rendered.add(group.key);
                nodes.push(lcCard(group));
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
                    {nodes.length === 0 ? (
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
    </div>
  );
}
