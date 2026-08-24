import { FileSearch } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
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
import { RiskPill } from '@/components/viz/RiskOrb';
import { useTableQuery } from '@/hooks/useTableQuery';
import { caseStatusClass, formatCurrency, relativeTime, riskTextClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

const STATUSES = ['NEW', 'INVESTIGATING', 'ESCALATED', 'CONFIRMED_FRAUD', 'FALSE_POSITIVE', 'RESOLVED'];

export default function Cases() {
  const navigate = useNavigate();
  const user = useAuth((state) => state.user);
  const [status, setStatus] = useState('');
  const [priority, setPriority] = useState('');
  const [mine, setMine] = useState(false);

  const table = useTableQuery<any>('cases', '/cases', {
    pageSize: 25,
    defaultSort: 'risk_score',
    filters: { status: status || undefined, priority: priority || undefined, mine: mine || undefined },
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Cases"
        description="Investigation workflow: triage, evidence, verdict. Analyst verdicts feed the retraining dataset."
        actions={
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={mine}
              onChange={(event) => setMine(event.target.checked)}
              className="h-3.5 w-3.5 rounded border-line bg-surface"
            />
            Assigned to me
          </label>
        }
      />

      <Panel>
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center">
          <SearchInput
            value={table.search}
            onChange={table.setSearch}
            placeholder="Search case number, title, customer or transaction"
            className="sm:max-w-md"
          />
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            aria-label="Status filter"
            className="sm:w-48"
            options={[{ value: '', label: 'Any status' }, ...STATUSES.map((value) => ({ value, label: value.replace(/_/g, ' ') }))]}
          />
          <Select
            value={priority}
            onChange={(event) => setPriority(event.target.value)}
            aria-label="Priority filter"
            className="sm:w-40"
            options={[
              { value: '', label: 'Any priority' },
              { value: 'CRITICAL', label: 'Critical' },
              { value: 'HIGH', label: 'High' },
              { value: 'MEDIUM', label: 'Medium' },
              { value: 'LOW', label: 'Low' },
            ]}
          />
        </div>

        {table.isLoading ? (
          <TableSkeleton rows={8} cols={7} />
        ) : table.isError ? (
          <ErrorState error={table.error} onRetry={() => table.refetch()} />
        ) : table.items.length === 0 ? (
          <EmptyState
            title="No cases match"
            description={mine ? `Nothing is currently assigned to ${user?.full_name}.` : 'Try a different status or search term.'}
            icon={<FileSearch className="h-6 w-6" />}
          />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Case</Th>
                  <Th sortable active={table.sortBy === 'risk_score'} direction={table.sortDir} onSort={() => table.toggleSort('risk_score')}>
                    Risk
                  </Th>
                  <Th className="hidden md:table-cell">Customer</Th>
                  <Th sortable active={table.sortBy === 'exposure_amount'} direction={table.sortDir} onSort={() => table.toggleSort('exposure_amount')}>
                    Exposure
                  </Th>
                  <Th className="hidden lg:table-cell">Assigned</Th>
                  <Th>Status</Th>
                  <Th sortable active={table.sortBy === 'created_at'} direction={table.sortDir} onSort={() => table.toggleSort('created_at')} className="hidden sm:table-cell">
                    Opened
                  </Th>
                </tr>
              </thead>
              <tbody>
                {table.items.map((item) => (
                  <Tr key={item.id} onClick={() => navigate(`/app/cases/${item.id}`)}>
                    <Td>
                      <span className="font-mono text-xs text-info">{item.case_number}</span>
                      <span className="mt-0.5 block max-w-xs truncate text-xs text-muted">{item.title}</span>
                    </Td>
                    <Td>
                      <RiskPill score={item.risk_score} band={item.risk_band} />
                    </Td>
                    <Td className="hidden font-mono text-2xs text-muted md:table-cell">{item.customer_id}</Td>
                    <Td className="tnum whitespace-nowrap">{formatCurrency(item.exposure_amount)}</Td>
                    <Td className="hidden text-xs text-muted lg:table-cell">{item.assigned_to_name ?? '—'}</Td>
                    <Td>
                      <Badge className={cn(caseStatusClass(item.status))}>{item.status.replace(/_/g, ' ')}</Badge>
                    </Td>
                    <Td className="hidden whitespace-nowrap text-2xs text-faint sm:table-cell">{relativeTime(item.created_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
          </>
        )}
      </Panel>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {STATUSES.map((value) => {
          const count = table.items.filter((item) => item.status === value).length;
          return (
            <button
              key={value}
              type="button"
              onClick={() => setStatus(status === value ? '' : value)}
              className={cn(
                'rounded-lg border px-3 py-2 text-left transition-colors',
                status === value ? 'border-info/40 bg-info/10' : 'border-line bg-surface hover:border-line-strong',
              )}
            >
              <p className={cn('text-2xs uppercase tracking-wide', riskTextClass(value === 'CONFIRMED_FRAUD' ? 'CRITICAL' : 'LOW'))}>
                {value.replace(/_/g, ' ')}
              </p>
              <p className="tnum mt-1 text-sm text-ink">{count} on page</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}
