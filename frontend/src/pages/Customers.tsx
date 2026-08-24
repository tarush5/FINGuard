import { Users } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  EmptyState,
  ErrorState,
  Pagination,
  Panel,
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
import { formatCurrency, formatNumber } from '@/lib/format';

export default function Customers() {
  const navigate = useNavigate();
  const [riskBand, setRiskBand] = useState('');
  const [segment, setSegment] = useState('');
  const [watchlisted, setWatchlisted] = useState(false);

  const table = useTableQuery<any>('customers', '/customers', {
    pageSize: 25,
    defaultSort: 'risk_score',
    filters: {
      risk_band: riskBand || undefined,
      segment: segment || undefined,
      watchlisted: watchlisted || undefined,
    },
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Customers"
        description="Behavioural profiles and customer-level risk. PII is masked unless your role carries customer:pii_read."
      />

      <Panel>
        <div className="flex flex-col gap-3 border-b border-line p-4 sm:flex-row sm:items-center">
          <SearchInput value={table.search} onChange={table.setSearch} placeholder="Search by customer id or name" className="sm:max-w-md" />
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
          <Select
            value={segment}
            onChange={(event) => setSegment(event.target.value)}
            aria-label="Segment"
            className="sm:w-40"
            options={[
              { value: '', label: 'Any segment' },
              { value: 'RETAIL', label: 'Retail' },
              { value: 'AFFLUENT', label: 'Affluent' },
              { value: 'SME', label: 'SME' },
              { value: 'PRIVATE', label: 'Private' },
            ]}
          />
          <label className="flex cursor-pointer items-center gap-2 whitespace-nowrap rounded-lg border border-line bg-surface px-3 py-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={watchlisted}
              onChange={(event) => setWatchlisted(event.target.checked)}
              className="h-3.5 w-3.5 rounded border-line bg-surface"
            />
            Watchlisted only
          </label>
        </div>

        {table.isLoading ? (
          <TableSkeleton rows={8} cols={7} />
        ) : table.isError ? (
          <ErrorState error={table.error} onRetry={() => table.refetch()} />
        ) : table.items.length === 0 ? (
          <EmptyState title="No customers match" icon={<Users className="h-6 w-6" />} />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Customer</Th>
                  <Th sortable active={table.sortBy === 'risk_score'} direction={table.sortDir} onSort={() => table.toggleSort('risk_score')}>
                    Risk
                  </Th>
                  <Th className="hidden md:table-cell">Segment</Th>
                  <Th sortable active={table.sortBy === 'transaction_count'} direction={table.sortDir} onSort={() => table.toggleSort('transaction_count')}>
                    Transactions
                  </Th>
                  <Th sortable active={table.sortBy === 'lifetime_value'} direction={table.sortDir} onSort={() => table.toggleSort('lifetime_value')}>
                    Lifetime value
                  </Th>
                  <Th className="hidden lg:table-cell">Avg amount</Th>
                  <Th className="hidden lg:table-cell">Devices</Th>
                  <Th>Flags</Th>
                </tr>
              </thead>
              <tbody>
                {table.items.map((customer) => (
                  <Tr key={customer.id} onClick={() => navigate(`/app/customers/${customer.id}`)}>
                    <Td>
                      <span className="block text-sm text-ink">{customer.full_name}</span>
                      <span className="block font-mono text-2xs text-faint">
                        {customer.id} · {customer.email}
                      </span>
                    </Td>
                    <Td>
                      <RiskPill score={customer.risk_score} band={customer.risk_band} />
                    </Td>
                    <Td className="hidden text-xs text-muted md:table-cell">{customer.segment}</Td>
                    <Td className="tnum">{formatNumber(customer.transaction_count)}</Td>
                    <Td className="tnum whitespace-nowrap">{formatCurrency(customer.lifetime_value, 'INR', true)}</Td>
                    <Td className="tnum hidden whitespace-nowrap lg:table-cell">{formatCurrency(customer.avg_transaction_amount)}</Td>
                    <Td className="tnum hidden lg:table-cell">{customer.distinct_device_count}</Td>
                    <Td>
                      <span className="flex flex-wrap gap-1">
                        {customer.watchlisted ? (
                          <Badge className="border-critical/25 bg-critical/10 text-critical">watchlist</Badge>
                        ) : null}
                        {customer.confirmed_fraud_count > 0 ? (
                          <Badge className="border-high/25 bg-high/10 text-high">{customer.confirmed_fraud_count} fraud</Badge>
                        ) : null}
                      </span>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={table.page} pages={table.pages} total={table.total} onPage={table.setPage} />
          </>
        )}
      </Panel>
    </div>
  );
}
