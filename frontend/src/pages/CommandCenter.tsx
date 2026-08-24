/**
 * Financial Risk Command Center.
 *
 * Every figure on this screen is fetched from the API; the live feed is a real
 * server-sent stream of transactions as they are scored.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  ArrowUpRight,
  CircleDot,
  Cpu,
  Gauge,
  Globe2,
  Play,
  ShieldAlert,
  Sparkles,
  Zap,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { DonutChart, KpiCard, TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  EmptyState,
  Panel,
  PanelHeader,
  Select,
  Skeleton,
  StatusDot,
  useToast,
} from '@/components/ui';
import { RiskPill } from '@/components/viz/RiskOrb';
import { api, streamTransactions } from '@/lib/api';
import {
  DECISION_COLORS,
  RISK_COLORS,
  bandOf,
  decisionBgClass,
  formatCurrency,
  formatMetric,
  formatNumber,
  formatTime,
  relativeTime,
  riskTextClass,
} from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';

interface Kpi {
  key: string;
  label: string;
  value: number;
  format: string;
  change_pct: number;
  comparison: string;
  invert_trend?: boolean;
}

const KPI_ACCENTS: Record<string, string> = {
  transactions: '#38BDF8',
  volume: '#60A5FA',
  fraud_amount: '#F87171',
  fraud_rate: '#FB923C',
  prevented_loss: '#34D399',
  open_cases: '#FBBF24',
  average_risk: '#A78BFA',
};

export default function CommandCenter() {
  const windowDays = useUi((state) => state.windowDays);
  const setWindowDays = useUi((state) => state.setWindowDays);
  const can = useAuth((state) => state.can);
  const user = useAuth((state) => state.user);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push } = useToast();

  const overview = useQuery({
    queryKey: ['overview', windowDays],
    queryFn: () => api.get<{ kpis: Kpi[]; currency: string; current: any; previous: any }>('/analytics/overview', { days: windowDays }),
    refetchInterval: 60_000,
  });

  const timeseries = useQuery({
    queryKey: ['timeseries', windowDays],
    queryFn: () => api.get<{ points: any[] }>('/analytics/timeseries', { days: windowDays, bucket: windowDays <= 3 ? 'hour' : 'day' }),
  });

  const decisions = useQuery({
    queryKey: ['breakdown', 'decision', windowDays],
    queryFn: () => api.get<{ items: any[] }>('/analytics/breakdown/decision', { days: windowDays }),
  });

  const geography = useQuery({
    queryKey: ['geography', windowDays],
    queryFn: () => api.get<{ locations: any[] }>('/analytics/geography', { days: windowDays }),
    enabled: can('analytics:read'),
  });

  const operations = useQuery({
    queryKey: ['operations'],
    queryFn: () => api.get<any>('/analytics/operations'),
    refetchInterval: 45_000,
  });

  const cases = useQuery({
    queryKey: ['cases', 'recent'],
    queryFn: () => api.get<any>('/cases', { page_size: 6, sort_by: 'risk_score', sort_dir: 'desc' }),
    enabled: can('case:read'),
  });

  const models = useQuery({
    queryKey: ['monitoring', 'models', 'compact'],
    queryFn: () => api.get<any>('/monitoring/models', { days: 7 }),
    enabled: can('monitoring:read'),
  });

  const scenarios = useQuery({
    queryKey: ['demo', 'scenarios'],
    queryFn: () => api.get<{ scenarios: any[] }>('/demo/scenarios'),
    enabled: can('transaction:read'),
  });

  const runScenario = useMutation({
    mutationFn: (scenario: string) => api.post<any>('/demo/run', { scenario, intensity: 1 }),
    onSuccess: (result) => {
      push({
        title: `${result.name} scenario complete`,
        description: `${result.transactions?.length ?? 0} transactions scored · ${result.cases?.length ?? 0} case(s) opened`,
        variant: 'success',
      });
      queryClient.invalidateQueries();
    },
    onError: (error: any) => push({ title: 'Scenario failed', description: error?.message, variant: 'error' }),
  });

  const currency = overview.data?.currency ?? 'INR';
  const points = timeseries.data?.points ?? [];

  const sparkFor = (key: string) =>
    points.map((point) => ({
      value:
        key === 'transactions'
          ? point.transactions
          : key === 'volume'
            ? point.volume
            : key === 'fraud_amount'
              ? point.fraud_amount
              : key === 'fraud_rate'
                ? point.fraud_rate
                : key === 'average_risk'
                  ? point.average_risk
                  : point.transactions,
    }));

  return (
    <div className="space-y-5">
      <PageHeader
        title="Financial Risk Command Center"
        description={`Live portfolio risk for the last ${windowDays} days. ${user?.platform_mode === 'demo' ? 'Synthetic demonstration data.' : ''}`}
        actions={
          <>
            <Select
              value={String(windowDays)}
              onChange={(event) => setWindowDays(Number(event.target.value))}
              aria-label="Analysis window"
              className="w-36"
              options={[
                { value: '1', label: 'Last 24 hours' },
                { value: '7', label: 'Last 7 days' },
                { value: '30', label: 'Last 30 days' },
                { value: '90', label: 'Last 90 days' },
              ]}
            />
            <Button variant="outline" icon={<Activity className="h-3.5 w-3.5" />} onClick={() => navigate('/app/transactions')}>
              Transactions
            </Button>
          </>
        }
      />

      {/* KPI row */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
        {overview.isLoading
          ? Array.from({ length: 7 }).map((_, index) => (
              <Panel key={index} className="p-4">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="mt-3 h-7 w-28" />
                <Skeleton className="mt-3 h-3 w-16" />
              </Panel>
            ))
          : overview.data?.kpis.map((kpi) => (
              <KpiCard
                key={kpi.key}
                label={kpi.label}
                value={formatMetric(kpi.value, kpi.format, currency)}
                changePct={kpi.change_pct}
                comparison={kpi.comparison}
                invertTrend={kpi.invert_trend}
                accent={KPI_ACCENTS[kpi.key] ?? '#38BDF8'}
                sparkline={sparkFor(kpi.key)}
                onClick={
                  kpi.key === 'open_cases'
                    ? () => navigate('/app/cases')
                    : kpi.key.includes('fraud')
                      ? () => navigate('/app/analytics/fraud')
                      : undefined
                }
              />
            ))}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* Volume & fraud trend */}
        <Panel className="xl:col-span-2">
          <PanelHeader
            title="Transaction volume and fraud"
            subtitle={`${formatNumber(overview.data?.current?.transactions ?? 0)} transactions · ${formatCurrency(overview.data?.current?.volume ?? 0, currency, true)} processed`}
            icon={<Activity className="h-4 w-4" />}
            action={
              <Link to="/app/analytics/financial" className="link flex items-center gap-1 text-xs">
                Financial analytics <ArrowUpRight className="h-3 w-3" />
              </Link>
            }
          />
          <div className="p-4">
            {timeseries.isLoading ? (
              <Skeleton className="h-[240px]" />
            ) : (
              <TrendChart
                data={points}
                xKey="bucket"
                series={[
                  { key: 'transactions', name: 'Transactions', color: '#38BDF8' },
                  { key: 'fraud_transactions', name: 'Fraud', color: '#F87171' },
                  { key: 'reviews', name: 'Manual reviews', color: '#FB923C' },
                ]}
              />
            )}
          </div>
        </Panel>

        {/* Live feed */}
        <LiveFeed currency={currency} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Decision mix */}
        <Panel>
          <PanelHeader title="Decision mix" subtitle="Outcomes across the window" icon={<Gauge className="h-4 w-4" />} />
          <div className="p-4">
            {decisions.isLoading ? (
              <Skeleton className="h-[220px]" />
            ) : (
              <>
                <DonutChart
                  data={decisions.data?.items ?? []}
                  nameKey="key"
                  valueKey="transactions"
                  colors={(decisions.data?.items ?? []).map((item: any) => DECISION_COLORS[item.key] ?? '#38BDF8')}
                  centerValue={formatNumber(
                    (decisions.data?.items ?? []).reduce((sum: number, item: any) => sum + item.transactions, 0),
                  )}
                  centerLabel="decisions"
                />
                <ul className="mt-3 space-y-1.5">
                  {(decisions.data?.items ?? []).map((item: any) => (
                    <li key={item.key} className="flex items-center justify-between text-xs">
                      <span className="flex items-center gap-2">
                        <span className="h-2 w-2 rounded-full" style={{ background: DECISION_COLORS[item.key] }} />
                        <span className="text-muted">{item.key.replace(/_/g, ' ')}</span>
                      </span>
                      <span className="tnum text-ink">
                        {formatNumber(item.transactions)}
                        <span className="ml-2 text-faint">{item.fraud_rate.toFixed(2)}% fraud</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </Panel>

        {/* Global risk map */}
        <Panel className="lg:col-span-2">
          <PanelHeader
            title="Global risk map"
            subtitle="Transaction concentration and fraud rate by city"
            icon={<Globe2 className="h-4 w-4" />}
            action={
              <Link to="/app/analytics/fraud" className="link flex items-center gap-1 text-xs">
                Fraud analytics <ArrowUpRight className="h-3 w-3" />
              </Link>
            }
          />
          <div className="p-4">
            {geography.isLoading ? <Skeleton className="h-[260px]" /> : <RiskMap locations={geography.data?.locations ?? []} currency={currency} />}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Case queue */}
        <Panel className="lg:col-span-2">
          <PanelHeader
            title="Investigation queue"
            subtitle={
              operations.data
                ? `${operations.data.resolved_cases} resolved · ${operations.data.sla_breached} past SLA · precision ${(operations.data.investigation_precision * 100).toFixed(0)}%`
                : undefined
            }
            icon={<ShieldAlert className="h-4 w-4" />}
            action={
              <Link to="/app/cases" className="link flex items-center gap-1 text-xs">
                All cases <ArrowUpRight className="h-3 w-3" />
              </Link>
            }
          />
          {cases.isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-12" />
              ))}
            </div>
          ) : (cases.data?.items ?? []).length === 0 ? (
            <EmptyState title="No open cases" description="The decision engine has not escalated anything in this window." />
          ) : (
            <ul className="divide-y divide-line/60">
              {(cases.data?.items ?? []).map((item: any) => (
                <li key={item.id}>
                  <Link
                    to={`/app/cases/${item.id}`}
                    className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-raised/50"
                  >
                    <RiskPill score={item.risk_score} band={item.risk_band} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-ink">{item.title}</p>
                      <p className="truncate text-2xs text-faint">
                        {item.case_number} · {item.customer_id} · {relativeTime(item.created_at)}
                      </p>
                    </div>
                    <div className="hidden text-right sm:block">
                      <p className="tnum text-sm text-ink">{formatCurrency(item.exposure_amount, currency, true)}</p>
                      <p className="text-2xs text-faint">{item.assigned_to_name ?? 'unassigned'}</p>
                    </div>
                    <Badge className={cn('shrink-0', decisionBgClass(item.status === 'CONFIRMED_FRAUD' ? 'DECLINE' : 'MANUAL_REVIEW'))}>
                      {item.status.replace(/_/g, ' ')}
                    </Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <div className="space-y-4">
          {/* Model health */}
          {can('monitoring:read') ? (
            <Panel>
              <PanelHeader
                title="Model health"
                subtitle="Production models and drift"
                icon={<Cpu className="h-4 w-4" />}
                action={
                  <Link to="/app/ml/monitoring" className="link text-xs">
                    Monitor
                  </Link>
                }
              />
              <div className="space-y-2.5 p-4">
                {models.isLoading ? (
                  <Skeleton className="h-20" />
                ) : (
                  <>
                    {(models.data?.models ?? []).map((model: any) => (
                      <div key={model.name} className="flex items-center gap-3 rounded-lg border border-line bg-surface px-3 py-2">
                        <StatusDot status={model.status} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs text-ink">{model.tag ?? model.name}</p>
                          <p className="text-2xs text-faint">
                            {model.metrics?.pr_auc !== undefined && model.metrics?.pr_auc !== null
                              ? `PR-AUC ${Number(model.metrics.pr_auc).toFixed(3)}`
                              : model.detail ?? '—'}
                            {model.predictions_in_window ? ` · ${formatNumber(model.predictions_in_window)} predictions` : ''}
                          </p>
                        </div>
                      </div>
                    ))}
                    {models.data?.drift ? (
                      <div className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2">
                        <span className="text-xs text-muted">Feature drift</span>
                        <Badge className={cn(models.data.drift.status === 'HEALTHY' ? 'border-positive/25 bg-positive/10 text-positive' : 'border-warning/25 bg-warning/10 text-warning')}>
                          {models.data.drift.status}
                        </Badge>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </Panel>
          ) : null}

          {/* Demo scenarios */}
          <Panel>
            <PanelHeader
              title="Demonstration scenarios"
              subtitle="Runs through the live decision path"
              icon={<Sparkles className="h-4 w-4 text-ai" />}
            />
            <div className="space-y-2 p-4">
              {(scenarios.data?.scenarios ?? []).map((scenario: any) => (
                <button
                  key={scenario.key}
                  type="button"
                  disabled={runScenario.isPending}
                  onClick={() => runScenario.mutate(scenario.key)}
                  className="group flex w-full items-start gap-3 rounded-lg border border-line bg-surface px-3 py-2.5 text-left transition-colors hover:border-ai/30 hover:bg-panel disabled:opacity-60"
                >
                  <Play className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ai" />
                  <span className="min-w-0">
                    <span className="block text-xs text-ink">{scenario.name}</span>
                    <span className="block text-2xs text-faint">{scenario.narrative}</span>
                  </span>
                </button>
              ))}
              {runScenario.isPending ? (
                <p className="flex items-center gap-2 text-2xs text-ai">
                  <Zap className="h-3 w-3 animate-pulse" /> Scoring scenario transactions…
                </p>
              ) : null}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

/** Real-time transaction feed backed by the SSE endpoint. */
function LiveFeed({ currency }: { currency: string }) {
  const [rows, setRows] = useState<any[]>([]);
  const [connected, setConnected] = useState(false);
  const [filter, setFilter] = useState<string>('ALL');
  const navigate = useNavigate();

  useEffect(() => {
    setConnected(true);
    const stop = streamTransactions(
      (batch) => {
        setRows((current) => {
          const seen = new Set(current.map((row) => row.id));
          const incoming = batch.filter((row) => !seen.has(row.id));
          return [...incoming.reverse(), ...current].slice(0, 40);
        });
      },
      { limit: 10, interval: 2.5, onError: () => setConnected(false) },
    );
    return () => {
      stop();
      setConnected(false);
    };
  }, []);

  const filtered = useMemo(
    () => (filter === 'ALL' ? rows : rows.filter((row) => row.risk_band === filter)),
    [rows, filter],
  );

  return (
    <Panel className="flex flex-col">
      <PanelHeader
        title="Live transaction feed"
        subtitle={connected ? 'Streaming decisions as they are scored' : 'Stream disconnected'}
        icon={<CircleDot className={cn('h-4 w-4', connected ? 'text-positive' : 'text-faint')} />}
        action={
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter feed by risk band"
            className="h-7 rounded-md border border-line bg-surface px-2 text-2xs text-muted"
          >
            <option value="ALL">All risk</option>
            <option value="CRITICAL">Critical</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
        }
      />
      <div className="max-h-[420px] min-h-[280px] flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <EmptyState
            title={connected ? 'Waiting for new transactions' : 'Feed unavailable'}
            description={
              connected
                ? 'New transactions appear here the moment they are scored. Run a demo scenario to generate activity.'
                : 'Reload the page to reconnect to the stream.'
            }
            icon={<Activity className="h-6 w-6" />}
          />
        ) : (
          <ul className="divide-y divide-line/50">
            <AnimatePresence initial={false}>
              {filtered.map((row) => (
                <motion.li
                  key={row.id}
                  layout
                  initial={{ opacity: 0, y: -8, backgroundColor: 'rgba(56,189,248,0.08)' }}
                  animate={{ opacity: 1, y: 0, backgroundColor: 'rgba(0,0,0,0)' }}
                  transition={{ duration: 0.45 }}
                >
                  <button
                    type="button"
                    onClick={() => navigate(`/app/transactions/${row.id}`)}
                    className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-raised/50"
                  >
                    <span className="tnum shrink-0 font-mono text-2xs text-faint">{formatTime(row.occurred_at)}</span>
                    <span className="tnum w-24 shrink-0 text-right text-sm text-ink">
                      {formatCurrency(row.amount, row.currency ?? currency, true)}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-xs text-muted">{row.merchant_id}</span>
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ background: RISK_COLORS[bandOf(row.risk_score)] }}
                    />
                    <span className={cn('w-16 shrink-0 text-right text-2xs uppercase tracking-wide', riskTextClass(row.risk_band))}>
                      {row.risk_band}
                    </span>
                  </button>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </Panel>
  );
}

/** Equirectangular scatter of city-level risk (no external map tiles). */
function RiskMap({ locations, currency }: { locations: any[]; currency: string }) {
  const [selected, setSelected] = useState<any | null>(null);
  const width = 720;
  const height = 300;

  const project = (lat: number, lon: number) => ({
    x: ((lon + 180) / 360) * width,
    y: ((90 - lat) / 180) * height,
  });

  const maxTransactions = Math.max(...locations.map((location) => location.transactions), 1);

  return (
    <div className="space-y-3">
      <div className="relative overflow-hidden rounded-lg border border-line bg-surface">
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Global transaction risk map">
          {/* Graticule */}
          {Array.from({ length: 7 }).map((_, index) => (
            <line
              key={`lat-${index}`}
              x1={0}
              x2={width}
              y1={(height / 6) * index}
              y2={(height / 6) * index}
              stroke="#1D2836"
              strokeDasharray="2 6"
            />
          ))}
          {Array.from({ length: 13 }).map((_, index) => (
            <line
              key={`lon-${index}`}
              y1={0}
              y2={height}
              x1={(width / 12) * index}
              x2={(width / 12) * index}
              stroke="#1D2836"
              strokeDasharray="2 6"
            />
          ))}
          <line x1={0} x2={width} y1={height / 2} y2={height / 2} stroke="#2A3849" />

          {locations.map((location) => {
            const { x, y } = project(location.latitude, location.longitude);
            const radius = 3 + (location.transactions / maxTransactions) * 12;
            const color = RISK_COLORS[bandOf(Math.min(location.fraud_rate * 20, 100))];
            return (
              <g key={`${location.city}-${location.country}`} onClick={() => setSelected(location)} className="cursor-pointer">
                {location.fraud_rate > 1 ? (
                  <circle cx={x} cy={y} r={radius + 5} fill={color} opacity={0.12} className="animate-pulse-ring" />
                ) : null}
                <circle cx={x} cy={y} r={radius} fill={color} fillOpacity={0.22} stroke={color} strokeWidth={1.2} />
                <circle cx={x} cy={y} r={1.6} fill={color} />
              </g>
            );
          })}
        </svg>
      </div>

      {selected ? (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="rounded-lg border border-line bg-surface p-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-ink">
              {selected.city}, {selected.country}
            </p>
            <button type="button" onClick={() => setSelected(null)} className="text-2xs text-faint hover:text-ink">
              clear
            </button>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ['Transactions', formatNumber(selected.transactions)],
              ['Volume', formatCurrency(selected.volume, currency, true)],
              ['Fraud rate', `${selected.fraud_rate.toFixed(2)}%`],
              ['Avg risk', selected.average_risk.toFixed(1)],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-2xs text-faint">{label}</dt>
                <dd className="tnum text-sm text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </motion.div>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {locations.slice(0, 4).map((location) => (
            <button
              key={`${location.city}-${location.country}`}
              type="button"
              onClick={() => setSelected(location)}
              className="rounded-lg border border-line bg-surface px-3 py-2 text-left transition-colors hover:border-line-strong"
            >
              <p className="truncate text-xs text-ink">{location.city}</p>
              <p className="tnum text-2xs text-faint">
                {formatNumber(location.transactions)} txns · {location.fraud_rate.toFixed(2)}%
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
