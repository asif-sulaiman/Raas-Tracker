import { useState, useEffect } from 'react';
import { UploadCloud, FileText, AlertCircle, CheckCircle2, Loader2, ArrowRight } from 'lucide-react';
import UploadArea from '../components/forms/UploadArea';
import MismatchSummary from '../components/cards/MismatchSummary';
import ComparisonResults from '../components/tables/ComparisonResults';
import UploadHistory from '../components/tables/UploadHistory';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';

export default function Upload() {
  const { apiFetch, user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const { confirm } = useConfirm();
  const [selectedFile, setSelectedFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadComplete, setUploadComplete] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [currentResults, setCurrentResults] = useState(null);
  const [uploadHistory, setUploadHistory] = useState([]);

  const loadHistory = async () => {
    try {
      const res = await apiFetch('/api/uploads');
      const data = await res.json();
      if (Array.isArray(data)) setUploadHistory(data);
    } catch {
      // History stays as-is on failure; upload/delete surface their own errors.
    }
  };

  useEffect(() => {
    loadHistory();
  }, []);

  const handleFileSelect = (file) => {
    setSelectedFile(file);
    setUploadComplete(false);
    setShowResults(false);
    setCurrentResults(null);
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    setIsUploading(true);

    const formData = new FormData();
    formData.append('file', selectedFile);

    try {
      const response = await apiFetch('/api/upload', { method: 'POST', body: formData });
      const data = await response.json();
      if (data.success) {
        setUploadComplete(true);
        setCurrentResults(data.results);
        setTimeout(() => setShowResults(true), 500);
        toast.success('File uploaded and compared');
        loadHistory();
      } else {
        toast.error(data.error || 'Upload failed');
      }
    } catch (err) {
      toast.error(err.message || 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  const handleViewHistory = async (uploadId) => {
    try {
      const res = await apiFetch(`/api/uploads/${uploadId}`);
      const data = await res.json();
      if (data.results) {
        setCurrentResults(data.results);
        setShowResults(true);
      }
    } catch (err) {
      toast.error(err.message || 'Failed to load results');
    }
  };

  const handleExport = (uploadId) => {
    window.open(`/api/uploads/${uploadId}/export`, '_blank');
  };

  const handleDelete = async (uploadId) => {
    const target = uploadHistory.find((u) => u.id === uploadId);
    const ok = await confirm({
      title: `Delete upload "${target?.filename || `#${uploadId}`}"?`,
      message: 'The uploaded file and its comparison results will be removed.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const response = await apiFetch(`/api/uploads/${uploadId}`, { method: 'DELETE' });
      const data = await response.json();
      if (data.success) {
        setUploadHistory(prev => prev.filter(u => u.id !== uploadId));
        toast.success('Upload deleted');
      } else {
        toast.error(data.error || 'Delete failed');
      }
    } catch (err) {
      toast.error(err.message || 'Delete failed');
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">
            Upload & Compare
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            Upload monthly stock files and compare against database records
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="info">PDF & Excel</Badge>
          <Badge variant="success">Auto-detect Format</Badge>
        </div>
      </div>

      {/* Upload Section */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Upload Area */}
        <div className="lg:col-span-2">
          <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
            <div className="flex items-center gap-2 mb-4">
              <UploadCloud className="h-5 w-5 text-blue-600 dark:text-blue-400" />
              <h3 className="text-base font-semibold text-slate-900 dark:text-white">
                Upload Stock File
              </h3>
            </div>
            
            <UploadArea 
              onFileSelect={handleFileSelect}
              disabled={isUploading}
            />

            {/* Upload Action */}
            <div className="mt-4 flex items-center justify-between">
              <div className="text-xs text-slate-500 dark:text-slate-400">
                {selectedFile ? (
                  <span className="flex items-center gap-1.5">
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                    File ready for upload
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5">
                    <AlertCircle className="h-4 w-4 text-slate-400" />
                    Select a file to begin comparison
                  </span>
                )}
              </div>
              
              <Button
                onClick={handleUpload}
                disabled={!selectedFile || isUploading}
                loading={isUploading}
                icon={isUploading ? Loader2 : ArrowRight}
              >
                {isUploading ? 'Processing...' : 'Upload & Compare'}
              </Button>
            </div>
          </div>
        </div>

        {/* Upload Tips */}
        <div className="rounded-xl border border-slate-200/80 dark:border-slate-800 bg-white dark:bg-slate-900 p-5 shadow-xs">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white mb-3">
            Supported Formats
          </h3>
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-rose-50 dark:bg-rose-950/40">
                <FileText className="h-4 w-4 text-rose-500" />
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-800 dark:text-white">PDF Files</p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  Digital or scanned balance sheets
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-50 dark:bg-emerald-950/40">
                <FileText className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-800 dark:text-white">Excel Files</p>
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                  .xlsx or .xls with unit conversion
                </p>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 dark:border-slate-800">
            <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">
              What happens after upload:
            </h4>
            <ol className="text-[11px] text-slate-500 dark:text-slate-400 space-y-1.5 list-decimal list-inside">
              <li>File is parsed (PDF/Excel)</li>
              <li>Chemicals matched against DB</li>
              <li>Mismatches identified</li>
              <li>Comparison report generated</li>
              <li>Results ready for review</li>
            </ol>
          </div>
        </div>
      </div>

      {/* Comparison Results */}
      {showResults && currentResults && (
        <div className="space-y-6">
          <MismatchSummary stats={currentResults.stats} />
          <ComparisonResults results={currentResults} />
        </div>
      )}

      {/* Upload History */}
      <UploadHistory
        uploads={uploadHistory}
        onViewResults={handleViewHistory}
        onReview={handleViewHistory}
        onExport={handleExport}
        onDelete={isAdmin ? handleDelete : undefined}
      />
    </div>
  );
}
