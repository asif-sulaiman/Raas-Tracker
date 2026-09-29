import { useState } from 'react';
import { ChevronDown, ChevronRight, ChevronLeft } from 'lucide-react';
import { formatNumber, formatDate } from '../../utils/format';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import clsx from 'clsx';

const TH = 'px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap';
const TD = 'px-4 py-3 whitespace-nowrap';

const PAYMENT_BADGE = {
  Paid: 'success',
  Partial: 'warning',
  Due: 'info',
  Overdue: 'error',
  Pending: 'default',
};

function paymentBadgeVariant(status) {
  return PAYMENT_BADGE[status] || 'default';
}

function Pagination({ currentPage, pageSize, totalRows, onPageChange }) {
  const totalPages = Math.ceil(totalRows / pageSize);
  if (totalPages <= 1) return null;

  return (
    <div className="p-4 border-t border-slate-100 dark:border-slate-800 flex items-center justify-between gap-3">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Page {currentPage} of {totalPages} · {totalRows} rows
      </p>
      <div className="flex items-center gap-1">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage === 1}
          aria-label="Previous page"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage === totalPages}
          aria-label="Next page"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={TH}>Customer</th>
              <th className={TH}>PI No</th>
              <th className={TH}>PI Date</th>
              <th className={TH}>LC No</th>
              <th className={TH}>LC Date</th>
              <th className={TH}>Product</th>
              <th className={TH}>Unit</th>
              <th className={TH}>Qty</th>
              <th className={TH}>Unit Price</th>
              <th className={TH}>Total ($)</th>
              <th className={TH}>Invoice Date</th>
              <th className={TH}>Latest Ship</th>
              <th className={TH}>Actual Ship</th>
              <th className={TH}>Maturity</th>
              <th className={TH}>Receive Date</th>
              <th className={TH}>Received ($)</th>
              <th className={TH}>Due ($)</th>
              <th className={TH}>Status</th>
              <th className={TH}>Comment</th>
            </tr>
          </thead>
          <tbody>
            {[...Array(5)].map((_, i) => (
              <tr key={i} className="animate-pulse">
                {[...Array(19)].map((_, j) => (
                  <td key={j} className={TD}>
                    <div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-3/4" />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GroupedSkeleton() {
  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={TH}>Period</th>
              <th className={TH}>Orders</th>
              <th className={TH}>Gross Sales</th>
              <th className={TH}>Received</th>
              <th className={TH}>Due</th>
              <th className={TH}>Overdue</th>
              <th className={TH}>Action</th>
            </tr>
          </thead>
          <tbody>
            {[...Array(3)].map((_, i) => (
              <tr key={i} className="animate-pulse">
                <td className={TD}><div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-1/2" /></td>
                {[...Array(5)].map((_, j) => (
                  <td key={j} className={TD}><div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-3/4" /></td>
                ))}
                <td className={TD}><div className="h-6 bg-slate-200 dark:bg-slate-700 rounded w-1/2" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 shadow-xs text-center">
      <p className="text-sm font-semibold text-slate-800 dark:text-white">No commercial data found</p>
      <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">Adjust filters or check back later.</p>
    </div>
  );
}

function DetailTable({ rows, currentPage, pageSize, totalRows, onPageChange }) {
  const paymentBadge = (status) => (
    <Badge variant={paymentBadgeVariant(status)} size="xs">
      {status || 'Pending'}
    </Badge>
  );

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={TH}>Customer</th>
              <th className={TH}>PI No</th>
              <th className={TH}>PI Date</th>
              <th className={TH}>LC No</th>
              <th className={TH}>LC Date</th>
              <th className={TH}>Product</th>
              <th className={TH}>Unit</th>
              <th className={TH}>Qty</th>
              <th className={TH}>Unit Price</th>
              <th className={TH}>Total ($)</th>
              <th className={TH}>Invoice Date</th>
              <th className={TH}>Latest Ship</th>
              <th className={TH}>Actual Ship</th>
              <th className={TH}>Maturity</th>
              <th className={TH}>Receive Date</th>
              <th className={TH}>Received ($)</th>
              <th className={TH}>Due ($)</th>
              <th className={TH}>Status</th>
              <th className={TH}>Comment</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
            {rows.map((row, idx) => (
              <tr key={row.sale_id != null ? `${row.sale_id}-${idx}` : idx} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40">
                <td className={clsx(TD, 'font-medium text-slate-900 dark:text-white')}>{row.customer_name || '-'}</td>
                <td className={TD}>{row.pi_number || '-'}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.pi_date)}</td>
                <td className={TD}>{row.lc_number || '-'}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.lc_date)}</td>
                <td className={clsx(TD, 'font-medium text-slate-900 dark:text-white')}>{row.product_name || '-'}</td>
                <td className={TD}>{row.unit || '-'}</td>
                <td className={clsx(TD, 'text-right font-mono text-slate-700 dark:text-slate-300')}>{formatNumber(row.quantity || 0)}</td>
                <td className={clsx(TD, 'text-right font-mono text-slate-700 dark:text-slate-300')}>{formatNumber(row.unit_price || 0)}</td>
                <td className={clsx(TD, 'text-right font-mono font-semibold text-slate-900 dark:text-white')}>{formatNumber(row.total_price || 0)}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.invoice_date)}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.latest_ship_date)}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.actual_ship_date)}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.maturity_date)}</td>
                <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(row.receive_date)}</td>
                <td className={clsx(TD, 'text-right font-mono text-emerald-600 dark:text-emerald-400')}>{formatNumber(row.received_amount || 0)}</td>
                <td className={clsx(TD, 'text-right font-mono', Number(row.due_amount) > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400 dark:text-slate-500')}>
                  {formatNumber(row.due_amount || 0)}
                </td>
                <td className={TD}>{paymentBadge(row.payment_status)}</td>
                <td className="px-4 py-3 text-xs text-slate-500 dark:text-slate-400 max-w-[18rem]">
                  {row.payment_comment || '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalRows > pageSize && (
        <Pagination currentPage={currentPage} pageSize={pageSize} totalRows={totalRows} onPageChange={onPageChange} />
      )}
    </div>
  );
}

function GroupedTable({ periods, currentPage, pageSize, totalRows, onPageChange }) {
  const [expandedPeriods, setExpandedPeriods] = useState(new Set());

  const togglePeriod = (periodStart) => {
    setExpandedPeriods(prev => {
      const next = new Set(prev);
      if (next.has(periodStart)) next.delete(periodStart);
      else next.add(periodStart);
      return next;
    });
  };

  const paymentBadge = (status) => (
    <Badge variant={paymentBadgeVariant(status)} size="xs">
      {status || 'Pending'}
    </Badge>
  );

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={TH}>Period</th>
              <th className={TH}>Orders</th>
              <th className={TH}>Gross Sales</th>
              <th className={TH}>Received</th>
              <th className={TH}>Due</th>
              <th className={TH}>Overdue</th>
              <th className={TH}>Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50 dark:divide-slate-800/50">
            {periods.map((period) => (
              <> 
                {/* Period Header Row */}
                <tr className="bg-slate-50 dark:bg-slate-800/50 cursor-pointer hover:bg-slate-100/80 dark:hover:bg-slate-700/50"
                    onClick={() => togglePeriod(period.period_start)}>
                  <td className={clsx(TD, 'font-semibold text-slate-900 dark:text-white')}>
                    <div className="flex items-center gap-2">
                      {expandedPeriods.has(period.period_start) ? (
                        <ChevronDown className="h-4 w-4 text-slate-500" />
                      ) : (
                        <ChevronRight className="h-4 w-4 text-slate-500" />
                      )}
                      <span>{formatDate(period.period_start)} → {formatDate(period.period_end)}</span>
                    </div>
                  </td>
                  <td className={clsx(TD, 'text-right font-mono')}>{period.kpis?.order_count ?? 0}</td>
                  <td className={clsx(TD, 'text-right font-mono font-semibold text-slate-900 dark:text-white')}>
                    ${formatNumber(period.kpis?.gross_sales ?? 0, 2)}
                  </td>
                  <td className={clsx(TD, 'text-right font-mono text-emerald-600 dark:text-emerald-400')}>
                    ${formatNumber(period.kpis?.received ?? 0, 2)}
                  </td>
                  <td className={clsx(TD, 'text-right font-mono', (period.kpis?.due ?? 0) > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400 dark:text-slate-500')}>
                    ${formatNumber(period.kpis?.due ?? 0, 2)}
                  </td>
                  <td className={clsx(TD, 'text-right font-mono text-rose-600 dark:text-rose-400')}>
                    ${formatNumber(period.kpis?.overdue ?? 0, 2)}
                  </td>
                  <td className={TD}>
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      {expandedPeriods.has(period.period_start) ? 'Collapse' : 'Expand'} {period.items?.length || 0} items
                    </span>
                  </td>
                </tr>

                {/* Expanded Detail Rows */}
                {expandedPeriods.has(period.period_start) && period.items?.length > 0 && (
                  <>
                    {period.items.map((item, idx) => (
                      <tr key={`${item.sale_id}-${idx}`} className="bg-slate-50/50 dark:bg-slate-800/30 hover:bg-slate-100/50 dark:hover:bg-slate-700/30">
                        <td className={clsx(TD, 'font-medium text-slate-900 dark:text-white pl-8')}>{item.customer_name || '-'}</td>
                        <td className={TD}>{item.pi_number || '-'}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.pi_date)}</td>
                        <td className={TD}>{item.lc_number || '-'}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.lc_date)}</td>
                        <td className={clsx(TD, 'font-medium text-slate-900 dark:text-white')}>{item.product_name || '-'}</td>
                        <td className={TD}>{item.unit || '-'}</td>
                        <td className={clsx(TD, 'text-right font-mono text-slate-700 dark:text-slate-300')}>{formatNumber(item.quantity || 0)}</td>
                        <td className={clsx(TD, 'text-right font-mono text-slate-700 dark:text-slate-300')}>{formatNumber(item.unit_price || 0)}</td>
                        <td className={clsx(TD, 'text-right font-mono font-semibold text-slate-900 dark:text-white')}>{formatNumber(item.total_price || 0)}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.invoice_date)}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.latest_ship_date)}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.actual_ship_date)}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.maturity_date)}</td>
                        <td className={clsx(TD, 'text-slate-500 dark:text-slate-400')}>{formatDate(item.receive_date)}</td>
                        <td className={clsx(TD, 'text-right font-mono text-emerald-600 dark:text-emerald-400')}>{formatNumber(item.received_amount || 0)}</td>
                        <td className={clsx(TD, 'text-right font-mono', Number(item.due_amount) > 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400 dark:text-slate-500')}>
                          {formatNumber(item.due_amount || 0)}
                        </td>
                        <td className={TD}>{paymentBadge(item.payment_status)}</td>
                        <td className="px-4 py-3 text-xs text-slate-500 dark:text-slate-400 max-w-[18rem]">
                          {item.payment_comment || '-'}
                        </td>
                      </tr>
                    ))}
                  </>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
      {totalRows > pageSize && (
        <Pagination currentPage={currentPage} pageSize={pageSize} totalRows={totalRows} onPageChange={onPageChange} />
      )}
    </div>
  );
}

export default function CommercialTable({
  data,                    // { rows: [], kpis: {}, meta: {} } for detail OR { periods: [], meta: {} } for grouped
  groupBy = 'none',        // 'none' | 'month' | 'week' | 'year'
  onPageChange,            // callback(page)
  currentPage = 1,
  pageSize = 50,
  totalRows = 0,
  isLoading = false,
}) {
  // DETAIL MODE (groupBy === 'none')
  if (groupBy === 'none') {
    const rows = data?.rows ?? data ?? [];

    if (isLoading) {
      return <DetailSkeleton />;
    }

    if (rows.length === 0) {
      return <EmptyState />;
    }

    return (
      <DetailTable
        rows={rows}
        currentPage={currentPage}
        pageSize={pageSize}
        totalRows={totalRows}
        onPageChange={onPageChange}
      />
    );
  }

  // GROUPED MODE (month/week/year)
  const periods = data?.periods ?? data ?? [];

  if (isLoading) {
    return <GroupedSkeleton />;
  }

  if (periods.length === 0) {
    return <EmptyState />;
  }

  return (
    <GroupedTable
      periods={periods}
      currentPage={currentPage}
      pageSize={pageSize}
      totalRows={totalRows}
      onPageChange={onPageChange}
    />
  );
}