/**
 * AI Investigator workspace.
 *
 * Two surfaces: an evidence-grounded assistant for a specific transaction or
 * case, and natural-language analytics that shows the generated SQL before the
 * results. The guardrails are displayed alongside, because "the model wrote the
 * query" is only acceptable if the reader can see what was executed.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Brain, Code2, Database, Send, Shield, Sparkles, Table2 } from 'lucide-react';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CategoryBars, TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  ErrorState,
  Panel,
  PanelHeader,
  Skeleton,
  Tab,
  TabList,
  TabPanel,
  Tabs,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatDuration, formatNumber } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

const SUGGESTED_QUESTIONS = [
  'Which merchants have the highest fraud rate?',
  'Show the fraud trend over time',
  'Which rules fire most often?',
  'Shared devices across accounts',
  'Decision breakdown',
  'Largest transactions by amount',
  'Riskiest customers',
  'Open cases queue',
];

export default function AIInvestigator() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState(params.get('tab') === 'sql' ? 'sql' : 'investigate');
  const canSql = useAuth((state) => state.can('ai:sql'));

  const status = useQuery({ queryKey: ['ai-status'], queryFn: () => api.get<any>('/ai/status') });

  return (
    <div className="space-y-4">
      <PageHeader
        title="AI investigator"
        description="Evidence is retrieved from the platform database first; the assistant only explains what it retrieved."
        actions={
          status.data ? (
            <Badge className={cn(status.data.available ? 'border-ai/25 bg-ai/10 text-ai' : '')}>
              {status.data.available ? `${status.data.provider} · ${status.data.model}` : 'deterministic mode'}
            </Badge>
          ) : null
        }
      />

      {status.data && !status.data.available ? (
        <div className="flex items-start gap-3 rounded-lg border border-line bg-surface px-4 py-3">
          <Shield className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
          <div>
            <p className="text-xs text-ink">No language model is configured, so the platform is running in deterministic mode.</p>
            <p className="mt-0.5 text-2xs text-muted">
              Evidence retrieval, SQL generation from the intent library, guardrails and logging all work exactly as they
              do with a model attached — the narration is template-based instead of generated. Set LLM_PROVIDER and
              LLM_API_KEY to enable generation.
            </p>
          </div>
        </div>
      ) : null}

      <Tabs
        value={tab}
        onValueChange={(value) => {
          setTab(value);
          setParams(value === 'sql' ? { tab: 'sql' } : {});
        }}
      >
        <TabList>
          <Tab value="investigate">Investigate</Tab>
          {canSql ? <Tab value="sql">Natural-language SQL</Tab> : null}
          <Tab value="guardrails">Guardrails</Tab>
        </TabList>

        <TabPanel value="investigate" className="pt-4">
          <InvestigatePanel />
        </TabPanel>

        {canSql ? (
          <TabPanel value="sql" className="pt-4">
            <SqlPanel />
          </TabPanel>
        ) : null}

        <TabPanel value="guardrails" className="pt-4">
          <Panel>
            <PanelHeader title="AI guardrails" subtitle="Enforced server side, not by prompt instruction alone" icon={<Shield className="h-4 w-4" />} />
            <ul className="divide-y divide-line/60">
              {(status.data?.guardrails ?? []).map((rule: string) => (
                <li key={rule} className="flex items-start gap-3 px-5 py-3">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ai" />
                  <span className="text-sm text-muted">{rule}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </TabPanel>
      </Tabs>
    </div>
  );
}

function InvestigatePanel() {
  const [transactionId, setTransactionId] = useState('');
  const [question, setQuestion] = useState('Why was this transaction flagged?');

  const recent = useQuery({
    queryKey: ['ai-recent-transactions'],
    queryFn: () => api.get<any>('/transactions', { page_size: 8, sort_by: 'risk_score', sort_dir: 'desc', days: 30 }),
  });

  const ask = useMutation({
    mutationFn: () => api.post<any>('/ai/ask', { question, transaction_id: transactionId }),
  });

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
      <Panel>
        <PanelHeader title="Pick a transaction" subtitle="Highest risk in the last 30 days" icon={<Database className="h-4 w-4" />} />
        <ul className="max-h-[420px] divide-y divide-line/60 overflow-y-auto">
          {(recent.data?.items ?? []).map((txn: any) => (
            <li key={txn.id}>
              <button
                type="button"
                onClick={() => setTransactionId(txn.id)}
                className={cn(
                  'w-full px-4 py-2.5 text-left transition-colors hover:bg-raised/50',
                  transactionId === txn.id && 'bg-ai/[0.08]',
                )}
              >
                <span className="block truncate font-mono text-2xs text-muted">{txn.id}</span>
                <span className="mt-0.5 flex items-center justify-between text-2xs">
                  <span className="text-faint">{txn.decision.replace(/_/g, ' ')}</span>
                  <span className="tnum text-ink">risk {txn.risk_score.toFixed(1)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t border-line p-3">
          <input
            value={transactionId}
            onChange={(event) => setTransactionId(event.target.value)}
            placeholder="or paste a transaction id"
            className="h-9 w-full rounded-lg border border-line bg-surface px-3 font-mono text-2xs text-ink"
            aria-label="Transaction id"
          />
        </div>
      </Panel>

      <div className="space-y-4">
        <Panel className="p-4">
          <div className="flex gap-2">
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && transactionId) ask.mutate();
              }}
              placeholder="Ask about this transaction…"
              className="h-10 flex-1 rounded-lg border border-line bg-surface px-3 text-sm text-ink"
              aria-label="Question"
            />
            <Button variant="ai" size="lg" icon={<Send className="h-3.5 w-3.5" />} loading={ask.isPending} disabled={!transactionId} onClick={() => ask.mutate()}>
              Ask
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {['Why was this transaction flagged?', 'What evidence supports the decision?', 'What should the analyst do next?'].map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setQuestion(preset)}
                className="rounded-full border border-line bg-surface px-2.5 py-1 text-2xs text-muted transition-colors hover:border-ai/30 hover:text-ink"
              >
                {preset}
              </button>
            ))}
          </div>
          {!transactionId ? <p className="mt-2 text-2xs text-faint">Select a transaction to ground the question in evidence.</p> : null}
        </Panel>

        {ask.isError ? <ErrorState error={ask.error} /> : null}

        {ask.data ? (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
            <Panel>
              <PanelHeader
                title="Assessment"
                subtitle={
                  ask.data.generated_by === 'llm'
                    ? 'Language model narration over retrieved evidence'
                    : 'Deterministic narration over retrieved evidence'
                }
                icon={<Sparkles className="h-4 w-4 text-ai" />}
                action={<Badge>{formatDuration(ask.data.latency_ms)}</Badge>}
              />
              <div className="p-5">
                <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink">{ask.data.answer}</pre>
              </div>
            </Panel>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Panel>
                <PanelHeader title="Evidence" subtitle={`${ask.data.evidence?.length ?? 0} facts retrieved from the database`} />
                <ul className="max-h-[360px] divide-y divide-line/60 overflow-y-auto">
                  {(ask.data.evidence ?? []).map((item: any, index: number) => (
                    <li key={index} className="px-4 py-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <Badge>{item.kind}</Badge>
                        <span className="font-mono text-[10px] text-faint">{item.source}</span>
                      </div>
                      <p className="mt-1 text-xs text-ink">{item.statement}</p>
                    </li>
                  ))}
                </ul>
              </Panel>

              <Panel>
                <PanelHeader title="Model factors" subtitle="Attribution behind the fraud probability" />
                <div className="p-4">
                  <ul className="space-y-2">
                    {(ask.data.model_factors ?? []).map((factor: any, index: number) => (
                      <li key={index} className="flex items-center justify-between rounded border border-line bg-surface px-3 py-2">
                        <span className="min-w-0 truncate text-xs text-ink">{factor.label}</span>
                        <span className="tnum shrink-0 text-2xs text-muted">
                          {factor.value} · {factor.impact_pct}%
                        </span>
                      </li>
                    ))}
                  </ul>
                  <dl className="mt-4 grid grid-cols-2 gap-2 border-t border-line pt-3">
                    {Object.entries(ask.data.scores ?? {})
                      .filter(([, value]) => typeof value === 'number')
                      .map(([key, value]) => (
                        <div key={key}>
                          <dt className="text-2xs text-faint">{key.replace(/_/g, ' ')}</dt>
                          <dd className="tnum text-sm text-ink">{Number(value).toFixed(4)}</dd>
                        </div>
                      ))}
                  </dl>
                </div>
              </Panel>
            </div>

            <p className="text-2xs text-faint">{ask.data.disclaimer}</p>
          </motion.div>
        ) : null}
      </div>
    </div>
  );
}

function SqlPanel() {
  const [question, setQuestion] = useState('Which merchants have the highest fraud rate?');
  const run = useMutation({ mutationFn: () => api.post<any>('/ai/sql', { question }) });

  const rows = run.data?.rows ?? [];
  const columns = run.data?.columns ?? [];
  const chart = run.data?.chart;

  return (
    <div className="space-y-4">
      <Panel className="p-4">
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') run.mutate();
            }}
            placeholder="Ask a question about the data…"
            className="h-10 flex-1 rounded-lg border border-line bg-surface px-3 text-sm text-ink"
            aria-label="Analytics question"
          />
          <Button variant="ai" size="lg" icon={<Brain className="h-3.5 w-3.5" />} loading={run.isPending} onClick={() => run.mutate()}>
            Run
          </Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {SUGGESTED_QUESTIONS.map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => {
                setQuestion(preset);
              }}
              className="rounded-full border border-line bg-surface px-2.5 py-1 text-2xs text-muted transition-colors hover:border-ai/30 hover:text-ink"
            >
              {preset}
            </button>
          ))}
        </div>
      </Panel>

      {run.isError ? <ErrorState error={run.error} /> : null}

      {run.data?.status === 'NO_QUERY' ? (
        <Panel className="p-5">
          <p className="text-sm text-ink">{run.data.message}</p>
          <ul className="mt-3 space-y-1">
            {run.data.supported_topics.map((topic: string) => (
              <li key={topic} className="text-xs text-muted">
                • {topic}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {run.data?.status === 'OK' ? (
        <>
          <Panel>
            <PanelHeader
              title="Generated SQL"
              subtitle={`source: ${run.data.source} · validated read-only · ${formatDuration(run.data.latency_ms)}`}
              icon={<Code2 className="h-4 w-4" />}
              action={<Badge>{formatNumber(run.data.row_count)} rows</Badge>}
            />
            <pre className="overflow-x-auto px-5 py-4 font-mono text-2xs leading-relaxed text-info">{run.data.sql}</pre>
            {run.data.tables?.length ? (
              <div className="flex flex-wrap gap-1.5 border-t border-line px-5 py-2.5">
                {run.data.tables.map((table: string) => (
                  <Badge key={table}>{table}</Badge>
                ))}
              </div>
            ) : null}
          </Panel>

          {chart && chart.type !== 'table' && rows.length ? (
            <Panel>
              <PanelHeader title="Visualisation" subtitle={`${chart.type} chart of ${chart.y} by ${chart.x}`} />
              <div className="p-4">
                {chart.type === 'line' ? (
                  <TrendChart data={[...rows].reverse()} xKey={chart.x} series={[{ key: chart.y, name: chart.y }]} height={260} />
                ) : (
                  <CategoryBars data={rows.slice(0, 15)} xKey={chart.x} yKey={chart.y} height={280} horizontal={rows.length > 8} />
                )}
              </div>
            </Panel>
          ) : null}

          <Panel>
            <PanelHeader title="Results" icon={<Table2 className="h-4 w-4" />} />
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr>
                    {columns.map((column: string) => (
                      <th key={column} className="label sticky top-0 border-b border-line bg-surface px-3 py-2">
                        {column.replace(/_/g, ' ')}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, 100).map((row: any, index: number) => (
                    <tr key={index} className="border-b border-line/50">
                      {columns.map((column: string) => (
                        <td key={column} className="tnum px-3 py-2 text-ink/90">
                          {typeof row[column] === 'number' ? Number(row[column]).toLocaleString() : String(row[column] ?? '—')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length > 100 ? (
              <p className="border-t border-line px-4 py-2 text-2xs text-faint">Showing the first 100 of {rows.length} rows.</p>
            ) : null}
          </Panel>
        </>
      ) : null}

      {run.isPending ? <Skeleton className="h-64" /> : null}
    </div>
  );
}
