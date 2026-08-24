/**
 * What-if policy simulator and cost-optimal threshold analysis.
 *
 * The simulation replays real transactions through the same ensemble and
 * decision functions used in production, so the deltas shown here are a genuine
 * replay rather than an estimate.
 */
import { useMutation, useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
import { Gauge, RefreshCcw, Sparkles, TrendingDown, TrendingUp } from 'lucide-react';
import { useEffect, useState } from 'react';
import { CategoryBars, TrendChart } from '@/components/charts';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, Button, ErrorState, Panel, PanelHeader, Skeleton } from '@/components/ui';
import { api } from '@/lib/api';
import { DECISION_COLORS, formatCurrency, formatNumber, formatPercent } from '@/lib/format';
import { cn } from '@/lib/utils';

export default function Simulator() {
  const policy = useQuery({ queryKey: ['risk-policy'], queryFn: () => api.get<any>('/risk/policy') });

  const [approve, setApprove] = useState(30);
  const [stepUp, setStepUp] = useState(70);
  const [review, setReview] = useState(85);
  const [days, setDays] = useState(30);

  useEffect(() => {
    if (policy.data) {
      setApprove(policy.data.thresholds.approve_below);
      setStepUp(policy.data.thresholds.step_up_below);
      setReview(policy.data.thresholds.review_below);
    }
  }, [policy.data]);

  const simulate = useMutation({
    mutationFn: () =>
      api.post<any>('/risk/simulate', {
        approve_below: approve,
        step_up_below: stepUp,
        review_below: review,
        days,
        sample_size: 6000,
      }),
  });

  const thresholds = useQuery({
    queryKey: ['threshold-optimisation'],
    queryFn: () => api.get<any>('/risk/threshold-optimisation', { days: 90, sample_size: 12000 }),
  });

  const impact = simulate.data?.impact;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Risk policy simulator"
        description="Move the decision thresholds and replay real history to see the effect on losses, false positives and analyst workload."
        actions={
          <Button
            variant="primary"
            icon={<RefreshCcw className="h-3.5 w-3.5" />}
            loading={simulate.isPending}
            onClick={() => simulate.mutate()}
          >
            Run simulation
          </Button>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[380px_1fr]">
        <Panel>
          <PanelHeader title="Candidate policy" subtitle="Score bands that map risk onto an action" icon={<Gauge className="h-4 w-4" />} />
          <div className="space-y-5 p-5">
            <Slider
              label="Approve below"
              value={approve}
              onChange={(value) => {
                setApprove(value);
                if (value > stepUp) setStepUp(value);
              }}
              accent="#34D399"
              hint="Everything under this score is auto-approved."
            />
            <Slider
              label="Step-up below"
              value={stepUp}
              min={approve}
              onChange={(value) => {
                setStepUp(value);
                if (value > review) setReview(value);
              }}
              accent="#FBBF24"
              hint="Step-up authentication band."
            />
            <Slider
              label="Manual review below"
              value={review}
              min={stepUp}
              onChange={setReview}
              accent="#FB923C"
              hint="Above this score the transaction is declined."
            />

            <div className="rounded-lg border border-line bg-surface p-3">
              <p className="label mb-2">Resulting bands</p>
              <ul className="space-y-1 text-xs">
                <li className="flex justify-between">
                  <span className="text-positive">Approve</span>
                  <span className="tnum text-muted">0 – {approve.toFixed(0)}</span>
                </li>
                <li className="flex justify-between">
                  <span className="text-warning">Step up</span>
                  <span className="tnum text-muted">{approve.toFixed(0)} – {stepUp.toFixed(0)}</span>
                </li>
                <li className="flex justify-between">
                  <span className="text-high">Manual review</span>
                  <span className="tnum text-muted">{stepUp.toFixed(0)} – {review.toFixed(0)}</span>
                </li>
                <li className="flex justify-between">
                  <span className="text-critical">Decline</span>
                  <span className="tnum text-muted">{review.toFixed(0)} – 100</span>
                </li>
              </ul>
            </div>

            <label className="block">
              <span className="label mb-1.5 block">Replay window</span>
              <select
                value={days}
                onChange={(event) => setDays(Number(event.target.value))}
                className="h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink"
              >
                <option value={7}>Last 7 days</option>
                <option value={30}>Last 30 days</option>
                <option value={90}>Last 90 days</option>
              </select>
            </label>

            {policy.data ? (
              <p className="text-2xs text-faint">
                Live policy: approve &lt; {policy.data.thresholds.approve_below}, step-up &lt;{' '}
                {policy.data.thresholds.step_up_below}, review &lt; {policy.data.thresholds.review_below} ·{' '}
                {policy.data.policy_version}
              </p>
            ) : null}
          </div>
        </Panel>

        <div className="space-y-4">
          <Panel>
            <PanelHeader
              title="Simulated impact"
              subtitle={
                simulate.data
                  ? `Replayed ${formatNumber(simulate.data.sample_size)} transactions from the last ${simulate.data.window_days} days`
                  : 'Run the simulation to compare against the live policy'
              }
              icon={<Sparkles className="h-4 w-4" />}
            />
            <div className="p-5">
              {simulate.isPending ? (
                <Skeleton className="h-32" />
              ) : simulate.isError ? (
                <ErrorState error={simulate.error} />
              ) : impact ? (
                <>
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                    <ImpactTile label="Expected fraud loss" value={impact.expected_fraud_loss_pct} invert />
                    <ImpactTile label="Prevented loss" value={impact.prevented_loss_pct} />
                    <ImpactTile label="False positives" value={impact.false_positives_pct} invert />
                    <ImpactTile label="Manual reviews" value={impact.manual_reviews_pct} invert />
                    <ImpactTile label="Customer friction" value={impact.customer_friction_pct} invert />
                  </div>

                  <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div>
                      <p className="label mb-2">Decision mix</p>
                      <CategoryBars
                        data={Object.keys({ ...simulate.data.baseline.decisions, ...simulate.data.candidate.decisions }).map((key) => ({
                          decision: key.replace(/_/g, ' '),
                          current: simulate.data.baseline.decisions[key] ?? 0,
                          candidate: simulate.data.candidate.decisions[key] ?? 0,
                        }))}
                        xKey="decision"
                        yKey="candidate"
                        colorBy={(row) => DECISION_COLORS[row.decision.replace(/ /g, '_')] ?? '#38BDF8'}
                        height={200}
                      />
                    </div>
                    <div>
                      <p className="label mb-2">Loss comparison</p>
                      <dl className="space-y-2">
                        {[
                          ['Fraud loss', simulate.data.baseline.fraud_loss, simulate.data.candidate.fraud_loss, true],
                          ['Prevented', simulate.data.baseline.prevented_loss, simulate.data.candidate.prevented_loss, false],
                          ['False positives', simulate.data.baseline.false_positives, simulate.data.candidate.false_positives, true],
                          ['Manual reviews', simulate.data.baseline.manual_reviews, simulate.data.candidate.manual_reviews, true],
                        ].map(([label, before, after, invert]) => (
                          <div key={label as string} className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2">
                            <span className="text-xs text-muted">{label}</span>
                            <span className="tnum flex items-center gap-2 text-xs">
                              <span className="text-faint">
                                {typeof before === 'number' && (before as number) > 1000
                                  ? formatCurrency(before as number, 'INR', true)
                                  : formatNumber(before as number)}
                              </span>
                              <span className="text-faint">→</span>
                              <span
                                className={cn(
                                  (after as number) === (before as number)
                                    ? 'text-muted'
                                    : ((after as number) < (before as number)) === Boolean(invert)
                                      ? 'text-positive'
                                      : 'text-critical',
                                )}
                              >
                                {typeof after === 'number' && (after as number) > 1000
                                  ? formatCurrency(after as number, 'INR', true)
                                  : formatNumber(after as number)}
                              </span>
                            </span>
                          </div>
                        ))}
                      </dl>
                    </div>
                  </div>

                  <p className="mt-4 text-2xs text-faint">{simulate.data.note}</p>
                </>
              ) : (
                <p className="text-sm text-muted">
                  Adjust the thresholds and run the simulation. Each transaction in the window is re-decided with the
                  stored model probability and rule score.
                </p>
              )}
            </div>
          </Panel>

          <Panel>
            <PanelHeader
              title="Cost-optimal model threshold"
              subtitle="Expected business cost across the model probability sweep"
            />
            <div className="p-5">
              {thresholds.isLoading ? (
                <Skeleton className="h-64" />
              ) : thresholds.isError ? (
                <ErrorState error={thresholds.error} />
              ) : (
                <>
                  <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                    {thresholds.data.optimal
                      ? [
                          ['Optimal threshold', thresholds.data.optimal.threshold.toFixed(3)],
                          ['Expected cost', formatCurrency(thresholds.data.optimal.total_cost, 'INR', true)],
                          ['Precision', formatPercent(thresholds.data.optimal.precision * 100, 1)],
                          ['Recall', formatPercent(thresholds.data.optimal.recall * 100, 1)],
                        ].map(([label, value]) => (
                          <div key={label} className="rounded-lg border border-line bg-surface px-3 py-2">
                            <p className="label">{label}</p>
                            <p className="tnum text-sm text-ink">{value}</p>
                          </div>
                        ))
                      : null}
                  </div>
                  <TrendChart
                    data={thresholds.data.curve}
                    xKey="threshold"
                    series={[
                      { key: 'total_cost', name: 'Total expected cost', color: '#F87171' },
                      { key: 'fraud_loss', name: 'Fraud loss', color: '#FB923C' },
                      { key: 'false_positive_cost', name: 'False positive cost', color: '#38BDF8' },
                    ]}
                    height={240}
                    formatter={(value) => formatCurrency(value, 'INR', true)}
                  />
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Badge>FN cost {formatCurrency(thresholds.data.policy.cost_false_negative)}</Badge>
                    <Badge>FP cost {formatCurrency(thresholds.data.policy.cost_false_positive)}</Badge>
                    <Badge>Review cost {formatCurrency(thresholds.data.policy.cost_manual_review)}</Badge>
                    <Badge>{formatNumber(thresholds.data.sample_size)} labelled samples</Badge>
                  </div>
                </>
              )}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Slider({
  label,
  value,
  onChange,
  min = 0,
  max = 100,
  accent,
  hint,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  accent: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="label">{label}</span>
        <span className="tnum text-sm font-medium" style={{ color: accent }}>
          {value.toFixed(0)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-current"
        style={{ accentColor: accent }}
        aria-label={label}
      />
      {hint ? <p className="mt-1 text-2xs text-faint">{hint}</p> : null}
    </div>
  );
}

function ImpactTile({ label, value, invert }: { label: string; value: number; invert?: boolean }) {
  const good = invert ? value < 0 : value > 0;
  const neutral = Math.abs(value) < 0.05;
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="rounded-lg border border-line bg-surface px-3 py-3">
      <p className="label">{label}</p>
      <p
        className={cn(
          'tnum mt-1.5 flex items-center gap-1 text-lg font-semibold',
          neutral ? 'text-muted' : good ? 'text-positive' : 'text-critical',
        )}
      >
        {neutral ? null : value > 0 ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
        {value > 0 ? '+' : ''}
        {value.toFixed(1)}%
      </p>
    </motion.div>
  );
}
