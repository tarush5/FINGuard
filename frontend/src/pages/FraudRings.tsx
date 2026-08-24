import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Network, Radar } from 'lucide-react';
import { useState } from 'react';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Panel,
  PanelHeader,
  Skeleton,
  useToast,
} from '@/components/ui';
import { NetworkGraph } from '@/components/viz/NetworkGraph';
import { RiskPill } from '@/components/viz/RiskOrb';
import { api } from '@/lib/api';
import { formatCurrency, formatDateTime, formatNumber, formatPercent } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function FraudRings() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [selected, setSelected] = useState<string | null>(null);

  const rings = useQuery({ queryKey: ['rings'], queryFn: () => api.get<any>('/fraud-rings', { page_size: 50 }) });
  const summary = useQuery({ queryKey: ['graph-summary'], queryFn: () => api.get<any>('/graph/summary') });
  const detail = useQuery({
    queryKey: ['ring', selected],
    queryFn: () => api.get<any>(`/fraud-rings/${selected}`),
    enabled: Boolean(selected),
  });

  const detect = useMutation({
    mutationFn: () => api.post<any>('/fraud-rings/detect', undefined, { min_members: 3, days: 90 }),
    onSuccess: (result) => {
      push({ title: `Ring detection complete`, description: `${result.detected} cluster(s) above the risk floor.`, variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['rings'] });
      queryClient.invalidateQueries({ queryKey: ['graph-summary'] });
    },
  });

  const items = rings.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Fraud rings"
        description="Connected components of the customer graph induced by shared devices and IP addresses, scored on size, density, contamination and value."
        actions={
          <Button variant="primary" icon={<Radar className="h-3.5 w-3.5" />} loading={detect.isPending} onClick={() => detect.mutate()}>
            Run detection
          </Button>
        }
      />

      {summary.data ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[
            ['Rings detected', formatNumber(summary.data.rings)],
            ['Ring members', formatNumber(summary.data.ring_members)],
            ['Shared devices', formatNumber(summary.data.shared_devices)],
            ['Blacklisted devices', formatNumber(summary.data.blacklisted_devices)],
            ['Ring exposure', formatCurrency(summary.data.ring_exposure, 'INR', true)],
            ['Highest ring risk', summary.data.highest_ring_risk.toFixed(1)],
          ].map(([label, value]) => (
            <Panel key={label} className="p-3">
              <p className="label">{label}</p>
              <p className="tnum mt-1 text-lg font-semibold text-ink">{value}</p>
            </Panel>
          ))}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[380px_1fr]">
        <Panel className="max-h-[720px] overflow-y-auto">
          <PanelHeader title="Detected clusters" subtitle={`${items.length} ring(s)`} icon={<Network className="h-4 w-4" />} />
          {rings.isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-20" />
              ))}
            </div>
          ) : rings.isError ? (
            <ErrorState error={rings.error} onRetry={() => rings.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              title="No rings detected"
              description="Run detection, or generate ring activity with the fraud ring demo scenario."
              icon={<Network className="h-6 w-6" />}
            />
          ) : (
            <ul className="divide-y divide-line/60">
              {items.map((ring: any) => (
                <li key={ring.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(ring.id)}
                    className={cn(
                      'w-full px-4 py-3 text-left transition-colors hover:bg-raised/50',
                      selected === ring.id && 'bg-info/[0.06]',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm text-ink">{ring.label}</p>
                        <p className="mt-0.5 text-2xs text-faint">
                          {ring.detection_method.replace(/_/g, ' ').toLowerCase()} · {formatDateTime(ring.detected_at)}
                        </p>
                      </div>
                      <RiskPill score={ring.risk_score} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Badge>{ring.member_count} accounts</Badge>
                      <Badge>{formatNumber(ring.transaction_count)} txns</Badge>
                      <Badge>{formatCurrency(ring.total_amount, 'INR', true)}</Badge>
                      <Badge className="border-critical/25 bg-critical/10 text-critical">
                        p(fraud) {formatPercent(ring.fraud_probability * 100, 0)}
                      </Badge>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel>
          <PanelHeader
            title={detail.data ? detail.data.ring.label : 'Ring network'}
            subtitle={detail.data ? `${detail.data.ring.member_count} members · density ${detail.data.ring.density}` : 'Select a ring to inspect its network'}
          />
          <div className="p-4">
            {!selected ? (
              <EmptyState title="No ring selected" description="Choose a cluster on the left to view its network and evidence." />
            ) : detail.isLoading || !detail.data ? (
              <Skeleton className="h-[460px]" />
            ) : detail.isError ? (
              <ErrorState error={detail.error} />
            ) : (
              <>
                <NetworkGraph nodes={detail.data.network?.nodes ?? []} edges={detail.data.network?.edges ?? []} height={420} />

                <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
                  <div>
                    <p className="label mb-2">Members</p>
                    <ul className="space-y-1">
                      {detail.data.ring.members.map((member: any) => (
                        <li key={member.entity_id} className="flex items-center justify-between rounded border border-line bg-surface px-2 py-1.5">
                          <span className="font-mono text-2xs text-muted">{member.entity_id}</span>
                          <span className="tnum text-2xs text-faint">centrality {member.centrality.toFixed(2)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <p className="label mb-2">Shared infrastructure</p>
                    <div className="space-y-2">
                      <div>
                        <p className="text-2xs text-faint">Devices</p>
                        {(detail.data.ring.shared_devices ?? []).length ? (
                          detail.data.ring.shared_devices.map((device: string) => (
                            <p key={device} className="font-mono text-2xs text-ink">
                              {device}
                            </p>
                          ))
                        ) : (
                          <p className="text-2xs text-muted">none</p>
                        )}
                      </div>
                      <div>
                        <p className="text-2xs text-faint">IP addresses</p>
                        {(detail.data.ring.shared_ips ?? []).length ? (
                          detail.data.ring.shared_ips.map((ip: string) => (
                            <p key={ip} className="font-mono text-2xs text-ink">
                              {ip}
                            </p>
                          ))
                        ) : (
                          <p className="text-2xs text-muted">none</p>
                        )}
                      </div>
                    </div>
                  </div>
                  <div>
                    <p className="label mb-2">Evidence</p>
                    <dl className="space-y-1.5 text-xs">
                      <div className="flex justify-between">
                        <dt className="text-muted">Fraud transactions</dt>
                        <dd className="tnum text-ink">{detail.data.ring.evidence?.fraud_transactions ?? 0}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted">Avg transaction risk</dt>
                        <dd className="tnum text-ink">{detail.data.ring.evidence?.avg_transaction_risk ?? 0}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted">Total amount</dt>
                        <dd className="tnum text-ink">{formatCurrency(detail.data.ring.total_amount, 'INR', true)}</dd>
                      </div>
                      <div className="flex justify-between">
                        <dt className="text-muted">Shared merchants</dt>
                        <dd className="tnum text-ink">{(detail.data.ring.shared_merchants ?? []).length}</dd>
                      </div>
                    </dl>
                  </div>
                </div>
              </>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
