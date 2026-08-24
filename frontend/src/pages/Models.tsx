import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Boxes, GitCompare, PlayCircle, Rocket, Undo2 } from 'lucide-react';
import { useState } from 'react';
import { CategoryBars, TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  Drawer,
  ErrorState,
  Panel,
  PanelHeader,
  Skeleton,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  useToast,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, formatScore, statusBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function Models() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const canTrain = useAuth((state) => state.can('model:train'));
  const canPromote = useAuth((state) => state.can('model:promote'));
  const [selected, setSelected] = useState<string | null>(null);
  const [compare, setCompare] = useState<string[]>([]);

  const models = useQuery({ queryKey: ['models'], queryFn: () => api.get<any>('/models') });
  const detail = useQuery({
    queryKey: ['model', selected],
    queryFn: () => api.get<any>(`/models/${selected}`),
    enabled: Boolean(selected),
  });
  const comparison = useQuery({
    queryKey: ['model-compare', compare],
    queryFn: () => api.get<any>(`/models/compare/${compare[0]}/${compare[1]}`),
    enabled: compare.length === 2,
  });

  const train = useMutation({
    mutationFn: () => api.post<any>('/models/train', { model: 'fraud', promote: true }),
    onSuccess: (result) => {
      const fraud = result.fraud;
      push({
        title: `${fraud.tag} trained and promoted`,
        description: `PR-AUC ${fraud.metrics.pr_auc} · recall ${fraud.metrics.recall} · threshold ${fraud.metrics.threshold}`,
        variant: 'success',
      });
      queryClient.invalidateQueries();
    },
    onError: (error: any) => push({ title: 'Training failed', description: error?.message, variant: 'error' }),
  });

  const promote = useMutation({
    mutationFn: (id: string) => api.post(`/models/${id}/promote`, { reason: 'Promoted from ML Studio' }),
    onSuccess: () => {
      push({ title: 'Model promoted to production', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['models'] });
    },
    onError: (error: any) => push({ title: 'Promotion failed', description: error?.message, variant: 'error' }),
  });

  const rollback = useMutation({
    mutationFn: (name: string) => api.post(`/models/${name}/rollback`, { reason: 'Rolled back from ML Studio' }),
    onSuccess: () => {
      push({ title: 'Rolled back to the previous version', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['models'] });
    },
    onError: (error: any) => push({ title: 'Rollback failed', description: error?.message, variant: 'error' }),
  });

  if (models.isError) return <ErrorState error={models.error} onRetry={() => models.refetch()} />;

  const items = models.data?.items ?? [];
  const production = models.data?.production ?? {};

  const toggleCompare = (id: string) => {
    setCompare((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id].slice(-2),
    );
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Model registry"
        description="Versioned models with their evaluation metrics, chosen operating point and deployment history. This registry is the serving source of truth."
        actions={
          canTrain ? (
            <Button variant="primary" icon={<PlayCircle className="h-3.5 w-3.5" />} loading={train.isPending} onClick={() => train.mutate()}>
              Train fraud model
            </Button>
          ) : null
        }
      />

      {models.data?.mlflow ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2.5 text-xs">
          <span className="text-muted">MLflow tracking</span>
          {models.data.mlflow.configured ? (
            <>
              <Badge className="border-positive/25 bg-positive/10 text-positive">connected</Badge>
              <span className="font-mono text-2xs text-faint">{models.data.mlflow.tracking_uri}</span>
            </>
          ) : (
            <>
              <Badge>not configured</Badge>
              <span className="text-2xs text-faint">
                Set MLFLOW_TRACKING_URI to mirror runs into MLflow; the registry table remains authoritative either way.
              </span>
            </>
          )}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {Object.entries(production).map(([name, model]: [string, any]) => (
          <Panel key={name} className="p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="label">{name.replace(/_/g, ' ')}</p>
                <p className="mt-1 truncate text-sm font-semibold text-ink">{model?.tag ?? 'not deployed'}</p>
              </div>
              {model ? <Badge className="border-positive/25 bg-positive/10 text-positive">production</Badge> : <Badge>none</Badge>}
            </div>
            {model ? (
              <>
                <dl className="mt-3 grid grid-cols-3 gap-2">
                  {[
                    ['PR-AUC', model.metrics?.pr_auc],
                    ['Recall', model.metrics?.recall],
                    ['Precision', model.metrics?.precision],
                  ].map(([label, value]) => (
                    <div key={label as string}>
                      <dt className="text-2xs text-faint">{label}</dt>
                      <dd className="tnum text-sm text-ink">{value !== undefined && value !== null ? formatScore(Number(value), 3) : '—'}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-2 text-2xs text-faint">
                  deployed {model.promoted_at ? formatDateTime(model.promoted_at) : '—'} · threshold {formatScore(model.threshold, 3)}
                </p>
                {canPromote ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="mt-2"
                    icon={<Undo2 className="h-3 w-3" />}
                    loading={rollback.isPending}
                    onClick={() => rollback.mutate(name)}
                  >
                    Roll back
                  </Button>
                ) : null}
              </>
            ) : null}
          </Panel>
        ))}
      </div>

      <Panel>
        <PanelHeader
          title="All versions"
          subtitle={`${items.length} registered version(s)`}
          icon={<Boxes className="h-4 w-4" />}
          action={
            compare.length === 2 ? (
              <Button size="sm" variant="outline" icon={<GitCompare className="h-3.5 w-3.5" />} onClick={() => setSelected(null)}>
                Comparing 2 versions
              </Button>
            ) : null
          }
        />
        {models.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-16" />
            ))}
          </div>
        ) : (
          <ul className="divide-y divide-line/60">
            {items.map((model: any) => (
              <li key={model.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <input
                  type="checkbox"
                  checked={compare.includes(model.id)}
                  onChange={() => toggleCompare(model.id)}
                  className="h-3.5 w-3.5 rounded border-line bg-surface"
                  aria-label={`Compare ${model.tag}`}
                />
                <button type="button" onClick={() => setSelected(model.id)} className="min-w-0 flex-1 text-left">
                  <span className="flex items-center gap-2">
                    <span className="text-sm text-ink">{model.tag}</span>
                    <Badge className={cn(model.stage === 'PRODUCTION' ? 'border-positive/25 bg-positive/10 text-positive' : '')}>
                      {model.stage}
                    </Badge>
                    <Badge>{model.algorithm}</Badge>
                  </span>
                  <span className="mt-0.5 block text-2xs text-faint">
                    trained {formatDateTime(model.trained_at)} by {model.trained_by} · {formatNumber(model.training_rows)} training rows ·{' '}
                    {(model.positive_rate * 100).toFixed(2)}% positive
                  </span>
                </button>
                <div className="hidden gap-4 sm:flex">
                  {[
                    ['ROC-AUC', model.metrics?.roc_auc],
                    ['PR-AUC', model.metrics?.pr_auc],
                    ['Recall', model.metrics?.recall],
                  ].map(([label, value]) => (
                    <div key={label as string} className="text-right">
                      <p className="text-2xs text-faint">{label}</p>
                      <p className="tnum text-xs text-ink">{value !== undefined && value !== null ? Number(value).toFixed(3) : '—'}</p>
                    </div>
                  ))}
                </div>
                {canPromote && model.stage !== 'PRODUCTION' ? (
                  <Button size="sm" variant="outline" icon={<Rocket className="h-3 w-3" />} onClick={() => promote.mutate(model.id)}>
                    Promote
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {comparison.data ? (
        <Panel>
          <PanelHeader
            title="Version comparison"
            subtitle={`${comparison.data.left.tag} vs ${comparison.data.right.tag}`}
            icon={<GitCompare className="h-4 w-4" />}
            action={
              <Button size="sm" variant="ghost" onClick={() => setCompare([])}>
                Clear
              </Button>
            }
          />
          <div className="overflow-x-auto p-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line">
                  <th className="label py-2 text-left">Metric</th>
                  <th className="label py-2 text-right">{comparison.data.left.tag}</th>
                  <th className="label py-2 text-right">{comparison.data.right.tag}</th>
                  <th className="label py-2 text-right">Delta</th>
                </tr>
              </thead>
              <tbody>
                {comparison.data.comparison.map((row: any) => (
                  <tr key={row.metric} className="border-b border-line/50">
                    <td className="py-2 text-muted">{row.metric.replace(/_/g, ' ')}</td>
                    <td className="tnum py-2 text-right text-ink">{row.left.toFixed(4)}</td>
                    <td className="tnum py-2 text-right text-ink">{row.right.toFixed(4)}</td>
                    <td
                      className={cn(
                        'tnum py-2 text-right',
                        row.delta > 0 ? 'text-positive' : row.delta < 0 ? 'text-critical' : 'text-muted',
                      )}
                    >
                      {row.delta > 0 ? '+' : ''}
                      {row.delta.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}

      <ModelDrawer open={Boolean(selected)} onClose={() => setSelected(null)} data={detail.data} loading={detail.isLoading} />
    </div>
  );
}

function ModelDrawer({ open, onClose, data, loading }: { open: boolean; onClose: () => void; data: any; loading: boolean }) {
  const [tab, setTab] = useState('metrics');

  return (
    <Drawer open={open} onClose={onClose} title={data?.tag ?? 'Model version'} width="max-w-3xl">
      {loading || !data ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Badge className={cn(statusBgClass(data.stage === 'PRODUCTION' ? 'HEALTHY' : 'INFO'))}>{data.stage}</Badge>
            <Badge>{data.algorithm}</Badge>
            <Badge>threshold {formatScore(data.threshold, 4)}</Badge>
            <Badge>{formatNumber(data.training_rows)} train rows</Badge>
            <Badge>{formatNumber(data.test_rows)} test rows</Badge>
          </div>
          <p className="text-xs text-muted">{data.notes}</p>

          <Tabs value={tab} onValueChange={setTab}>
            <TabList>
              <Tab value="metrics">Metrics</Tab>
              <Tab value="curves">Curves</Tab>
              <Tab value="importance">Feature importance</Tab>
              <Tab value="params">Hyperparameters</Tab>
            </TabList>

            <TabPanel value="metrics" className="pt-4">
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {Object.entries(data.metrics ?? {})
                  .filter(([, value]) => typeof value === 'number')
                  .map(([key, value]) => (
                    <div key={key} className="rounded-lg border border-line bg-surface px-3 py-2">
                      <dt className="label">{key.replace(/_/g, ' ')}</dt>
                      <dd className="tnum text-sm text-ink">{Number(value).toFixed(4)}</dd>
                    </div>
                  ))}
              </dl>
              {data.curves?.confusion_matrix ? (
                <div className="mt-4">
                  <p className="label mb-2">Confusion matrix (test window)</p>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(data.curves.confusion_matrix).map(([key, value]) => (
                      <div key={key} className="rounded-lg border border-line bg-surface px-3 py-2">
                        <p className="text-2xs text-faint">{key.replace(/_/g, ' ')}</p>
                        <p className="tnum text-sm text-ink">{formatNumber(Number(value))}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </TabPanel>

            <TabPanel value="curves" className="space-y-4 pt-4">
              {data.curves?.roc_curve?.length ? (
                <div>
                  <p className="label mb-2">ROC curve</p>
                  <TrendChart data={data.curves.roc_curve} xKey="fpr" series={[{ key: 'tpr', name: 'True positive rate', color: '#38BDF8' }]} height={200} showLegend={false} />
                </div>
              ) : null}
              {data.curves?.pr_curve?.length ? (
                <div>
                  <p className="label mb-2">Precision-recall curve</p>
                  <TrendChart data={data.curves.pr_curve} xKey="recall" series={[{ key: 'precision', name: 'Precision', color: '#34D399' }]} height={200} showLegend={false} />
                </div>
              ) : null}
              {data.curves?.threshold_curve?.length ? (
                <div>
                  <p className="label mb-2">Expected cost by threshold</p>
                  <TrendChart
                    data={data.curves.threshold_curve}
                    xKey="threshold"
                    series={[{ key: 'total_cost', name: 'Total cost', color: '#F87171' }]}
                    height={200}
                    showLegend={false}
                  />
                </div>
              ) : null}
            </TabPanel>

            <TabPanel value="importance" className="pt-4">
              {data.feature_importance?.length ? (
                <CategoryBars
                  data={data.feature_importance.slice(0, 15)}
                  xKey="label"
                  yKey="importance_pct"
                  horizontal
                  height={420}
                  color="#A78BFA"
                  formatter={(value) => `${value.toFixed(2)}%`}
                />
              ) : (
                <p className="text-xs text-muted">Feature importance is available for the production version.</p>
              )}
            </TabPanel>

            <TabPanel value="params" className="pt-4">
              <pre className="overflow-x-auto rounded-lg border border-line bg-void px-3 py-2 text-2xs text-muted">
                {JSON.stringify(data.hyperparameters, null, 2)}
              </pre>
            </TabPanel>
          </Tabs>
        </div>
      )}
    </Drawer>
  );
}
