import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { GaugeCircle, PlayCircle } from 'lucide-react';
import { TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  ErrorState,
  Panel,
  PanelHeader,
  ProgressBar,
  Skeleton,
  useToast,
} from '@/components/ui';
import { RiskOrb } from '@/components/viz/RiskOrb';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function Quality() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const canRun = useAuth((state) => state.can('pipeline:run'));

  const quality = useQuery({ queryKey: ['quality'], queryFn: () => api.get<any>('/quality') });

  const run = useMutation({
    mutationFn: () => api.post<any>('/quality/run'),
    onSuccess: (result) => {
      push({
        title: `Trust score ${result.trust_score}%`,
        description: `${result.failed_checks.length} check(s) below threshold`,
        variant: result.failed_checks.length ? 'warning' : 'success',
      });
      queryClient.invalidateQueries({ queryKey: ['quality'] });
    },
  });

  if (quality.isError) return <ErrorState error={quality.error} onRetry={() => quality.refetch()} />;

  const data = quality.data;
  const trust = data?.trust_score ?? 0;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Data quality"
        description="Completeness, validity, consistency, uniqueness, freshness and accuracy — measured with real SQL against the warehouse."
        actions={
          canRun ? (
            <Button variant="primary" icon={<PlayCircle className="h-3.5 w-3.5" />} loading={run.isPending} onClick={() => run.mutate()}>
              Run quality suite
            </Button>
          ) : null
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
        <Panel className="flex flex-col items-center justify-center p-5">
          {quality.isLoading ? (
            <Skeleton className="h-40 w-40 rounded-full" />
          ) : (
            <>
              <RiskOrb
                score={trust}
                band={trust >= 98 ? 'LOW' : trust >= 94 ? 'MEDIUM' : 'CRITICAL'}
                size={160}
                label="Financial data trust score"
              />
              <Badge className={cn('mt-3', statusBgClass(data.status))}>{data.status}</Badge>
              <p className="mt-2 text-2xs text-faint">evaluated {formatDateTime(data.evaluated_at)}</p>
            </>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel>
            <PanelHeader title="Quality dimensions" icon={<GaugeCircle className="h-4 w-4" />} />
            <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(data?.dimensions ?? {}).map(([dimension, score]) => (
                <div key={dimension}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="text-muted">{dimension.toLowerCase()}</span>
                    <span className="tnum text-ink">{Number(score).toFixed(2)}%</span>
                  </div>
                  <ProgressBar
                    value={Number(score)}
                    barClassName={Number(score) >= 99 ? 'bg-positive' : Number(score) >= 95 ? 'bg-warning' : 'bg-critical'}
                  />
                </div>
              ))}
            </div>
          </Panel>

          {data?.trend?.length ? (
            <Panel>
              <PanelHeader title="Trust score trend" subtitle="Across recorded runs" />
              <div className="p-4">
                <TrendChart
                  data={data.trend.map((point: any) => ({
                    run: formatDateTime(point.run_at),
                    trust_score: point.trust_score,
                    failed_checks: point.failed_checks,
                  }))}
                  xKey="run"
                  series={[
                    { key: 'trust_score', name: 'Trust score', color: '#34D399' },
                    { key: 'failed_checks', name: 'Failed checks', color: '#F87171' },
                  ]}
                  height={200}
                />
              </div>
            </Panel>
          ) : null}
        </div>
      </div>

      <Panel>
        <PanelHeader
          title="Checks"
          subtitle={`${data?.checks?.length ?? 0} checks · ${data?.failed_checks?.length ?? 0} below threshold`}
        />
        {quality.isLoading ? (
          <Skeleton className="m-4 h-64" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr>
                  {['Check', 'Dataset', 'Dimension', 'Expectation', 'Scanned', 'Failed', 'Score', 'Status'].map((heading) => (
                    <th key={heading} className="label border-b border-line px-3 py-2">
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data?.checks ?? []).map((check: any, index: number) => (
                  <tr key={index} className="border-b border-line/50">
                    <td className="px-3 py-2 text-ink">{check.check_name.replace(/_/g, ' ')}</td>
                    <td className="px-3 py-2 font-mono text-2xs text-muted">{check.dataset}</td>
                    <td className="px-3 py-2 text-muted">{check.dimension.toLowerCase()}</td>
                    <td className="max-w-xs truncate px-3 py-2 text-2xs text-faint" title={check.expectation}>
                      {check.expectation}
                    </td>
                    <td className="tnum px-3 py-2 text-muted">{formatNumber(check.rows_scanned)}</td>
                    <td className={cn('tnum px-3 py-2', check.rows_failed ? 'text-critical' : 'text-muted')}>{formatNumber(check.rows_failed)}</td>
                    <td className="tnum px-3 py-2 text-ink">{check.score.toFixed(2)}%</td>
                    <td className="px-3 py-2">
                      <Badge className={cn(statusBgClass(check.status))}>{check.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
