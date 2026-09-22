import { useState, useRef, useCallback } from 'react';
import { UploadCloud, FileText, FileSpreadsheet, X, CheckCircle2, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

const ACCEPTED_TYPES = {
  'application/pdf': { label: 'PDF', icon: FileText, color: 'text-rose-500' },
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': { label: 'XLSX', icon: FileSpreadsheet, color: 'text-emerald-500' },
  'application/vnd.ms-excel': { label: 'XLS', icon: FileSpreadsheet, color: 'text-emerald-500' }
};

export default function UploadArea({ onFileSelect, disabled = false }) {
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState(null);
  const fileInputRef = useRef(null);

  const validateFile = (file) => {
    if (!file) return 'No file selected';
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['pdf', 'xlsx', 'xls'].includes(ext)) {
      return 'Only PDF and Excel files are accepted';
    }
    if (file.size > 50 * 1024 * 1024) {
      return 'File size must be less than 50MB';
    }
    return null;
  };

  const handleFile = useCallback((file) => {
    setError(null);
    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSelectedFile(file);
    if (onFileSelect) onFileSelect(file);
  }, [onFileSelect]);

  const handleDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!disabled) setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget === e.target) {
      setIsDragging(false);
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (disabled) return;
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const handleClick = () => {
    if (!disabled) fileInputRef.current?.click();
  };

  const handleInputChange = (e) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = '';
  };

  const clearFile = (e) => {
    e.stopPropagation();
    setSelectedFile(null);
    setError(null);
    if (onFileSelect) onFileSelect(null);
  };

  const getFileIcon = (file) => {
    if (!file) return null;
    const ext = file.name.split('.').pop().toLowerCase();
    const type = Object.values(ACCEPTED_TYPES).find(t => t.label.toLowerCase() === ext);
    if (type) {
      const Icon = type.icon;
      return <Icon className={clsx('h-6 w-6', type.color)} />;
    }
    return <FileText className="h-6 w-6 text-slate-400" />;
  };

  return (
    <div className="space-y-2">
      <div
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onClick={handleClick}
        className={clsx(
          'relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 transition-all duration-200 cursor-pointer',
          disabled && 'opacity-50 cursor-not-allowed',
          isDragging
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30 scale-[1.01]'
            : selectedFile
            ? 'border-emerald-300 dark:border-emerald-700 bg-emerald-50/50 dark:bg-emerald-950/20'
            : error
            ? 'border-rose-300 dark:border-rose-700 bg-rose-50/50 dark:bg-rose-950/20'
            : 'border-slate-300 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/30 hover:border-blue-400 dark:hover:border-blue-600 hover:bg-blue-50/30 dark:hover:bg-blue-950/20'
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.xlsx,.xls"
          onChange={handleInputChange}
          className="hidden"
          disabled={disabled}
        />

        {selectedFile ? (
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-emerald-100 dark:bg-emerald-950/50">
              <CheckCircle2 className="h-7 w-7 text-emerald-600 dark:text-emerald-400" />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-800 dark:text-white">
                {selectedFile.name}
              </p>
              <div className="flex items-center gap-2 mt-1">
                {getFileIcon(selectedFile)}
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {(selectedFile.size / 1024).toFixed(1)} KB
                </span>
                <span className="text-xs text-slate-400">•</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {selectedFile.name.split('.').pop().toUpperCase()}
                </span>
              </div>
            </div>
            <button
              onClick={clearFile}
              className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 dark:text-rose-400 hover:underline cursor-pointer"
            >
              <X className="h-3.5 w-3.5" /> Remove file
            </button>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-rose-100 dark:bg-rose-950/50">
              <AlertCircle className="h-7 w-7 text-rose-600 dark:text-rose-400" />
            </div>
            <div>
              <p className="text-sm font-medium text-rose-600 dark:text-rose-400">{error}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                Click to try again
              </p>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3 text-center">
            <div className={clsx(
              'flex h-14 w-14 items-center justify-center rounded-xl transition-colors',
              isDragging
                ? 'bg-blue-200 dark:bg-blue-800'
                : 'bg-slate-100 dark:bg-slate-800'
            )}>
              <UploadCloud className={clsx(
                'h-7 w-7 transition-colors',
                isDragging ? 'text-blue-600 dark:text-blue-400' : 'text-slate-400 dark:text-slate-500'
              )} />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-800 dark:text-white">
                {isDragging ? 'Drop your file here' : 'Drag & drop your stock file here'}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                or <span className="text-blue-600 dark:text-blue-400 font-medium">browse files</span>
              </p>
            </div>
            <div className="flex items-center gap-2 mt-1">
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-rose-50 dark:bg-rose-950/40 text-[10px] font-medium text-rose-600 dark:text-rose-400">
                <FileText className="h-3 w-3" /> PDF
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-50 dark:bg-emerald-950/40 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                <FileSpreadsheet className="h-3 w-3" /> XLSX
              </span>
              <span className="text-[10px] text-slate-400 dark:text-slate-500">Max 50MB</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
