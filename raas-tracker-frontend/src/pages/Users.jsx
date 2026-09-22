import { useState, useEffect } from 'react';
import { Users as UsersIcon, Plus, Trash2, KeyRound, Ban, Copy, Check } from 'lucide-react';
import Button from '../components/ui/Button';
import Badge from '../components/ui/Badge';
import Modal from '../components/modals/Modal';
import { useAuth } from '../context/AuthContext';
import { useConfirm } from '../context/ConfirmContext';
import { toast } from 'sonner';
import { formatDateTime } from '../utils/format';

const inputCls =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-2.5 py-1.5 text-sm text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500';

export default function Users() {
  const { user, apiFetch } = useAuth();
  const { confirm } = useConfirm();
  const [users, setUsers] = useState([]);
  const [keys, setKeys] = useState([]);
  const [newName, setNewName] = useState('');
  const [newPass, setNewPass] = useState('');
  const [newRole, setNewRole] = useState('user');
  const [keyName, setKeyName] = useState('');
  const [keyExpiry, setKeyExpiry] = useState('');
  const [keyIps, setKeyIps] = useState('');
  const [freshKey, setFreshKey] = useState(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(null);

  const fetchAll = async () => {
    try {
      const [uRes, kRes] = await Promise.all([
        apiFetch('/api/users'),
        apiFetch('/api/keys'),
      ]);
      const [u, k] = await Promise.all([uRes.json(), kRes.json()]);
      if (Array.isArray(u)) setUsers(u);
      if (Array.isArray(k)) setKeys(k);
    } catch {
      // Lists stay as-is on failure; mutations surface their own errors.
    }
  };

  useEffect(() => { fetchAll(); }, []);

  if (user && user.role !== 'admin') {
    return <p className="text-sm text-rose-500 p-6">Admins only.</p>;
  }

  const handleCreateUser = async () => {
    setError(null);
    try {
      await apiFetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: newName.trim(), password: newPass, role: newRole }),
      });
      setNewName('');
      setNewPass('');
      fetchAll();
    } catch (err) {
      setError(err.message || 'Could not create user');
    }
  };

  const handleDeleteUser = async (u) => {
    const ok = await confirm({
      title: `Delete user "${u.username}"?`,
      message: 'Their sessions end immediately.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/users/${u.id}`, { method: 'DELETE' });
      toast.success(`User "${u.username}" deleted`);
      fetchAll();
    } catch (err) {
      toast.error(err.message || 'Could not delete user');
    }
  };

  const handleRevokeSessions = async (u) => {
    const ok = await confirm({
      title: `Log out "${u.username}" everywhere?`,
      message: 'All sessions end immediately. API keys stay active.',
      confirmLabel: 'Log out',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/users/${u.id}/revoke`, { method: 'POST' });
      toast.success(`"${u.username}" logged out everywhere`);
      fetchAll();
    } catch (err) {
      toast.error(err.message || 'Could not revoke sessions');
    }
  };

  const handleCreateKey = async () => {
    setError(null);
    try {
      const res = await apiFetch('/api/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: keyName.trim(), expires_at: keyExpiry || null, allowed_ips: keyIps.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      setFreshKey(data.key);
      setCopied(false);
      setKeyName('');
      setKeyExpiry('');
      setKeyIps('');
      fetchAll();
    } catch (err) {
      setError(err.message || 'Could not create key');
    }
  };

  const handleRevokeKey = async (k) => {
    const ok = await confirm({
      title: `Revoke API key "${k.name}"?`,
      message: 'Scripts using it stop working immediately.',
      confirmLabel: 'Revoke',
      danger: true,
    });
    if (!ok) return;
    try {
      await apiFetch(`/api/keys/${k.id}`, { method: 'DELETE' });
      toast.success(`API key "${k.name}" revoked`);
      fetchAll();
    } catch (err) {
      toast.error(err.message || 'Could not revoke key');
    }
  };

  const copyKey = async () => {
    try {
      await navigator.clipboard.writeText(freshKey);
      setCopied(true);
    } catch { /* ignore */ }
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <h2 className="text-xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
        <UsersIcon className="h-5 w-5 text-blue-600" /> Users & API Keys
      </h2>
      {error && <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>}

      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-white mb-3">Users ({users.length})</h3>
        <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700 mb-4">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 uppercase tracking-wider">
              <tr>
                <th className="px-3 py-2">Username</th>
                <th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">Created</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="px-3 py-2 font-medium text-slate-900 dark:text-white">{u.username}</td>
                  <td className="px-3 py-2">
                    <Badge variant={u.role === 'admin' ? 'new' : 'default'}>{u.role}</Badge>
                  </td>
                  <td className="px-3 py-2 text-slate-500">{formatDateTime(u.created_at)}</td>
                  <td className="px-3 py-2">
                    <div className="flex justify-end gap-1.5">
                      <Button variant="secondary" size="sm" icon={Ban} onClick={() => handleRevokeSessions(u)} title="Log out everywhere">
                        {null}
                      </Button>
                      <Button variant="secondary" size="sm" icon={Trash2} onClick={() => handleDeleteUser(u)} title="Delete user" className="!text-rose-600 dark:!text-rose-400">
                        {null}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-2.5">
          <input value={newName} onChange={(e) => setNewName(e.target.value)} className={inputCls} placeholder="Username" />
          <input type="password" value={newPass} onChange={(e) => setNewPass(e.target.value)} className={inputCls} placeholder="Password (min 8)" />
          <select value={newRole} onChange={(e) => setNewRole(e.target.value)} className={inputCls}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
          <Button variant="primary" size="sm" icon={Plus} onClick={handleCreateUser}>Add User</Button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-white mb-1">Script API Keys ({keys.length})</h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
          Long-lived keys for automation. Revoking a key never touches user logins.
        </p>
        <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700 mb-4">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 dark:bg-slate-800/60 text-slate-500 uppercase tracking-wider">
              <tr>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Fingerprint</th>
                <th className="px-3 py-2">Expires</th>
                <th className="px-3 py-2">Allowed IPs</th>
                <th className="px-3 py-2">Last used</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {keys.length === 0 ? (
                <tr><td colSpan={7} className="px-3 py-4 text-center text-slate-400">No API keys yet</td></tr>
              ) : keys.map((k) => (
                <tr key={k.id}>
                  <td className="px-3 py-2 font-medium text-slate-900 dark:text-white">{k.name}</td>
                  <td className="px-3 py-2 font-mono text-slate-500">{k.fingerprint}…</td>
                  <td className="px-3 py-2 text-slate-500">{k.expires_at || 'never'}</td>
                  <td className="px-3 py-2 text-slate-500">{k.allowed_ips || 'any'}</td>
                  <td className="px-3 py-2 text-slate-500">{k.last_used_at ? `${formatDateTime(k.last_used_at)} (${k.last_used_ip || '?'})` : '—'}</td>
                  <td className="px-3 py-2">
                    <Badge variant={k.revoked ? 'error' : 'success'}>{k.revoked ? 'revoked' : 'active'}</Badge>
                  </td>
                  <td className="px-3 py-2 text-right">
                    {!k.revoked && (
                      <Button variant="secondary" size="sm" icon={Trash2} onClick={() => handleRevokeKey(k)} title="Revoke key" className="!text-rose-600 dark:!text-rose-400">
                        {null}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-2.5">
          <input value={keyName} onChange={(e) => setKeyName(e.target.value)} className={inputCls} placeholder="Key name (e.g. lab-uploader)" />
          <input type="date" value={keyExpiry} onChange={(e) => setKeyExpiry(e.target.value)} className={inputCls} title="Expiry date (optional)" />
          <input value={keyIps} onChange={(e) => setKeyIps(e.target.value)} className={inputCls} placeholder="Allowed IPs, comma-separated" />
          <Button variant="primary" size="sm" icon={KeyRound} onClick={handleCreateKey}>Create Key</Button>
        </div>
      </div>

      <Modal isOpen={!!freshKey} onClose={() => setFreshKey(null)} title="API Key Created" subtitle="Copy it now — it will never be shown again">
        <div className="flex items-center gap-2 rounded-lg bg-slate-100 dark:bg-slate-800 px-3 py-2.5 font-mono text-xs break-all">
          <span className="flex-1">{freshKey}</span>
          <button onClick={copyKey} className="p-1.5 rounded-lg hover:bg-slate-200 dark:hover:bg-slate-700 cursor-pointer" title="Copy">
            {copied ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4 text-slate-500" />}
          </button>
        </div>
      </Modal>
    </div>
  );
}
