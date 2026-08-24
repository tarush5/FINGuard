import { useQuery } from '@tanstack/react-query';
import { Store } from 'lucide-react';
import { useState } from 'react';
import { CategoryBars } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Drawer,
  EmptyState,
  ErrorState,
  Pagination,
  Panel,
  PanelHeader,
  SearchInput,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  Tr,
} from '@/components/ui';
import { RiskPill } from '@/components/viz/RiskOrb';
import { useTableQuery } from '@/hooks/useTableQuery';
import { api } from '@/lib/api';
import { RISK_COLORS, bandOf, formatCurrency, formatNumber, formatPercent } from '@/lib/format';

export default function Merchants() {
  const [riskBand, setRiskBand] = useState('');
  const [highRiskOnly, setHighRiskOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const table = useTableQuery<any>('merchants', '/merchants', {
    pageSize: 25,
    defaultSort: 'risk_score',
    filters: { risk_band: riskBand || undefined, high_risk_only: highRiskOnly || undefined },
  });

  const detail = useQuery({
    queryKey: ['merchant', selected],
    queryFn: () => api.get<any>(`/merchants/${selected}`),
    enabled: Boolean(selected),
  });

  const analytics = useQuery({
    queryKey: ['merchant-analytics'],
    queryFn: () => api.get<any>('/analytics/merchants', { limit: 12, days: 30 }),
  });

  return (
    <div className="space-y-4">
      <PageHeader title="Merchants" description="Merchant risk, fraud rate and transaction concentration." />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2">
          <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center">
            <SearchInput value={table.search} onChange={table.setSearch} placeholder="Search merchant name or id" className="sm:max-w-sm" />
            <Select
              value={riskBand}
              onChange={(event) => setRiskBand(event.target.value)}
              aria-label="Risk band"
              className="sm:w-40"
              options={[
                { value: '', label: 'Any risk' },
                { value: 'LOW', label: 'Low' },
                { value: 'MEDIUM', label: 'Medium' },
                { value: 'HIGH', label: 'High' },
                { value: 'CRITICAL', label: 'Critical' },
              ]}
            />
            <label className="flex cursor-pointer items-center gap-2 whitespace-nowrap rounded-lg border border-line bg-surface px-3 py-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={highRiskOnly}
                onChange={(event) => setHighRiskOnly(event.target.checked)}
                className="h-3.5 w-3.5 rounded border-line bg-surface"
              />
              High risk only
            </label>
          </div>

          {table.isLoading ? (
            <TableSkeleton rows={8} cols={6} />
          ) : table.isError ? (
            <ErrorState error={table.error} onRetry={() => table.refetch()} />
          ) : table.items.length === 0 ? (
            <EmptyState title="No merchants match" icon={<Store className="h-6 w-6" />} />
          ) : (
            <>
              <Table>
                <thead>
                  <tr>
                    <Th>Merchant</Th>
                    <Th sortable active={table.sortBy === 'risk_score'} direction={table.sortDir} onSort={() => table.toggleSort('risk_score')}>
                      Risk
                    </Th>
                    <Th sortable active={table.sortBy === 'fraud_rate'} direction={table.sortDir} onSort={() => table.toggleSort('fraud_rate')}>
                      Fraud rate
                    </Th>
                    <Th sortable active={table.sortBy === 'transaction_count'} direction={table.sortDir} onSort={() => table.toggleSort('transaction_count')}>
                      Transactions
                    </Th>
                    <Th className="hidden md:table-cell">Volume</Th>
                    <Th className="hidden lg:table-cell">Avg ticket</Th>
                  </tr>
                </thead>
                <tbody>
                  {table.items.map((merchant) => (
                    <Tr key={merchant.id} onClick={() => setSelected(merchant.id)}>
                      <Td>
                        <span className="block text-sm text-ink">{merchant.name}</span>
                        <span className="block text-2xs text-faint">
                          {merchant.category} · {merchant.city}, {merchant.country}
                        </span>
                      </Td>
                      <Td>
                        <RiskPill score={merchant.risk_score} band={merchant.risk_band} />
                      </Td>
                      <Td className="tnum">
                        <span style={{ color: RISK_COLORS[bandOf(Math.min(merchant.fraud_rate * 1000, 100))] }}>
                          {formatPercent(merchant.fraud_rate * 100, 2)}
                        </span>
                      </Td>
                      <Td className="tnum">{formatNumber(merchant.transaction_count)}</Td>
                      <Td className="tnum hidden whitespace-nowrap md:table-cell">{formatCurrency(merchant.transaction_volume, 'INR', true)}</Td>
                      <Td className="tnum hidden whitespace-nowrap lg:table-cell">{formatCurrency(merchant.avg_ticket)}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
              <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
            </>
          )}
        </Panel>

        <Panel>
          <PanelHeader title="Riskiest merchants" subtitle="Last 30 days" />
          <div className="p-3">
            <CategoryBars
              data={(analytics.data?.items ?? []).map((item: any) => ({ name: item.name, fraud_rate: item.fraud_rate }))}
              xKey="name"
              yKey="fraud_rate"
              horizontal
              height={380}
              colorBy={(row) => RISK_COLORS[bandOf(Math.min(row.fraud_rate * 10, 100))]}
              formatter={(value) => `${value.toFixed(2)}%`}
            />
          </div>
        </Panel>
      </div>

      <Drawer open={Boolean(selected)} onClose={() => setSelected(null)} title={detail.data?.merchant?.name ?? 'Merchant'} width="max-w-2xl">
        {detail.isLoading || !detail.data ? (
          <p className="text-xs text-muted">Loading…</p>
        ) : (
          <div className="space-y-5">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {[
                ['Merchant id', detail.data.merchant.id],
                ['Category', detail.data.merchant.category],
                ['MCC', detail.data.merchant.mcc],
                ['Risk score', detail.data.merchant.risk_score.toFixed(1)],
                ['Fraud rate', formatPercent(detail.data.merchant.fraud_rate * 100, 2)],
                ['Chargeback rate', formatPercent(detail.data.merchant.chargeback_rate * 100, 2)],
                ['Transactions', formatNumber(detail.data.merchant.transaction_count)],
                ['Volume', formatCurrency(detail.data.merchant.transaction_volume, 'INR', true)],
                ['Avg ticket', formatCurrency(detail.data.merchant.avg_ticket)],
              ].map(([label, value]) => (
                <div key={label as string}>
                  <dt className="label">{label}</dt>
                  <dd className="tnum text-sm text-ink">{String(value)}</dd>
                </div>
              ))}
            </dl>

            <div>
              <p className="label mb-2">Decision mix</p>
              <div className="flex flex-wrap gap-2">
                {detail.data.decision_mix.map((entry: any) => (
                  <Badge key={entry.decision}>
                    {entry.decision.replace(/_/g, ' ')} · {formatNumber(entry.count)}
                  </Badge>
                ))}
              </div>
            </div>

            <div>
              <p className="label mb-2">Top customers by volume</p>
              <ul className="space-y-1">
                {detail.data.top_customers.map((customer: any) => (
                  <li key={customer.customer_id} className="flex items-center justify-between rounded border border-line bg-surface px-2 py-1.5">
                    <span className="font-mono text-2xs text-muted">{customer.customer_id}</span>
                    <span className="tnum text-2xs text-ink">
                      {customer.transactions} txns · {formatCurrency(customer.volume, 'INR', true)}
                    </span>
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
