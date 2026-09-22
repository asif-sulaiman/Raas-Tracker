import { useState } from 'react';
import clsx from 'clsx';
import Badge from '../ui/Badge';
import { formatNumber } from '../../utils/format';
import { ChevronDown, ChevronRight } from 'lucide-react';

const TABS = [
  { key: 'matches', label: 'Matched', variant: 'matched' },
  { key: 'last_month_mismatches', label: 'Last Month', variant: 'warning' },
  { key: 'this_month_mismatches', label: 'This Month', variant: 'info' },
  { key: 'both_mismatches', label: 'Both Months', variant: 'error' },
  { key: 'not_in_db', label: 'Not in DB', variant: 'new' },
  { key: 'not_in_upload', label: 'Not in Upload', variant: 'default' }
];

function ComparisonRow({ row, type }) {
  const diff = row.diff_last || row.diff_this || 0;
  
  return (
    <tr className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
      <td className="px-4 py-3 font-medium text-slate-900 dark:text-white">
        {row.name}
      </td>
      <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
        {row.db_last !== undefined ? formatNumber(row.db_last) : '-'}
      </td>
      <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
        {row.upload_last !== undefined ? formatNumber(row.upload_last) : '-'}
      </td>
      <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
        {row.db_this !== undefined ? formatNumber(row.db_this) : '-'}
      </td>
      <td className="px-4 py-3 text-slate-600 dark:text-slate-400">
        {row.upload_this !== undefined ? formatNumber(row.upload_this) : '-'}
      </td>
      <td className="px-4 py-3">
        {diff !== 0 ? (
          <span className={clsx(
            'text-xs font-semibold',
            diff > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'
          )}>
            {diff > 0 ? '+' : ''}{formatNumber(diff)}
          </span>
        ) : (
          <span className="text-xs text-slate-400">-</span>
        )}
      </td>
      <td className="px-4 py-3">
        {row.batch_number && (
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {row.batch_number}
          </span>
        )}
      </td>
    </tr>
  );
}

export default function ComparisonResults({ results = {} }) {
  const [activeTab, setActiveTab] = useState('matches');
  const [expandedSections, setExpandedSections] = useState({
    matches: true,
    last_month_mismatches: true,
    this_month_mismatches: true,
    both_mismatches: true,
    not_in_db: true,
    not_in_upload: true
  });

  const toggleSection = (key) => {
    setExpandedSections(prev => ({
      ...prev,
      [key]: !prev[key]
    }));
  };

  const getTabCount = (key) => {
    const data = results[key];
    return Array.isArray(data) ? data.length : 0;
  };

  const hasData = Object.keys(results).some(key => {
    const data = results[key];
    return Array.isArray(data) && data.length > 0;
  });

  if (!hasData) {
    return (
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center">
        <p className="text-slate-500 dark:text-slate-400 text-sm">
          Upload a file to see comparison results
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
      {/* Tab Navigation */}
      <div className="flex overflow-x-auto border-b border-slate-200/80 dark:border-slate-800">
        {TABS.map((tab) => {
          const count = getTabCount(tab.key);
          if (count === 0) return null;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={clsx(
                'flex items-center gap-2 px-4 py-3 text-xs font-semibold whitespace-nowrap border-b-2 transition-colors cursor-pointer',
                activeTab === tab.key
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400 bg-blue-50/50 dark:bg-blue-950/20'
                  : 'border-transparent text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/50'
              )}
            >
              {tab.label}
              <span className={clsx(
                'px-1.5 py-0.5 rounded-md text-[10px] font-bold',
                activeTab === tab.key
                  ? 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400'
              )}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Table Content */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-50/80 dark:bg-slate-800/60 border-b border-slate-200/80 dark:border-slate-800 text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wider">
            <tr>
              <th className="px-4 py-3">Chemical Name</th>
              <th className="px-4 py-3">DB Last</th>
              <th className="px-4 py-3">Upload Last</th>
              <th className="px-4 py-3">DB This</th>
              <th className="px-4 py-3">Upload This</th>
              <th className="px-4 py-3">Difference</th>
              <th className="px-4 py-3">Batch</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
            {Array.isArray(results[activeTab]) && results[activeTab].length > 0 ? (
              results[activeTab].map((row, idx) => (
                <ComparisonRow key={row.name || idx} row={row} type={activeTab} />
              ))
            ) : (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-400 dark:text-slate-500">
                  No items in this category
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="px-4 py-2.5 border-t border-slate-200/80 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/30 text-xs text-slate-500 dark:text-slate-400">
        Showing {getTabCount(activeTab)} items in "{TABS.find(t => t.key === activeTab)?.label}" category
      </div>
    </div>
  );
}
