import { useQuery } from '@tanstack/react-query';
import { BarChart3 } from 'lucide-react';
import { CategoryBars, DonutChart, KpiCard, TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import { ErrorState, Panel, PanelHeader, Select, Skeleton } from '@/components/ui';
import { api } from '@/lib/api';
import { CHART_COLORS, formatCurrency, formatMetric, formatNumber } from '@/lib/format';
import { useUi } from '@/store/ui';

export default function FinancialAnalytics() {
  const days = useUi((state) => state.windowDays);
  const setDays = useUi((state) => state.setWindowDays);

  const overview = useQuery({ queryKey: ['overview', days], queryFn: () => api.get<any>('/analytics/overview', { days }) });
  const series = useQuery({ queryKey: ['timeseries', days], queryFn: () => api.get<any>('/analytics/timeseries', { days, bucket: 'day' }) });
  const channels = useQuery({ queryKey: ['breakdown', 'channel', days], queryFn: () => api.get<any>('/analytics/breakdown/channel', { days }) });
  const methods = useQuery({ queryKey: ['breakdown', 'payment_method', days], queryFn: () => api.get<any>('/analytics/breakdown/payment_method', { days }) });
  const categories = useQuery({ queryKey: ['breakdown', 'merchant_category', days], queryFn: () => api.get<any>('/analytics/breakdown/merchant_category', { days, limit: 12 }) });
  const countries = useQuery({ queryKey: ['breakdown', 'country', days], queryFn: () => api.get<any>('/analytics/breakdown/country', { days, limit: 10 }) });
  const customers = useQuery({ queryKey: ['customer-analytics'], queryFn: () => api.get<any>('/analytics/customers', { limit: 10 }) });

  const currency = overview.data?.currency ?? 'INR';

  if (overview.isError) return <ErrorState error={overview.error} onRetry={() => overview.refetch()} />;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Financial analytics"
        description="Volume, value and behaviour across channels, payment methods, categories and geographies."
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

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {overview.isLoading
          ? Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-28" />)
          : overview.data.kpis
              .filter((kpi: any) => ['transactions', 'volume', 'fraud_amount', 'prevented_loss'].includes(kpi.key))
              .map((kpi: any) => (
                <KpiCard
                  key={kpi.key}
                  label={kpi.label}
                  value={formatMetric(kpi.value, kpi.format, currency)}
                  changePct={kpi.change_pct}
                  comparison={kpi.comparison}
                  invertTrend={kpi.invert_trend}
                />
              ))}
      </div>

      <Panel>
        <PanelHeader
          title="Volume and value"
          subtitle={`Average transaction ${formatCurrency(overview.data?.current?.average_amount ?? 0, currency)}`}
          icon={<BarChart3 className="h-4 w-4" />}
        />
        <div className="p-4">
          {series.isLoading ? (
            <Skeleton className="h-[260px]" />
          ) : (
            <TrendChart
              data={series.data.points}
              xKey="bucket"
              height={280}
              series={[
                { key: 'volume', name: 'Volume', color: '#38BDF8' },
                { key: 'fraud_amount', name: 'Fraud amount', color: '#F87171' },
              ]}
              formatter={(value) => formatCurrency(value, currency, true)}
            />
          )}
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel>
          <PanelHeader title="Channel mix" />
          <div className="p-4">
            {channels.isLoading ? (
              <Skeleton className="h-[220px]" />
            ) : (
              <DonutChart
                data={channels.data.items}
                nameKey="key"
                valueKey="transactions"
                centerValue={formatNumber(channels.data.items.reduce((sum: number, item: any) => sum + item.transactions, 0))}
                centerLabel="transactions"
              />
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Payment methods" />
          <div className="p-4">
            {methods.isLoading ? (
              <Skeleton className="h-[220px]" />
            ) : (
              <CategoryBars data={methods.data.items} xKey="key" yKey="volume" height={220} formatter={(value) => formatCurrency(value, currency, true)} />
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Geography" subtitle="Transaction volume by country" />
          <div className="p-4">
            {countries.isLoading ? (
              <Skeleton className="h-[220px]" />
            ) : (
              <CategoryBars
                data={countries.data.items}
                xKey="key"
                yKey="transactions"
                horizontal
                height={220}
                color={CHART_COLORS[1]}
              />
            )}
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel>
          <PanelHeader title="Merchant categories" subtitle="Volume and fraud rate by category" />
          <div className="p-4">
            {categories.isLoading ? (
              <Skeleton className="h-[300px]" />
            ) : (
              <CategoryBars data={categories.data.items} xKey="key" yKey="volume" height={300} formatter={(value) => formatCurrency(value, currency, true)} />
            )}
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Customer value and risk" />
          <div className="grid gap-4 p-4 sm:grid-cols-2">
            <div>
              <p className="label mb-2">Risk distribution</p>
              <ul className="space-y-1.5">
                {(customers.data?.risk_distribution ?? []).map((band: any) => (
                  <li key={band.band} className="flex items-center justify-between rounded border border-line bg-surface px-2.5 py-1.5 text-xs">
                    <span className="text-muted">{band.band}</span>
                    <span className="tnum text-ink">
                      {formatNumber(band.customers)}
                      <span className="ml-2 text-faint">{formatCurrency(band.average_lifetime_value, currency, true)} avg LTV</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="label mb-2">Most valuable customers</p>
              <ul className="space-y-1.5">
                {(customers.data?.most_valuable ?? []).slice(0, 6).map((customer: any) => (
                  <li key={customer.id} className="flex items-center justify-between rounded border border-line bg-surface px-2.5 py-1.5 text-xs">
                    <span className="truncate text-muted">{customer.name}</span>
                    <span className="tnum text-ink">{formatCurrency(customer.lifetime_value, currency, true)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
