/**
 * Collapsible, searchable primary navigation.
 *
 * Sections are permission-filtered: a role that cannot read a resource never
 * sees the link, so the UI never offers an action the API would refuse.
 */
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Boxes,
  Brain,
  ChevronDown,
  Database,
  FileSearch,
  FlaskConical,
  GaugeCircle,
  GitBranch,
  LayoutDashboard,
  LineChart,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  ScrollText,
  Search,
  Shield,
  ShieldAlert,
  SlidersHorizontal,
  Store,
  Users,
  Waypoints,
  Workflow,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';
import { cn } from '@/lib/utils';

interface NavItem {
  label: string;
  to: string;
  icon: ReactNode;
  permission?: string;
  badge?: string;
}

interface NavSection {
  label: string;
  icon?: ReactNode;
  items: NavItem[];
}

const SECTIONS: NavSection[] = [
  {
    label: 'Overview',
    items: [
      { label: 'Command Center', to: '/app', icon: <LayoutDashboard className="h-4 w-4" /> },
      { label: 'Transactions', to: '/app/transactions', icon: <Activity className="h-4 w-4" />, permission: 'transaction:read' },
    ],
  },
  {
    label: 'Fraud',
    icon: <ShieldAlert className="h-4 w-4" />,
    items: [
      { label: 'Alerts', to: '/app/alerts', icon: <AlertTriangle className="h-4 w-4" />, permission: 'alert:read' },
      { label: 'Cases', to: '/app/cases', icon: <FileSearch className="h-4 w-4" />, permission: 'case:read' },
      { label: 'Fraud Rings', to: '/app/rings', icon: <Network className="h-4 w-4" />, permission: 'graph:read' },
      { label: 'Rules', to: '/app/rules', icon: <SlidersHorizontal className="h-4 w-4" />, permission: 'rule:read' },
    ],
  },
  {
    label: 'Risk',
    icon: <Shield className="h-4 w-4" />,
    items: [
      { label: 'Customers', to: '/app/customers', icon: <Users className="h-4 w-4" />, permission: 'customer:read' },
      { label: 'Merchants', to: '/app/merchants', icon: <Store className="h-4 w-4" />, permission: 'merchant:read' },
      { label: 'Policy Simulator', to: '/app/simulator', icon: <GaugeCircle className="h-4 w-4" />, permission: 'risk:simulate' },
    ],
  },
  {
    label: 'Analytics',
    icon: <BarChart3 className="h-4 w-4" />,
    items: [
      { label: 'Financial', to: '/app/analytics/financial', icon: <LineChart className="h-4 w-4" />, permission: 'analytics:read' },
      { label: 'Fraud Analytics', to: '/app/analytics/fraud', icon: <Radar className="h-4 w-4" />, permission: 'analytics:read' },
      { label: 'Forecasting', to: '/app/analytics/forecasting', icon: <Waypoints className="h-4 w-4" />, permission: 'forecast:read' },
    ],
  },
  {
    label: 'ML Studio',
    icon: <Brain className="h-4 w-4" />,
    items: [
      { label: 'Models', to: '/app/ml/models', icon: <Boxes className="h-4 w-4" />, permission: 'model:read' },
      { label: 'Experiments', to: '/app/ml/experiments', icon: <FlaskConical className="h-4 w-4" />, permission: 'model:read' },
      { label: 'Monitoring', to: '/app/ml/monitoring', icon: <GaugeCircle className="h-4 w-4" />, permission: 'monitoring:read' },
    ],
  },
  {
    label: 'AI',
    items: [{ label: 'AI Investigator', to: '/app/ai', icon: <Brain className="h-4 w-4" />, permission: 'ai:query', badge: 'AI' }],
  },
  {
    label: 'Data Platform',
    icon: <Database className="h-4 w-4" />,
    items: [
      { label: 'Sources', to: '/app/data/sources', icon: <Database className="h-4 w-4" />, permission: 'data:read' },
      { label: 'Pipelines', to: '/app/data/pipelines', icon: <Workflow className="h-4 w-4" />, permission: 'data:read' },
      { label: 'Quality', to: '/app/data/quality', icon: <GaugeCircle className="h-4 w-4" />, permission: 'data:read' },
      { label: 'Lineage', to: '/app/data/lineage', icon: <GitBranch className="h-4 w-4" />, permission: 'data:read' },
    ],
  },
  {
    label: 'Governance',
    icon: <ScrollText className="h-4 w-4" />,
    items: [
      { label: 'Users', to: '/app/governance/users', icon: <Users className="h-4 w-4" />, permission: 'governance:read' },
      { label: 'Policies', to: '/app/governance/policies', icon: <Shield className="h-4 w-4" />, permission: 'governance:read' },
      { label: 'Audit Trail', to: '/app/governance/audit', icon: <ScrollText className="h-4 w-4" />, permission: 'audit:read' },
    ],
  },
  {
    label: 'Operations',
    items: [{ label: 'System Health', to: '/app/system', icon: <Activity className="h-4 w-4" />, permission: 'monitoring:read' }],
  },
];

export function Sidebar({ mobileOpen, onNavigate }: { mobileOpen?: boolean; onNavigate?: () => void }) {
  const collapsed = useUi((state) => state.sidebarCollapsed);
  const toggle = useUi((state) => state.toggleSidebar);
  const can = useAuth((state) => state.can);
  const [filter, setFilter] = useState('');
  const location = useLocation();

  const sections = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return SECTIONS.map((section) => ({
      ...section,
      items: section.items.filter(
        (item) =>
          (!item.permission || can(item.permission)) &&
          (!query || item.label.toLowerCase().includes(query) || section.label.toLowerCase().includes(query)),
      ),
    })).filter((section) => section.items.length > 0);
  }, [filter, can]);

  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-line bg-base transition-[width] duration-200 ease-swift',
        collapsed && !mobileOpen ? 'w-[68px]' : 'w-[248px]',
      )}
      aria-label="Primary navigation"
    >
      <div className="flex h-14 items-center gap-2.5 border-b border-line px-4">
        <BrandMark />
        {!collapsed || mobileOpen ? (
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold tracking-tight text-ink">FINGuard</p>
            <p className="truncate text-[10px] uppercase tracking-[0.16em] text-faint">Risk Intelligence</p>
          </div>
        ) : null}
        <button
          type="button"
          onClick={toggle}
          className="hidden rounded p-1 text-faint transition-colors hover:text-ink lg:block"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </button>
      </div>

      {!collapsed || mobileOpen ? (
        <div className="border-b border-line px-3 py-2.5">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter navigation"
              aria-label="Filter navigation"
              className="h-8 w-full rounded-md border border-line bg-surface pl-8 pr-2 text-xs text-ink placeholder:text-faint focus:border-info/40"
            />
          </div>
        </div>
      ) : null}

      <nav className="scrollbar-none flex-1 overflow-y-auto px-2 py-3">
        {sections.map((section) => (
          <NavSectionBlock
            key={section.label}
            section={section}
            collapsed={collapsed && !mobileOpen}
            currentPath={location.pathname}
            onNavigate={onNavigate}
          />
        ))}
      </nav>
    </aside>
  );
}

function NavSectionBlock({
  section,
  collapsed,
  currentPath,
  onNavigate,
}: {
  section: NavSection;
  collapsed: boolean;
  currentPath: string;
  onNavigate?: () => void;
}) {
  const containsActive = section.items.some((item) => currentPath.startsWith(item.to));
  const [open, setOpen] = useState(true);

  return (
    <div className="mb-1">
      {!collapsed ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center justify-between px-2 py-1.5 text-2xs font-medium uppercase tracking-[0.14em] text-faint transition-colors hover:text-muted"
          aria-expanded={open}
        >
          <span className="flex items-center gap-2">
            {section.icon}
            {section.label}
          </span>
          <ChevronDown className={cn('h-3 w-3 transition-transform', !open && '-rotate-90')} />
        </button>
      ) : (
        <div className="my-2 border-t border-line/60" />
      )}

      <AnimatePresence initial={false}>
        {open || collapsed ? (
          <motion.ul
            initial={collapsed ? false : { height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.16 }}
            className="space-y-0.5 overflow-hidden"
          >
            {section.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/app'}
                  onClick={onNavigate}
                  title={collapsed ? item.label : undefined}
                  className={({ isActive }) =>
                    cn(
                      'group relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors',
                      isActive || (containsActive && currentPath === item.to)
                        ? 'bg-info/10 text-ink'
                        : 'text-muted hover:bg-raised/60 hover:text-ink',
                      collapsed && 'justify-center px-0',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive ? (
                        <motion.span
                          layoutId="nav-active"
                          className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-info"
                        />
                      ) : null}
                      <span className={cn('shrink-0', isActive ? 'text-info' : 'text-faint group-hover:text-muted')}>
                        {item.icon}
                      </span>
                      {!collapsed ? (
                        <>
                          <span className="truncate">{item.label}</span>
                          {item.badge ? (
                            <span className="ml-auto rounded bg-ai/15 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-ai">
                              {item.badge}
                            </span>
                          ) : null}
                        </>
                      ) : null}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </motion.ul>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn('h-7 w-7 shrink-0', className)} aria-hidden>
      <defs>
        <linearGradient id="brand-gradient" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#38BDF8" />
          <stop offset="100%" stopColor="#34D399" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="#0C121B" stroke="#1D2836" />
      <path d="M16 5.5l8.5 3.2v6.6c0 5.1-3.5 9.6-8.5 10.9-5-1.3-8.5-5.8-8.5-10.9V8.7L16 5.5z" fill="none" stroke="url(#brand-gradient)" strokeWidth="1.6" />
      <path d="M11.6 16.2l3.1 3.1 5.9-6.2" fill="none" stroke="#34D399" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
