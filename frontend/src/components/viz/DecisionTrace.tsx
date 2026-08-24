/**
 * Decision Trace - the audit view of how one transaction was decided.
 *
 * Renders the six pipeline stages returned by `/transactions/{id}/trace`, with
 * measured latency per stage and the evidence each stage contributed. Every
 * value here comes from stored records, not from a re-computation in the UI.
 */
import { AnimatePresence, motion } from 'framer-motion';
import {
  Binary,
  ChevronDown,
  Cpu,
  Gavel,
  Network,
  ShieldCheck,
  SlidersHorizontal,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Badge } from '@/components/ui';
import { decisionBgClass, formatDuration, formatScore, riskTextClass } from '@/lib/format';
import { cn } from '@/lib/utils';

export interface TraceStage {
  stage: string;
  duration_ms: number;
  summary: string;
  detail: Record<string, any>;
}

const STAGE_META: Record<string, { icon: ReactNode; label: string; hint: string }> = {
  FEATURES: {
    icon: <Binary className="h-4 w-4" />,
    label: 'Feature engineering',
    hint: 'Point-in-time features computed from history strictly older than this transaction.',
  },
  RULES: {
    icon: <SlidersHorizontal className="h-4 w-4" />,
    label: 'Rule engine',
    hint: 'Analyst-authored rules evaluated against the feature namespace.',
  },
  MODEL: {
    icon: <Cpu className="h-4 w-4" />,
    label: 'Machine learning',
    hint: 'Gradient boosted classifier plus the unsupervised anomaly detector.',
  },
  GRAPH: {
    icon: <Network className="h-4 w-4" />,
    label: 'Graph intelligence',
    hint: 'Device, IP and ring neighbourhood risk.',
  },
  RISK: {
    icon: <ShieldCheck className="h-4 w-4" />,
    label: 'Ensemble risk',
    hint: 'Weighted blend of every signal onto a 0-100 scale.',
  },
  DECISION: {
    icon: <Gavel className="h-4 w-4" />,
    label: 'Decision engine',
    hint: 'Threshold policy applied, with any rule-forced escalation.',
  },
};

export function DecisionTrace({ stages, className }: { stages: TraceStage[]; className?: string }) {
  const [open, setOpen] = useState<string | null>('RISK');
  const total = stages.reduce((sum, stage) => sum + (stage.duration_ms ?? 0), 0);

  return (
    <div className={cn('relative', className)}>
      <div className="mb-4 flex items-center justify-between">
        <p className="label">Decision trace</p>
        <p className="tnum text-2xs text-faint">total {formatDuration(total)}</p>
      </div>

      <ol className="relative space-y-2">
        {/* The connecting spine, with a flowing dash to suggest the pipeline. */}
        <svg className="pointer-events-none absolute left-[19px] top-2 h-[calc(100%-16px)] w-px" aria-hidden>
          <line
            x1="0.5"
            y1="0"
            x2="0.5"
            y2="100%"
            stroke="#2A3849"
            strokeWidth="1"
            strokeDasharray="4 6"
            className="animate-flow-dash"
          />
        </svg>

        {stages.map((stage, index) => {
          const meta = STAGE_META[stage.stage] ?? {
            icon: <Binary className="h-4 w-4" />,
            label: stage.stage,
            hint: '',
          };
          const expanded = open === stage.stage;
          return (
            <li key={stage.stage}>
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: index * 0.05 }}
                className={cn(
                  'panel-flat overflow-hidden transition-colors',
                  expanded ? 'border-info/30' : 'hover:border-line-strong',
                )}
              >
                <button
                  type="button"
                  onClick={() => setOpen(expanded ? null : stage.stage)}
                  aria-expanded={expanded}
                  className="flex w-full items-center gap-3 px-3 py-3 text-left"
                >
                  <span
                    className={cn(
                      'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border',
                      expanded ? 'border-info/40 bg-info/10 text-info' : 'border-line bg-raised text-muted',
                    )}
                  >
                    {meta.icon}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-medium text-ink">{meta.label}</span>
                      <span className="tnum text-2xs text-faint">{formatDuration(stage.duration_ms)}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-muted">{stage.summary}</span>
                  </span>
                  <ChevronDown
                    className={cn('h-4 w-4 shrink-0 text-faint transition-transform', expanded && 'rotate-180')}
                  />
                </button>

                <AnimatePresence initial={false}>
                  {expanded ? (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                      className="overflow-hidden border-t border-line"
                    >
                      <div className="px-4 py-3">
                        {meta.hint ? <p className="mb-3 text-2xs text-faint">{meta.hint}</p> : null}
                        <StageDetail stage={stage} />
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </motion.div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function StageDetail({ stage }: { stage: TraceStage }) {
  const detail = stage.detail ?? {};

  if (stage.stage === 'FEATURES') {
    const notable: Record<string, unknown> = detail.notable ?? {};
    return (
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        {Object.entries(notable).map(([key, value]) => (
          <div key={key}>
            <dt className="text-2xs text-faint">{key.replace(/_/g, ' ')}</dt>
            <dd className="tnum text-sm text-ink">{formatValue(value)}</dd>
          </div>
        ))}
      </dl>
    );
  }

  if (stage.stage === 'RULES') {
    const triggered: any[] = detail.triggered ?? [];
    if (!triggered.length) return <p className="text-xs text-muted">No rule triggered on this transaction.</p>;
    return (
      <ul className="space-y-2">
        {triggered.map((rule) => (
          <li key={rule.code} className="rounded-lg border border-line bg-surface px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-xs text-info">{rule.code}</span>
              <span className="tnum text-xs text-warning">+{formatScore(rule.risk_points, 0)} pts</span>
            </div>
            {rule.name ? <p className="mt-1 text-xs text-ink">{rule.name}</p> : null}
            {rule.matched_values ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {Object.entries(rule.matched_values as Record<string, any>).map(([field, match]) => (
                  <span key={field} className="rounded border border-line bg-raised px-1.5 py-0.5 text-2xs text-muted">
                    {match?.label ?? field}: <span className="tnum text-ink">{formatValue(match?.actual)}</span>
                    {match?.operator ? ` ${symbolFor(match.operator)} ${formatValue(match.expected)}` : ''}
                  </span>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    );
  }

  if (stage.stage === 'MODEL') {
    const explanation = detail.explanation ?? {};
    const factors: any[] = explanation.top_factors ?? [];
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Badge className="border-info/25 bg-info/10 text-info">{detail.model_version ?? 'model'}</Badge>
          {explanation.method ? <Badge>{explanation.method}</Badge> : null}
          {detail.threshold !== undefined && detail.threshold !== null ? (
            <Badge>threshold {formatScore(detail.threshold, 3)}</Badge>
          ) : null}
          {detail.is_trained_model === false ? (
            <Badge className="border-warning/25 bg-warning/10 text-warning">cold-start scorecard</Badge>
          ) : null}
        </div>
        <FactorBars factors={factors} />
      </div>
    );
  }

  if (stage.stage === 'GRAPH') {
    const signals: any[] = detail.signals ?? [];
    if (!signals.length) return <p className="text-xs text-muted">No graph signal fired for this transaction.</p>;
    return (
      <ul className="space-y-2">
        {signals.map((signal, index) => (
          <li key={index} className="rounded-lg border border-line bg-surface px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="text-2xs uppercase tracking-wide text-ai">{signal.type}</span>
              <span className="tnum text-2xs text-muted">weight {formatScore(signal.weight, 3)}</span>
            </div>
            <p className="mt-1 text-xs text-ink">{signal.detail}</p>
          </li>
        ))}
      </ul>
    );
  }

  if (stage.stage === 'RISK') {
    const components: Record<string, number> = detail.components ?? {};
    const weights: Record<string, number> = detail.weights ?? {};
    const factors: any[] = detail.top_factors ?? [];
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {Object.entries(components).map(([key, value]) => (
            <div key={key} className="rounded-lg border border-line bg-surface px-3 py-2">
              <p className="text-2xs text-faint">{key.replace(/_/g, ' ')}</p>
              <p className="tnum text-sm text-ink">{formatScore(value, key.includes('score') && value > 1 ? 1 : 4)}</p>
              {weights[key.replace('_score', '').replace('fraud_probability', 'model')] !== undefined ? (
                <p className="text-2xs text-faint">
                  weight {weights[key.replace('_score', '').replace('fraud_probability', 'model')]}
                </p>
              ) : null}
            </div>
          ))}
        </div>
        <div>
          <p className="label mb-2">Contribution to final score</p>
          <FactorBars factors={factors} valueKey="points" />
        </div>
      </div>
    );
  }

  if (stage.stage === 'DECISION') {
    return (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              'rounded-md border px-2 py-1 text-2xs font-medium uppercase tracking-wide',
              decisionBgClass(String(detail.decision ?? stage.summary)),
            )}
          >
            {detail.decision ?? stage.summary}
          </span>
          {detail.policy_version ? <Badge>{detail.policy_version}</Badge> : null}
          {detail.forced_by_rule ? (
            <Badge className="border-critical/25 bg-critical/10 text-critical">forced by {detail.forced_by_rule}</Badge>
          ) : null}
        </div>
        {detail.reason ? <p className="text-xs text-ink">{detail.reason}</p> : null}
        {detail.thresholds ? (
          <div className="flex flex-wrap gap-2">
            {Object.entries(detail.thresholds as Record<string, number>).map(([key, value]) => (
              <span key={key} className="rounded border border-line bg-surface px-2 py-1 text-2xs text-muted">
                {key.replace(/_/g, ' ')} <span className="tnum text-ink">{value}</span>
              </span>
            ))}
          </div>
        ) : null}
        {Array.isArray(detail.reason_codes) && detail.reason_codes.length ? (
          <div className="flex flex-wrap gap-1.5">
            {detail.reason_codes.map((code: string) => (
              <span key={code} className="rounded bg-raised px-1.5 py-0.5 font-mono text-2xs text-muted">
                {code}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  return <pre className="overflow-x-auto text-2xs text-muted">{JSON.stringify(detail, null, 2)}</pre>;
}

export function FactorBars({ factors, valueKey = 'impact_pct' }: { factors: any[]; valueKey?: string }) {
  if (!factors?.length) return <p className="text-xs text-muted">No attribution available.</p>;
  const max = Math.max(...factors.map((factor) => Math.abs(Number(factor[valueKey] ?? 0))), 0.0001);

  return (
    <ul className="space-y-2">
      {factors.map((factor, index) => {
        const raw = Number(factor[valueKey] ?? 0);
        const width = (Math.abs(raw) / max) * 100;
        const positive = (factor.contribution ?? raw) >= 0;
        return (
          <li key={`${factor.key ?? factor.feature}-${index}`} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                {factor.source ? (
                  <span className="rounded bg-raised px-1 py-0.5 text-[9px] uppercase tracking-wide text-faint">
                    {factor.source}
                  </span>
                ) : null}
                <span className="truncate text-xs text-ink">{factor.label ?? factor.feature ?? factor.key}</span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-line">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${width}%` }}
                  transition={{ duration: 0.5, delay: index * 0.04, ease: [0.22, 1, 0.36, 1] }}
                  className={cn('h-full rounded-full', positive ? 'bg-critical/70' : 'bg-positive/70')}
                />
              </div>
            </div>
            <span className={cn('tnum text-xs', positive ? 'text-critical' : 'text-positive')}>
              {valueKey === 'points' ? `${raw >= 0 ? '+' : ''}${raw.toFixed(1)}` : `${raw.toFixed(1)}%`}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '--';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return value.toLocaleString();
    return value.toFixed(Math.abs(value) < 1 ? 4 : 2);
  }
  if (Array.isArray(value)) return value.slice(0, 3).join(', ');
  if (typeof value === 'object') return JSON.stringify(value).slice(0, 40);
  return String(value);
}

function symbolFor(operator: string): string {
  return (
    {
      gt: '>',
      gte: '≥',
      lt: '<',
      lte: '≤',
      eq: '=',
      ne: '≠',
      in: '∈',
      not_in: '∉',
      between: 'between',
      is_true: 'is true',
      is_false: 'is false',
    }[operator] ?? operator
  );
}

/** Risk band label used next to a trace. */
export function BandLabel({ band, score }: { band: string; score: number }) {
  return (
    <span className={cn('tnum text-sm font-medium', riskTextClass(band))}>
      {formatScore(score, 1)} <span className="text-2xs uppercase tracking-wide">{band}</span>
    </span>
  );
}
