import { useQuery } from '@tanstack/react-query';
import { ShieldAlert } from 'lucide-react';
import { useState } from 'react';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, Drawer, ErrorState, Panel, ProgressBar, Skeleton } from '@/components/ui';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, relativeTime, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const LAYER_COLORS: Record<string, string> = {
  raw: 'border-line bg-line/40 text-muted',
  bronze: 'border-warning/25 bg-warning/10 text-warning',
  silver: 'border-info/25 bg-info/10 text-info',
  gold: 'border-positive/25 bg-positive/10 text-positive',
};

export default function DataSources() {
  const [selected, setSelected] = useState<string | null>(null);

  const datasets = useQuery({ queryKey: ['datasets'], queryFn: () => api.get<any>('/datasets') });
  const detail = useQuery({
    queryKey: ['dataset', selected],
    queryFn: () => api.get<any>(`/datasets/${selected}`),
    enabled: Boolean(selected),
  });

  if (datasets.isError) return <ErrorState error={datasets.error} onRetry={() => datasets.refetch()} />;

  const byLayer = (datasets.data?.items ?? []).reduce((acc: Record<string, any[]>, dataset: any) => {
    (acc[dataset.layer] ??= []).push(dataset);
    return acc;
  }, {});

  return (
    <div className="space-y-4">
      <PageHeader
        title="Data catalogue"
        description="Every dataset in the platform with its owner, classification, freshness and quality score."
      />

      {datasets.isLoading ? (
        <Skeleton className="h-64" />
      ) : (
        Object.entries(byLayer).map(([layer, items]) => (
          <div key={layer}>
            <div className="mb-2 flex items-center gap-2">
              <Badge className={cn(LAYER_COLORS[layer] ?? '')}>{layer}</Badge>
              <span className="text-2xs text-faint">{(items as any[]).length} dataset(s)</span>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {(items as any[]).map((dataset) => (
                <button key={dataset.id} type="button" onClick={() => setSelected(dataset.name)} className="text-left">
                  <Panel className="h-full p-4 transition-colors hover:border-line-strong">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate font-mono text-sm text-ink">{dataset.name}</p>
                        <p className="mt-0.5 line-clamp-2 text-2xs text-muted">{dataset.description}</p>
                      </div>
                      {dataset.contains_pii ? (
                        <Badge className="border-critical/25 bg-critical/10 text-critical">
                          <ShieldAlert className="h-3 w-3" /> PII
                        </Badge>
                      ) : null}
                    </div>
                    <dl className="mt-3 grid grid-cols-3 gap-2">
                      <div>
                        <dt className="text-2xs text-faint">Rows</dt>
                        <dd className="tnum text-xs text-ink">{formatNumber(dataset.row_count)}</dd>
                      </div>
                      <div>
                        <dt className="text-2xs text-faint">Columns</dt>
                        <dd className="tnum text-xs text-ink">{dataset.column_count}</dd>
                      </div>
                      <div>
                        <dt className="text-2xs text-faint">Cadence</dt>
                        <dd className="text-xs text-ink">{dataset.refresh_cadence}</dd>
                      </div>
                    </dl>
                    {dataset.quality_score ? (
                      <div className="mt-3">
                        <div className="mb-1 flex justify-between text-2xs">
                          <span className="text-faint">Quality</span>
                          <span className="tnum text-ink">{dataset.quality_score.toFixed(1)}%</span>
                        </div>
                        <ProgressBar
                          value={dataset.quality_score}
                          barClassName={dataset.quality_score >= 98 ? 'bg-positive' : dataset.quality_score >= 94 ? 'bg-warning' : 'bg-critical'}
                        />
                      </div>
                    ) : null}
                    <p className="mt-3 text-2xs text-faint">
                      {dataset.owner} · refreshed {dataset.last_refreshed_at ? relativeTime(dataset.last_refreshed_at) : 'never'}
                    </p>
                  </Panel>
                </button>
              ))}
            </div>
          </div>
        ))
      )}

      <Drawer open={Boolean(selected)} onClose={() => setSelected(null)} title={selected ?? 'Dataset'} width="max-w-2xl">
        {detail.isLoading || !detail.data ? (
          <Skeleton className="h-64" />
        ) : (
          <div className="space-y-5">
            <div>
              <p className="text-sm text-muted">{detail.data.dataset.description}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge className={cn(LAYER_COLORS[detail.data.dataset.layer] ?? '')}>{detail.data.dataset.layer}</Badge>
                <Badge>{detail.data.dataset.classification}</Badge>
                <Badge>{formatNumber(detail.data.dataset.row_count)} rows</Badge>
                <Badge>owner {detail.data.dataset.owner}</Badge>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="label mb-1.5">Upstream</p>
                {detail.data.upstream.length ? (
                  detail.data.upstream.map((name: string) => (
                    <p key={name} className="font-mono text-2xs text-ink">
                      {name}
                    </p>
                  ))
                ) : (
                  <p className="text-2xs text-muted">source dataset</p>
                )}
              </div>
              <div>
                <p className="label mb-1.5">Downstream</p>
                {detail.data.downstream.length ? (
                  detail.data.downstream.map((name: string) => (
                    <p key={name} className="font-mono text-2xs text-ink">
                      {name}
                    </p>
                  ))
                ) : (
                  <p className="text-2xs text-muted">terminal dataset</p>
                )}
              </div>
            </div>

            <div>
              <p className="label mb-2">Recent quality checks</p>
              <ul className="space-y-1.5">
                {detail.data.checks.slice(0, 12).map((check: any, index: number) => (
                  <li key={index} className="rounded-lg border border-line bg-surface px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs text-ink">{check.check_name.replace(/_/g, ' ')}</span>
                      <Badge className={cn(statusBgClass(check.status))}>{check.status}</Badge>
                    </div>
                    <p className="mt-1 text-2xs text-faint">
                      {check.dimension} · {formatNumber(check.rows_scanned)} scanned · {check.rows_failed} failed · score{' '}
                      {check.score.toFixed(2)}% · {formatDateTime(check.run_at)}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
