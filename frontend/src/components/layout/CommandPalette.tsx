/**
 * Command palette (Ctrl/Cmd-K).
 *
 * Combines static navigation commands with live entity search: typing an id or
 * name queries transactions, customers, merchants and cases through the API and
 * jumps straight to the record.
 */
import { useQuery } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  ArrowRight,
  Brain,
  CornerDownLeft,
  FileSearch,
  Loader2,
  Network,
  Search,
  Store,
  Users,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { formatCurrency, riskTextClass } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuth } from '@/store/auth';
import { useUi } from '@/store/ui';

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  group: string;
  permission?: string;
  run: () => void;
}

export function CommandPalette() {
  const open = useUi((state) => state.commandOpen);
  const setOpen = useUi((state) => state.setCommandOpen);
  const navigate = useNavigate();
  const can = useAuth((state) => state.can);
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(!open);
      }
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, setOpen]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setCursor(0);
      setTimeout(() => inputRef.current?.focus(), 40);
    }
  }, [open]);

  const trimmed = query.trim();
  const searchEnabled = open && trimmed.length >= 2;

  const search = useQuery({
    queryKey: ['command-search', trimmed],
    enabled: searchEnabled,
    staleTime: 15_000,
    queryFn: async () => {
      const [transactions, customers, merchants, cases] = await Promise.all([
        can('transaction:read')
          ? api.get<any>('/transactions', { search: trimmed, page_size: 4 }).catch(() => ({ items: [] }))
          : Promise.resolve({ items: [] }),
        can('customer:read')
          ? api.get<any>('/customers', { search: trimmed, page_size: 4 }).catch(() => ({ items: [] }))
          : Promise.resolve({ items: [] }),
        can('merchant:read')
          ? api.get<any>('/merchants', { search: trimmed, page_size: 4 }).catch(() => ({ items: [] }))
          : Promise.resolve({ items: [] }),
        can('case:read')
          ? api.get<any>('/cases', { search: trimmed, page_size: 4 }).catch(() => ({ items: [] }))
          : Promise.resolve({ items: [] }),
      ]);
      return { transactions: transactions.items, customers: customers.items, merchants: merchants.items, cases: cases.items };
    },
  });

  const staticCommands: Command[] = useMemo(
    () => [
      { id: 'nav-home', label: 'Command Center', icon: <Activity className="h-4 w-4" />, group: 'Navigate', run: () => navigate('/app') },
      {
        id: 'nav-transactions',
        label: 'Transactions',
        icon: <Activity className="h-4 w-4" />,
        group: 'Navigate',
        permission: 'transaction:read',
        run: () => navigate('/app/transactions'),
      },
      {
        id: 'nav-cases',
        label: 'Open cases',
        icon: <FileSearch className="h-4 w-4" />,
        group: 'Navigate',
        permission: 'case:read',
        run: () => navigate('/app/cases'),
      },
      {
        id: 'nav-rings',
        label: 'Fraud rings',
        icon: <Network className="h-4 w-4" />,
        group: 'Navigate',
        permission: 'graph:read',
        run: () => navigate('/app/rings'),
      },
      {
        id: 'nav-rules',
        label: 'Rule management',
        icon: <Activity className="h-4 w-4" />,
        group: 'Navigate',
        permission: 'rule:read',
        run: () => navigate('/app/rules'),
      },
      {
        id: 'nav-ai',
        label: 'Ask the AI investigator',
        hint: 'Evidence-grounded answers',
        icon: <Brain className="h-4 w-4" />,
        group: 'AI',
        permission: 'ai:query',
        run: () => navigate('/app/ai'),
      },
      {
        id: 'nav-sql',
        label: 'Run a natural-language SQL query',
        icon: <Brain className="h-4 w-4" />,
        group: 'AI',
        permission: 'ai:sql',
        run: () => navigate('/app/ai?tab=sql'),
      },
      {
        id: 'nav-models',
        label: 'Model registry',
        icon: <Activity className="h-4 w-4" />,
        group: 'ML',
        permission: 'model:read',
        run: () => navigate('/app/ml/models'),
      },
      {
        id: 'nav-monitoring',
        label: 'Model monitoring & drift',
        icon: <Activity className="h-4 w-4" />,
        group: 'ML',
        permission: 'monitoring:read',
        run: () => navigate('/app/ml/monitoring'),
      },
      {
        id: 'nav-pipelines',
        label: 'Pipeline runs',
        icon: <Activity className="h-4 w-4" />,
        group: 'Data',
        permission: 'data:read',
        run: () => navigate('/app/data/pipelines'),
      },
      {
        id: 'nav-quality',
        label: 'Data quality',
        icon: <Activity className="h-4 w-4" />,
        group: 'Data',
        permission: 'data:read',
        run: () => navigate('/app/data/quality'),
      },
      {
        id: 'nav-simulator',
        label: 'Policy simulator',
        icon: <Activity className="h-4 w-4" />,
        group: 'Risk',
        permission: 'risk:simulate',
        run: () => navigate('/app/simulator'),
      },
    ],
    [navigate],
  );

  const filteredCommands = useMemo(() => {
    const lower = trimmed.toLowerCase();
    return staticCommands
      .filter((command) => !command.permission || can(command.permission))
      .filter((command) => !lower || command.label.toLowerCase().includes(lower) || command.group.toLowerCase().includes(lower));
  }, [staticCommands, trimmed, can]);

  const entityCommands: Command[] = useMemo(() => {
    if (!search.data) return [];
    const commands: Command[] = [];
    search.data.transactions?.forEach((txn: any) =>
      commands.push({
        id: `txn-${txn.id}`,
        label: txn.id,
        hint: `${formatCurrency(txn.amount, txn.currency)} · ${txn.decision}`,
        icon: <Activity className={cn('h-4 w-4', riskTextClass(txn.risk_band))} />,
        group: 'Transactions',
        run: () => navigate(`/app/transactions/${txn.id}`),
      }),
    );
    search.data.customers?.forEach((customer: any) =>
      commands.push({
        id: `cust-${customer.id}`,
        label: `${customer.id} · ${customer.full_name}`,
        hint: `${customer.risk_band} · ${customer.transaction_count} txns`,
        icon: <Users className="h-4 w-4" />,
        group: 'Customers',
        run: () => navigate(`/app/customers/${customer.id}`),
      }),
    );
    search.data.merchants?.forEach((merchant: any) =>
      commands.push({
        id: `merch-${merchant.id}`,
        label: `${merchant.name}`,
        hint: `${merchant.category} · risk ${merchant.risk_score}`,
        icon: <Store className="h-4 w-4" />,
        group: 'Merchants',
        run: () => navigate(`/app/merchants/${merchant.id}`),
      }),
    );
    search.data.cases?.forEach((item: any) =>
      commands.push({
        id: `case-${item.id}`,
        label: `${item.case_number} · ${item.title}`,
        hint: item.status,
        icon: <FileSearch className="h-4 w-4" />,
        group: 'Cases',
        run: () => navigate(`/app/cases/${item.id}`),
      }),
    );
    return commands;
  }, [search.data, navigate]);

  const all = useMemo(
    () => [...entityCommands, ...filteredCommands],
    [entityCommands, filteredCommands],
  );
  const grouped = useMemo(() => {
    const groups = new Map<string, Command[]>();
    all.forEach((command) => {
      if (!groups.has(command.group)) groups.set(command.group, []);
      groups.get(command.group)!.push(command);
    });
    return Array.from(groups.entries());
  }, [all]);

  useEffect(() => setCursor(0), [query, search.data]);

  const run = (command: Command) => {
    command.run();
    setOpen(false);
  };

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-[70] flex items-start justify-center bg-void/85 p-4 pt-[12vh] backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => setOpen(false)}
        >
          <motion.div
            initial={{ opacity: 0, y: -10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            onClick={(event) => event.stopPropagation()}
            className="panel w-full max-w-2xl overflow-hidden p-0"
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
          >
            <div className="flex items-center gap-3 border-b border-line px-4 py-3">
              <Search className="h-4 w-4 text-faint" />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    setCursor((value) => Math.min(value + 1, all.length - 1));
                  } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    setCursor((value) => Math.max(value - 1, 0));
                  } else if (event.key === 'Enter' && all[cursor]) {
                    event.preventDefault();
                    run(all[cursor]);
                  }
                }}
                placeholder="Search transactions, customers, merchants, cases or commands…"
                className="flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
                aria-label="Command palette search"
              />
              {search.isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin text-faint" /> : null}
              <kbd className="rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-[10px] text-muted">esc</kbd>
            </div>

            <div className="max-h-[54vh] overflow-y-auto py-2">
              {grouped.length === 0 ? (
                <p className="px-4 py-8 text-center text-xs text-faint">
                  {trimmed.length >= 2 ? 'No matching records or commands.' : 'Type at least two characters to search records.'}
                </p>
              ) : (
                grouped.map(([group, commands]) => (
                  <div key={group} className="mb-1">
                    <p className="label px-4 py-1.5">{group}</p>
                    {commands.map((command) => {
                      const index = all.indexOf(command);
                      const active = index === cursor;
                      return (
                        <button
                          key={command.id}
                          type="button"
                          onMouseEnter={() => setCursor(index)}
                          onClick={() => run(command)}
                          className={cn(
                            'flex w-full items-center gap-3 px-4 py-2 text-left transition-colors',
                            active ? 'bg-info/10' : 'hover:bg-raised/60',
                          )}
                        >
                          <span className="text-faint">{command.icon}</span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-ink">{command.label}</span>
                            {command.hint ? <span className="block truncate text-2xs text-muted">{command.hint}</span> : null}
                          </span>
                          {active ? <CornerDownLeft className="h-3.5 w-3.5 text-faint" /> : <ArrowRight className="h-3.5 w-3.5 text-transparent" />}
                        </button>
                      );
                    })}
                  </div>
                ))
              )}
            </div>

            <div className="flex items-center gap-4 border-t border-line px-4 py-2 text-[10px] text-faint">
              <span>↑↓ navigate</span>
              <span>↵ open</span>
              <span>esc close</span>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
