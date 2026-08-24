import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Pagination,
  Panel,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  Tr,
  useToast,
} from '@/components/ui';
import { useTableQuery } from '@/hooks/useTableQuery';
import { api } from '@/lib/api';
import { formatCurrency, formatScore, relativeTime, riskBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function Alerts() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const canWrite = useAuth((state) => state.can('case:write'));
  const [severity, setSeverity] = useState('');
  const [status, setStatus] = useState('');

  const table = useTableQuery<any>('alerts', '/alerts', {
    pageSize: 25,
    filters: { severity: severity || undefined, status: status || undefined },
    refetchInterval: 45_000,
  });

  const dismiss = async (id: string) => {
    try {
      await api.patch(`/alerts/${id}`, { status: 'DISMISSED' });
      push({ title: 'Alert dismissed', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
    } catch (error: any) {
      push({ title: 'Could not dismiss the alert', description: error?.message, variant: 'error' });
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Alerts"
        description="Every decision above the alert threshold, with the evidence that raised it."
      />

      <Panel>
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row">
          <Select
            value={severity}
            onChange={(event) => setSeverity(event.target.value)}
            aria-label="Severity"
            className="sm:w-44"
            options={[
              { value: '', label: 'Any severity' },
              { value: 'CRITICAL', label: 'Critical' },
              { value: 'HIGH', label: 'High' },
              { value: 'MEDIUM', label: 'Medium' },
              { value: 'LOW', label: 'Low' },
            ]}
          />
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            aria-label="Status"
            className="sm:w-44"
            options={[
              { value: '', label: 'Any status' },
              { value: 'OPEN', label: 'Open' },
              { value: 'TRIAGED', label: 'Triaged' },
              { value: 'DISMISSED', label: 'Dismissed' },
              { value: 'ESCALATED', label: 'Escalated' },
            ]}
          />
        </div>

        {table.isLoading ? (
          <TableSkeleton rows={8} cols={6} />
        ) : table.isError ? (
          <ErrorState error={table.error} onRetry={() => table.refetch()} />
        ) : table.items.length === 0 ? (
          <EmptyState title="No alerts" description="Nothing has breached the alert threshold." icon={<AlertTriangle className="h-6 w-6" />} />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Severity</Th>
                  <Th>Alert</Th>
                  <Th className="hidden md:table-cell">Amount</Th>
                  <Th>Risk</Th>
                  <Th className="hidden lg:table-cell">Rules</Th>
                  <Th>Status</Th>
                  <Th className="hidden sm:table-cell">Raised</Th>
                  {canWrite ? <Th>Actions</Th> : null}
                </tr>
              </thead>
              <tbody>
                {table.items.map((alert) => (
                  <Tr key={alert.id}>
                    <Td>
                      <Badge className={cn(riskBgClass(alert.severity))}>{alert.severity}</Badge>
                    </Td>
                    <Td>
                      <button
                        type="button"
                        onClick={() => alert.transaction_id && navigate(`/app/transactions/${alert.transaction_id}`)}
                        className="text-left"
                      >
                        <span className="block max-w-md truncate text-sm text-ink">{alert.title}</span>
                        <span className="block max-w-md truncate text-2xs text-faint">{alert.description}</span>
                      </button>
                    </Td>
                    <Td className="tnum hidden whitespace-nowrap md:table-cell">{formatCurrency(alert.amount)}</Td>
                    <Td className="tnum">{formatScore(alert.risk_score)}</Td>
                    <Td className="hidden lg:table-cell">
                      <div className="flex flex-wrap gap-1">
                        {(alert.triggered_rules ?? []).slice(0, 3).map((code: string) => (
                          <span key={code} className="rounded bg-raised px-1.5 py-0.5 font-mono text-[10px] text-muted">
                            {code}
                          </span>
                        ))}
                      </div>
                    </Td>
                    <Td>
                      <span className="text-xs text-muted">{alert.status}</span>
                      {alert.case_id ? (
                        <Link to={`/app/cases/${alert.case_id}`} className="link block text-[10px]">
                          view case
                        </Link>
                      ) : null}
                    </Td>
                    <Td className="hidden whitespace-nowrap text-2xs text-faint sm:table-cell">{relativeTime(alert.created_at)}</Td>
                    {canWrite ? (
                      <Td>
                        {alert.status === 'OPEN' ? (
                          <Button size="sm" variant="ghost" onClick={() => dismiss(alert.id)}>
                            Dismiss
                          </Button>
                        ) : null}
                      </Td>
                    ) : null}
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
          </>
        )}
      </Panel>
    </div>
  );
}
