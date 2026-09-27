import { useState, useEffect, useCallback } from 'react';
import { Building2, Plus, Pencil, Trash2 } from 'lucide-react';
import Button from '../components/ui/Button';
import Modal from '../components/modals/Modal';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

const EMPTY = { name: '', code: '', country: '', address: '', contact_person: '', swift: '', lc_bank: '' };

const FIELDS = [
  ['name', 'Company name', 'text', true],
  ['code', 'Code', 'text', false],
  ['country', 'Country', 'text', false],
  ['address', 'Address', 'text', false],
  ['contact_person', 'Contact person', 'text', false],
  ['swift', 'SWIFT', 'text', false],
  ['lc_bank', 'LC bank', 'text', false],
];

export default function Companies() {
  const { user, apiFetch } = useAuth();
  const { confirm } = useConfirm();
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);

  // Fetch only — no synchronous setState here so the mount effect stays clean;
  // event-handler refreshers turn the spinner on themselves before calling this.
  const loadCompanies = useCallback(async () => {
    try {
      const res = await apiFetch('/api/companies');
      const data = await res.json();
      if (Array.isArray(data)) setCompanies(data);
      else throw new Error('Unexpected response from server');
    } catch (err) {
      setError(err.message || 'Could not load companies');
      setCompanies([]);
    } finally {
      setLoading(false);
    }
  }, [apiFetch]);

  useEffect(() => {
    (async () => {
      await loadCompanies();
    })();
  }, [loadCompanies]);

  if (user && user.role !== 'admin') {
    return <p className="text-sm text-rose-500 p-6">Admins only.</p>;
  }

  const openAdd = () => {
    setEditing(null);
    setForm(EMPTY);
    setShowModal(true);
  };

  const openEdit = (row) => {
    setEditing(row);
    setForm({
      name: row.name || '',
      code: row.code || '',
      country: row.country || '',
      address: row.address || '',
      contact_person: row.contact_person || '',
      swift: row.swift || '',
      lc_bank: row.lc_bank || '',
    });
    setShowModal(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error('Enter a company name');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await apiFetch(`/api/companies/${editing.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...form, name: form.name.trim() }),
        });
        toast.success(`Company "${form.name.trim()}" updated`);
      } else {
        await apiFetch('/api/companies', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...form, name: form.name.trim() }),
        });
        toast.success(`Company "${form.name.trim()}" added`);
      }
      setShowModal(false);
      setEditing(null);
      setForm(EMPTY);
      setLoading(true);
      setError(null);
      loadCompanies();
    } catch (err) {
      toast.error(err.message || 'Could not save company');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (row) => {
    const ok = await confirm({
      title: `Delete company "${row.name}"?`,
      message: 'Blocked while sales reference it — rename instead.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/companies/${row.id}`, { method: 'DELETE' });
      toast.success(`Company "${row.name}" deleted`);
      setLoading(true);
      setError(null);
      loadCompanies();
    } catch (err) {
      toast.error(err.message || 'Could not delete company');
    }
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight flex items-center gap-2">
            <Building2 className="h-5 w-5 text-blue-600" /> Companies
          </h2>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Customer master — every PI, recipe and report links here</p>
        </div>
        <Button variant="primary" size="sm" icon={Plus} onClick={openAdd}>Add Company</Button>
      </div>
      {error && <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
        {loading ? (
          <p className="text-xs text-slate-500 dark:text-slate-400 py-6 text-center">Loading companies…</p>
        ) : companies.length === 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400 py-6 text-center">No companies yet — add the first customer above.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 uppercase tracking-wider">
                <tr>
                  <th className="px-3 py-2">Name</th>
                  <th className="px-3 py-2">Code</th>
                  <th className="px-3 py-2">Country</th>
                  <th className="px-3 py-2">Contact</th>
                  <th className="px-3 py-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {companies.map((c) => (
                  <tr key={c.id}>
                    <td className="px-3 py-2 font-medium text-slate-900 dark:text-white">{c.name}</td>
                    <td className="px-3 py-2 font-mono text-slate-500">{c.code || '—'}</td>
                    <td className="px-3 py-2 text-slate-500">{c.country || '—'}</td>
                    <td className="px-3 py-2 text-slate-500">{c.contact_person || '—'}</td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-1.5">
                        <Button variant="secondary" size="sm" icon={Pencil} onClick={() => openEdit(c)} title="Edit company" aria-label={`Edit ${c.name}`}>
                          {null}
                        </Button>
                        <Button variant="secondary" size="sm" icon={Trash2} onClick={() => handleDelete(c)} title="Delete company" aria-label={`Delete ${c.name}`} className="!text-rose-600 dark:!text-rose-400">
                          {null}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <Modal
        isOpen={showModal}
        onClose={() => { setShowModal(false); setEditing(null); }}
        title={editing ? 'Edit Company' : 'Add Company'}
        subtitle={editing ? editing.name : 'SWIFT, LC bank and code are optional'}
        footer={
          <>
            <Button variant="secondary" onClick={() => { setShowModal(false); setEditing(null); }} disabled={saving}>Cancel</Button>
            <Button variant="primary" onClick={handleSave} loading={saving} disabled={saving}>Save</Button>
          </>
        }
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {FIELDS.map(([key, label, type, required]) => (
            <div key={key} className={key === 'name' || key === 'address' ? 'sm:col-span-2' : ''}>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1.5">
                {label}{required ? ' *' : ''}
              </label>
              <input
                aria-label={label}
                type={type}
                value={form[key]}
                onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                className={inputCls}
              />
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
