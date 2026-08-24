import { useQuery } from '@tanstack/react-query';
import { ShieldCheck, Users as UsersIcon } from 'lucide-react';
import { useState } from 'react';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, ErrorState, Panel, PanelHeader, Skeleton, Tab, TabList, TabPanel, Tabs } from '@/components/ui';
import { api } from '@/lib/api';
import { relativeTime } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function Users() {
  const [tab, setTab] = useState('users');
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get<any>('/users', { page_size: 50 }) });
  const roles = useQuery({ queryKey: ['governance-roles'], queryFn: () => api.get<any>('/governance/roles') });

  if (users.isError) return <ErrorState error={users.error} onRetry={() => users.refetch()} />;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Team and access"
        description="Who can see and do what. Routes declare the permission they require, never the role, so this matrix is the single source of truth."
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabList>
          <Tab value="users" count={users.data?.items?.length}>
            Users
          </Tab>
          <Tab value="roles" count={roles.data?.roles?.length}>
            Roles and permissions
          </Tab>
        </TabList>

        <TabPanel value="users" className="pt-4">
          <Panel>
            <PanelHeader title="Platform users" icon={<UsersIcon className="h-4 w-4" />} />
            {users.isLoading ? (
              <Skeleton className="m-4 h-64" />
            ) : (
              <ul className="divide-y divide-line/60">
                {(users.data?.items ?? []).map((user: any) => (
                  <li key={user.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-info/15 text-xs font-semibold text-info">
                      {user.full_name.charAt(0)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink">{user.full_name}</span>
                      <span className="block truncate text-2xs text-faint">
                        {user.email} · {user.department ?? 'unassigned'}
                      </span>
                    </span>
                    <span className="flex flex-wrap gap-1">
                      {user.roles.map((role: string) => (
                        <Badge key={role} className="border-info/25 bg-info/10 text-info">
                          {role.replace(/_/g, ' ')}
                        </Badge>
                      ))}
                    </span>
                    <span className="text-2xs text-faint">
                      {user.last_login_at ? `last seen ${relativeTime(user.last_login_at)}` : 'never signed in'}
                    </span>
                    <Badge className={cn(user.is_active ? 'border-positive/25 bg-positive/10 text-positive' : '')}>
                      {user.is_active ? 'active' : 'inactive'}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </TabPanel>

        <TabPanel value="roles" className="pt-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {(roles.data?.roles ?? []).map((role: any) => (
              <Panel key={role.name} className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-ink">{role.name.replace(/_/g, ' ')}</p>
                    <p className="mt-1 text-2xs text-muted">{role.description}</p>
                  </div>
                  <Badge>{role.user_count} user(s)</Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-1">
                  {role.permissions.map((permission: string) => (
                    <span
                      key={permission}
                      className={cn(
                        'rounded border px-1.5 py-0.5 font-mono text-[10px]',
                        permission.includes('pii')
                          ? 'border-critical/25 bg-critical/10 text-critical'
                          : permission.startsWith('ai:')
                            ? 'border-ai/25 bg-ai/10 text-ai'
                            : 'border-line bg-surface text-muted',
                      )}
                    >
                      {permission}
                    </span>
                  ))}
                </div>
              </Panel>
            ))}
          </div>

          <Panel className="mt-4">
            <PanelHeader title="PII access" icon={<ShieldCheck className="h-4 w-4" />} />
            <p className="px-5 py-4 text-xs text-muted">
              Only <span className="text-ink">ADMIN</span> and <span className="text-ink">FRAUD_INVESTIGATOR</span> see
              unmasked email, phone, national id and IP addresses. Masking is applied in the serialisation layer that
              every response passes through, so a new endpoint cannot leak PII by omission.
            </p>
          </Panel>
        </TabPanel>
      </Tabs>
    </div>
  );
}
