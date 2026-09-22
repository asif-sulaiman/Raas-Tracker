import { useState, useRef } from 'react';
import { UploadCloud, FileText, X, AlertCircle, Loader2 } from 'lucide-react';
import clsx from 'clsx';
import Modal from '../modals/Modal';
import Button from '../ui/Button';
import { useAuth } from '../../context/AuthContext';

export default function UploadPI({ isOpen, onClose, onParsed }) {
  const { apiFetch } = useAuth();
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [error, setError] = useState(null);
  const [parsing, setParsing] = useState(false);
  const fileInputRef = useRef(null);

  const reset = () => {
    setSelectedFile(null);
    setError(null);
    setParsing(false);
    setIsDragging(false);
  };

  const handleClose = () => {
    reset();
    onClose?.();
  };

  const validateFile = (file) => {
    if (!file) return 'No file selected';
    const name = file.name.toLowerCase();
    if (!name.endsWith('.pdf') && !name.endsWith('.docx')) {
      return 'Only .pdf and .docx PI files are accepted. For legacy .doc, save as .docx or PDF and retry';
    }
    if (file.size > 50 * 1024 * 1024) {
      return 'File size must be less than 50MB';
    }
    return null;
  };

  const handleFile = (file) => {
    setError(null);
    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSelectedFile(file);
  };

  const handleParse = async () => {
    if (!selectedFile) return;
    setParsing(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      const res = await apiFetch('/api/sales/parse', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      reset();
      onParsed?.(data);
    } catch (err) {
      setError(err.message || 'Could not parse PI file');
    } finally {
      setParsing(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Upload PI Document"
      subtitle="Upload a PDF or .docx proforma invoice - data will be extracted for your review"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={handleClose} disabled={parsing}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            icon={parsing ? Loader2 : UploadCloud}
            loading={parsing}
            disabled={!selectedFile || parsing}
            onClick={handleParse}
          >
            Parse & Review
          </Button>
        </>
      }
    >
      <div className="space-y-2">
        <div
          onDragEnter={(e) => { e.preventDefault(); if (!parsing) setIsDragging(true); }}
          onDragLeave={(e) => { e.preventDefault(); if (e.currentTarget === e.target) setIsDragging(false); }}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            if (parsing) return;
            const file = e.dataTransfer.files?.[0];
            if (file) handleFile(file);
          }}
          onClick={() => { if (!parsing) fileInputRef.current?.click(); }}
          className={clsx(
            'relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 transition-all duration-200 cursor-pointer',
            parsing && 'opacity-60 cursor-wait',
            isDragging
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30 scale-[1.01]'
              : selectedFile
              ? 'border-emerald-300 dark:border-emerald-700 bg-emerald-50/50 dark:bg-emerald-950/20'
              : error
              ? 'border-rose-300 dark:border-rose-700 bg-rose-50/50 dark:bg-rose-950/20'
              : 'border-slate-300 dark:border-slate-700 bg-slate-50/50 dark:bg-slate-800/30 hover:border-blue-400'
          )}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); e.target.value = ''; }}
            className="hidden"
            disabled={parsing}
          />

          {selectedFile ? (
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-blue-100 dark:bg-blue-950/50">
                <FileText className="h-7 w-7 text-blue-600 dark:text-blue-400" />
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-800 dark:text-white">{selectedFile.name}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                  {(selectedFile.size / 1024).toFixed(1)} KB • {selectedFile.name.toLowerCase().endsWith('.pdf') ? 'PDF' : 'DOCX'}
                </p>
              </div>
              {!parsing && (
                <button
                  onClick={(e) => { e.stopPropagation(); setSelectedFile(null); setError(null); }}
                  className="inline-flex items-center gap-1 text-xs font-medium text-rose-600 dark:text-rose-400 hover:underline cursor-pointer"
                >
                  <X className="h-3.5 w-3.5" /> Remove file
                </button>
              )}
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-rose-100 dark:bg-rose-950/50">
                <AlertCircle className="h-7 w-7 text-rose-600 dark:text-rose-400" />
              </div>
              <p className="text-sm font-medium text-rose-600 dark:text-rose-400">{error}</p>
              <p className="text-xs text-slate-500">Click to try again</p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-slate-100 dark:bg-slate-800">
                <UploadCloud className="h-7 w-7 text-slate-400" />
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-800 dark:text-white">
                  {isDragging ? 'Drop your PI file here' : 'Drag & drop your PI PDF/.docx here'}
                </p>
                <p className="text-xs text-slate-500 mt-1">
                  or <span className="text-blue-600 font-medium">browse files</span>
                </p>
              </div>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 dark:bg-blue-950/40 text-[10px] font-medium text-blue-600 dark:text-blue-400">
                <FileText className="h-3 w-3" /> PDF/DOCX • Max 50MB
              </span>
            </div>
          )}
        </div>
        <p className="text-[11px] text-slate-400 dark:text-slate-500">
          Tip: the PI number, date and client are read from the Invoice Number, Invoice Date and Mailing Address in the document.
        </p>
      </div>
    </Modal>
  );
}
