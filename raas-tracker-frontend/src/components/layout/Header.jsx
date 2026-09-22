import { User, LogOut } from 'lucide-react';
import Badge from '../ui/Badge';
import NotificationBell from '../notifications/NotificationBell';
import { useAuth } from '../../context/AuthContext';

export default function Header() {
  const { user, logout } = useAuth();

  return (
    <header className="h-16 bg-white border-b border-gray-200 flex items-center justify-between px-6">
      {/* Left: Title */}
      <div className="flex items-center gap-4">
        <h1 className="text-xl font-semibold text-gray-800">
          RAAS Tracker
        </h1>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-4">
        {/* Notifications */}
        <NotificationBell />

        {/* User */}
        <div className="flex items-center gap-2 pl-4 border-l border-gray-200">
          <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center">
            <User size={16} className="text-white" />
          </div>
          <span className="text-sm font-medium text-gray-700 hidden sm:block">
            {user?.username || '…'}
          </span>
          {user?.role === 'admin' && (
            <Badge variant="new" size="xs">admin</Badge>
          )}
          <button
            onClick={logout}
            className="p-2 rounded-lg hover:bg-gray-100 text-gray-500 hover:text-rose-600 transition-colors cursor-pointer"
            title="Log out"
          >
            <LogOut size={18} />
          </button>
        </div>
      </div>
    </header>
  );
}
