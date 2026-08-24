/** Authenticated application shell: sidebar, top bar, command palette, outlet. */
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { CommandPalette } from '@/components/layout/CommandPalette';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { useAuth } from '@/store/auth';

export function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const location = useLocation();
  const mode = useAuth((state) => state.user?.platform_mode);

  useEffect(() => setMobileNavOpen(false), [location.pathname]);

  return (
    <div className="flex h-screen overflow-hidden bg-void">
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      <AnimatePresence>
        {mobileNavOpen ? (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-void/80 backdrop-blur-sm lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setMobileNavOpen(false)}
            />
            <motion.div
              className="fixed inset-y-0 left-0 z-50 lg:hidden"
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ type: 'spring', damping: 30, stiffness: 320 }}
            >
              <Sidebar mobileOpen onNavigate={() => setMobileNavOpen(false)} />
            </motion.div>
          </>
        ) : null}
      </AnimatePresence>

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenMobileNav={() => setMobileNavOpen(true)} />

        {mode === 'demo' ? (
          <div className="flex items-center justify-center gap-2 border-b border-warning/20 bg-warning/[0.06] px-4 py-1.5 text-2xs text-warning">
            <span className="h-1.5 w-1.5 rounded-full bg-warning" />
            Demo mode — every customer, merchant and transaction shown here is synthetic.
          </div>
        ) : null}

        <main className="grid-bg flex-1 overflow-y-auto" id="main-content">
          <motion.div
            key={location.pathname}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8"
          >
            <Outlet />
          </motion.div>
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}

/** Page header used by every screen for a consistent rhythm. */
export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  breadcrumb?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {breadcrumb ? <div className="mb-1.5 text-2xs text-faint">{breadcrumb}</div> : null}
        <h1 className="text-balance text-xl font-semibold tracking-tight text-ink sm:text-2xl">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
