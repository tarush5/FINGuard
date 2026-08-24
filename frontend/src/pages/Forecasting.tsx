import { useQuery } from '@tanstack/react-query';
import { Waypoints } from 'lucide-react';
import { useState } from 'react';
import { ForecastChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, ErrorState, Panel, PanelHeader, Select, Skeleton, Tab, TabList, Tabs } from '@/components/ui';
import { api } from '@/lib/api';
import { formatCurrency, formatNumber, formatPercent } from '@/lib/format';

const METRICS = [
  { value: 'transactions', label: 'Transaction volume' },
  { value: 'volume', label: 'Processed value' },
  { value: 'fraud_transactions', label: 'Fraud volume' },
  { value: 'fraud_amount', label: 'Fraud amount' },
];

export default function Forecasting() {
  const [metric, setMetric] = useState('transactions');
  const [horizon, setHorizon] = useState('7d');

  const forecast = useQuery({
    queryKey: ['forecast', metric],
    queryFn: () => api.get<any>(`/forecasting/${metric}`, { horizons: '7,30,90' }),
  });

  const workload = useQuery({
    queryKey: ['forecast-workload'],
    queryFn: () => api.get<any>('/forecasting-workload', { horizon: 7 }),
  });

  const isCurrency = metric.includes('amount') || metric === 'volume';
  const fmt = (value: number) => (isCurrency ? formatCurrency(value, 'INR', true) : formatNumber(value));

  if (forecast.isError) return <ErrorState error={forecast.error} onRetry={() => forecast.refetch()} />;

  const data = forecast.data;
  const selected = data?.forecasts?.[horizon];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Forecasting"
        description="Weekly-seasonal decomposition with a damped trend. Intervals come from in-sample residual spread, and the backtest error is reported alongside every projection."
        actions={
          <Select
            value={metric}
            onChange={(event) => setMetric(event.target.value)}
            aria-label="Metric"
            className="w-52"
            options={METRICS}
          />
        }
      />

      {data?.status === 'INSUFFICIENT_HISTORY' ? (
        <Panel className="p-8 text-center">
          <p className="text-sm text-ink">Not enough history to forecast.</p>
          <p className="mt-1 text-xs text-muted">{data.message}</p>
        </Panel>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {forecast.isLoading
              ? Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-24" />)
              : [
                  ['Recent daily average', fmt(data.recent.average)],
                  ['Trend per day', `${data.model.trend_per_day > 0 ? '+' : ''}${data.model.trend_per_day.toFixed(2)}`],
                  ['Backtest error (MAPE)', formatPercent(data.model.in_sample_mape_pct, 2)],
                  ['History used', `${data.model.history_days} days`],
                ].map(([label, value]) => (
                  <Panel key={label as string} className="p-4">
                    <p className="label">{label}</p>
                    <p className="tnum mt-1.5 text-xl font-semibold text-ink">{value}</p>
                  </Panel>
                ))}
          </div>

          <Panel>
            <PanelHeader
              title={`${METRICS.find((item) => item.value === metric)?.label} forecast`}
              subtitle={data ? data.method : undefined}
              icon={<Waypoints className="h-4 w-4" />}
              action={
                <Tabs value={horizon} onValueChange={setHorizon}>
                  <TabList className="border-0">
                    <Tab value="7d">7 days</Tab>
                    <Tab value="30d">30 days</Tab>
                    <Tab value="90d">90 days</Tab>
                  </TabList>
                </Tabs>
              }
            />
            <div className="p-4">
              {forecast.isLoading ? (
                <Skeleton className="h-[300px]" />
              ) : selected ? (
                <>
                  <div className="mb-4 flex flex-wrap gap-2">
                    <Badge className="border-ai/25 bg-ai/10 text-ai">
                      {selected.horizon_days}-day total {fmt(selected.total)}
                    </Badge>
                    <Badge>daily average {fmt(selected.daily_average)}</Badge>
                    <Badge>95% interval shown</Badge>
                  </div>
                  <ForecastChart history={data.history} forecast={selected.points} height={300} />
                </>
              ) : null}
            </div>
          </Panel>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Panel>
              <PanelHeader title="Investigation workload" subtitle="Projected analyst effort for the next 7 days" />
              <div className="p-4">
                {workload.isLoading ? (
                  <Skeleton className="h-40" />
                ) : workload.data?.status === 'INSUFFICIENT_HISTORY' ? (
                  <p className="text-xs text-muted">{workload.data.message}</p>
                ) : (
                  <>
                    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      {[
                        ['Open cases', formatNumber(workload.data.open_cases)],
                        ['Projected new cases', formatNumber(workload.data.projected_new_cases)],
                        ['Daily average', workload.data.projected_daily_average.toFixed(1)],
                        ['Analyst hours', workload.data.analyst_hours_required.toFixed(1)],
                      ].map(([label, value]) => (
                        <div key={label as string} className="rounded-lg border border-line bg-surface px-3 py-2">
                          <dt className="label">{label}</dt>
                          <dd className="tnum text-sm text-ink">{value}</dd>
                        </div>
                      ))}
                    </dl>
                    <p className="mt-3 text-2xs text-faint">
                      Assumes {workload.data.assumptions.minutes_per_case} minutes average handling time per case.
                    </p>
                  </>
                )}
              </div>
            </Panel>

            <Panel>
              <PanelHeader title="Seasonality" subtitle="Weekday multipliers learned from history" />
              <div className="p-4">
                {forecast.isLoading ? (
                  <Skeleton className="h-40" />
                ) : (
                  <ul className="space-y-1.5">
                    {Object.entries(data.model.seasonality as Record<string, number>).map(([index, value]) => {
                      const days = ['Day 1', 'Day 2', 'Day 3', 'Day 4', 'Day 5', 'Day 6', 'Day 7'];
                      return (
                        <li key={index} className="flex items-center gap-3">
                          <span className="w-16 text-2xs text-faint">{days[Number(index)]}</span>
                          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                            <span
                              className="block h-full rounded-full bg-info"
                              style={{ width: `${Math.min(value * 60, 100)}%` }}
                            />
                          </span>
                          <span className="tnum w-12 text-right text-2xs text-ink">{value.toFixed(3)}</span>
                        </li>
                      );
                    })}
                  </ul>
                )}
                <p className="mt-3 text-2xs text-faint">
                  Values above 1.0 indicate above-average activity for that position in the weekly cycle.
                </p>
              </div>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
