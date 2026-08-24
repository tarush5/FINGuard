import { FlaskConical } from 'lucide-react';
import { useState } from 'react';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Drawer,
  EmptyState,
  ErrorState,
  Pagination,
  Panel,
  PanelHeader,
  Table,
  TableSkeleton,
  Td,
  Th,
  Tr,
} from '@/components/ui';
import { useTableQuery } from '@/hooks/useTableQuery';
import { formatDateTime, formatNumber, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function Experiments() {
  const [selected, setSelected] = useState<any | null>(null);
  const table = useTableQuery<any>('experiments', '/experiments', { pageSize: 25 });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Experiments"
        description="Every training run with its parameters, metrics and dataset size. Runs are mirrored into MLflow when a tracking server is configured."
      />

      <Panel>
        <PanelHeader title="Training runs" subtitle={`${table.total} run(s)`} icon={<FlaskConical className="h-4 w-4" />} />
        {table.isLoading ? (
          <TableSkeleton rows={6} cols={6} />
        ) : table.isError ? (
          <ErrorState error={table.error} onRetry={() => table.refetch()} />
        ) : table.items.length === 0 ? (
          <EmptyState title="No training runs yet" description="Train a model from the registry screen." />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Model</Th>
                  <Th>Status</Th>
                  <Th className="hidden md:table-cell">Rows</Th>
                  <Th className="hidden lg:table-cell">Key metrics</Th>
                  <Th>Started</Th>
                </tr>
              </thead>
              <tbody>
                {table.items.map((run: any) => (
                  <Tr key={run.id} onClick={() => setSelected(run)}>
                    <Td>
                      <span className="block font-mono text-2xs text-info">{run.run_name}</span>
                      <span className="block text-2xs text-faint">by {run.triggered_by}</span>
                    </Td>
                    <Td>
                      <span className="block text-sm text-ink">{run.model_name.replace(/_/g, ' ')}</span>
                      <span className="block text-2xs text-faint">{run.algorithm}</span>
                    </Td>
                    <Td>
                      <Badge className={cn(statusBgClass(run.status))}>{run.status}</Badge>
                    </Td>
                    <Td className="tnum hidden md:table-cell">{formatNumber(run.dataset_rows)}</Td>
                    <Td className="hidden lg:table-cell">
                      <span className="flex flex-wrap gap-1">
                        {Object.entries(run.metrics ?? {})
                          .filter(([key]) => ['pr_auc', 'roc_auc', 'recall'].includes(key))
                          .map(([key, value]) => (
                            <span key={key} className="rounded bg-raised px-1.5 py-0.5 text-[10px] text-muted">
                              {key} {Number(value).toFixed(3)}
                            </span>
                          ))}
                      </span>
                    </Td>
                    <Td className="whitespace-nowrap text-2xs text-muted">{formatDateTime(run.started_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
          </>
        )}
      </Panel>

      <Drawer open={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.run_name ?? 'Run'} width="max-w-2xl">
        {selected ? (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge className={cn(statusBgClass(selected.status))}>{selected.status}</Badge>
              <Badge>{selected.algorithm}</Badge>
              <Badge>{selected.duration_seconds.toFixed(2)}s</Badge>
              <Badge>{formatNumber(selected.dataset_rows)} rows</Badge>
              {selected.mlflow_run_id ? <Badge className="border-ai/25 bg-ai/10 text-ai">MLflow {selected.mlflow_run_id.slice(0, 8)}</Badge> : null}
            </div>

            <div>
              <p className="label mb-2">Metrics</p>
              <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {Object.entries(selected.metrics ?? {}).map(([key, value]) => (
                  <div key={key} className="rounded-lg border border-line bg-surface px-3 py-2">
                    <dt className="text-2xs text-faint">{key.replace(/_/g, ' ')}</dt>
                    <dd className="tnum text-sm text-ink">
                      {typeof value === 'number' ? Number(value).toFixed(4) : String(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <div>
              <p className="label mb-2">Parameters</p>
              <pre className="overflow-x-auto rounded-lg border border-line bg-void px-3 py-2 text-2xs text-muted">
                {JSON.stringify(selected.parameters, null, 2)}
              </pre>
            </div>

            {selected.error ? (
              <div className="rounded-lg border border-critical/25 bg-critical/[0.06] px-3 py-2">
                <p className="text-2xs uppercase tracking-wide text-critical">Error</p>
                <p className="mt-1 text-xs text-ink">{selected.error}</p>
              </div>
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
