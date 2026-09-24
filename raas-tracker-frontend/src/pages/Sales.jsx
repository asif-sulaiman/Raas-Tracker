import { useState, useEffect, useCallback, useRef } from 'react';
import { UploadCloud, Plus, DollarSign, RefreshCw, Search, Download } from 'lucide-react';
import Button from '../components/ui/Button';
import KpiCard from '../components/cards/KpiCard';
import PipelineColumn from '../components/sales/PipelineColumn';
import UploadPI from '../components/sales/UploadPI';
import ReviewModal from '../components/sales/ReviewModal';
import LCModal from '../components/sales/LCModal';
import PaymentModal from '../components/sales/PaymentModal';
import SaleDetailModal from '../components/sales/SaleDetailModal';
import { STAGES, isOverdue } from '../utils/sales';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';

export default function Sales() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const [sales, setSales] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [movingId, setMovingId] = useState(null);

  const [showUpload, setShowUpload] = useState(false);
  const [reviewData, setReviewData] = useState(null);
  const [lcSale, setLcSale] = useState(null);
  const [paymentSale, setPaymentSale] = useState(null);
  const [detailSaleId, setDetailSaleId] = useState(null);
  const [query, setQuery] = useState('');

  const abortRef = useRef(null);
  const fetchData = useCallback(async (q) => {
    // Abort the in-flight search so out-of-order responses can't win.
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const url = q ? `/api/sales?q=${encodeURIComponent(q)}` : '/api/sales';
    try {
      const [salesRes, summaryRes] = await Promise.all([
        apiFetch(url, { signal: ctrl.signal }),
        apiFetch('/api/sales/summary', { signal: ctrl.signal }),
      ]);
      const [salesData, summaryData] = await Promise.all([salesRes.json(), summaryRes.json()]);
      if (Array.isArray(salesData)) setSales(salesData);
      if (summaryData) setSummary(summaryData);
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      // List stays as-is on failure; mutations surface their own errors.
    } finally {
      if (abortRef.current === ctrl) setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const t = setTimeout(() => fetchData(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query, fetchData]);

  const handleExport = () => {
    window.open('/api/sales/export', '_blank');
  };

  const salesByStage = (stage) => sales.filter((s) => s.stage === stage);

  const handleCardAction = (sale, stageDef) => {
    if (stageDef.action === 'lc') {
      setLcSale(sale);
    } else if (stageDef.action === 'payment') {
      setPaymentSale(sale);
    } else if (stageDef.action === 'move') {
      handleMove(sale.id);
    }
  };

  const handleMove = async (saleId, notes) => {
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
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5 gap-4 items-start">
          {STAGES.map((st) => (
            <PipelineColumn
              key={st.key}
              stage={st.key}
              title={st.title}
              sales={salesByStage(st.key)}
              actionLabel={movingId ? null : st.actionLabel}
              actionVariant={st.actionVariant}
              onAction={(sale) => handleCardAction(sale, st)}
              onView={(sale) => setDetailSaleId(sale.id)}
              onDelete={isAdmin ? handleDelete : undefined}
            />
          ))}
        </div>
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

      <LCModal
        isOpen={!!lcSale}
        sale={lcSale}
        onClose={() => setLcSale(null)}
        onSaved={fetchData}
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
    </div>
  );
}
