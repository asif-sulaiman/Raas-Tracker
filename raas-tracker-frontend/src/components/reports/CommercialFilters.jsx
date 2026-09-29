import { useSearchParams } from 'react-router-dom';
import { Filter, X } from 'lucide-react';
import Button from '../ui/Button';
import { DATE_PRESETS, dateAnchorOptions } from '../../utils/datePresets';

export default function CommercialFilters({
  onFiltersChange,
  companies = [],
  _initialFilters = {},
}) {
  const [searchParams, setSearchParams] = useSearchParams();

  // Read all filters from URL (with defaults)
  const filters = {
    date_anchor: searchParams.get('date_anchor') || 'pi_date',
    date_from: searchParams.get('date_from') || '',
    date_to: searchParams.get('date_to') || '',
    date_preset: searchParams.get('date_preset') || '',
    customer_name: searchParams.get('customer_name') || '',
    product_name: searchParams.get('product_name') || '',
    company_id: searchParams.get('company_id') || '',
    stage: searchParams.get('stage') || '',
    payment_status: searchParams.get('payment_status') || '',
    group_by: searchParams.get('group_by') || 'none',
    page: parseInt(searchParams.get('page') || '1', 10),
  };

  const updateFilters = (newFilters) => {
    const params = new URLSearchParams(searchParams);
    Object.entries(newFilters).forEach(([k, v]) => {
      if (v === '' || v === null || v === undefined) params.delete(k);
      else params.set(k, String(v));
    });
    // Reset page to 1 when filters change
    params.set('page', '1');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ ...filters, ...newFilters, page: 1 });
  };

  const handlePresetChange = (preset) => {
    if (!preset) {
      updateFilters({ date_preset: '', date_from: '', date_to: '' });
      return;
    }
    const { from, to } = DATE_PRESETS[preset]();
    updateFilters({ date_preset: preset, date_from: from, date_to: to });
  };

  const clearAll = () => {
    const currentGroupBy = filters.group_by;
    setSearchParams({ group_by: currentGroupBy }, { replace: true });
    onFiltersChange?.({
      ...filters,
      date_anchor: 'pi_date',
      date_from: '',
      date_to: '',
      date_preset: '',
      customer_name: '',
      product_name: '',
      company_id: '',
      stage: '',
      payment_status: '',
      group_by: currentGroupBy,
      page: 1,
    });
  };

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-4 shadow-xs space-y-3">
      {/* Row 1 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="relative">
          <label htmlFor="quick-search" className="sr-only">
            Quick Search: PI No / Customer
          </label>
          <input
            id="quick-search"
            type="text"
            placeholder="Quick Search: PI No / Customer"
            value={filters.customer_name}
            onChange={(e) =>
              updateFilters({
                customer_name: e.target.value,
                product_name: e.target.value,
              })
            }
            className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          />
          <Filter className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
        </div>

        <div>
          <label htmlFor="date-anchor" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
            Date Anchor
          </label>
          <select
            id="date-anchor"
            value={filters.date_anchor}
            onChange={(e) => updateFilters({ date_anchor: e.target.value })}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            {dateAnchorOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="date-preset" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
            Quick Range
          </label>
          <select
            id="date-preset"
            value={filters.date_preset}
            onChange={(e) => handlePresetChange(e.target.value)}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            <option value="">Custom...</option>
            <option value="This Month">This Month</option>
            <option value="Last Month">Last Month</option>
            <option value="This Quarter">This Quarter</option>
            <option value="Last Quarter">Last Quarter</option>
            <option value="This Year">This Year</option>
            <option value="Last Year">Last Year</option>
          </select>
        </div>

        <div className="flex items-end gap-2">
          <div className="flex-1">
            <label htmlFor="date-from" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
              From
            </label>
            <input
              id="date-from"
              type="date"
              value={filters.date_from}
              onChange={(e) => updateFilters({ date_from: e.target.value, date_preset: '' })}
              className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
            />
          </div>
          <div className="flex-1">
            <label htmlFor="date-to" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
              To
            </label>
            <input
              id="date-to"
              type="date"
              value={filters.date_to}
              onChange={(e) => updateFilters({ date_to: e.target.value, date_preset: '' })}
              className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
            />
          </div>
        </div>
      </div>

      {/* Row 2 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
        <div>
          <label htmlFor="stage" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
            Stage
          </label>
          <select
            id="stage"
            value={filters.stage}
            onChange={(e) => updateFilters({ stage: e.target.value })}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            <option value="">All Stages</option>
            <option value="pi_issued">PI Issued</option>
            <option value="lc_received">LC Received</option>
            <option value="shipment_ongoing">Shipment Ongoing</option>
            <option value="payment_due">Payment Due</option>
            <option value="completed">Completed</option>
          </select>
        </div>

        <div>
          <label htmlFor="payment-status" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
            Payment Status
          </label>
          <select
            id="payment-status"
            value={filters.payment_status}
            onChange={(e) => updateFilters({ payment_status: e.target.value })}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            <option value="">All Statuses</option>
            <option value="Paid">Paid</option>
            <option value="Overdue">Overdue</option>
            <option value="Partial">Partial</option>
            <option value="Due">Due</option>
            <option value="Pending">Pending</option>
          </select>
        </div>

        <div>
          <label htmlFor="company" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
            Company
          </label>
          <select
            id="company"
            value={filters.company_id}
            onChange={(e) => updateFilters({ company_id: e.target.value })}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            <option value="">All Companies</option>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="group-by" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
            Group By
          </label>
          <select
            id="group-by"
            value={filters.group_by}
            onChange={(e) => updateFilters({ group_by: e.target.value })}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            <option value="none">None (Detail Rows)</option>
            <option value="month">Month</option>
            <option value="week">Week</option>
            <option value="year">Year</option>
          </select>
        </div>

        <div className="flex items-end gap-2">
          <Button variant="secondary" size="sm" onClick={clearAll} className="flex-1">
            <X className="h-4 w-4 mr-1" />
            Clear
          </Button>
        </div>
      </div>
    </div>
  );
}