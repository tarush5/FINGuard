import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, Database, RefreshCcw, Server, Zap } from 'lucide-react';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  ErrorState,
  Panel,
  PanelHeader,
  ProgressBar,
  Skeleton,
  StatusDot,
  Table,
  Td,
  Th,
  Tr,
  useToast,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatDateTime, formatDuration, formatNumber, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function SystemHealth() {
  const queryClient = useQueryClient();
  const { push } = useToast();

  const health = useQuery({
    queryKey: ['system-health'],
    queryFn: () => api.get<any>('/monitoring/system'),
    refetchInterval: 20_000,
  });
  const topics = useQuery({ queryKey: ['topics'], queryFn: () => api.get<any>('/events/topics'), refetchInterval: 20_000 });
  const dlq = useQuery({ queryKey: ['dlq'], queryFn: () => api.get<any>('/events/dead-letter', { page_size: 20 }) });

  const replay = useMutation({
    mutationFn: (id: string) => api.post(`/events/dead-letter/${id}/replay`),
    onSuccess: () => {
      push({ title: 'Event replayed', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['dlq'] });
    },
    onError: (error: any) => push({ title: 'Replay failed', description: error?.message, variant: 'error' }),
  });

  if (health.isError) return <ErrorState error={health.error} onRetry={() => health.refetch()} />;

  const data = health.data;

  return (
    <div className="space-y-4">
      <PageHeader
        title="System health"
        description="Component status, decision latency against targets, event bus throughput and failed-event recovery."
        actions={
          <Button variant="outline" icon={<RefreshCcw className="h-3.5 w-3.5" />} onClick={() => health.refetch()}>
            Refresh
          </Button>
        }
      />

      {health.isLoading ? (
        <Skeleton className="h-24" />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {data.components.map((component: any) => (
            <Panel key={component.component} className="p-4">
              <div className="flex items-center gap-2">
                <StatusDot status={component.status} />
                <p className="label">{component.component.replace(/_/g, ' ')}</p>
              </div>
              <Badge className={cn('mt-2', statusBgClass(component.status))}>{component.status}</Badge>
              <p className="mt-2 text-2xs text-faint">{component.detail}</p>
            </Panel>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2">
          <PanelHeader title="Latency against targets" subtitle="Percentiles over the retained sample window" icon={<Activity className="h-4 w-4" />} />
          <div className="p-4">
            {health.isLoading ? (
              <Skeleton className="h-48" />
            ) : (data.latency ?? []).length === 0 ? (
              <p className="text-xs text-muted">No latency samples recorded yet — process a transaction to populate this panel.</p>
            ) : (
              <ul className="space-y-3">
                {data.latency.map((entry: any) => (
                  <li key={entry.name}>
                    <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
                      <span className="text-ink">{entry.name}</span>
                      <span className="tnum flex items-center gap-3 text-2xs">
                        <span className="text-faint">p50 {formatDuration(entry.p50)}</span>
                        <span className="text-muted">p95 {formatDuration(entry.p95)}</span>
                        <span className="text-faint">p99 {formatDuration(entry.p99)}</span>
                        <span className={cn(entry.within_target ? 'text-positive' : 'text-warning')}>
                          target {formatDuration(entry.target_p95_ms)}
                        </span>
                      </span>
                    </div>
                    <ProgressBar
                      value={Math.min((entry.p95 / entry.target_p95_ms) * 100, 100)}
                      barClassName={entry.within_target ? 'bg-positive' : 'bg-warning'}
                    />
                    <p className="mt-0.5 text-[10px] text-faint">{formatNumber(entry.samples)} samples in window</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Throughput" icon={<Zap className="h-4 w-4" />} />
          <div className="space-y-2 p-4">
            {health.isLoading ? (
              <Skeleton className="h-32" />
            ) : (
              <>
                {[
                  ['Transactions (last hour)', formatNumber(data.throughput.transactions_last_hour)],
                  ['Decisions since start', formatNumber(data.throughput.decisions_total)],
                  ['Duplicates rejected', formatNumber(data.throughput.duplicates_rejected)],
                  ['Uptime', formatDuration(data.uptime_seconds * 1000)],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2">
                    <span className="text-xs text-muted">{label}</span>
                    <span className="tnum text-sm text-ink">{value}</span>
                  </div>
                ))}
                <div className="rounded-lg border border-line bg-surface px-3 py-2">
                  <p className="label mb-1">Database</p>
                  <p className="tnum text-2xs text-muted">
                    {data.database.dialect} · {formatNumber(data.database.queries)} queries · {data.database.avg_query_ms}ms avg
                  </p>
                </div>
              </>
            )}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader
          title="Event bus"
          subtitle={topics.data ? `driver: ${topics.data.driver}${topics.data.brokers ? ` · ${topics.data.brokers.join(', ')}` : ''}` : undefined}
          icon={<Server className="h-4 w-4" />}
        />
        {topics.isLoading ? (
          <Skeleton className="m-4 h-40" />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Topic</Th>
                <Th>Published</Th>
                <Th>Processed</Th>
                <Th>Retries</Th>
                <Th>Dead-lettered</Th>
                <Th className="hidden md:table-cell">Consumer lag</Th>
                <Th className="hidden lg:table-cell">Handler p95</Th>
              </tr>
            </thead>
            <tbody>
              {(topics.data?.topics ?? []).map((topic: any) => (
                <Tr key={topic.topic}>
                  <Td className="font-mono text-2xs text-info">{topic.topic}</Td>
                  <Td className="tnum">{formatNumber(topic.published)}</Td>
                  <Td className="tnum">{formatNumber(topic.processed)}</Td>
                  <Td className={cn('tnum', topic.retries ? 'text-warning' : '')}>{formatNumber(topic.retries)}</Td>
                  <Td className={cn('tnum', topic.dead_lettered ? 'text-critical' : '')}>{formatNumber(topic.dead_lettered)}</Td>
                  <Td className="tnum hidden md:table-cell">{formatNumber(topic.consumer_lag)}</Td>
                  <Td className="tnum hidden lg:table-cell">{formatDuration(topic.p95_handler_ms)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="Dead letter queue"
          subtitle="Events that exhausted their retries and can be replayed"
          icon={<Database className="h-4 w-4" />}
        />
        {dlq.isLoading ? (
          <Skeleton className="m-4 h-24" />
        ) : (dlq.data?.items ?? []).length === 0 ? (
          <p className="px-5 py-8 text-center text-xs text-faint">No dead-lettered events. Every message has been processed.</p>
        ) : (
          <ul className="divide-y divide-line/60">
            {dlq.data.items.map((event: any) => (
              <li key={event.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <Badge className={cn(statusBgClass(event.status === 'REPLAYED' ? 'SUCCESS' : 'FAILED'))}>{event.status}</Badge>
                <span className="font-mono text-2xs text-muted">{event.topic}</span>
                <span className="min-w-0 flex-1 truncate text-2xs text-faint">
                  {event.error_type}: {event.error_message}
                </span>
                <span className="text-2xs text-faint">{event.attempts} attempts · {formatDateTime(event.created_at)}</span>
                {event.status !== 'REPLAYED' ? (
                  <Button size="sm" variant="outline" loading={replay.isPending} onClick={() => replay.mutate(event.id)}>
                    Replay
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel>
        <PanelHeader title="Recent pipeline runs" />
        {health.isLoading ? (
          <Skeleton className="m-4 h-32" />
        ) : (
          <ul className="divide-y divide-line/60">
            {(data.pipelines ?? []).map((run: any) => (
              <li key={run.id} className="flex flex-wrap items-center gap-3 px-5 py-2.5">
                <Badge className={cn(statusBgClass(run.status))}>{run.status}</Badge>
                <span className="text-xs text-ink">{run.pipeline.replace(/_/g, ' ')}</span>
                <span className="text-2xs text-faint">{run.type}</span>
                <span className="tnum ml-auto text-2xs text-muted">
                  {formatNumber(run.records_out)} records · {formatDuration(run.duration_ms)}
                </span>
                <span className="text-2xs text-faint">{formatDateTime(run.started_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
