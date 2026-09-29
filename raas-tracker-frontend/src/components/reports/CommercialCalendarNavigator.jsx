import { ChevronLeft, ChevronRight, Calendar, Minus, Plus } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import Button from '../ui/Button';
import clsx from 'clsx';
import { DATE_PRESETS, dateAnchorOptions } from '../../utils/datePresets';

// Parse YYYY-MM-DD as UTC date to avoid timezone issues
function parseUTC(dateStr) {
  const [year, month, day] = dateStr.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

// Format Date as YYYY-MM-DD in UTC
function formatUTC(date) {
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export default function CommercialCalendarNavigator({ 
  groupBy = 'month',
  dateAnchor = 'pi_date',
  dateFrom,
  dateTo,
  onFiltersChange,
  periodStart,
  periodEnd,
  isLoading = false,
}) {
  const [searchParams, setSearchParams] = useSearchParams();

  // Navigate to previous/next period based on groupBy
  const navigatePeriod = (direction) => {
    if (!periodStart || !periodEnd) return;
    
    const start = parseUTC(periodStart);
    let newStart, newEnd;
    
    switch (groupBy) {
      case 'month':
        newStart = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + direction, 1));
        newEnd = new Date(Date.UTC(newStart.getUTCFullYear(), newStart.getUTCMonth() + 1, 0));
        break;
      case 'week':
        // periodStart is already the Monday of the week; just add/subtract 7 days
        newStart = new Date(Date.UTC(
          start.getUTCFullYear(),
          start.getUTCMonth(),
          start.getUTCDate() + direction * 7
        ));
        newEnd = new Date(Date.UTC(
          newStart.getUTCFullYear(),
          newStart.getUTCMonth(),
          newStart.getUTCDate() + 6
        ));
        break;
      case 'year':
        newStart = new Date(Date.UTC(start.getUTCFullYear() + direction, 0, 1));
        newEnd = new Date(Date.UTC(newStart.getUTCFullYear(), 11, 31));
        break;
      default:
        return;
    }
    
    const params = new URLSearchParams(searchParams);
    params.set('date_from', formatUTC(newStart));
    params.set('date_to', formatUTC(newEnd));
    params.set('page', '1');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ date_from: formatUTC(newStart), date_to: formatUTC(newEnd), page: 1 });
  };

  // Quick preset handler
  const handlePreset = (presetName) => {
    if (!presetName) {
      const params = new URLSearchParams(searchParams);
      params.delete('date_from');
      params.delete('date_to');
      params.delete('date_preset');
      params.set('page', '1');
      setSearchParams(params, { replace: true });
      onFiltersChange?.({ date_from: '', date_to: '', date_preset: '', page: 1 });
      return;
    }
    
    const { from, to } = DATE_PRESETS[presetName]();
    const params = new URLSearchParams(searchParams);
    params.set('date_from', from);
    params.set('date_to', to);
    params.set('date_preset', presetName);
    params.set('page', '1');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ date_from: from, date_to: to, date_preset: presetName, page: 1 });
  };

  // Custom date inputs
  const handleDateFrom = (e) => {
    const params = new URLSearchParams(searchParams);
    params.set('date_from', e.target.value);
    params.set('page', '1');
    params.delete('date_preset');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ date_from: e.target.value, date_preset: '', page: 1 });
  };

  const handleDateTo = (e) => {
    const params = new URLSearchParams(searchParams);
    params.set('date_to', e.target.value);
    params.set('page', '1');
    params.delete('date_preset');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ date_to: e.target.value, date_preset: '', page: 1 });
  };

  const handleAnchorChange = (e) => {
    const params = new URLSearchParams(searchParams);
    params.set('date_anchor', e.target.value);
    params.set('page', '1');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ date_anchor: e.target.value, page: 1 });
  };

  const clearDates = () => {
    const params = new URLSearchParams(searchParams);
    params.delete('date_from');
    params.delete('date_to');
    params.delete('date_preset');
    params.set('page', '1');
    setSearchParams(params, { replace: true });
    onFiltersChange?.({ date_from: '', date_to: '', date_preset: '', page: 1 });
  };

  const formatPeriodLabel = () => {
    if (!periodStart || !periodEnd) return 'No period selected';
    const start = parseUTC(periodStart);
    const end = parseUTC(periodEnd);
    
    const startStr = start.toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
    if (groupBy === 'month') return startStr;
    if (groupBy === 'year') return start.getUTCFullYear().toString();
    // week
    const endStr = end.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
    const startStrShort = start.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
    return `${startStrShort} \u2013 ${endStr}`;
  };

  // Layout:
  // [ \u25c4 Prev ]  \u2014\u2014  [ Period Label ]  \u2014\u2014  [ Next \u25ba ]
  // Date Anchor: [pi_date \u25bc]  |  Preset: [This Month \u25bc]  |  From: [\ud83d\udcc5]  To: [\ud83d\udcc5]  [Clear]
  
  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-4 shadow-xs space-y-3">
      {/* Period Navigator */}
      <div className="flex items-center justify-between gap-3">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => navigatePeriod(-1)}
          disabled={isLoading}
          aria-label="Previous period"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        
        <div className="flex-1 text-center">
          <div className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
            {groupBy.charAt(0).toUpperCase() + groupBy.slice(1)} View
          </div>
          <div className="text-lg font-semibold text-slate-900 dark:text-white">
            {formatPeriodLabel()}
          </div>
        </div>
        
        <Button
          variant="secondary"
          size="sm"
          onClick={() => navigatePeriod(1)}
          disabled={isLoading}
          aria-label="Next period"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>

      {/* Date Controls */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div>
          <label htmlFor="nav-date-anchor" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Date Anchor</label>
          <select
            id="nav-date-anchor"
            value={dateAnchor}
            onChange={handleAnchorChange}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            {dateAnchorOptions.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
        </div>
        
        <div>
          <label htmlFor="nav-date-preset" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">Quick Preset</label>
          <select
            id="nav-date-preset"
            value={searchParams.get('date_preset') || ''}
            onChange={(e) => handlePreset(e.target.value)}
            className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
          >
            <option value="">Custom Range</option>
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
            <label htmlFor="nav-date-from" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">From</label>
            <input
              id="nav-date-from"
              type="date"
              value={dateFrom || ''}
              onChange={handleDateFrom}
              className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
            />
          </div>
          <div className="flex-1">
            <label htmlFor="nav-date-to" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">To</label>
            <input
              id="nav-date-to"
              type="date"
              value={dateTo || ''}
              onChange={handleDateTo}
              className="w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500"
            />
          </div>
        </div>
        
        <div className="flex items-end">
          <Button variant="secondary" size="sm" onClick={clearDates} className="w-full">
            Clear Dates
          </Button>
        </div>
      </div>
    </div>
  );
}