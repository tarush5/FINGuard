import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Network, Smartphone } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  ErrorState,
  Panel,
  PanelHeader,
  Skeleton,
  Tab,
  TabList,
  TabPanel,
  Tabs,
} from '@/components/ui';
import { NetworkGraph } from '@/components/viz/NetworkGraph';
import { RiskOrb } from '@/components/viz/RiskOrb';
import { api } from '@/lib/api';
import {
  caseStatusClass,
  decisionBgClass,
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatScore,
  relativeTime,
} from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function CustomerDetail() {
  const { customerId = '' } = useParams();
  const canGraph = useAuth((state) => state.can('graph:read'));
  const [tab, setTab] = useState('transactions');

  const detail = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () => api.get<any>(`/customers/${customerId}`),
  });

  const graph = useQuery({
    queryKey: ['customer-graph', customerId],
    queryFn: () => api.get<any>(`/graph/customer/${customerId}`, { depth: 2 }),
    enabled: canGraph && tab === 'network',
  });

  if (detail.isLoading) return <Skeleton className="h-96" />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;

  const { customer, accounts, recent_transactions: transactions, devices, cases, statistics } = detail.data;

  return (
    <div className="space-y-4">
      <PageHeader
        breadcrumb={
          <Link to="/app/customers" className="inline-flex items-center gap-1 hover:text-ink">
            <ArrowLeft className="h-3 w-3" /> Customers
          </Link>
        }
        title={customer.full_name}
        description={`${customer.id} · ${customer.segment} · onboarded ${formatDateTime(customer.onboarded_at)}`}
        actions={
          <>
            {customer.watchlisted ? <Badge className="border-critical/25 bg-critical/10 text-critical">watchlisted</Badge> : null}
            <Badge>{customer.kyc_status}</Badge>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_1fr]">
        <Panel className="flex items-center justify-center p-5">
          <RiskOrb score={customer.risk_score} band={customer.risk_band} size={148} label="Customer risk" />
        </Panel>
        <Panel className="p-5">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              ['Email', customer.email],
              ['Phone', customer.phone ?? '—'],
              ['Location', `${customer.city}, ${customer.country}`],
              ['Tenure', `${formatNumber(customer.tenure_days)} days`],
              ['Transactions', formatNumber(statistics.transactions)],
              ['Total volume', formatCurrency(statistics.total_volume, 'INR', true)],
              ['Average amount', formatCurrency(customer.avg_transaction_amount)],
              ['Max amount', formatCurrency(customer.max_transaction_amount)],
              ['Lifetime value', formatCurrency(customer.lifetime_value, 'INR', true)],
              ['Devices', formatNumber(customer.distinct_device_count)],
              ['Average risk', formatScore(statistics.average_risk)],
              ['Confirmed fraud', formatNumber(customer.confirmed_fraud_count)],
            ].map(([label, value]) => (
              <div key={label as string} className="min-w-0">
                <dt className="label">{label}</dt>
                <dd className="tnum truncate text-sm text-ink" title={String(value)}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          {customer.pii_masked ? (
            <p className="mt-4 border-t border-line pt-3 text-2xs text-faint">
              Contact details are masked for your role. Fraud investigators and admins see full values.
            </p>
          ) : null}
        </Panel>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabList>
          <Tab value="transactions" count={transactions?.length}>
            Transactions
          </Tab>
          <Tab value="devices" count={devices?.length}>
            Devices
          </Tab>
          <Tab value="accounts" count={accounts?.length}>
            Accounts
          </Tab>
          <Tab value="cases" count={cases?.length}>
            Cases
          </Tab>
          <Tab value="network">Network</Tab>
        </TabList>

        <TabPanel value="transactions" className="pt-4">
          <Panel>
            <PanelHeader title="Recent transactions" />
            <ul className="divide-y divide-line/60">
              {transactions.map((txn: any) => (
                <li key={txn.id}>
                  <Link to={`/app/transactions/${txn.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-raised/50">
                    <span className="tnum w-28 shrink-0 text-sm text-ink">{formatCurrency(txn.amount, txn.currency)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-2xs text-muted">{txn.id}</span>
                      <span className="block text-[10px] text-faint">
                        {formatDateTime(txn.occurred_at)} · {txn.merchant_id} · {txn.channel}
                      </span>
                    </span>
                    <span className="tnum text-xs text-muted">{formatScore(txn.risk_score)}</span>
                    <Badge className={cn(decisionBgClass(txn.decision))}>{txn.decision.replace(/_/g, ' ')}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        </TabPanel>

        <TabPanel value="devices" className="pt-4">
          <Panel>
            <PanelHeader title="Devices" subtitle="Shared devices are the strongest structural fraud signal" icon={<Smartphone className="h-4 w-4" />} />
            <ul className="divide-y divide-line/60">
              {devices.map((device: any) => (
                <li key={device.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <span className="font-mono text-xs text-ink">{device.id}</span>
                  <span className="text-2xs text-muted">
                    {device.device_type} · {device.os}
                  </span>
                  <span className="text-2xs text-faint">
                    {device.transactions_on_device} txns · first seen {relativeTime(device.first_seen_for_customer)}
                  </span>
                  {device.shared_with_others > 0 ? (
                    <Badge className="border-critical/25 bg-critical/10 text-critical">
                      shared with {device.shared_with_others} other account(s)
                    </Badge>
                  ) : null}
                  {device.is_blacklisted ? <Badge className="border-critical/25 bg-critical/10 text-critical">blacklisted</Badge> : null}
                </li>
              ))}
            </ul>
          </Panel>
        </TabPanel>

        <TabPanel value="accounts" className="pt-4">
          <Panel>
            <PanelHeader title="Accounts" />
            <ul className="divide-y divide-line/60">
              {accounts.map((account: any) => (
                <li key={account.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                  <span>
                    <span className="block text-sm text-ink">
                      {account.account_type} {account.masked_number}
                    </span>
                    <span className="block font-mono text-2xs text-faint">{account.id}</span>
                  </span>
                  <span className="tnum text-sm text-muted">
                    balance {formatCurrency(account.balance, account.currency, true)} · limit{' '}
                    {formatCurrency(account.credit_limit, account.currency, true)}
                  </span>
                  <Badge>{account.status}</Badge>
                </li>
              ))}
            </ul>
          </Panel>
        </TabPanel>

        <TabPanel value="cases" className="pt-4">
          <Panel>
            <PanelHeader title="Cases" />
            {cases.length === 0 ? (
              <p className="px-5 py-8 text-center text-xs text-faint">No cases have been opened for this customer.</p>
            ) : (
              <ul className="divide-y divide-line/60">
                {cases.map((item: any) => (
                  <li key={item.id}>
                    <Link to={`/app/cases/${item.id}`} className="flex items-center justify-between px-5 py-3 hover:bg-raised/50">
                      <span className="font-mono text-xs text-info">{item.case_number}</span>
                      <span className="tnum text-xs text-muted">risk {formatScore(item.risk_score)}</span>
                      <Badge className={cn(caseStatusClass(item.status))}>{item.status.replace(/_/g, ' ')}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </TabPanel>

        <TabPanel value="network" className="pt-4">
          <Panel>
            <PanelHeader title="Entity network" icon={<Network className="h-4 w-4" />} />
            <div className="p-4">
              {graph.isLoading ? (
                <Skeleton className="h-[460px]" />
              ) : graph.isError ? (
                <ErrorState error={graph.error} />
              ) : (
                <NetworkGraph nodes={graph.data?.nodes ?? []} edges={graph.data?.edges ?? []} />
              )}
            </div>
          </Panel>
        </TabPanel>
      </Tabs>
    </div>
  );
}
