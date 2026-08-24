import { motion } from 'framer-motion';
import { ArrowRight, Lock, Mail, ShieldCheck } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { BrandMark } from '@/components/layout/Sidebar';
import { Button, Input } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/store/auth';

/** Demo identities seeded by `python -m app.datagen.seed`. */
const DEMO_ACCOUNTS = [
  { role: 'Admin', email: 'admin@finguard.io', blurb: 'Full platform access' },
  { role: 'Risk Analyst', email: 'risk.analyst@finguard.io', blurb: 'Rules, thresholds, simulation' },
  { role: 'Investigator', email: 'investigator@finguard.io', blurb: 'Cases and unmasked PII' },
  { role: 'Data Scientist', email: 'scientist@finguard.io', blurb: 'Models, drift, retraining' },
  { role: 'Executive', email: 'exec@finguard.io', blurb: 'Read-only reporting' },
  { role: 'Auditor', email: 'auditor@finguard.io', blurb: 'Audit trail and governance' },
];

const DEMO_PASSWORD = 'FinGuard#2026';

export default function Login() {
  const login = useAuth((state) => state.login);
  const status = useAuth((state) => state.status);
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };

  const [email, setEmail] = useState('admin@finguard.io');
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (status === 'authenticated') return <Navigate to={location.state?.from ?? '/app'} replace />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(location.state?.from ?? '/app', { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Unable to sign in right now.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Left: form */}
      <div className="flex flex-col justify-center px-6 py-12 sm:px-12 lg:px-16">
        <div className="mx-auto w-full max-w-sm">
          <Link to="/" className="mb-10 flex items-center gap-3">
            <BrandMark className="h-9 w-9" />
            <div>
              <p className="text-base font-semibold tracking-tight text-ink">FINGuard</p>
              <p className="text-2xs uppercase tracking-[0.18em] text-faint">Risk Intelligence</p>
            </div>
          </Link>

          <h1 className="text-2xl font-semibold tracking-tight text-ink">Sign in</h1>
          <p className="mt-1.5 text-sm text-muted">
            Access the risk command center with your analyst credentials.
          </p>

          <form onSubmit={onSubmit} className="mt-8 space-y-4">
            <Input
              label="Work email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              icon={<Mail className="h-3.5 w-3.5" />}
            />
            <Input
              label="Password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              icon={<Lock className="h-3.5 w-3.5" />}
              error={error ?? undefined}
            />
            <Button type="submit" variant="primary" size="lg" className="w-full justify-center" loading={submitting}>
              Open Risk Command Center
              <ArrowRight className="h-4 w-4" />
            </Button>
          </form>

          <div className="mt-8">
            <p className="label mb-2">Demo identities</p>
            <p className="mb-3 text-2xs text-faint">
              Each role sees a different slice of the platform. Password for all: <span className="font-mono text-muted">{DEMO_PASSWORD}</span>
            </p>
            <div className="grid gap-1.5">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  type="button"
                  onClick={() => {
                    setEmail(account.email);
                    setPassword(DEMO_PASSWORD);
                    setError(null);
                  }}
                  className="flex items-center justify-between rounded-lg border border-line bg-surface px-3 py-2 text-left transition-colors hover:border-info/30 hover:bg-panel"
                >
                  <span>
                    <span className="block text-xs text-ink">{account.role}</span>
                    <span className="block text-2xs text-faint">{account.blurb}</span>
                  </span>
                  <span className="font-mono text-2xs text-muted">{account.email.split('@')[0]}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Right: context panel */}
      <div className="relative hidden overflow-hidden border-l border-line bg-base lg:block">
        <div className="absolute inset-0 bg-grid bg-grid opacity-60" aria-hidden />
        <div className="absolute inset-0 bg-radial-fade" aria-hidden />
        <div className="relative flex h-full flex-col justify-center px-16">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="max-w-md"
          >
            <span className="inline-flex items-center gap-2 rounded-full border border-info/25 bg-info/10 px-3 py-1 text-2xs uppercase tracking-[0.16em] text-info">
              <ShieldCheck className="h-3 w-3" /> Decision path
            </span>
            <h2 className="mt-6 text-balance text-3xl font-semibold leading-tight tracking-tight text-ink">
              Every decision is traceable, from raw event to analyst verdict.
            </h2>
            <p className="mt-4 text-sm leading-relaxed text-muted">
              FINGuard scores each transaction through point-in-time features, an
              analyst-authored rule engine, a gradient boosted model, graph
              neighbourhood risk and a cost-aware decision policy — then records
              exactly why it landed where it did.
            </p>

            <ol className="mt-8 space-y-3">
              {[
                ['Validate & deduplicate', 'Idempotent ingestion keyed on event id'],
                ['Feature engineering', '35 point-in-time features, no leakage'],
                ['Rules & model', 'Configurable rules + XGBoost with SHAP'],
                ['Graph intelligence', 'Device, IP and ring neighbourhood'],
                ['Decision & case', 'Cost-optimised thresholds, auditable trace'],
              ].map(([title, detail], index) => (
                <motion.li
                  key={title}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.2 + index * 0.08 }}
                  className="flex gap-3"
                >
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line bg-surface font-mono text-2xs text-info">
                    {index + 1}
                  </span>
                  <span>
                    <span className="block text-sm text-ink">{title}</span>
                    <span className="block text-2xs text-faint">{detail}</span>
                  </span>
                </motion.li>
              ))}
            </ol>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
