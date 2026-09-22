import { NavLink } from 'react-router-dom';
import {
  Home,
  Upload,
  FlaskConical,
  BookOpen,
  FileText,
  ClipboardList,
  DollarSign,
  Users,
  Menu,
  X
} from 'lucide-react';
import { useState } from 'react';
import { useAuth } from '../../context/AuthContext';

const navItems = [
  { path: '/', label: 'Home', icon: Home },
  { path: '/upload', label: 'Upload & Compare', icon: Upload },
  { path: '/chemicals', label: 'Chemical Stock', icon: FlaskConical },
  { path: '/recipes', label: 'Recipes', icon: BookOpen },
  { path: '/reports', label: 'Reports', icon: FileText },
  { path: '/audit-logs', label: 'Audit Logs', icon: ClipboardList },
  { path: '/sales', label: 'Sales Pipeline', icon: DollarSign },
  { path: '/users', label: 'Users & Keys', icon: Users, adminOnly: true },
];

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const { user } = useAuth();
  const visibleItems = navItems.filter((item) => !item.adminOnly || user?.role === 'admin');

  return (
    <>
      {/* Mobile overlay */}
      {!collapsed && (
        <div 
          className="fixed inset-0 bg-black/50 z-40 lg:hidden"
          onClick={() => setCollapsed(true)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed top-0 left-0 h-full z-50
        bg-white dark:bg-gray-800 
        border-r border-gray-200 dark:border-gray-700
        transition-all duration-300
        ${collapsed ? 'w-16' : 'w-64'}
        lg:relative lg:translate-x-0
        ${collapsed ? '-translate-x-full lg:translate-x-0' : 'translate-x-0'}
      `}>
        {/* Header */}
        <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200 dark:border-gray-700">
          {!collapsed && (
            <span className="text-lg font-semibold text-gray-800 dark:text-white">
              RAAS Tracker
            </span>
          )}
          <button 
            onClick={() => setCollapsed(!collapsed)}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400"
          >
            {collapsed ? <Menu size={20} /> : <X size={20} />}
          </button>
        </div>

        {/* Navigation */}
        <nav className="p-2">
          {visibleItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `
                flex items-center gap-3 px-3 py-2.5 mb-1 rounded-lg
                transition-colors duration-200
                ${isActive 
                  ? 'bg-primary text-white' 
                  : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'}
                ${collapsed ? 'justify-center' : ''}
              `}
              title={collapsed ? item.label : ''}
            >
              <item.icon size={20} />
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>
      </aside>
    </>
  );
}
