import { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';

export default function Layout() {
  // Two independent pieces of state: the desktop icon rail must never drive
  // the mobile drawer (the old single `collapsed` boolean conflated them, so
  // the drawer opened expanded over the page on load and its only toggle
  // slid off-screen with it).
  const [desktopCollapsed, setDesktopCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const [currentPath, setCurrentPath] = useState(location.pathname);

  // Any route change dismisses the drawer. Adjusted during render (React's
  // documented "store information from previous renders" pattern) instead of
  // an effect: an effect would render the stale drawer once more and trip
  // react(set-state-in-effect).
  if (currentPath !== location.pathname) {
    setCurrentPath(location.pathname);
    setMobileOpen(false);
  }

  return (
    <div className="flex h-dvh bg-gray-50 dark:bg-gray-900">
      <Sidebar
        open={mobileOpen}
        collapsed={desktopCollapsed}
        onClose={() => setMobileOpen(false)}
        onToggleCollapse={() => setDesktopCollapsed((value) => !value)}
      />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header
          navigationOpen={mobileOpen}
          onOpenNavigation={() => setMobileOpen(true)}
        />
        <main className="flex-1 overflow-auto p-6 pb-[calc(1.5rem+env(safe-area-inset-bottom))]">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
