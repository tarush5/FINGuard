import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, GaugeCircle, RefreshCcw } from 'lucide-react';
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
  useToast,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatDateTime, formatDuration, formatNumber, formatScore, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function Monitoring() {
  const queryClient = useQueryClient();
  const { push } = useToast();

  const monitoring = useQuery({
    queryKey: ['monitoring-models'],
    queryFn: () => api.get<any>('/monitoring/models', { days: 7 }),
    refetchInterval: 60_000,
  });
  const drift = useQuery({ queryKey: ['drift'], queryFn: () => api.get<any>('/monitoring/drift') });
  const feedback = useQuery({ queryKey: ['feedback'], queryFn: () => api.get<any>('/feedback', { page_size: 10 }) });
  const latency = useQuery({ queryKey: ['latency'], queryFn: () => api.get<any>('/monitoring/latency') });

  const recompute = useMutation({
    mutationFn: () => api.get<any>('/monitoring/drift', { recompute: true, window_days: 7 }),
    onSuccess: (result) => {
      push({
        title: `Drift recomputed: ${result.status}`,
        description: `${result.features?.filter((item: any) => item.status !== 'HEALTHY').length ?? 0} feature(s) above threshold`,
        variant: result.status === 'HEALTHY' ? 'success' : 'warning',
      });
      queryClient.invalidateQueries({ queryKey: ['drift'] });
      queryClient.invalidateQueries({ queryKey: ['monitoring-models'] });
    },
  });

  if (monitoring.isError) return <ErrorState error={monitoring.error} onRetry={() => monitoring.refetch()} />;

  const retraining = monitoring.data?.retraining;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Model monitoring"
        description="Production model health, feature and prediction drift, decision latency and the analyst feedback backlog."
        actions={
          <Button variant="outline" icon={<RefreshCcw className="h-3.5 w-3.5" />} loading={recompute.isPending} onClick={() => recompute.mutate()}>
            Recompute drift
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {monitoring.isLoading
          ? Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-36" />)
          : (monitoring.data?.models ?? []).map((model: any) => (
              <Panel key={model.name} className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="label">{model.name.replace(/_/g, ' ')}</p>
                    <p className="mt-1 truncate text-sm font-semibold text-ink">{model.tag ?? '—'}</p>
                  </div>
                  <span className="flex items-center gap-2">
                    <StatusDot status={model.status} />
                    <Badge className={cn(statusBgClass(model.status))}>{model.status}</Badge>
                  </span>
                </div>
                {model.metrics ? (
                  <dl className="mt-3 grid grid-cols-2 gap-2">
                    {Object.entries(model.metrics)
                      .filter(([, value]) => value !== null && value !== undefined)
                      .slice(0, 4)
                      .map(([key, value]) => (
                        <div key={key}>
                          <dt className="text-2xs text-faint">{key.replace(/_/g, ' ')}</dt>
                          <dd className="tnum text-sm text-ink">{formatScore(Number(value), 3)}</dd>
                        </div>
                      ))}
                  </dl>
                ) : null}
                <p className="mt-3 text-2xs text-faint">
                  {formatNumber(model.predictions_in_window ?? 0)} predictions in window
                  {model.deployed_at ? ` · deployed ${formatDateTime(model.deployed_at)}` : ''}
                </p>
                {model.issues?.length ? (
                  <ul className="mt-2 space-y-1">
                    {model.issues.map((issue: string) => (
                      <li key={issue} className="text-2xs text-warning">
                        • {issue}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </Panel>
            ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2">
          <PanelHeader
            title="Feature and prediction drift"
            subtitle={
              drift.data
                ? `PSI thresholds: warning ${drift.data.thresholds?.warning ?? 0.1}, critical ${drift.data.thresholds?.critical ?? 0.25} · computed ${formatDateTime(drift.data.computed_at)}`
                : undefined
            }
            icon={<GaugeCircle className="h-4 w-4" />}
            action={drift.data ? <Badge className={cn(statusBgClass(drift.data.status))}>{drift.data.status}</Badge> : null}
          />
          <div className="p-4">
            {drift.isLoading ? (
              <Skeleton className="h-64" />
            ) : !drift.data?.features?.length ? (
              <p className="text-xs text-muted">
                No drift computation is stored yet. Recompute to compare the current window against the baseline.
              </p>
            ) : (
              <ul className="space-y-2">
                {drift.data.features.map((feature: any) => (
                  <li key={`${feature.feature}-${feature.drift_type ?? 'feature'}`} className="rounded-lg border border-line bg-surface px-3 py-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span className="text-sm text-ink">{feature.feature.replace(/_/g, ' ')}</span>
                        {feature.drift_type === 'prediction' ? <Badge className="border-ai/25 bg-ai/10 text-ai">prediction</Badge> : null}
                      </span>
                      <span className="flex items-center gap-3">
                        <span className="tnum text-2xs text-faint">KS {feature.ks_statistic?.toFixed(3)}</span>
                        <span className="tnum text-xs text-ink">PSI {feature.psi.toFixed(4)}</span>
                        <Badge className={cn(statusBgClass(feature.status))}>{feature.status}</Badge>
                      </span>
                    </div>
                    <div className="mt-2">
                      <ProgressBar
                        value={Math.min(feature.psi * 200, 100)}
                        barClassName={
                          feature.status === 'CRITICAL' ? 'bg-critical' : feature.status === 'WARNING' ? 'bg-warning' : 'bg-positive'
                        }
                      />
                    </div>
                    <p className="mt-1.5 text-2xs text-faint">
                      baseline mean {formatScore(feature.baseline_mean, 4)} → current {formatScore(feature.current_mean, 4)} (
                      {feature.shift_pct > 0 ? '+' : ''}
                      {feature.shift_pct}%)
                      {feature.note ? ` · ${feature.note}` : ''}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel>
            <PanelHeader title="Retraining readiness" subtitle="Analyst labels awaiting the next run" />
            <div className="p-4">
              {retraining ? (
                <>
                  <div className="mb-3 flex items-baseline gap-2">
                    <span className="tnum text-2xl font-semibold text-ink">{formatNumber(retraining.pending_labels)}</span>
                    <span className="text-xs text-muted">of {retraining.threshold_to_retrain} labels</span>
                  </div>
                  <ProgressBar
                    value={Math.min((retraining.pending_labels / retraining.threshold_to_retrain) * 100, 100)}
                    barClassName={retraining.ready ? 'bg-positive' : 'bg-info'}
                  />
                  <dl className="mt-3 space-y-1.5 text-xs">
                    <div className="flex justify-between">
                      <dt className="text-muted">Confirmed fraud</dt>
                      <dd className="tnum text-ink">{retraining.pending_confirmed_fraud}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">False positives</dt>
                      <dd className="tnum text-ink">{retraining.pending_false_positives}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-muted">Production model</dt>
                      <dd className="text-ink">{retraining.production_model ?? '—'}</dd>
                    </div>
                  </dl>
                  <p className="mt-3 text-2xs text-faint">
                    {retraining.ready
                      ? 'Enough new labels have accumulated to justify a retraining run.'
                      : 'More analyst verdicts are needed before retraining is worthwhile.'}
                  </p>
                </>
              ) : (
                <Skeleton className="h-32" />
              )}
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Decision latency" subtitle="Measured percentiles vs targets" icon={<Activity className="h-4 w-4" />} />
            <div className="p-4">
              {latency.isLoading ? (
                <Skeleton className="h-40" />
              ) : (
                <ul className="space-y-2">
                  {Object.entries(latency.data?.measured ?? {})
                    .filter(([name]) => name.startsWith('decision') || name === 'api.request')
                    .map(([name, value]: [string, any]) => {
                      const target = latency.data.targets[name];
                      const within = target ? value.p95 <= target : true;
                      return (
                        <li key={name} className="flex items-center justify-between rounded border border-line bg-surface px-2.5 py-1.5">
                          <span className="text-2xs text-muted">{name.replace('decision.', '')}</span>
                          <span className="flex items-center gap-2 text-2xs">
                            <span className="tnum text-ink">p95 {formatDuration(value.p95)}</span>
                            {target ? (
                              <span className={cn('tnum', within ? 'text-positive' : 'text-warning')}>/ {formatDuration(target)}</span>
                            ) : null}
                          </span>
                        </li>
                      );
                    })}
                </ul>
              )}
              <p className="mt-2 text-2xs text-faint">{latency.data?.note}</p>
            </div>
          </Panel>
        </div>
      </div>

      <Panel>
        <PanelHeader title="Analyst feedback" subtitle="Verdicts that will train the next model" />
        {feedback.isLoading ? (
          <Skeleton className="h-40 m-4" />
        ) : (feedback.data?.items ?? []).length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-faint">No analyst verdicts recorded yet.</p>
        ) : (
          <ul className="divide-y divide-line/60">
            {feedback.data.items.map((item: any) => (
              <li key={item.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                <Badge
                  className={cn(
                    item.label === 1 ? 'border-critical/25 bg-critical/10 text-critical' : 'border-positive/25 bg-positive/10 text-positive',
                  )}
                >
                  {item.verdict.replace(/_/g, ' ')}
                </Badge>
                <span className="font-mono text-2xs text-muted">{item.transaction_id}</span>
                <span className="text-2xs text-faint">
                  model said {formatScore(item.predicted_probability, 4)} · {item.model_version}
                </span>
                <span className="ml-auto text-2xs text-faint">{item.analyst_name}</span>
                {item.used_in_training ? <Badge>in training set</Badge> : <Badge className="border-info/25 bg-info/10 text-info">pending</Badge>}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
