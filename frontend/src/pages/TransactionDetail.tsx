import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, Brain, MapPin, Network, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { FactorBars } from '@/components/viz/DecisionTrace';
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
} from '@/components/ui';
import { DecisionTrace } from '@/components/viz/DecisionTrace';
import { NetworkGraph } from '@/components/viz/NetworkGraph';
import { RiskOrb } from '@/components/viz/RiskOrb';
import { api } from '@/lib/api';
import {
  decisionBgClass,
  formatCurrency,
  formatDateTime,
  formatDuration,
  formatScore,
} from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function TransactionDetail() {
  const { transactionId = '' } = useParams();
  const can = useAuth((state) => state.can);
  const [tab, setTab] = useState('trace');
  const [aiOpen, setAiOpen] = useState(false);

  const detail = useQuery({
    queryKey: ['transaction', transactionId],
    queryFn: () => api.get<any>(`/transactions/${transactionId}`),
  });

  const trace = useQuery({
    queryKey: ['transaction-trace', transactionId],
    queryFn: () => api.get<any>(`/transactions/${transactionId}/trace`),
    enabled: can('risk:read'),
  });

  const explanation = useQuery({
    queryKey: ['transaction-explain', transactionId],
    queryFn: () => api.get<any>(`/transactions/${transactionId}/explain`),
    enabled: can('risk:read') && tab === 'model',
  });

  const graph = useQuery({
    queryKey: ['transaction-graph', detail.data?.transaction?.customer_id],
    queryFn: () => api.get<any>(`/graph/customer/${detail.data.transaction.customer_id}`, { depth: 2 }),
    enabled: Boolean(detail.data?.transaction?.customer_id) && can('graph:read') && tab === 'network',
  });

  const txn = detail.data?.transaction;

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;

  return (
    <div className="space-y-4">
      <PageHeader
        breadcrumb={
          <Link to="/app/transactions" className="inline-flex items-center gap-1 hover:text-ink">
            <ArrowLeft className="h-3 w-3" /> Transactions
          </Link>
        }
        title={txn.id}
        description={`${formatDateTime(txn.occurred_at)} · ${txn.channel} · ${txn.payment_method}`}
        actions={
          <>
            <Badge className={cn('h-7 px-2.5 text-xs', decisionBgClass(txn.decision))}>{txn.decision.replace(/_/g, ' ')}</Badge>
            {can('ai:query') ? (
              <Button variant="ai" icon={<Sparkles className="h-3.5 w-3.5" />} onClick={() => setAiOpen(true)}>
                Ask AI
              </Button>
            ) : null}
          </>
        }
      />

      {/* Summary strip */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_1fr]">
        <Panel className="flex items-center justify-center p-5">
          <RiskOrb score={txn.risk_score} band={txn.risk_band} size={148} label="Ensemble risk" />
        </Panel>

        <Panel className="p-5">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {[
              ['Amount', formatCurrency(txn.amount, txn.currency)],
              ['Customer', txn.customer_id],
              ['Merchant', txn.merchant_id],
              ['Device', txn.device_id ?? 'unknown'],
              ['Location', `${txn.city || '—'}, ${txn.country}`],
              ['IP address', txn.ip_address ?? '—'],
              ['Fraud probability', formatScore(txn.fraud_probability, 4)],
              ['Anomaly score', formatScore(txn.anomaly_score, 4)],
              ['Graph risk', formatScore(txn.graph_risk, 4)],
              ['Rule score', formatScore(txn.rule_score, 1)],
              ['Model version', txn.model_version ?? '—'],
              ['Decision latency', formatDuration(txn.processing_ms)],
            ].map(([label, value]) => (
              <div key={label as string} className="min-w-0">
                <dt className="label">{label}</dt>
                <dd className="tnum mt-1 truncate text-sm text-ink" title={String(value)}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>

          <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">
            <Link to={`/app/customers/${txn.customer_id}`} className="link text-xs">
              Customer 360 →
            </Link>
            <span className="text-faint">·</span>
            <Link to={`/app/merchants/${txn.merchant_id}`} className="link text-xs">
              Merchant profile →
            </Link>
            {txn.is_fraud !== null && txn.is_fraud !== undefined ? (
              <>
                <span className="text-faint">·</span>
                <Badge className={txn.is_fraud ? 'border-critical/25 bg-critical/10 text-critical' : 'border-positive/25 bg-positive/10 text-positive'}>
                  labelled {txn.is_fraud ? 'fraud' : 'legitimate'} ({txn.label_source})
                </Badge>
              </>
            ) : null}
          </div>
        </Panel>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabList>
          <Tab value="trace">Decision trace</Tab>
          <Tab value="features">Features</Tab>
          <Tab value="model">Model explanation</Tab>
          <Tab value="network">Network</Tab>
        </TabList>

        <TabPanel value="trace" className="pt-4">
          {trace.isLoading || !trace.data ? (
            <Skeleton className="h-96" />
          ) : trace.isError ? (
            <ErrorState error={trace.error} />
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]">
              <Panel className="p-5">
                <DecisionTrace stages={trace.data.stages} />
              </Panel>
              <div className="space-y-4">
                <Panel>
                  <PanelHeader title="Score composition" subtitle="Weighted contribution of each signal" />
                  <div className="p-4">
                    <FactorBars factors={trace.data.risk?.top_factors ?? []} valueKey="points" />
                  </div>
                </Panel>
                <Panel>
                  <PanelHeader title="Stage latency" subtitle="Measured on this transaction" />
                  <ul className="divide-y divide-line/60">
                    {Object.entries(trace.data.latency ?? {}).map(([key, value]) => (
                      <li key={key} className="flex items-center justify-between px-4 py-2 text-xs">
                        <span className="text-muted">{key.replace(/_/g, ' ')}</span>
                        <span className="tnum text-ink">{formatDuration(Number(value))}</span>
                      </li>
                    ))}
                  </ul>
                </Panel>
              </div>
            </div>
          )}
        </TabPanel>

        <TabPanel value="features" className="pt-4">
          <Panel>
            <PanelHeader
              title="Feature vector"
              subtitle="Point-in-time values used to score this transaction"
              icon={<MapPin className="h-4 w-4" />}
            />
            <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 p-5 sm:grid-cols-3 lg:grid-cols-4">
              {Object.entries(detail.data.feature_vector ?? {}).map(([key, value]) => (
                <div key={key} className="border-b border-line/40 pb-2">
                  <p className="text-2xs text-faint">{key.replace(/_/g, ' ')}</p>
                  <p className="tnum text-sm text-ink">
                    {typeof value === 'number' ? (Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4)) : String(value)}
                  </p>
                </div>
              ))}
            </div>
          </Panel>
        </TabPanel>

        <TabPanel value="model" className="pt-4">
          {explanation.isLoading || !explanation.data ? (
            <Skeleton className="h-72" />
          ) : explanation.isError ? (
            <ErrorState error={explanation.error} />
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel>
                <PanelHeader
                  title="Model attribution"
                  subtitle={`${explanation.data.model_version} · method ${explanation.data.explanation?.method ?? 'n/a'}`}
                  icon={<Brain className="h-4 w-4" />}
                />
                <div className="p-4">
                  <FactorBars factors={explanation.data.explanation?.top_factors ?? []} />
                  <p className="mt-4 text-2xs text-faint">
                    {explanation.data.explanation?.method === 'shap'
                      ? 'Exact additive attributions from Tree SHAP; positive values push the probability up.'
                      : 'Fallback attribution (importance weighted by deviation from the training baseline).'}
                  </p>
                </div>
              </Panel>
              <Panel>
                <PanelHeader title="Ensemble factors" subtitle="How the final score was assembled" />
                <div className="p-4">
                  <FactorBars factors={explanation.data.ensemble_factors ?? []} valueKey="points" />
                  <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4">
                    {Object.entries(explanation.data.components ?? {}).map(([key, value]) => (
                      <div key={key}>
                        <dt className="text-2xs text-faint">{key.replace(/_/g, ' ')}</dt>
                        <dd className="tnum text-sm text-ink">{formatScore(Number(value), 4)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              </Panel>
            </div>
          )}
        </TabPanel>

        <TabPanel value="network" className="pt-4">
          <Panel>
            <PanelHeader
              title="Entity network"
              subtitle="Customer, device, IP and merchant neighbourhood"
              icon={<Network className="h-4 w-4" />}
            />
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

      <AiDrawer open={aiOpen} onClose={() => setAiOpen(false)} transactionId={transactionId} />
    </div>
  );
}

function AiDrawer({ open, onClose, transactionId }: { open: boolean; onClose: () => void; transactionId: string }) {
  const [question, setQuestion] = useState('Why was this transaction flagged?');
  const ask = useMutation({
    mutationFn: (value: string) => api.post<any>('/ai/ask', { question: value, transaction_id: transactionId }),
  });

  return (
    <Drawer open={open} onClose={onClose} title="AI investigator" width="max-w-2xl">
      <div className="space-y-4">
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') ask.mutate(question);
            }}
            className="h-9 flex-1 rounded-lg border border-line bg-surface px-3 text-sm text-ink"
            placeholder="Ask about this transaction…"
            aria-label="Question for the AI investigator"
          />
          <Button variant="ai" loading={ask.isPending} onClick={() => ask.mutate(question)}>
            Ask
          </Button>
        </div>

        {ask.isError ? <ErrorState error={ask.error} /> : null}

        {ask.data ? (
          <>
            <div className="rounded-lg border border-ai/25 bg-ai/[0.05] p-4">
              <div className="mb-2 flex items-center gap-2">
                <Sparkles className="h-3.5 w-3.5 text-ai" />
                <span className="text-2xs uppercase tracking-wide text-ai">
                  {ask.data.generated_by === 'llm' ? 'Model narration over retrieved evidence' : 'Deterministic narration'}
                </span>
              </div>
              <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink">{ask.data.answer}</pre>
            </div>

            <div>
              <p className="label mb-2">Evidence ({ask.data.evidence?.length ?? 0} items retrieved from the database)</p>
              <ul className="space-y-1.5">
                {(ask.data.evidence ?? []).map((item: any, index: number) => (
                  <li key={index} className="rounded-lg border border-line bg-surface px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-2xs uppercase tracking-wide text-faint">{item.kind}</span>
                      <span className="font-mono text-[10px] text-faint">{item.source}</span>
                    </div>
                    <p className="mt-1 text-xs text-ink">{item.statement}</p>
                  </li>
                ))}
              </ul>
            </div>

            <p className="text-2xs text-faint">{ask.data.disclaimer}</p>
          </>
        ) : (
          <p className="text-xs text-muted">
            The assistant retrieves evidence from the platform database first, then explains it. It never invents
            transactions, scores or rules.
          </p>
        )}
      </div>
    </Drawer>
  );
}
