import { Workflow } from 'lucide-react';
import { useState } from 'react';
import { CategoryBars } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Drawer,
  ErrorState,
  Pagination,
  Panel,
  PanelHeader,
  Select,
  Skeleton,
  StatusDot,
  Table,
  Td,
  Th,
  Tr,
} from '@/components/ui';
import { useTableQuery } from '@/hooks/useTableQuery';
import { formatDateTime, formatDuration, formatNumber, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function Pipelines() {
  const [status, setStatus] = useState('');
  const [selected, setSelected] = useState<any | null>(null);

  const table = useTableQuery<any>('pipelines', '/pipelines', {
    pageSize: 25,
    filters: { status: status || undefined },
    refetchInterval: 60_000,
  });

  const summary = (table.payload as any)?.summary ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Pipelines"
        description="Every ingestion, scoring, aggregation and quality run recorded by the platform, with the records it processed."
        actions={
          <Select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            aria-label="Status"
            className="w-40"
            options={[
              { value: '', label: 'Any status' },
              { value: 'SUCCESS', label: 'Success' },
              { value: 'WARNING', label: 'Warning' },
              { value: 'FAILED', label: 'Failed' },
              { value: 'RUNNING', label: 'Running' },
            ]}
          />
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2">
          <PanelHeader title="Run history" subtitle={`${table.total} run(s)`} icon={<Workflow className="h-4 w-4" />} />
          {table.isLoading ? (
            <Skeleton className="m-4 h-64" />
          ) : table.isError ? (
            <ErrorState error={table.error} onRetry={() => table.refetch()} />
          ) : (
            <>
              <Table>
                <thead>
                  <tr>
                    <Th>Pipeline</Th>
                    <Th>Status</Th>
                    <Th className="hidden md:table-cell">Records</Th>
                    <Th className="hidden lg:table-cell">Duration</Th>
                    <Th>Started</Th>
                  </tr>
                </thead>
                <tbody>
                  {table.items.map((run: any) => (
                    <Tr key={run.id} onClick={() => setSelected(run)}>
                      <Td>
                        <span className="block text-sm text-ink">{run.pipeline.replace(/_/g, ' ')}</span>
                        <span className="block text-2xs text-faint">
                          {run.pipeline_type} · triggered by {run.triggered_by}
                        </span>
                      </Td>
                      <Td>
                        <span className="flex items-center gap-2">
                          <StatusDot status={run.status} />
                          <Badge className={cn(statusBgClass(run.status))}>{run.status}</Badge>
                        </span>
                      </Td>
                      <Td className="tnum hidden md:table-cell">
                        {formatNumber(run.records_out)}
                        {run.records_failed ? <span className="ml-1 text-critical">/{run.records_failed} failed</span> : null}
                      </Td>
                      <Td className="tnum hidden lg:table-cell">{formatDuration(run.duration_ms)}</Td>
                      <Td className="whitespace-nowrap text-2xs text-muted">{formatDateTime(run.started_at)}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
              <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
            </>
          )}
        </Panel>

        <Panel>
          <PanelHeader title="Throughput by pipeline" subtitle="Records processed" />
          <div className="p-3">
            {summary.length ? (
              <CategoryBars
                data={summary.map((entry: any) => ({ pipeline: entry.pipeline.replace(/_/g, ' '), records: entry.records_processed }))}
                xKey="pipeline"
                yKey="records"
                horizontal
                height={300}
                color="#34D399"
              />
            ) : (
              <Skeleton className="h-64" />
            )}
          </div>
          {summary.length ? (
            <ul className="divide-y divide-line/60 border-t border-line">
              {summary.map((entry: any) => (
                <li key={entry.pipeline} className="flex items-center justify-between px-4 py-2 text-2xs">
                  <span className="text-muted">{entry.pipeline.replace(/_/g, ' ')}</span>
                  <span className="tnum text-ink">
                    {entry.runs} runs · {formatDuration(entry.average_duration_ms)} avg
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      </div>

      <Drawer open={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.pipeline?.replace(/_/g, ' ') ?? 'Run'}>
        {selected ? (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge className={cn(statusBgClass(selected.status))}>{selected.status}</Badge>
              <Badge>{selected.pipeline_type}</Badge>
              <Badge>{formatDuration(selected.duration_ms)}</Badge>
              <Badge>run {selected.run_key}</Badge>
            </div>

            <dl className="grid grid-cols-3 gap-3">
              {[
                ['Records in', formatNumber(selected.records_in)],
                ['Records out', formatNumber(selected.records_out)],
                ['Failed', formatNumber(selected.records_failed)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-line bg-surface px-3 py-2">
                  <dt className="label">{label}</dt>
                  <dd className="tnum text-sm text-ink">{value}</dd>
                </div>
              ))}
            </dl>

            {selected.steps?.length ? (
              <div>
                <p className="label mb-2">Steps</p>
                <ol className="space-y-1.5">
                  {selected.steps.map((step: any, index: number) => (
                    <li key={index} className="flex items-center justify-between rounded border border-line bg-surface px-3 py-2">
                      <span className="text-xs text-ink">
                        {index + 1}. {step.step}
                      </span>
                      <Badge className={cn(statusBgClass(step.status))}>{step.status}</Badge>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}

            <div>
              <p className="label mb-2">Metrics</p>
              <pre className="overflow-x-auto rounded-lg border border-line bg-void px-3 py-2 text-2xs text-muted">
                {JSON.stringify(selected.metrics, null, 2)}
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
