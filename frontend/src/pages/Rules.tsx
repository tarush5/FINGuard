/**
 * Rule management: create, edit, activate and back-test detection rules.
 *
 * Conditions are authored as data and validated by the API before they are
 * stored, so a malformed or unsafe rule can never reach the decision path.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Plus, SlidersHorizontal, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { CategoryBars } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import {
  Badge,
  Button,
  Drawer,
  EmptyState,
  ErrorState,
  Input,
  Modal,
  Panel,
  PanelHeader,
  Select,
  Table,
  TableSkeleton,
  Td,
  Th,
  Tr,
  useToast,
} from '@/components/ui';
import { api } from '@/lib/api';
import { formatNumber, formatPercent, relativeTime, riskBgClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';

const OPERATORS = [
  { value: 'gt', label: 'greater than' },
  { value: 'gte', label: 'at least' },
  { value: 'lt', label: 'less than' },
  { value: 'lte', label: 'at most' },
  { value: 'eq', label: 'equals' },
  { value: 'ne', label: 'not equal to' },
  { value: 'is_true', label: 'is true' },
  { value: 'is_false', label: 'is false' },
];

export default function Rules() {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const canWrite = useAuth((state) => state.can('rule:write'));
  const [createOpen, setCreateOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const rules = useQuery({
    queryKey: ['rules'],
    queryFn: () => api.get<any>('/rules', { page_size: 100 }),
  });

  const detail = useQuery({
    queryKey: ['rule', selected],
    queryFn: () => api.get<any>(`/rules/${selected}`),
    enabled: Boolean(selected),
  });

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => api.patch(`/rules/${id}`, { is_active }),
    onSuccess: () => {
      push({ title: 'Rule updated', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['rules'] });
    },
    onError: (error: any) => push({ title: 'Update failed', description: error?.message, variant: 'error' }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/rules/${id}`),
    onSuccess: () => {
      push({ title: 'Rule retired', variant: 'success' });
      setSelected(null);
      queryClient.invalidateQueries({ queryKey: ['rules'] });
    },
  });

  const items = rules.data?.items ?? [];
  const fields = rules.data?.available_fields ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Detection rules"
        description="Rules are stored as data and evaluated by the engine at decision time — no deployment needed to change them."
        actions={
          canWrite ? (
            <Button variant="primary" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => setCreateOpen(true)}>
              New rule
            </Button>
          ) : null
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <Panel className="lg:col-span-3">
          <PanelHeader
            title="Rule library"
            subtitle={`${items.length} rules · ${items.filter((rule: any) => rule.is_active).length} active`}
            icon={<SlidersHorizontal className="h-4 w-4" />}
          />
          {rules.isLoading ? (
            <TableSkeleton rows={8} cols={6} />
          ) : rules.isError ? (
            <ErrorState error={rules.error} onRetry={() => rules.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState title="No rules configured" description="Create your first detection rule." />
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Code</Th>
                  <Th>Rule</Th>
                  <Th className="hidden lg:table-cell">Condition</Th>
                  <Th>Points</Th>
                  <Th>Hits</Th>
                  <Th className="hidden md:table-cell">Precision</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {items.map((rule: any) => (
                  <Tr key={rule.id} onClick={() => setSelected(rule.id)}>
                    <Td className="font-mono text-xs text-info">{rule.code}</Td>
                    <Td>
                      <span className="block max-w-xs truncate text-sm text-ink">{rule.name}</span>
                      <span className="flex items-center gap-1.5">
                        <Badge className={cn('mt-1', riskBgClass(rule.severity))}>{rule.severity}</Badge>
                        {rule.is_shadow ? <Badge className="mt-1 border-ai/25 bg-ai/10 text-ai">shadow</Badge> : null}
                      </span>
                    </Td>
                    <Td className="hidden max-w-xs truncate font-mono text-[10px] text-muted lg:table-cell" >
                      {rule.condition_text}
                    </Td>
                    <Td className="tnum">{rule.risk_points}</Td>
                    <Td className="tnum">{formatNumber(rule.hit_count)}</Td>
                    <Td className="tnum hidden md:table-cell">
                      {rule.true_positive_count + rule.false_positive_count > 0 ? (
                        <span className={rule.precision >= 0.5 ? 'text-positive' : 'text-warning'}>
                          {formatPercent(rule.precision * 100, 1)}
                        </span>
                      ) : (
                        <span className="text-faint">no verdicts</span>
                      )}
                    </Td>
                    <Td onClick={(event: any) => event.stopPropagation()}>
                      {canWrite ? (
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            toggle.mutate({ id: rule.id, is_active: !rule.is_active });
                          }}
                          className={cn(
                            'relative h-5 w-9 rounded-full transition-colors',
                            rule.is_active ? 'bg-positive/70' : 'bg-line-strong',
                          )}
                          aria-label={rule.is_active ? 'Deactivate rule' : 'Activate rule'}
                        >
                          <span
                            className={cn(
                              'absolute top-0.5 h-4 w-4 rounded-full bg-ink transition-transform',
                              rule.is_active ? 'translate-x-4' : 'translate-x-0.5',
                            )}
                          />
                        </button>
                      ) : (
                        <Badge className={rule.is_active ? 'border-positive/25 bg-positive/10 text-positive' : ''}>
                          {rule.is_active ? 'active' : 'inactive'}
                        </Badge>
                      )}
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Panel>

        <Panel>
          <PanelHeader title="Hit distribution" subtitle="Triggers by rule" />
          <div className="p-3">
            <CategoryBars
              data={[...items].sort((a: any, b: any) => b.hit_count - a.hit_count).slice(0, 10).map((rule: any) => ({
                code: rule.code,
                hits: rule.hit_count,
              }))}
              xKey="code"
              yKey="hits"
              horizontal
              height={320}
              color="#38BDF8"
            />
          </div>
        </Panel>
      </div>

      <RuleDrawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        detail={detail.data}
        loading={detail.isLoading}
        canWrite={canWrite}
        onDelete={(id) => remove.mutate(id)}
      />

      <CreateRuleModal open={createOpen} onClose={() => setCreateOpen(false)} fields={fields} />
    </div>
  );
}

function RuleDrawer({
  open,
  onClose,
  detail,
  loading,
  canWrite,
  onDelete,
}: {
  open: boolean;
  onClose: () => void;
  detail: any;
  loading: boolean;
  canWrite: boolean;
  onDelete: (id: string) => void;
}) {
  const rule = detail?.rule;
  return (
    <Drawer open={open} onClose={onClose} title={rule ? `${rule.code} · ${rule.name}` : 'Rule'} width="max-w-2xl">
      {loading || !rule ? (
        <p className="text-xs text-muted">Loading…</p>
      ) : (
        <div className="space-y-5">
          <div>
            <p className="text-sm text-muted">{rule.description}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge className={riskBgClass(rule.severity)}>{rule.severity}</Badge>
              <Badge>{rule.category}</Badge>
              <Badge>action {rule.action}</Badge>
              <Badge>+{rule.risk_points} points</Badge>
              <Badge>v{rule.version}</Badge>
            </div>
          </div>

          <div>
            <p className="label mb-2">Condition</p>
            <p className="rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs text-ink">{rule.condition_text}</p>
            <pre className="mt-2 overflow-x-auto rounded-lg border border-line bg-void px-3 py-2 text-[10px] text-muted">
              {JSON.stringify(rule.condition, null, 2)}
            </pre>
          </div>

          <div className="grid grid-cols-3 gap-3">
            {[
              ['Hits', formatNumber(rule.hit_count)],
              ['Confirmed fraud', formatNumber(rule.true_positive_count)],
              ['False positives', formatNumber(rule.false_positive_count)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-line bg-surface px-3 py-2">
                <p className="label">{label}</p>
                <p className="tnum text-sm text-ink">{value}</p>
              </div>
            ))}
          </div>

          {detail.daily_hits?.length ? (
            <div>
              <p className="label mb-2">Daily hits</p>
              <CategoryBars data={detail.daily_hits} xKey="date" yKey="hits" height={160} color="#FB923C" />
            </div>
          ) : null}

          {detail.recent_executions?.length ? (
            <div>
              <p className="label mb-2">Recent triggers</p>
              <ul className="space-y-1.5">
                {detail.recent_executions.slice(0, 8).map((execution: any) => (
                  <li key={execution.id} className="rounded-lg border border-line bg-surface px-3 py-2">
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-2xs text-muted">{execution.transaction_id}</span>
                      <span className="text-2xs text-faint">{relativeTime(execution.evaluated_at)}</span>
                    </div>
                    {execution.matched_values ? (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {Object.entries(execution.matched_values as Record<string, any>).map(([field, match]) => (
                          <span key={field} className="rounded bg-raised px-1.5 py-0.5 text-[10px] text-muted">
                            {match?.label ?? field}: {String(match?.actual)}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {canWrite ? (
            <Button variant="danger" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => onDelete(rule.id)}>
              Retire rule
            </Button>
          ) : null}
        </div>
      )}
    </Drawer>
  );
}

function CreateRuleModal({ open, onClose, fields }: { open: boolean; onClose: () => void; fields: any[] }) {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [form, setForm] = useState({
    code: '',
    name: '',
    description: '',
    category: 'BEHAVIOUR',
    severity: 'MEDIUM',
    risk_points: 15,
    action: 'SCORE',
    priority: 100,
    field: 'amount_ratio_to_avg',
    operator: 'gt',
    value: '3',
  });
  const [backtest, setBacktest] = useState<any | null>(null);

  const condition = () => {
    const base: Record<string, unknown> = { field: form.field, op: form.operator };
    if (!['is_true', 'is_false'].includes(form.operator)) {
      base.value = Number.isNaN(Number(form.value)) ? form.value : Number(form.value);
    }
    return base;
  };

  const test = useMutation({
    mutationFn: () => api.post<any>('/rules/test', { condition: condition(), sample_size: 2000, days: 60 }),
    onSuccess: (result) => setBacktest(result),
    onError: (error: any) => push({ title: 'Back-test failed', description: error?.message, variant: 'error' }),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<any>('/rules', {
        code: form.code.toUpperCase(),
        name: form.name,
        description: form.description,
        category: form.category,
        severity: form.severity,
        risk_points: Number(form.risk_points),
        action: form.action,
        priority: Number(form.priority),
        condition: condition(),
      }),
    onSuccess: () => {
      push({ title: 'Rule created', description: 'It is now part of the live decision path.', variant: 'success' });
      queryClient.invalidateQueries({ queryKey: ['rules'] });
      onClose();
    },
    onError: (error: any) => push({ title: 'Could not create the rule', description: error?.message, variant: 'error' }),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create a detection rule"
      description="Back-test against real history before activating."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="outline" icon={<FlaskConical className="h-3.5 w-3.5" />} loading={test.isPending} onClick={() => test.mutate()}>
            Back-test
          </Button>
          <Button variant="primary" loading={create.isPending} onClick={() => create.mutate()} disabled={!form.code || !form.name}>
            Create rule
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Input label="Code" placeholder="R-CUSTOM-001" value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value.toUpperCase() })} />
          <Input label="Name" placeholder="Descriptive rule name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        </div>
        <Input label="Description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />

        <div className="rounded-lg border border-line bg-surface p-3">
          <p className="label mb-2">Condition</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Select
              value={form.field}
              onChange={(event) => setForm({ ...form, field: event.target.value })}
              aria-label="Field"
              options={fields.map((field: any) => ({ value: field.field, label: field.label }))}
            />
            <Select value={form.operator} onChange={(event) => setForm({ ...form, operator: event.target.value })} aria-label="Operator" options={OPERATORS} />
            {!['is_true', 'is_false'].includes(form.operator) ? (
              <Input value={form.value} onChange={(event) => setForm({ ...form, value: event.target.value })} aria-label="Value" />
            ) : null}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Select
            label="Severity"
            value={form.severity}
            onChange={(event) => setForm({ ...form, severity: event.target.value })}
            options={['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((value) => ({ value, label: value }))}
          />
          <Select
            label="Action"
            value={form.action}
            onChange={(event) => setForm({ ...form, action: event.target.value })}
            options={['SCORE', 'STEP_UP', 'REVIEW', 'DECLINE'].map((value) => ({ value, label: value }))}
          />
          <Input
            label="Risk points"
            type="number"
            value={String(form.risk_points)}
            onChange={(event) => setForm({ ...form, risk_points: Number(event.target.value) })}
          />
          <Input
            label="Priority"
            type="number"
            value={String(form.priority)}
            onChange={(event) => setForm({ ...form, priority: Number(event.target.value) })}
          />
        </div>

        {backtest ? (
          <div className="rounded-lg border border-info/25 bg-info/[0.05] p-3">
            <p className="label mb-2">Back-test on {formatNumber(backtest.sample_size)} recent transactions</p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ['Hits', formatNumber(backtest.hits)],
                ['Hit rate', `${backtest.hit_rate_pct}%`],
                ['True positives', formatNumber(backtest.true_positives)],
                ['Precision', formatPercent(backtest.precision * 100, 1)],
                ['Recall', formatPercent(backtest.recall * 100, 1)],
              ].map(([label, value]) => (
                <div key={label}>
                  <p className="text-2xs text-faint">{label}</p>
                  <p className="tnum text-sm text-ink">{value}</p>
                </div>
              ))}
            </div>
            <p className="mt-2 font-mono text-2xs text-muted">{backtest.condition_text}</p>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
