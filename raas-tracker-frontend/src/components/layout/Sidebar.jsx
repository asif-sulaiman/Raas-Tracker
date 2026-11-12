import { Link, NavLink } from 'react-router-dom';
import {
  Home,
  Upload,
  FlaskConical,
  BookOpen,
  FileText,
  ClipboardList,
  DollarSign,
  Users,
  Building2,
  Menu,
  X
} from 'lucide-react';
import { useRef } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useDialogLayer } from '../../hooks/useDialogLayer';
import BrandLogo from '../BrandLogo';
import { ICON_HIT_AREA } from '../ui/Button';

const navItems = [
  { path: '/', label: 'Home', icon: Home },
  { path: '/upload', label: 'Upload & Compare', icon: Upload },
  { path: '/chemicals', label: 'Chemical Stock', icon: FlaskConical },
  { path: '/recipes', label: 'Recipes', icon: BookOpen },
  { path: '/reports', label: 'Reports', icon: FileText },
  { path: '/audit-logs', label: 'Audit Logs', icon: ClipboardList },
  { path: '/sales', label: 'Sales Pipeline', icon: DollarSign },
  { path: '/users', label: 'Users & Keys', icon: Users, adminOnly: true },
  { path: '/companies', label: 'Companies', icon: Building2, adminOnly: true },
];

export default function Sidebar({
  open = false,
  collapsed = false,
  onClose = () => {},
  onToggleCollapse = () => {},
}) {
  const { user } = useAuth();
  const panelRef = useRef(null);
  const closeButtonRef = useRef(null);
  const visibleItems = navItems.filter((item) => !item.adminOnly || user?.role === 'admin');

  // The drawer is a dialog layer: it shares Escape, the Tab trap, focus
  // restore and the body scroll lock with Modal so only the topmost layer
  // reacts when a modal opens over it.
  useDialogLayer({
    isOpen: open,
    onClose,
    containerRef: panelRef,
    initialFocusRef: closeButtonRef,
  });

  const renderNav = (railCollapsed, onNavigate) => visibleItems.map((item) => (
    <NavLink
      key={item.path}
      to={item.path}
      onClick={onNavigate}
      className={({ isActive }) => `
        flex items-center gap-3 px-3 py-2.5 mb-1 rounded-lg
        transition-colors duration-200
        ${isActive
          ? 'bg-primary text-white'
          : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}
        ${railCollapsed ? 'justify-center' : ''}
      `}
      title={railCollapsed ? item.label : ''}
    >
      <item.icon size={20} />
      {!railCollapsed && <span>{item.label}</span>}
    </NavLink>
  ));

  return (
    <>
      {/* Desktop rail: always mounted, hidden below lg. */}
      <aside
        id="app-sidebar"
        className={`
          hidden lg:flex flex-col shrink-0 h-full z-30
          bg-white dark:bg-gray-800
          border-r border-gray-200 dark:border-gray-700
          transition-all duration-300
          ${collapsed ? 'w-16' : 'w-64'}
        `}
      >
        <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200 dark:border-gray-700">
          <Link
            to="/"
            className="flex min-w-0 items-center gap-2 rounded-lg transition-opacity hover:opacity-80"
            title="Go to Home"
          >
            <BrandLogo size="h-8 w-8" />
            {!collapsed && (
              <span className="text-lg font-semibold text-gray-800 dark:text-white truncate">
                RAAS Tracker
              </span>
            )}
          </Link>
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            aria-expanded={!collapsed}
            className={`p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400 ${ICON_HIT_AREA}`}
          >
            {collapsed ? <Menu size={20} /> : <X size={20} />}
          </button>
        </div>

        <nav className="p-2">
          {renderNav(collapsed)}
        </nav>
      </aside>

      {/* Mobile drawer: rendered only while open, so it cannot cover the page
          on load and is absent from the accessibility tree once dismissed. */}
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
          className="fixed inset-0 z-50 lg:hidden"
        >
          <button
            type="button"
            aria-label="Close navigation"
            onClick={onClose}
            className="absolute inset-0 h-full w-full bg-black/50"
          />
          <div
            ref={panelRef}
            tabIndex={-1}
            className="absolute inset-y-0 left-0 flex w-64 max-w-[80vw] flex-col bg-white dark:bg-gray-800 shadow-xl"
          >
            <div className="flex h-16 items-center justify-between px-4 border-b border-gray-200 dark:border-gray-700">
              <Link
                to="/"
                onClick={onClose}
                className="flex min-w-0 items-center gap-2 rounded-lg transition-opacity hover:opacity-80"
                title="Go to Home"
              >
                <BrandLogo size="h-8 w-8" />
                <span className="text-lg font-semibold text-gray-800 dark:text-white truncate">
                  RAAS Tracker
                </span>
              </Link>
              <button
                ref={closeButtonRef}
                type="button"
                aria-label="Close menu"
                onClick={onClose}
                className={`p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400 ${ICON_HIT_AREA}`}
              >
                <X size={20} />
              </button>
            </div>

            <nav className="p-2 overflow-y-auto">
              {renderNav(false, onClose)}
            </nav>
          </div>
        </div>
      )}
    </>
  );
}
