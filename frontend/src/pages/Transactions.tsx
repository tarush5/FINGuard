import { Activity, Download, Filter, X } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
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
import { decisionBgClass, formatCurrency, formatDuration, formatTime, relativeTime } from '@/lib/format';
import { cn, downloadJson } from '@/lib/utils';

export default function Transactions() {
  const navigate = useNavigate();
  const [showFilters, setShowFilters] = useState(false);
  const [decision, setDecision] = useState('');
  const [riskBand, setRiskBand] = useState('');
  const [channel, setChannel] = useState('');
  const [paymentMethod, setPaymentMethod] = useState('');
  const [minAmount, setMinAmount] = useState('');
  const [minRisk, setMinRisk] = useState('');
  const [days, setDays] = useState('30');

  const table = useTableQuery<any>('transactions', '/transactions', {
    pageSize: 25,
    defaultSort: 'occurred_at',
    filters: {
      decision: decision || undefined,
      risk_band: riskBand || undefined,
      channel: channel || undefined,
      payment_method: paymentMethod || undefined,
      min_amount: minAmount ? Number(minAmount) : undefined,
      min_risk: minRisk ? Number(minRisk) : undefined,
      days: Number(days),
    },
  });

  const activeFilters = [decision, riskBand, channel, paymentMethod, minAmount, minRisk].filter(Boolean).length;

  const clearFilters = () => {
    setDecision('');
    setRiskBand('');
    setChannel('');
    setPaymentMethod('');
    setMinAmount('');
    setMinRisk('');
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Transactions"
        description="Every scored transaction with its decision, risk breakdown and processing latency."
        actions={
          <>
            <Button
              variant={showFilters ? 'primary' : 'outline'}
              icon={<Filter className="h-3.5 w-3.5" />}
              onClick={() => setShowFilters((value) => !value)}
            >
              Filters
              {activeFilters ? <span className="ml-1 rounded bg-void/20 px-1 text-2xs">{activeFilters}</span> : null}
            </Button>
            <Button
              variant="ghost"
              icon={<Download className="h-3.5 w-3.5" />}
              onClick={() => downloadJson(table.items, `finguard-transactions-page-${table.page}.json`)}
              disabled={!table.items.length}
            >
              Export page
            </Button>
          </>
        }
      />

      <Panel>
        <div className="flex flex-col gap-3 border-b border-line p-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <SearchInput
              value={table.search}
              onChange={table.setSearch}
              placeholder="Search by transaction, customer, merchant, device or session id"
              className="sm:max-w-lg"
            />
            <Select
              value={days}
              onChange={(event) => setDays(event.target.value)}
              aria-label="Time window"
              className="sm:w-40"
              options={[
                { value: '1', label: 'Last 24 hours' },
                { value: '7', label: 'Last 7 days' },
                { value: '30', label: 'Last 30 days' },
                { value: '90', label: 'Last 90 days' },
                { value: '365', label: 'Last year' },
              ]}
            />
          </div>

          {showFilters ? (
            <div className="grid grid-cols-2 gap-3 rounded-lg border border-line bg-surface p-3 md:grid-cols-3 lg:grid-cols-6">
              <Select
                label="Decision"
                value={decision}
                onChange={(event) => setDecision(event.target.value)}
                options={[
                  { value: '', label: 'Any' },
                  { value: 'APPROVE', label: 'Approve' },
                  { value: 'STEP_UP', label: 'Step up' },
                  { value: 'MANUAL_REVIEW', label: 'Manual review' },
                  { value: 'DECLINE', label: 'Decline' },
                ]}
              />
              <Select
                label="Risk band"
                value={riskBand}
                onChange={(event) => setRiskBand(event.target.value)}
                options={[
                  { value: '', label: 'Any' },
                  { value: 'LOW', label: 'Low' },
                  { value: 'MEDIUM', label: 'Medium' },
                  { value: 'HIGH', label: 'High' },
                  { value: 'CRITICAL', label: 'Critical' },
                ]}
              />
              <Select
                label="Channel"
                value={channel}
                onChange={(event) => setChannel(event.target.value)}
                options={[
                  { value: '', label: 'Any' },
                  { value: 'WEB', label: 'Web' },
                  { value: 'MOBILE_APP', label: 'Mobile app' },
                  { value: 'POS', label: 'POS' },
                  { value: 'API', label: 'API' },
                ]}
              />
              <Select
                label="Payment method"
                value={paymentMethod}
                onChange={(event) => setPaymentMethod(event.target.value)}
                options={[
                  { value: '', label: 'Any' },
                  { value: 'CARD', label: 'Card' },
                  { value: 'UPI', label: 'UPI' },
                  { value: 'NETBANKING', label: 'Net banking' },
                  { value: 'WALLET', label: 'Wallet' },
                ]}
              />
              <label className="block">
                <span className="label mb-1.5 block">Min amount</span>
                <input
                  type="number"
                  value={minAmount}
                  onChange={(event) => setMinAmount(event.target.value)}
                  placeholder="0"
                  className="h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink"
                />
              </label>
              <label className="block">
                <span className="label mb-1.5 block">Min risk</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={minRisk}
                  onChange={(event) => setMinRisk(event.target.value)}
                  placeholder="0"
                  className="h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink"
                />
              </label>
              {activeFilters ? (
                <div className="col-span-full">
                  <Button size="sm" variant="ghost" icon={<X className="h-3 w-3" />} onClick={clearFilters}>
                    Clear filters
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {table.isLoading ? (
          <TableSkeleton rows={8} cols={7} />
        ) : table.isError ? (
          <ErrorState error={table.error} onRetry={() => table.refetch()} />
        ) : table.items.length === 0 ? (
          <EmptyState
            title="No transactions match these filters"
            description="Widen the time window or clear the filters."
            icon={<Activity className="h-6 w-6" />}
            action={
              activeFilters ? (
                <Button size="sm" onClick={clearFilters}>
                  Clear filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th sortable active={table.sortBy === 'occurred_at'} direction={table.sortDir} onSort={() => table.toggleSort('occurred_at')}>
                    Time
                  </Th>
                  <Th>Transaction</Th>
                  <Th sortable active={table.sortBy === 'amount'} direction={table.sortDir} onSort={() => table.toggleSort('amount')}>
                    Amount
                  </Th>
                  <Th className="hidden md:table-cell">Merchant</Th>
                  <Th className="hidden lg:table-cell">Channel</Th>
                  <Th sortable active={table.sortBy === 'risk_score'} direction={table.sortDir} onSort={() => table.toggleSort('risk_score')}>
                    Risk
                  </Th>
                  <Th>Decision</Th>
                  <Th className="hidden xl:table-cell">Latency</Th>
                </tr>
              </thead>
              <tbody>
                {table.items.map((txn) => (
                  <Tr key={txn.id} onClick={() => navigate(`/app/transactions/${txn.id}`)}>
                    <Td className="whitespace-nowrap">
                      <span className="tnum font-mono text-2xs text-muted">{formatTime(txn.occurred_at)}</span>
                      <span className="block text-[10px] text-faint">{relativeTime(txn.occurred_at)}</span>
                    </Td>
                    <Td>
                      <span className="font-mono text-xs text-ink">{txn.id}</span>
                      <span className="block text-[10px] text-faint">{txn.customer_id}</span>
                    </Td>
                    <Td className="tnum whitespace-nowrap font-medium">{formatCurrency(txn.amount, txn.currency)}</Td>
                    <Td className="hidden md:table-cell">
                      <span className="text-xs text-muted">{txn.merchant_id}</span>
                      <span className="block text-[10px] text-faint">{txn.merchant_category}</span>
                    </Td>
                    <Td className="hidden lg:table-cell">
                      <span className="text-xs text-muted">{txn.channel}</span>
                      <span className="block text-[10px] text-faint">{txn.payment_method}</span>
                    </Td>
                    <Td>
                      <RiskPill score={txn.risk_score} band={txn.risk_band} />
                    </Td>
                    <Td>
                      <span className="flex items-center gap-2">
                        <Badge className={cn(decisionBgClass(txn.decision))}>{txn.decision.replace(/_/g, ' ')}</Badge>
                        {txn.is_fraud ? <Badge className="border-critical/25 bg-critical/10 text-critical">fraud</Badge> : null}
                      </span>
                    </Td>
                    <Td className="tnum hidden text-2xs text-faint xl:table-cell">{formatDuration(txn.processing_ms)}</Td>
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
