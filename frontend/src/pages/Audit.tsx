import { ScrollText } from 'lucide-react';
import { useState } from 'react';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Drawer,
  EmptyState,
  ErrorState,
  Pagination,
  Panel,
  SearchInput,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  Tr,
} from '@/components/ui';
import { useTableQuery } from '@/hooks/useTableQuery';
import { formatDateTime, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function Audit() {
  const [action, setAction] = useState('');
  const [entityType, setEntityType] = useState('');
  const [selected, setSelected] = useState<any | null>(null);

  const table = useTableQuery<any>('audit', '/audit', {
    pageSize: 30,
    filters: { action: action || undefined, entity_type: entityType || undefined },
  });

  const actions: string[] = (table.payload as any)?.actions ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Audit trail"
        description="Append-only record of who did what, when, from where and why — with the request id, model version and rule version in force at the time."
      />

      <Panel>
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center">
          <SearchInput value={table.search} onChange={table.setSearch} placeholder="Search by actor email" className="sm:max-w-sm" />
          <Select
            value={action}
            onChange={(event) => setAction(event.target.value)}
            aria-label="Action"
            className="sm:w-56"
            options={[{ value: '', label: 'Any action' }, ...actions.map((value) => ({ value, label: value }))]}
          />
          <Select
            value={entityType}
            onChange={(event) => setEntityType(event.target.value)}
            aria-label="Entity type"
            className="sm:w-44"
            options={[
              { value: '', label: 'Any entity' },
              { value: 'CASE', label: 'Case' },
              { value: 'RULE', label: 'Rule' },
              { value: 'USER', label: 'User' },
              { value: 'TRANSACTION', label: 'Transaction' },
              { value: 'MODEL_VERSION', label: 'Model version' },
              { value: 'POLICY', label: 'Policy' },
            ]}
          />
        </div>

        {table.isLoading ? (
          <TableSkeleton rows={10} cols={6} />
        ) : table.isError ? (
          <ErrorState error={table.error} onRetry={() => table.refetch()} />
        ) : table.items.length === 0 ? (
          <EmptyState title="No audit records match" icon={<ScrollText className="h-6 w-6" />} />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>When</Th>
                  <Th>Actor</Th>
                  <Th>Action</Th>
                  <Th className="hidden md:table-cell">Entity</Th>
                  <Th className="hidden lg:table-cell">Request</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {table.items.map((entry: any) => (
                  <Tr key={entry.id} onClick={() => setSelected(entry)}>
                    <Td className="whitespace-nowrap text-2xs text-muted">{formatDateTime(entry.created_at)}</Td>
                    <Td>
                      <span className="block text-xs text-ink">{entry.actor_email ?? 'system'}</span>
                      <span className="block text-[10px] text-faint">{(entry.actor_roles ?? []).join(', ')}</span>
                    </Td>
                    <Td className="font-mono text-2xs text-info">{entry.action}</Td>
                    <Td className="hidden md:table-cell">
                      <span className="block text-2xs text-muted">{entry.entity_type}</span>
                      <span className="block font-mono text-[10px] text-faint">{entry.entity_id ?? '—'}</span>
                    </Td>
                    <Td className="hidden font-mono text-[10px] text-faint lg:table-cell">{entry.request_id}</Td>
                    <Td>
                      <Badge className={cn(statusBgClass(entry.status))}>{entry.status}</Badge>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
          </>
        )}
      </Panel>

      <Drawer open={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.action ?? 'Audit record'}>
        {selected ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3">
              {[
                ['When', formatDateTime(selected.created_at)],
                ['Actor', selected.actor_email ?? 'system'],
                ['Roles', (selected.actor_roles ?? []).join(', ') || '—'],
                ['Entity', `${selected.entity_type} ${selected.entity_id ?? ''}`],
                ['IP address', selected.ip_address ?? '—'],
                ['Request id', selected.request_id ?? '—'],
                ['Model version', selected.model_version ?? '—'],
                ['Rule version', selected.rule_version ?? '—'],
              ].map(([label, value]) => (
                <div key={label as string} className="min-w-0">
                  <dt className="label">{label}</dt>
                  <dd className="truncate text-xs text-ink" title={String(value)}>
                    {String(value)}
                  </dd>
                </div>
              ))}
            </dl>

            {selected.reason ? (
              <div>
                <p className="label mb-1">Reason</p>
                <p className="text-xs text-ink">{selected.reason}</p>
              </div>
            ) : null}

            {selected.user_agent ? (
              <div>
                <p className="label mb-1">User agent</p>
                <p className="break-all font-mono text-[10px] text-faint">{selected.user_agent}</p>
              </div>
            ) : null}

            <div>
              <p className="label mb-1">Details</p>
              <pre className="overflow-x-auto rounded-lg border border-line bg-void px-3 py-2 text-2xs text-muted">
                {JSON.stringify(selected.details, null, 2)}
              </pre>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
