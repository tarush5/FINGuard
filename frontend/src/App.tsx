import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Suspense, lazy, useEffect, type ReactNode } from 'react';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { ToastProvider } from '@/components/ui';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/store/auth';

/* Routes are code-split: the shell and the command center load first, every
 * other screen arrives on demand. */
const Landing = lazy(() => import('@/pages/Landing'));
const Login = lazy(() => import('@/pages/Login'));
const CommandCenter = lazy(() => import('@/pages/CommandCenter'));
const Transactions = lazy(() => import('@/pages/Transactions'));
const TransactionDetail = lazy(() => import('@/pages/TransactionDetail'));
const Alerts = lazy(() => import('@/pages/Alerts'));
const Cases = lazy(() => import('@/pages/Cases'));
const CaseDetail = lazy(() => import('@/pages/CaseDetail'));
const FraudRings = lazy(() => import('@/pages/FraudRings'));
const Rules = lazy(() => import('@/pages/Rules'));
const Customers = lazy(() => import('@/pages/Customers'));
const CustomerDetail = lazy(() => import('@/pages/CustomerDetail'));
const Merchants = lazy(() => import('@/pages/Merchants'));
const Simulator = lazy(() => import('@/pages/Simulator'));
const FinancialAnalytics = lazy(() => import('@/pages/FinancialAnalytics'));
const FraudAnalytics = lazy(() => import('@/pages/FraudAnalytics'));
const Forecasting = lazy(() => import('@/pages/Forecasting'));
const Models = lazy(() => import('@/pages/Models'));
const Experiments = lazy(() => import('@/pages/Experiments'));
const Monitoring = lazy(() => import('@/pages/Monitoring'));
const AIInvestigator = lazy(() => import('@/pages/AIInvestigator'));
const DataSources = lazy(() => import('@/pages/DataSources'));
const Pipelines = lazy(() => import('@/pages/Pipelines'));
const Quality = lazy(() => import('@/pages/Quality'));
const Lineage = lazy(() => import('@/pages/Lineage'));
const Users = lazy(() => import('@/pages/Users'));
const Policies = lazy(() => import('@/pages/Policies'));
const Audit = lazy(() => import('@/pages/Audit'));
const SystemHealth = lazy(() => import('@/pages/SystemHealth'));
const NotFound = lazy(() => import('@/pages/NotFound'));

// eslint-disable-next-line react-refresh/only-export-components
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 20_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Never retry an auth or permission failure - it will not succeed.
        if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) return false;
        return failureCount < 2;
      },
    },
  },
});

function FullScreenLoader({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex h-screen items-center justify-center bg-void">
      <div className="flex flex-col items-center gap-3">
        <Loader2 className="h-6 w-6 animate-spin text-info" />
        <p className="text-xs text-muted">{label}…</p>
      </div>
    </div>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuth((state) => state.status);
  const location = useLocation();

  if (status === 'idle' || status === 'loading') return <FullScreenLoader label="Restoring session" />;
  if (status !== 'authenticated') return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  return <>{children}</>;
}

function Bootstrap({ children }: { children: ReactNode }) {
  const bootstrap = useAuth((state) => state.bootstrap);
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Bootstrap>
            <a
              href="#main-content"
              className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[100] focus:rounded focus:bg-info focus:px-3 focus:py-2 focus:text-sm focus:text-void"
            >
              Skip to content
            </a>
            <Suspense fallback={<FullScreenLoader />}>
              <Routes>
                <Route path="/" element={<Landing />} />
                <Route path="/login" element={<Login />} />

                <Route
                  path="/app"
                  element={
                    <RequireAuth>
                      <AppShell />
                    </RequireAuth>
                  }
                >
                  <Route index element={<CommandCenter />} />
                  <Route path="transactions" element={<Transactions />} />
                  <Route path="transactions/:transactionId" element={<TransactionDetail />} />
                  <Route path="alerts" element={<Alerts />} />
                  <Route path="cases" element={<Cases />} />
                  <Route path="cases/:caseId" element={<CaseDetail />} />
                  <Route path="rings" element={<FraudRings />} />
                  <Route path="rules" element={<Rules />} />
                  <Route path="customers" element={<Customers />} />
                  <Route path="customers/:customerId" element={<CustomerDetail />} />
                  <Route path="merchants" element={<Merchants />} />
                  <Route path="simulator" element={<Simulator />} />
                  <Route path="analytics/financial" element={<FinancialAnalytics />} />
                  <Route path="analytics/fraud" element={<FraudAnalytics />} />
                  <Route path="analytics/forecasting" element={<Forecasting />} />
                  <Route path="ml/models" element={<Models />} />
                  <Route path="ml/experiments" element={<Experiments />} />
                  <Route path="ml/monitoring" element={<Monitoring />} />
                  <Route path="ai" element={<AIInvestigator />} />
                  <Route path="data/sources" element={<DataSources />} />
                  <Route path="data/pipelines" element={<Pipelines />} />
                  <Route path="data/quality" element={<Quality />} />
                  <Route path="data/lineage" element={<Lineage />} />
                  <Route path="governance/users" element={<Users />} />
                  <Route path="governance/policies" element={<Policies />} />
                  <Route path="governance/audit" element={<Audit />} />
                  <Route path="system" element={<SystemHealth />} />
                </Route>

                <Route path="*" element={<NotFound />} />
              </Routes>
            </Suspense>
          </Bootstrap>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
