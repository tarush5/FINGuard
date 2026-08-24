/**
 * Case investigation workspace.
 *
 * Two-column layout: case facts and timeline on the left, AI summary, evidence,
 * model explanation and recommended action on the right, with sticky
 * investigation controls.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  CheckCircle2,
  Clock,
  FileText,
  Network,
  Send,
  ShieldAlert,
  Sparkles,
  UserCheck,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  Drawer,
  ErrorState,
  Modal,
  Panel,
  PanelHeader,
  Skeleton,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  useToast,
} from '@/components/ui';
import { DecisionTrace, FactorBars } from '@/components/viz/DecisionTrace';
import { NetworkGraph } from '@/components/viz/NetworkGraph';
import { RiskOrb } from '@/components/viz/RiskOrb';
import { api } from '@/lib/api';
import {
  caseStatusClass,
  decisionBgClass,
  formatCurrency,
  formatDateTime,
  formatScore,
  relativeTime,
} from '@/lib/format';
import { cn, downloadJson } from '@/lib/utils';
import { useAuth } from '@/store/auth';

export default function CaseDetail() {
  const { caseId = '' } = useParams();
  const queryClient = useQueryClient();
  const { push } = useToast();
  const can = useAuth((state) => state.can);
  const [tab, setTab] = useState('overview');
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState('');
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  const detail = useQuery({
    queryKey: ['case', caseId],
    queryFn: () => api.get<any>(`/cases/${caseId}`),
  });

  const summary = useQuery({
    queryKey: ['case-summary', caseId],
    queryFn: () => api.post<any>(`/ai/cases/${caseId}/summary`),
    enabled: false,
  });

  const trace = useQuery({
    queryKey: ['case-trace', detail.data?.case?.primary_transaction_id],
    queryFn: () => api.get<any>(`/transactions/${detail.data.case.primary_transaction_id}/trace`),
    enabled: Boolean(detail.data?.case?.primary_transaction_id) && can('risk:read') && tab === 'models',
  });

  const graph = useQuery({
    queryKey: ['case-graph', detail.data?.case?.customer_id],
    queryFn: () => api.get<any>(`/graph/customer/${detail.data.case.customer_id}`, { depth: 2 }),
    enabled: Boolean(detail.data?.case?.customer_id) && can('graph:read') && tab === 'network',
  });

  const transition = useMutation({
    mutationFn: (payload: { status: string; notes?: string }) => api.patch<any>(`/cases/${caseId}/status`, payload),
    onSuccess: (updated) => {
      push({ title: `Case moved to ${updated.status.replace(/_/g, ' ')}`, variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['case', caseId] });
      queryClient.invalidateQueries({ queryKey: ['cases'] });
    },
    onError: (error: any) => push({ title: 'Could not update the case', description: error?.message, variant: 'error' }),
  });

  const addNote = useMutation({
    mutationFn: (body: string) => api.post<any>(`/cases/${caseId}/notes`, { body }),
    onSuccess: () => {
      push({ title: 'Note added', variant: 'success' });
      setNote('');
      setNoteOpen(false);
      queryClient.invalidateQueries({ queryKey: ['case', caseId] });
    },
  });

  const report = useMutation({
    mutationFn: () => api.post<any>(`/ai/cases/${caseId}/report`),
    onSuccess: (data) => {
      downloadJson(data, `finguard-case-${data.case.case_number}.json`);
      push({ title: 'Investigation report generated', description: 'Downloaded as JSON.', variant: 'success' });
    },
  });

  if (detail.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-72" />
        <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
          <Skeleton className="h-[520px]" />
          <Skeleton className="h-[520px]" />
        </div>
      </div>
    );
  }
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;

  const { case: caseRecord, customer, merchant, timeline, notes, related_transactions: related, alerts } = detail.data;
  const terminal = ['CONFIRMED_FRAUD', 'FALSE_POSITIVE', 'RESOLVED'].includes(caseRecord.status);

  return (
    <div className="space-y-4">
      <PageHeader
        breadcrumb={
          <Link to="/app/cases" className="inline-flex items-center gap-1 hover:text-ink">
            <ArrowLeft className="h-3 w-3" /> Cases
          </Link>
        }
        title={`Case ${caseRecord.case_number}`}
        description={caseRecord.title}
        actions={
          <>
            <Badge className={cn('h-7 px-2.5 text-xs', caseStatusClass(caseRecord.status))}>
              {caseRecord.status.replace(/_/g, ' ')}
            </Badge>
            <Button variant="outline" icon={<FileText className="h-3.5 w-3.5" />} loading={report.isPending} onClick={() => report.mutate()}>
              Generate report
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_380px]">
        {/* Left column */}
        <div className="space-y-4">
          <Panel className="p-5">
            <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
              <RiskOrb score={caseRecord.risk_score} band={caseRecord.risk_band} size={128} label="Case risk" />
              <dl className="grid flex-1 grid-cols-2 gap-4 sm:grid-cols-3">
                {[
                  ['Exposure', formatCurrency(caseRecord.exposure_amount)],
                  ['Priority', caseRecord.priority],
                  ['Customer', caseRecord.customer_id ?? '—'],
                  ['Merchant', caseRecord.merchant_id ?? '—'],
                  ['Assigned to', caseRecord.assigned_to_name ?? 'unassigned'],
                  ['SLA due', caseRecord.sla_due_at ? relativeTime(caseRecord.sla_due_at) : '—'],
                  ['Opened', formatDateTime(caseRecord.created_at)],
                  ['Opened by', caseRecord.opened_by],
                  ['Transaction', caseRecord.primary_transaction_id ?? '—'],
                ].map(([label, value]) => (
                  <div key={label as string} className="min-w-0">
                    <dt className="label">{label}</dt>
                    <dd className="truncate text-sm text-ink" title={String(value)}>
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          </Panel>

          <Tabs value={tab} onValueChange={setTab}>
            <TabList>
              <Tab value="overview">Overview</Tab>
              <Tab value="timeline" count={timeline?.length}>
                Timeline
              </Tab>
              <Tab value="transactions" count={related?.length}>
                Transactions
              </Tab>
              <Tab value="models">Decision</Tab>
              <Tab value="network">Network</Tab>
              <Tab value="notes" count={notes?.length}>
                Notes
              </Tab>
            </TabList>

            <TabPanel value="overview" className="space-y-4 pt-4">
              <Panel>
                <PanelHeader title="Customer" subtitle={customer?.pii_masked ? 'PII masked for your role' : 'Full detail'} />
                <dl className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
                  {customer
                    ? [
                        ['Name', customer.full_name],
                        ['Email', customer.email],
                        ['Segment', customer.segment],
                        ['Risk band', customer.risk_band],
                        ['Transactions', customer.transaction_count],
                        ['Avg amount', formatCurrency(customer.avg_transaction_amount)],
                        ['Devices', customer.distinct_device_count],
                        ['Confirmed fraud', customer.confirmed_fraud_count],
                      ].map(([label, value]) => (
                        <div key={label as string} className="min-w-0">
                          <dt className="label">{label}</dt>
                          <dd className="truncate text-sm text-ink">{String(value)}</dd>
                        </div>
                      ))
                    : null}
                </dl>
              </Panel>

              <Panel>
                <PanelHeader title="Merchant" />
                <dl className="grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
                  {merchant
                    ? [
                        ['Name', merchant.name],
                        ['Category', merchant.category],
                        ['Fraud rate', `${(merchant.fraud_rate * 100).toFixed(2)}%`],
                        ['Risk score', formatScore(merchant.risk_score)],
                        ['Transactions', merchant.transaction_count],
                        ['Volume', formatCurrency(merchant.transaction_volume, 'INR', true)],
                        ['Avg ticket', formatCurrency(merchant.avg_ticket)],
                        ['High risk', merchant.high_risk_flag ? 'yes' : 'no'],
                      ].map(([label, value]) => (
                        <div key={label as string} className="min-w-0">
                          <dt className="label">{label}</dt>
                          <dd className="truncate text-sm text-ink">{String(value)}</dd>
                        </div>
                      ))
                    : null}
                </dl>
              </Panel>

              {alerts?.length ? (
                <Panel>
                  <PanelHeader title="Alerts" subtitle={`${alerts.length} alert(s) linked to this case`} />
                  <ul className="divide-y divide-line/60">
                    {alerts.map((alert: any) => (
                      <li key={alert.id} className="px-5 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm text-ink">{alert.title}</p>
                          <Badge className={cn(decisionBgClass(alert.severity === 'CRITICAL' ? 'DECLINE' : 'MANUAL_REVIEW'))}>
                            {alert.severity}
                          </Badge>
                        </div>
                        <p className="mt-1 text-xs text-muted">{alert.description}</p>
                      </li>
                    ))}
                  </ul>
                </Panel>
              ) : null}
            </TabPanel>

            <TabPanel value="timeline" className="pt-4">
              <Panel>
                <PanelHeader title="Investigation timeline" icon={<Clock className="h-4 w-4" />} />
                <ol className="relative p-5">
                  <span className="absolute bottom-5 left-[27px] top-6 w-px bg-line" aria-hidden />
                  {(timeline ?? []).map((event: any, index: number) => (
                    <motion.li
                      key={event.id}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: index * 0.03 }}
                      className="relative mb-4 flex gap-4 last:mb-0"
                    >
                      <span
                        className={cn(
                          'z-10 mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 border-base',
                          event.severity === 'CRITICAL'
                            ? 'bg-critical'
                            : event.severity === 'WARNING'
                              ? 'bg-warning'
                              : 'bg-info',
                        )}
                      />
                      <div className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-2xs uppercase tracking-wide text-faint">{event.event_type.replace(/_/g, ' ')}</span>
                          <span className="tnum text-2xs text-faint">{formatDateTime(event.occurred_at)}</span>
                        </div>
                        <p className="mt-1 text-sm text-ink">{event.description}</p>
                        {event.entity_id ? (
                          <p className="mt-1 font-mono text-[10px] text-faint">
                            {event.entity_type}: {event.entity_id}
                          </p>
                        ) : null}
                        <p className="mt-1 text-2xs text-muted">by {event.actor}</p>
                      </div>
                    </motion.li>
                  ))}
                </ol>
              </Panel>
            </TabPanel>

            <TabPanel value="transactions" className="pt-4">
              <Panel>
                <PanelHeader title="Customer transactions" subtitle="Most recent activity for this customer" />
                <ul className="divide-y divide-line/60">
                  {(related ?? []).map((txn: any) => (
                    <li key={txn.id}>
                      <Link to={`/app/transactions/${txn.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-raised/50">
                        <span className="tnum w-28 shrink-0 text-sm text-ink">{formatCurrency(txn.amount, txn.currency)}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-mono text-2xs text-muted">{txn.id}</span>
                          <span className="block text-[10px] text-faint">
                            {formatDateTime(txn.occurred_at)} · {txn.merchant_id}
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

            <TabPanel value="models" className="pt-4">
              <Panel className="p-5">
                {!caseRecord.primary_transaction_id ? (
                  <p className="text-sm text-muted">This case has no primary transaction.</p>
                ) : trace.isLoading || !trace.data ? (
                  <Skeleton className="h-96" />
                ) : trace.isError ? (
                  <ErrorState error={trace.error} />
                ) : (
                  <DecisionTrace stages={trace.data.stages} />
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

            <TabPanel value="notes" className="pt-4">
              <Panel>
                <PanelHeader
                  title="Case notes"
                  action={
                    can('case:write') ? (
                      <Button size="sm" onClick={() => setNoteOpen(true)}>
                        Add note
                      </Button>
                    ) : null
                  }
                />
                {notes?.length ? (
                  <ul className="divide-y divide-line/60">
                    {notes.map((item: any) => (
                      <li key={item.id} className="px-5 py-3">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs text-ink">{item.author_name}</span>
                          <span className="text-2xs text-faint">{relativeTime(item.created_at)}</span>
                        </div>
                        <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{item.body}</p>
                        {item.is_ai_generated ? (
                          <Badge className="mt-2 border-ai/25 bg-ai/10 text-ai">AI generated</Badge>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="px-5 py-8 text-center text-xs text-faint">No notes on this case yet.</p>
                )}
              </Panel>
            </TabPanel>
          </Tabs>
        </div>

        {/* Right column: sticky investigation controls */}
        <div className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <Panel>
            <PanelHeader title="AI case summary" icon={<Sparkles className="h-4 w-4 text-ai" />} />
            <div className="p-4">
              {caseRecord.ai_summary || summary.data ? (
                <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-ink">
                  {summary.data?.summary ?? caseRecord.ai_summary}
                </pre>
              ) : (
                <p className="text-xs text-muted">
                  Generate a summary of the evidence attached to this case. Evidence is retrieved from the database;
                  the narrative only restates it.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="ai" loading={summary.isFetching} onClick={() => summary.refetch()}>
                  {caseRecord.ai_summary ? 'Regenerate' : 'Generate summary'}
                </Button>
                {summary.data ? (
                  <Button size="sm" variant="ghost" onClick={() => setEvidenceOpen(true)}>
                    Show evidence ({summary.data.evidence?.length ?? 0})
                  </Button>
                ) : null}
              </div>
              {summary.data ? (
                <p className="mt-2 text-2xs text-faint">
                  {summary.data.generated_by === 'llm' ? 'Model narration over retrieved evidence.' : 'Deterministic narration (no LLM configured).'}
                </p>
              ) : null}
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="Evidence" subtitle="Signals recorded at decision time" icon={<ShieldAlert className="h-4 w-4" />} />
            <div className="p-4">
              <FactorBars factors={caseRecord.evidence?.top_factors ?? []} valueKey="points" />
              {caseRecord.evidence?.triggered_rules?.length ? (
                <div className="mt-4 border-t border-line pt-3">
                  <p className="label mb-2">Triggered rules</p>
                  <div className="flex flex-wrap gap-1.5">
                    {caseRecord.evidence.triggered_rules.map((rule: any) => (
                      <span key={rule.code} className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-2xs text-info">
                        {rule.code} +{rule.risk_points}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </Panel>

          {can('case:write') ? (
            <Panel>
              <PanelHeader title="Verdict" subtitle="Feeds rule precision and model retraining" icon={<UserCheck className="h-4 w-4" />} />
              <div className="space-y-2 p-4">
                {terminal ? (
                  <p className="rounded-lg border border-line bg-surface px-3 py-2 text-xs text-muted">
                    Case closed as <span className="text-ink">{caseRecord.status.replace(/_/g, ' ')}</span>
                    {caseRecord.resolution_notes ? ` — ${caseRecord.resolution_notes}` : ''}
                  </p>
                ) : (
                  <>
                    {caseRecord.status === 'NEW' ? (
                      <Button
                        className="w-full justify-center"
                        loading={transition.isPending}
                        onClick={() => transition.mutate({ status: 'INVESTIGATING' })}
                      >
                        Start investigating
                      </Button>
                    ) : null}
                    <Button
                      variant="danger"
                      className="w-full justify-center"
                      icon={<XCircle className="h-3.5 w-3.5" />}
                      loading={transition.isPending}
                      onClick={() =>
                        transition.mutate({
                          status: 'CONFIRMED_FRAUD',
                          notes: 'Confirmed fraudulent after investigation.',
                        })
                      }
                    >
                      Confirm fraud
                    </Button>
                    <Button
                      className="w-full justify-center border border-positive/30 bg-positive/10 text-positive hover:bg-positive/20"
                      icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                      loading={transition.isPending}
                      onClick={() =>
                        transition.mutate({
                          status: 'FALSE_POSITIVE',
                          notes: 'Customer verified the activity; releasing the hold.',
                        })
                      }
                    >
                      Mark false positive
                    </Button>
                    {caseRecord.status !== 'ESCALATED' ? (
                      <Button
                        variant="outline"
                        className="w-full justify-center"
                        loading={transition.isPending}
                        onClick={() => transition.mutate({ status: 'ESCALATED' })}
                      >
                        Escalate
                      </Button>
                    ) : null}
                  </>
                )}
              </div>
            </Panel>
          ) : null}
        </div>
      </div>

      <Modal
        open={noteOpen}
        onClose={() => setNoteOpen(false)}
        title="Add a case note"
        description="Notes are part of the audit trail."
        footer={
          <>
            <Button variant="ghost" onClick={() => setNoteOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" icon={<Send className="h-3.5 w-3.5" />} loading={addNote.isPending} onClick={() => addNote.mutate(note)} disabled={!note.trim()}>
              Save note
            </Button>
          </>
        }
      >
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={6}
          className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink"
          placeholder="What did you verify, and what did you conclude?"
        />
      </Modal>

      <Drawer open={evidenceOpen} onClose={() => setEvidenceOpen(false)} title="Retrieved evidence">
        <ul className="space-y-2">
          {(summary.data?.evidence ?? []).map((item: any, index: number) => (
            <li key={index} className="rounded-lg border border-line bg-surface px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-2xs uppercase tracking-wide text-faint">{item.kind}</span>
                <span className="font-mono text-[10px] text-faint">{item.source}</span>
              </div>
              <p className="mt-1 text-xs text-ink">{item.statement}</p>
            </li>
          ))}
        </ul>
      </Drawer>
    </div>
  );
}
