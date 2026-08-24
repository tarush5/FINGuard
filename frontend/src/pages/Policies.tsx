import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Brain, Shield } from 'lucide-react';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, ErrorState, Panel, PanelHeader, Skeleton, useToast } from '@/components/ui';
import { api } from '@/lib/api';
import { formatNumber, formatPercent } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function Policies() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const canWrite = useAuth((state) => state.can('governance:write'));

  const policies = useQuery({ queryKey: ['policies'], queryFn: () => api.get<any>('/governance/policies') });
  const aiUsage = useQuery({ queryKey: ['ai-usage'], queryFn: () => api.get<any>('/governance/ai-usage') });

  const toggle = useMutation({
    mutationFn: ({ key, enforced }: { key: string; enforced: boolean }) =>
      api.patch(`/governance/policies/${key}`, { enforced }),
    onSuccess: () => {
      push({ title: 'Policy updated', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['policies'] });
    },
    onError: (error: any) => push({ title: 'Update failed', description: error?.message, variant: 'error' }),
  });

  if (policies.isError) return <ErrorState error={policies.error} onRetry={() => policies.refetch()} />;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Governance policies"
        description="Privacy, AI governance, MLOps approval, retention and risk policy — each with the configuration actually enforced by the platform."
      />

      {policies.isLoading ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {(policies.data?.items ?? []).map((policy: any) => (
            <Panel key={policy.key} className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Shield className="h-3.5 w-3.5 text-muted" />
                    <p className="text-sm font-semibold text-ink">{policy.name}</p>
                  </div>
                  <p className="mt-1 font-mono text-2xs text-faint">{policy.key}</p>
                </div>
                {canWrite ? (
                  <button
                    type="button"
                    onClick={() => toggle.mutate({ key: policy.key, enforced: !policy.enforced })}
                    className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', policy.enforced ? 'bg-positive/70' : 'bg-line-strong')}
                    aria-label={policy.enforced ? 'Disable enforcement' : 'Enable enforcement'}
                  >
                    <span
                      className={cn(
                        'absolute top-0.5 h-4 w-4 rounded-full bg-ink transition-transform',
                        policy.enforced ? 'translate-x-4' : 'translate-x-0.5',
                      )}
                    />
                  </button>
                ) : (
                  <Badge className={cn(policy.enforced ? 'border-positive/25 bg-positive/10 text-positive' : '')}>
                    {policy.enforced ? 'enforced' : 'disabled'}
                  </Badge>
                )}
              </div>

              <p className="mt-3 text-xs text-muted">{policy.description}</p>

              <div className="mt-3 flex flex-wrap gap-2">
                <Badge>{policy.category.replace(/_/g, ' ')}</Badge>
                <Badge>owner {policy.owner}</Badge>
              </div>

              <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-void px-3 py-2 text-2xs text-muted">
                {JSON.stringify(policy.config, null, 2)}
              </pre>
            </Panel>
          ))}
        </div>
      )}

      <Panel>
        <PanelHeader
          title="AI usage"
          subtitle="Every AI interaction is logged with its question, generated SQL and outcome"
          icon={<Brain className="h-4 w-4 text-ai" />}
        />
        <div className="p-5">
          {aiUsage.isLoading ? (
            <Skeleton className="h-32" />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  ['Total queries', formatNumber(aiUsage.data.total_queries)],
                  ['Blocked queries', formatNumber(aiUsage.data.blocked_queries)],
                  ['Block rate', formatPercent(aiUsage.data.block_rate_pct, 2)],
                  ['Surfaces', String(aiUsage.data.by_surface.length)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-lg border border-line bg-surface px-3 py-2">
                    <p className="label">{label}</p>
                    <p className="tnum text-sm text-ink">{value}</p>
                  </div>
                ))}
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <p className="label mb-2">By surface</p>
                  <ul className="space-y-1.5">
                    {aiUsage.data.by_surface.map((entry: any) => (
                      <li key={entry.surface} className="flex items-center justify-between rounded border border-line bg-surface px-2.5 py-1.5 text-xs">
                        <span className="text-muted">{entry.surface.replace(/_/g, ' ')}</span>
                        <span className="tnum text-ink">
                          {formatNumber(entry.queries)} · {entry.avg_latency_ms.toFixed(0)}ms avg
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="label mb-2">Top users</p>
                  <ul className="space-y-1.5">
                    {aiUsage.data.top_users.map((entry: any) => (
                      <li key={entry.user} className="flex items-center justify-between rounded border border-line bg-surface px-2.5 py-1.5 text-xs">
                        <span className="truncate text-muted">{entry.user}</span>
                        <span className="tnum text-ink">{formatNumber(entry.queries)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </>
          )}
        </div>
      </Panel>
    </div>
  );
}
