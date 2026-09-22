import clsx from 'clsx';
import { formatDateTime } from '../../utils/format';
import Badge from '../ui/Badge';
import Button from '../ui/Button';
import { Eye, ClipboardCheck, Download, Trash2, Clock, FileText, FileSpreadsheet } from 'lucide-react';

const STATUS_MAP = {
  uploaded: { label: 'Uploaded', variant: 'uploaded', dot: true },
  completed: { label: 'Completed', variant: 'matched', dot: true },
  approved: { label: 'Approved', variant: 'success', dot: true },
  adjusted: { label: 'Adjusted', variant: 'info', dot: true },
  rejected: { label: 'Rejected', variant: 'error', dot: true }
};

const FILE_TYPE_ICONS = {
  pdf: { icon: FileText, color: 'text-rose-500 bg-rose-50 dark:bg-rose-950/40' },
  xlsx: { icon: FileSpreadsheet, color: 'text-emerald-500 bg-emerald-50 dark:bg-emerald-950/40' },
  xls: { icon: FileSpreadsheet, color: 'text-emerald-500 bg-emerald-50 dark:bg-emerald-950/40' }
};

export default function UploadHistory({ uploads = [], onViewResults, onReview, onExport, onDelete }) {
  const getFileTypeInfo = (filename) => {
    if (!filename) return null;
    const ext = filename.split('.').pop().toLowerCase();
    return FILE_TYPE_ICONS[ext] || null;
  };

  if (uploads.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-8 text-center">
        <FileText className="h-10 w-10 text-slate-300 dark:text-slate-600 mx-auto mb-3" />
        <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
          No uploads yet
        </p>
        <p className="text-xs text-slate-400 dark:text-slate-500 mt-1">
          Upload a PDF or Excel file to get started
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xs overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-200/80 dark:border-slate-800">
        <h3 className="text-base font-semibold text-slate-900 dark:text-white">
          Upload History
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          Recent stock file uploads and their reconciliation status
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-50/80 dark:bg-slate-800/60 border-b border-slate-200/80 dark:border-slate-800 text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wider">
            <tr>
              <th className="px-4 py-3">File</th>
              <th className="px-4 py-3">Upload Date</th>
              <th className="px-4 py-3">Total</th>
              <th className="px-4 py-3">Matched</th>
              <th className="px-4 py-3">Match %</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800/80">
            {uploads.map((upload) => {
              const statusInfo = STATUS_MAP[upload.status] || STATUS_MAP.uploaded;
              const fileInfo = getFileTypeInfo(upload.filename);
              const FileIcon = fileInfo?.icon || FileText;

              return (
                <tr key={upload.id} className="hover:bg-slate-50/80 dark:hover:bg-slate-800/40 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5">
                      <div className={clsx(
                        'flex h-8 w-8 items-center justify-center rounded-lg',
                        fileInfo?.color || 'text-slate-500 bg-slate-50 dark:bg-slate-800'
                      )}>
                        <FileIcon className="h-4 w-4" />
                      </div>
                      <span className="font-medium text-slate-900 dark:text-white truncate max-w-[200px]">
                        {upload.filename || `Upload #${upload.id}`}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
                      <Clock className="h-3.5 w-3.5" />
                      {formatDateTime(upload.upload_date)}
                    </div>
                  </td>
                  <td className="px-4 py-3 font-mono font-bold text-slate-800 dark:text-slate-100">
                    {upload.total_chemicals || 0}
                  </td>
                  <td className="px-4 py-3 font-mono text-slate-700 dark:text-slate-300">
                    {upload.matched || 0}
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx(
                      'font-semibold',
                      (upload.match_percentage || 0) >= 90
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : (upload.match_percentage || 0) >= 70
                        ? 'text-amber-600 dark:text-amber-400'
                        : 'text-rose-600 dark:text-rose-400'
                    )}>
                      {(upload.match_percentage || 0).toFixed(1)}%
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={statusInfo.variant} dot={statusInfo.dot}>
                      {statusInfo.label}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5">
                      {onViewResults && (
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={Eye}
                          onClick={() => onViewResults(upload.id)}
                        >
                          View
                        </Button>
                      )}
                      {onReview && upload.status !== 'approved' && (
                        <Button
                          variant="secondary"
                          size="sm"
                          icon={ClipboardCheck}
                          onClick={() => onReview(upload.id)}
                        >
                          Review
                        </Button>
                      )}
                      {onExport && (
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={Download}
                          onClick={() => onExport(upload.id)}
                        />
                      )}
                      {onDelete && (
                        <button
                          onClick={() => onDelete(upload.id)}
                          className="p-1.5 rounded-md text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer"
                          title="Delete upload"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
