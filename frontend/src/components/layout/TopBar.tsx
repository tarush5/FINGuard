/** Top bar: global search trigger, live system status, notifications, profile. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { Bell, Check, LogOut, Menu, Search, ShieldCheck, User } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Badge, Button, StatusDot } from '@/components/ui';
import { api } from '@/lib/api';
import { relativeTime, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';

interface HealthPayload {
  status: string;
  components: { component: string; status: string; detail: string }[];
  event_bus: { driver: string; running: boolean };
  throughput: { transactions_last_hour: number; decisions_total: number };
}

interface NotificationItem {
  id: string;
  severity: string;
  category: string;
  title: string;
  body: string;
  link?: string | null;
  read: boolean;
  created_at: string;
}

export function TopBar({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const setCommandOpen = useUi((state) => state.setCommandOpen);
  const user = useAuth((state) => state.user);
  const logout = useAuth((state) => state.logout);
  const canMonitor = useAuth((state) => state.can('monitoring:read'));
  const navigate = useNavigate();
  const [profileOpen, setProfileOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const queryClient = useQueryClient();

  const health = useQuery({
    queryKey: ['system-health-compact'],
    queryFn: () => api.get<HealthPayload>('/monitoring/system'),
    refetchInterval: 30_000,
    enabled: canMonitor,
  });

  const notifications = useQuery({
    queryKey: ['notifications', 'topbar'],
    queryFn: () => api.get<{ items: NotificationItem[]; unread_count: number }>('/notifications', { page_size: 12 }),
    refetchInterval: 25_000,
  });

  const unread = notifications.data?.unread_count ?? 0;
  const status = health.data?.status ?? 'HEALTHY';

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-base/85 px-4 backdrop-blur-xl">
      <button
        type="button"
        onClick={onOpenMobileNav}
        className="rounded p-1.5 text-muted transition-colors hover:text-ink lg:hidden"
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" />
      </button>

      <button
        type="button"
        onClick={() => setCommandOpen(true)}
        className="group flex h-9 flex-1 max-w-md items-center gap-2.5 rounded-lg border border-line bg-surface px-3 text-left text-sm text-faint transition-colors hover:border-line-strong hover:bg-panel"
      >
        <Search className="h-3.5 w-3.5" />
        <span className="flex-1 truncate">Search or ask FINGuard…</span>
        <kbd className="hidden rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-[10px] text-muted sm:block">
          Ctrl K
        </kbd>
      </button>

      <div className="ml-auto flex items-center gap-1.5">
        {canMonitor ? (
          <Link
            to="/app/system"
            className="hidden items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-2xs transition-colors hover:border-line-strong md:flex"
            title="System health"
          >
            <StatusDot status={status} />
            <span className="text-muted">
              {health.data?.event_bus?.driver === 'kafka' ? 'Kafka' : 'Event bus'}
            </span>
            <span className={cn('rounded border px-1.5 py-0.5 uppercase tracking-wide', statusBgClass(status))}>
              {status}
            </span>
          </Link>
        ) : null}

        <div className="relative">
          <Button
            size="icon"
            variant="ghost"
            onClick={() => {
              setNotificationsOpen((value) => !value);
              setProfileOpen(false);
            }}
            aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
            aria-expanded={notificationsOpen}
          >
            <Bell className="h-4 w-4" />
            {unread > 0 ? (
              <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-critical px-1 text-[9px] font-semibold text-void">
                {unread > 99 ? '99+' : unread}
              </span>
            ) : null}
          </Button>

          <AnimatePresence>
            {notificationsOpen ? (
              <NotificationPanel
                items={notifications.data?.items ?? []}
                onClose={() => setNotificationsOpen(false)}
                onMarkAll={async () => {
                  await api.post('/notifications/read-all');
                  queryClient.invalidateQueries({ queryKey: ['notifications'] });
                }}
                onOpenItem={(item) => {
                  setNotificationsOpen(false);
                  if (item.link) navigate(`/app${item.link}`);
                }}
              />
            ) : null}
          </AnimatePresence>
        </div>

        <div className="relative">
          <button
            type="button"
            onClick={() => {
              setProfileOpen((value) => !value);
              setNotificationsOpen(false);
            }}
            className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2 py-1.5 transition-colors hover:border-line-strong"
            aria-expanded={profileOpen}
            aria-label="Account menu"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-info/15 text-2xs font-semibold text-info">
              {user?.full_name?.charAt(0) ?? '?'}
            </span>
            <span className="hidden text-xs text-ink sm:block">{user?.full_name?.split(' ')[0]}</span>
          </button>

          <AnimatePresence>
            {profileOpen ? (
              <motion.div
                initial={{ opacity: 0, y: -6, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -6, scale: 0.98 }}
                className="panel absolute right-0 top-full z-50 mt-2 w-64 overflow-hidden p-0"
              >
                <div className="border-b border-line px-4 py-3">
                  <p className="text-sm font-medium text-ink">{user?.full_name}</p>
                  <p className="truncate text-2xs text-muted">{user?.email}</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {user?.roles.map((role) => (
                      <Badge key={role} className="border-info/25 bg-info/10 text-info">
                        {role.replace(/_/g, ' ')}
                      </Badge>
                    ))}
                  </div>
                  {user?.can_view_pii ? (
                    <p className="mt-2 flex items-center gap-1.5 text-2xs text-positive">
                      <ShieldCheck className="h-3 w-3" /> PII visible for this role
                    </p>
                  ) : (
                    <p className="mt-2 text-2xs text-faint">PII is masked for your role.</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setProfileOpen(false);
                    navigate('/app/governance/users');
                  }}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm text-muted transition-colors hover:bg-raised hover:text-ink"
                >
                  <User className="h-3.5 w-3.5" /> Team & roles
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    await logout();
                    navigate('/login');
                  }}
                  className="flex w-full items-center gap-2 border-t border-line px-4 py-2.5 text-left text-sm text-muted transition-colors hover:bg-raised hover:text-critical"
                >
                  <LogOut className="h-3.5 w-3.5" /> Sign out
                </button>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    </header>
  );
}

function NotificationPanel({
  items,
  onClose,
  onMarkAll,
  onOpenItem,
}: {
  items: NotificationItem[];
  onClose: () => void;
  onMarkAll: () => void;
  onOpenItem: (item: NotificationItem) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: -6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -6, scale: 0.98 }}
      className="panel absolute right-0 top-full z-50 mt-2 w-[360px] overflow-hidden p-0"
    >
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <p className="text-sm font-medium text-ink">Notifications</p>
        <button type="button" onClick={onMarkAll} className="flex items-center gap-1 text-2xs text-muted hover:text-ink">
          <Check className="h-3 w-3" /> Mark all read
        </button>
      </div>
      <div className="max-h-[420px] overflow-y-auto">
        {items.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-faint">Nothing to review right now.</p>
        ) : (
          items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onOpenItem(item)}
              className={cn(
                'flex w-full gap-3 border-b border-line/60 px-4 py-3 text-left transition-colors last:border-0 hover:bg-raised/60',
                !item.read && 'bg-info/[0.04]',
              )}
            >
              <span
                className={cn(
                  'mt-1 h-1.5 w-1.5 shrink-0 rounded-full',
                  item.severity === 'CRITICAL'
                    ? 'bg-critical'
                    : item.severity === 'WARNING'
                      ? 'bg-warning'
                      : item.severity === 'HIGH'
                        ? 'bg-high'
                        : 'bg-info',
                )}
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-medium text-ink">{item.title}</span>
                  <span className="shrink-0 text-[10px] text-faint">{relativeTime(item.created_at)}</span>
                </span>
                <span className="mt-0.5 line-clamp-2 block text-2xs text-muted">{item.body}</span>
              </span>
            </button>
          ))
        )}
      </div>
    </motion.div>
  );
}
