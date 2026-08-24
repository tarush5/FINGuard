/** Public landing page: what FINGuard is, how it decides, and what it is built on. */
import { motion, useReducedMotion } from 'framer-motion';
import {
  ArrowRight,
  Boxes,
  Brain,
  Database,
  Gauge,
  GitBranch,
  Network,
  ScrollText,
  ShieldCheck,
  Workflow,
  Zap,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BrandMark } from '@/components/layout/Sidebar';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';

const CAPABILITIES = [
  {
    icon: <Zap className="h-5 w-5" />,
    title: 'Real-time risk',
    body: 'Every transaction runs through validation, deduplication, point-in-time features, rules, models and graph risk before a decision is returned.',
  },
  {
    icon: <ShieldCheck className="h-5 w-5" />,
    title: 'Fraud intelligence',
    body: 'A configurable rule engine sits alongside a gradient boosted classifier and an unsupervised anomaly detector — each contributing a measurable share of the score.',
  },
  {
    icon: <Network className="h-5 w-5" />,
    title: 'Graph detection',
    body: 'Shared devices, shared IPs and coordinated cash-out patterns surface as connected components with centrality and density scoring.',
  },
  {
    icon: <Brain className="h-5 w-5" />,
    title: 'Explainable AI',
    body: 'Tree SHAP attributions per prediction, unified with rule hits and graph signals into one ranked explanation an auditor can follow.',
  },
  {
    icon: <Gauge className="h-5 w-5" />,
    title: 'Decision engine',
    body: 'Thresholds are configuration, and the operating point is chosen by minimising expected business cost rather than defaulting to 0.5.',
  },
  {
    icon: <Boxes className="h-5 w-5" />,
    title: 'ML monitoring',
    body: 'Registry, promotion and rollback, PSI-based feature and prediction drift, and an analyst feedback loop that feeds retraining.',
  },
  {
    icon: <Database className="h-5 w-5" />,
    title: 'Financial analytics',
    body: 'Loss accounting, detection performance, merchant and customer risk, heatmaps and forecasts with stated backtest error.',
  },
  {
    icon: <ScrollText className="h-5 w-5" />,
    title: 'Enterprise governance',
    body: 'Permission-based RBAC, PII masking at the serialisation boundary, an append-only audit trail and full AI query logging.',
  },
];

const PIPELINE = [
  { label: 'Transaction', detail: 'Kafka topic transactions.raw' },
  { label: 'Validation', detail: 'Schema + idempotency' },
  { label: 'Features', detail: '35 point-in-time features' },
  { label: 'Rules', detail: 'Analyst-authored, versioned' },
  { label: 'Model', detail: 'XGBoost + isolation forest' },
  { label: 'Graph', detail: 'Device / IP neighbourhood' },
  { label: 'Risk', detail: 'Weighted ensemble 0-100' },
  { label: 'Decision', detail: 'Approve / step-up / review / decline' },
];

const STACK = [
  ['Backend', 'FastAPI · SQLAlchemy · Pydantic · PostgreSQL · Redis'],
  ['Streaming', 'Kafka topics with retries, DLQ and idempotent consumers'],
  ['ML', 'XGBoost · scikit-learn · SHAP · MLflow registry'],
  ['Graph', 'NetworkX projection over the relational store (Neo4j optional)'],
  ['Frontend', 'React · TypeScript · Vite · Tailwind · TanStack Query'],
  ['Platform', 'Docker Compose · GitHub Actions · Airflow · dbt'],
];

export default function Landing() {
  const reduceMotion = useReducedMotion();
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (reduceMotion) return;
    const timer = setInterval(() => setStep((value) => (value + 1) % (PIPELINE.length + 2)), 900);
    return () => clearInterval(timer);
  }, [reduceMotion]);

  return (
    <div className="min-h-screen bg-void">
      {/* Nav */}
      <header className="sticky top-0 z-40 border-b border-line bg-void/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
          <Link to="/" className="flex items-center gap-3">
            <BrandMark className="h-8 w-8" />
            <div>
              <p className="text-sm font-semibold tracking-tight text-ink">FINGuard</p>
              <p className="text-[10px] uppercase tracking-[0.18em] text-faint">Risk Intelligence</p>
            </div>
          </Link>
          <nav className="hidden items-center gap-6 md:flex">
            {['Capabilities', 'Architecture', 'Security', 'Stack'].map((item) => (
              <a key={item} href={`#${item.toLowerCase()}`} className="text-xs text-muted transition-colors hover:text-ink">
                {item}
              </a>
            ))}
          </nav>
          <Link to="/login">
            <Button variant="primary" size="sm">
              Open command center
              <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </Link>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden border-b border-line">
        <div className="absolute inset-0 bg-grid bg-grid opacity-70" aria-hidden />
        <div className="absolute inset-0 bg-radial-fade" aria-hidden />
        <div className="relative mx-auto grid max-w-7xl gap-12 px-6 py-20 lg:grid-cols-2 lg:py-28">
          <div>
            <motion.span
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              className="inline-flex items-center gap-2 rounded-full border border-info/25 bg-info/10 px-3 py-1 text-2xs uppercase tracking-[0.16em] text-info"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-info" />
              Financial crime platform
            </motion.span>

            <motion.h1
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.05 }}
              className="mt-6 text-balance text-4xl font-semibold leading-[1.1] tracking-tight text-ink sm:text-5xl lg:text-6xl"
            >
              Detect fraud. Understand risk. Act in real time.
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className="mt-6 max-w-xl text-base leading-relaxed text-muted"
            >
              FINGuard turns financial events into intelligent risk decisions using real-time analytics, machine
              learning, graph intelligence and explainable AI — and records exactly why every decision was made.
            </motion.p>

            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 }}
              className="mt-8 flex flex-wrap gap-3"
            >
              <Link to="/login">
                <Button variant="primary" size="lg">
                  Open Risk Command Center
                  <ArrowRight className="h-4 w-4" />
                </Button>
              </Link>
              <a href="#architecture">
                <Button variant="outline" size="lg">
                  Explore architecture
                </Button>
              </a>
            </motion.div>

            <motion.dl
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.25 }}
              className="mt-12 grid max-w-lg grid-cols-3 gap-6 border-t border-line pt-8"
            >
              {[
                ['6', 'decision stages'],
                ['35', 'point-in-time features'],
                ['10', 'Kafka topics'],
              ].map(([value, label]) => (
                <div key={label}>
                  <dt className="tnum text-2xl font-semibold text-ink">{value}</dt>
                  <dd className="mt-1 text-2xs uppercase tracking-wide text-faint">{label}</dd>
                </div>
              ))}
            </motion.dl>
          </div>

          {/* Animated decision flow */}
          <div className="relative flex items-center justify-center">
            <div className="w-full max-w-sm">
              <div className="panel p-6">
                <p className="label mb-4">Live decision path</p>
                <ol className="space-y-2">
                  {PIPELINE.map((stage, index) => {
                    const active = !reduceMotion && step === index;
                    const passed = !reduceMotion && step > index;
                    return (
                      <li
                        key={stage.label}
                        className={cn(
                          'flex items-center gap-3 rounded-lg border px-3 py-2 transition-all duration-300',
                          active
                            ? 'border-info/40 bg-info/[0.08]'
                            : passed
                              ? 'border-line bg-surface'
                              : 'border-line/60 bg-surface/40',
                        )}
                      >
                        <span
                          className={cn(
                            'flex h-6 w-6 shrink-0 items-center justify-center rounded font-mono text-[10px]',
                            active ? 'bg-info text-void' : passed ? 'bg-raised text-positive' : 'bg-raised text-faint',
                          )}
                        >
                          {index + 1}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className={cn('block text-xs', active ? 'text-ink' : 'text-muted')}>{stage.label}</span>
                          <span className="block truncate text-[10px] text-faint">{stage.detail}</span>
                        </span>
                      </li>
                    );
                  })}
                </ol>

                <div className="mt-5 rounded-lg border border-critical/25 bg-critical/[0.07] p-4 text-center">
                  <p className="label">Ensemble risk score</p>
                  <p className="tnum mt-1 text-4xl font-semibold text-critical">87</p>
                  <p className="mt-1 text-2xs uppercase tracking-[0.16em] text-critical">Manual review</p>
                </div>
                <p className="mt-3 text-center text-[10px] text-faint">
                  Illustrative example of the trace rendered for every transaction.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Capabilities */}
      <section id="capabilities" className="border-b border-line py-20">
        <div className="mx-auto max-w-7xl px-6">
          <h2 className="text-balance text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            One platform, from raw event to analyst verdict
          </h2>
          <p className="mt-3 max-w-2xl text-sm text-muted">
            Each capability is wired to the next: rules feed the ensemble, the ensemble feeds the decision, decisions
            open cases, and analyst verdicts train the next model.
          </p>

          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITIES.map((capability, index) => (
              <motion.div
                key={capability.title}
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-80px' }}
                transition={{ delay: (index % 4) * 0.06 }}
                className="panel group p-5 transition-colors hover:border-line-strong"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-line bg-surface text-info transition-colors group-hover:border-info/30">
                  {capability.icon}
                </div>
                <h3 className="mt-4 text-sm font-semibold text-ink">{capability.title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-muted">{capability.body}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Architecture */}
      <section id="architecture" className="border-b border-line bg-base py-20">
        <div className="mx-auto max-w-7xl px-6">
          <h2 className="text-balance text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Architecture</h2>
          <p className="mt-3 max-w-2xl text-sm text-muted">
            Scoring is synchronous because the caller needs an answer. Everything downstream — alerting, case creation,
            notifications, monitoring — is fanned out over the event bus, and consumers are idempotent so at-least-once
            delivery is safe.
          </p>

          <div className="mt-10 grid gap-4 lg:grid-cols-3">
            {[
              {
                icon: <Workflow className="h-4 w-4" />,
                title: 'Event-driven core',
                items: [
                  'transactions.raw → validated → enriched',
                  'fraud.predictions · risk.events',
                  'alerts.created · cases.created',
                  'analyst.feedback · model.events',
                  'Retries with backoff, then dead-letter',
                  'Deduplication keyed on event id',
                ],
              },
              {
                icon: <GitBranch className="h-4 w-4" />,
                title: 'Data platform',
                items: [
                  'Catalogued datasets with owners and PII flags',
                  'Quality suite across six dimensions',
                  'Interactive lineage including the feedback loop',
                  'Pipeline run history with record counts',
                  'Feature store shared by training and serving',
                  'Point-in-time correctness by construction',
                ],
              },
              {
                icon: <Boxes className="h-4 w-4" />,
                title: 'MLOps',
                items: [
                  'Chronological train/validation/test split',
                  'Cost-minimising threshold selection',
                  'Registry with promotion and rollback',
                  'PSI feature and prediction drift',
                  'Analyst verdicts as retraining labels',
                  'MLflow mirroring when configured',
                ],
              },
            ].map((column) => (
              <div key={column.title} className="panel p-5">
                <div className="flex items-center gap-2 text-info">
                  {column.icon}
                  <h3 className="text-sm font-semibold text-ink">{column.title}</h3>
                </div>
                <ul className="mt-4 space-y-2">
                  {column.items.map((item) => (
                    <li key={item} className="flex gap-2 text-xs text-muted">
                      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-info/60" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Security */}
      <section id="security" className="border-b border-line py-20">
        <div className="mx-auto max-w-7xl px-6">
          <div className="grid gap-10 lg:grid-cols-2">
            <div>
              <h2 className="text-balance text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                Security and governance are structural
              </h2>
              <p className="mt-3 text-sm text-muted">
                Authorisation is permission-based and declared per route. PII masking happens in the serialisation layer
                every response passes through, so a new endpoint cannot leak it by omission. Generated SQL is validated
                against an allow-list filtered by the caller's own permissions.
              </p>
              <ul className="mt-6 space-y-2.5">
                {[
                  'JWT access tokens with server-side refresh rotation and reuse detection',
                  'Seven roles mapped to fine-grained permissions',
                  'PII masked for every role without customer:pii_read',
                  'Rate limiting, security headers, request validation',
                  'Append-only audit trail with request and model versions',
                  'AI queries logged; generated SQL blocked unless read-only',
                ].map((item) => (
                  <li key={item} className="flex gap-3 text-sm text-muted">
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-positive" />
                    {item}
                  </li>
                ))}
              </ul>
            </div>

            <div id="stack">
              <h2 className="text-balance text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Technology</h2>
              <dl className="mt-6 space-y-3">
                {STACK.map(([label, value]) => (
                  <div key={label} className="panel px-4 py-3">
                    <dt className="label">{label}</dt>
                    <dd className="mt-1 text-xs text-muted">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h2 className="text-balance text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            See a decision explained end to end
          </h2>
          <p className="mt-3 text-sm text-muted">
            The demo environment ships with a synthetic portfolio, a trained model and one-click scenarios for account
            takeover, card testing, fraud rings, false positives and model drift.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <Link to="/login">
              <Button variant="primary" size="lg">
                Open Risk Command Center
                <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          </div>
          <p className="mt-6 text-2xs text-faint">
            All demonstration data is synthetic. No real customer or payment information is used anywhere in this
            platform.
          </p>
        </div>
      </section>

      <footer className="border-t border-line py-8">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-3 px-6 sm:flex-row">
          <div className="flex items-center gap-2.5">
            <BrandMark className="h-6 w-6" />
            <span className="text-xs text-muted">FINGuard · Financial risk intelligence</span>
          </div>
          <p className="text-2xs text-faint">Built as a demonstration of production financial-crime engineering.</p>
        </div>
      </footer>
    </div>
  );
}
