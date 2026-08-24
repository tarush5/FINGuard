import { useQuery } from '@tanstack/react-query';
import { Radar, ShieldCheck } from 'lucide-react';
import { CategoryBars, Heatmap, KpiCard, TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, ErrorState, Panel, PanelHeader, ProgressBar, Select, Skeleton } from '@/components/ui';
import { api } from '@/lib/api';
import { RISK_COLORS, bandOf, formatCurrency, formatNumber, formatPercent } from '@/lib/format';
import { useUi } from '@/store/ui';

export default function FraudAnalytics() {
  const days = useUi((state) => state.windowDays);
  const setDays = useUi((state) => state.setWindowDays);

  const losses = useQuery({ queryKey: ['losses', days], queryFn: () => api.get<any>('/analytics/losses', { days }) });
  const performance = useQuery({ queryKey: ['performance', days], queryFn: () => api.get<any>('/analytics/performance', { days }) });
  const heatmap = useQuery({ queryKey: ['heatmap', days], queryFn: () => api.get<any>('/analytics/heatmap', { days }) });
  const series = useQuery({ queryKey: ['timeseries', days], queryFn: () => api.get<any>('/analytics/timeseries', { days, bucket: 'day' }) });
  const operations = useQuery({ queryKey: ['operations'], queryFn: () => api.get<any>('/analytics/operations') });

  if (losses.isError) return <ErrorState error={losses.error} onRetry={() => losses.refetch()} />;

  const currency = losses.data?.currency ?? 'INR';

  return (
    <div className="space-y-4">
      <PageHeader
        title="Fraud analytics"
        description="Loss accounting, detection performance and the temporal / categorical shape of fraud."
        actions={
          <Select
            value={String(days)}
            onChange={(event) => setDays(Number(event.target.value))}
            aria-label="Window"
            className="w-40"
            options={[
              { value: '7', label: 'Last 7 days' },
              { value: '30', label: 'Last 30 days' },
              { value: '90', label: 'Last 90 days' },
            ]}
          />
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {losses.isLoading
          ? Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-28" />)
          : [
              { label: 'Gross fraud loss', value: formatCurrency(losses.data.gross_fraud_loss, currency, true), accent: '#F87171' },
              { label: 'Prevented fraud', value: formatCurrency(losses.data.prevented_fraud, currency, true), accent: '#34D399' },
              { label: 'False positive cost', value: formatCurrency(losses.data.false_positive_cost, currency, true), accent: '#FB923C' },
              { label: 'Investigation cost', value: formatCurrency(losses.data.investigation_cost, currency, true), accent: '#FBBF24' },
              { label: 'Net loss', value: formatCurrency(losses.data.net_loss, currency, true), accent: '#A78BFA' },
            ].map((kpi) => <KpiCard key={kpi.label} label={kpi.label} value={kpi.value} accent={kpi.accent} />)}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2">
          <PanelHeader title="Fraud over time" subtitle="Fraudulent transactions and amount by day" icon={<Radar className="h-4 w-4" />} />
          <div className="p-4">
            {series.isLoading ? (
              <Skeleton className="h-[260px]" />
            ) : (
              <TrendChart
                data={series.data.points}
                xKey="bucket"
                height={260}
                series={[
                  { key: 'fraud_transactions', name: 'Fraud transactions', color: '#F87171' },
                  { key: 'declines', name: 'Declines', color: '#FB923C' },
                  { key: 'reviews', name: 'Manual reviews', color: '#FBBF24' },
                ]}
              />
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Detection performance" subtitle="Decision outcome vs ground truth" icon={<ShieldCheck className="h-4 w-4" />} />
          <div className="space-y-4 p-4">
            {performance.isLoading ? (
              <Skeleton className="h-48" />
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    ['True positives', performance.data.true_positives, 'text-positive'],
                    ['False positives', performance.data.false_positives, 'text-warning'],
                    ['False negatives', performance.data.false_negatives, 'text-critical'],
                    ['True negatives', performance.data.true_negatives, 'text-muted'],
                  ].map(([label, value, className]) => (
                    <div key={label as string} className="rounded-lg border border-line bg-surface px-3 py-2">
                      <p className="label">{label}</p>
                      <p className={`tnum text-sm ${className}`}>{formatNumber(value as number)}</p>
                    </div>
                  ))}
                </div>
                <div className="space-y-2.5">
                  {[
                    ['Precision', performance.data.precision],
                    ['Recall', performance.data.recall],
                    ['F1', performance.data.f1],
                  ].map(([label, value]) => (
                    <div key={label as string}>
                      <div className="mb-1 flex items-center justify-between text-xs">
                        <span className="text-muted">{label}</span>
                        <span className="tnum text-ink">{formatPercent((value as number) * 100, 1)}</span>
                      </div>
                      <ProgressBar
                        value={(value as number) * 100}
                        barClassName={(value as number) >= 0.6 ? 'bg-positive' : (value as number) >= 0.3 ? 'bg-warning' : 'bg-critical'}
                      />
                    </div>
                  ))}
                </div>
                <p className="text-2xs text-faint">{performance.data.note}</p>
              </>
            )}
          </div>
        </Panel>
      </div>

      <Panel>
        <PanelHeader title="Fraud heatmap" subtitle="Fraud rate by weekday and hour of day" />
        <div className="p-4">
          {heatmap.isLoading ? <Skeleton className="h-[260px]" /> : <Heatmap cells={heatmap.data.cells} maxValue={heatmap.data.max_fraud_rate} />}
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel>
          <PanelHeader title="Fraud by pattern" subtitle="Labelled fraud typology" />
          <div className="p-4">
            {losses.isLoading ? (
              <Skeleton className="h-[240px]" />
            ) : (
              <CategoryBars
                data={(losses.data.by_fraud_type ?? []).filter((row: any) => row.key !== 'UNKNOWN')}
                xKey="key"
                yKey="fraud_transactions"
                horizontal
                height={240}
                color="#F87171"
              />
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Fraud by channel" />
          <div className="p-4">
            {losses.isLoading ? (
              <Skeleton className="h-[240px]" />
            ) : (
              <CategoryBars
                data={losses.data.by_channel ?? []}
                xKey="key"
                yKey="fraud_rate"
                height={240}
                colorBy={(row) => RISK_COLORS[bandOf(Math.min(row.fraud_rate * 20, 100))]}
                formatter={(value) => `${value.toFixed(2)}%`}
              />
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Investigation outcomes" />
          <div className="space-y-3 p-4">
            {operations.isLoading ? (
              <Skeleton className="h-[240px]" />
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    ['Confirmed fraud', operations.data.confirmed_fraud_cases],
                    ['False positives', operations.data.false_positive_cases],
                    ['Resolved cases', operations.data.resolved_cases],
                    ['Past SLA', operations.data.sla_breached],
                  ].map(([label, value]) => (
                    <div key={label as string} className="rounded-lg border border-line bg-surface px-3 py-2">
                      <p className="label">{label}</p>
                      <p className="tnum text-sm text-ink">{formatNumber(value as number)}</p>
                    </div>
                  ))}
                </div>
                <div>
                  <div className="mb-1 flex justify-between text-xs">
                    <span className="text-muted">Investigation precision</span>
                    <span className="tnum text-ink">{formatPercent(operations.data.investigation_precision * 100, 1)}</span>
                  </div>
                  <ProgressBar value={operations.data.investigation_precision * 100} />
                  <p className="mt-1 text-2xs text-faint">Share of decided cases that turned out to be genuine fraud.</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(operations.data.cases_by_status ?? []).map((entry: any) => (
                    <Badge key={entry.status}>
                      {entry.status.replace(/_/g, ' ')} · {entry.count}
                    </Badge>
                  ))}
                </div>
              </>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
