/**
 * FINGuard UI primitives.
 *
 * A small, deliberately plain component set: thin borders, restrained colour,
 * tabular numerals, visible focus rings and keyboard-operable overlays.
 */
import { AnimatePresence, motion } from 'framer-motion';
import { AlertCircle, ChevronLeft, ChevronRight, Loader2, Search, X } from 'lucide-react';
import {
  createContext,
  forwardRef,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react';
import { cn } from '@/lib/utils';

/* -------------------------------------------------------------------- Button */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'ai' | 'outline';
type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

const buttonVariants: Record<ButtonVariant, string> = {
  primary: 'bg-info text-void hover:bg-info/90 font-medium shadow-[0_0_20px_-8px_rgba(56,189,248,0.8)]',
  secondary: 'bg-raised text-ink hover:bg-line-strong border border-line',
  outline: 'border border-line-strong text-ink hover:bg-raised',
  ghost: 'text-muted hover:text-ink hover:bg-raised/70',
  danger: 'bg-critical/15 text-critical border border-critical/30 hover:bg-critical/25',
  ai: 'bg-ai/15 text-ai border border-ai/30 hover:bg-ai/25',
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-9 px-4 text-sm gap-2',
  lg: 'h-11 px-6 text-sm gap-2',
  icon: 'h-9 w-9 justify-center',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'md', loading, icon, children, disabled, ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex select-none items-center rounded-lg transition-all duration-150 ease-swift',
        'disabled:cursor-not-allowed disabled:opacity-50',
        buttonVariants[variant],
        buttonSizes[size],
        className,
      )}
      {...props}
    >
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

/* --------------------------------------------------------------------- Panel */

export function Panel({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('panel', className)} {...props}>
      {children}
    </div>
  );
}

export function PanelHeader({
  title,
  subtitle,
  action,
  icon,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex items-start justify-between gap-4 border-b border-line px-5 py-4', className)}>
      <div className="flex min-w-0 items-start gap-3">
        {icon ? <div className="mt-0.5 text-muted">{icon}</div> : null}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold tracking-tight text-ink">{title}</h3>
          {subtitle ? <p className="mt-0.5 text-xs text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

/* --------------------------------------------------------------------- Badge */

export function Badge({
  children,
  className,
  dot,
}: {
  children: ReactNode;
  className?: string;
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-2xs font-medium uppercase tracking-wide',
        'border-line bg-line/40 text-muted',
        className,
      )}
    >
      {dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden /> : null}
      {children}
    </span>
  );
}

export function StatusDot({ status, className }: { status: string; className?: string }) {
  const color =
    {
      HEALTHY: 'bg-positive',
      PASS: 'bg-positive',
      SUCCESS: 'bg-positive',
      RUNNING: 'bg-info',
      WARNING: 'bg-warning',
      WARN: 'bg-warning',
      CRITICAL: 'bg-critical',
      FAIL: 'bg-critical',
      FAILED: 'bg-critical',
    }[status] ?? 'bg-faint';
  return (
    <span className={cn('relative flex h-2 w-2', className)} aria-label={status}>
      <span className={cn('absolute inline-flex h-full w-full rounded-full opacity-60', color, 'animate-pulse-ring')} />
      <span className={cn('relative inline-flex h-2 w-2 rounded-full', color)} />
    </span>
  );
}

/* --------------------------------------------------------------------- Input */

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  icon?: ReactNode;
  label?: string;
  hint?: string;
  error?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, icon, label, hint, error, id, ...props },
  ref,
) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  return (
    <div className="w-full">
      {label ? (
        <label htmlFor={inputId} className="label mb-1.5 block">
          {label}
        </label>
      ) : null}
      <div className="relative">
        {icon ? (
          <div className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint">{icon}</div>
        ) : null}
        <input
          ref={ref}
          id={inputId}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
          className={cn(
            'h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink placeholder:text-faint',
            'transition-colors focus:border-info/50 focus:bg-panel',
            icon && 'pl-9',
            error && 'border-critical/60',
            className,
          )}
          {...props}
        />
      </div>
      {error ? (
        <p id={`${inputId}-error`} className="mt-1 flex items-center gap-1 text-xs text-critical">
          <AlertCircle className="h-3 w-3" aria-hidden /> {error}
        </p>
      ) : hint ? (
        <p id={`${inputId}-hint`} className="mt-1 text-xs text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
});

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search…',
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
}) {
  return (
    <Input
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      icon={<Search className="h-3.5 w-3.5" aria-hidden />}
      className={className}
      aria-label={placeholder}
    />
  );
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: { value: string; label: string }[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, label, options, id, ...props },
  ref,
) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  return (
    <div className="w-full">
      {label ? (
        <label htmlFor={selectId} className="label mb-1.5 block">
          {label}
        </label>
      ) : null}
      <select
        ref={ref}
        id={selectId}
        className={cn(
          'h-9 w-full appearance-none rounded-lg border border-line bg-surface px-3 text-sm text-ink',
          'transition-colors focus:border-info/50 focus:bg-panel',
          className,
        )}
        {...props}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value} className="bg-panel">
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
});

/* ---------------------------------------------------------------------- Tabs */

interface TabsContextValue {
  value: string;
  setValue: (value: string) => void;
  baseId: string;
}
const TabsContext = createContext<TabsContextValue | null>(null);

export function Tabs({
  value,
  onValueChange,
  children,
  className,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
  className?: string;
}) {
  const baseId = useId();
  const context = useMemo(() => ({ value, setValue: onValueChange, baseId }), [value, onValueChange, baseId]);
  return (
    <TabsContext.Provider value={context}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

export function TabList({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div
      ref={ref}
      role="tablist"
      className={cn('scrollbar-none flex gap-1 overflow-x-auto border-b border-line', className)}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        const tabs = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
        const index = tabs.findIndex((tab) => tab === document.activeElement);
        if (index === -1) return;
        event.preventDefault();
        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
        tabs[next].focus();
        tabs[next].click();
      }}
    >
      {children}
    </div>
  );
}

export function Tab({ value, children, count }: { value: string; children: ReactNode; count?: number }) {
  const context = useContext(TabsContext);
  if (!context) throw new Error('Tab must be used inside Tabs');
  const active = context.value === value;
  return (
    <button
      role="tab"
      type="button"
      aria-selected={active}
      aria-controls={`${context.baseId}-${value}`}
      tabIndex={active ? 0 : -1}
      onClick={() => context.setValue(value)}
      className={cn(
        'relative whitespace-nowrap px-3.5 py-2.5 text-sm transition-colors',
        active ? 'text-ink' : 'text-muted hover:text-ink',
      )}
    >
      <span className="flex items-center gap-2">
        {children}
        {count !== undefined ? (
          <span className="tnum rounded bg-line px-1.5 py-0.5 text-2xs text-muted">{count}</span>
        ) : null}
      </span>
      {active ? (
        <motion.span layoutId={`tab-${context.baseId}`} className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-info" />
      ) : null}
    </button>
  );
}

export function TabPanel({ value, children, className }: { value: string; children: ReactNode; className?: string }) {
  const context = useContext(TabsContext);
  if (!context) throw new Error('TabPanel must be used inside Tabs');
  if (context.value !== value) return null;
  return (
    <div role="tabpanel" id={`${context.baseId}-${value}`} className={cn('animate-slide-up', className)}>
      {children}
    </div>
  );
}

/* --------------------------------------------------------------------- Table */

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn('w-full border-collapse text-left', className)}>{children}</table>
    </div>
  );
}

export function Th({
  children,
  className,
  sortable,
  active,
  direction,
  onSort,
}: {
  children?: ReactNode;
  className?: string;
  sortable?: boolean;
  active?: boolean;
  direction?: 'asc' | 'desc';
  onSort?: () => void;
}) {
  return (
    <th
      scope="col"
      className={cn(
        'sticky top-0 z-10 whitespace-nowrap border-b border-line bg-surface/95 px-3 py-2.5 backdrop-blur',
        'label',
        className,
      )}
      aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : undefined}
    >
      {sortable ? (
        <button
          type="button"
          onClick={onSort}
          className={cn('flex items-center gap-1 transition-colors hover:text-ink', active && 'text-info')}
        >
          {children}
          <span aria-hidden className="text-[9px]">
            {active ? (direction === 'asc' ? '▲' : '▼') : '↕'}
          </span>
        </button>
      ) : (
        children
      )}
    </th>
  );
}

export function Tr({
  children,
  className,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <tr
      onClick={onClick}
      onKeyDown={
        onClick
          ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      tabIndex={onClick ? 0 : undefined}
      className={cn(
        'border-b border-line/60 transition-colors last:border-0',
        onClick && 'cursor-pointer hover:bg-raised/60 focus:bg-raised/60',
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function Td({
  children,
  className,
  colSpan,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  colSpan?: number;
  onClick?: (event: React.MouseEvent<HTMLTableCellElement>) => void;
}) {
  return (
    <td colSpan={colSpan} onClick={onClick} className={cn('cell text-ink/90', className)}>
      {children}
    </td>
  );
}

/* ---------------------------------------------------------------- Pagination */

export function Pagination({
  page,
  pages,
  total,
  onPage,
}: {
  page: number;
  pages: number;
  total: number;
  onPage: (page: number) => void;
}) {
  if (pages <= 1) {
    return <div className="px-4 py-3 text-xs text-faint">{total.toLocaleString()} result(s)</div>;
  }
  return (
    <div className="flex items-center justify-between gap-4 border-t border-line px-4 py-3">
      <p className="tnum text-xs text-faint">
        Page {page} of {pages} · {total.toLocaleString()} results
      </p>
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <Button size="icon" variant="ghost" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------- States */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton h-4 w-full', className)} />;
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 p-4">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex gap-3">
          {Array.from({ length: cols }).map((__, colIndex) => (
            <Skeleton key={colIndex} className={cn('h-6', colIndex === 0 ? 'w-1/4' : 'flex-1')} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  icon,
  action,
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      {icon ? <div className="text-faint">{icon}</div> : null}
      <div>
        <p className="text-sm font-medium text-ink">{title}</p>
        {description ? <p className="mt-1 max-w-md text-xs text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : 'Something went wrong.';
  const code = (error as { code?: string })?.code;
  const requestId = (error as { requestId?: string })?.requestId;
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
      <AlertCircle className="h-7 w-7 text-critical/80" aria-hidden />
      <div>
        <p className="text-sm font-medium text-ink">{message}</p>
        <p className="mt-1 font-mono text-2xs text-faint">
          {code ? `${code}` : ''}
          {requestId ? ` · ${requestId}` : ''}
        </p>
      </div>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------- Modals */

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl';
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('button, input, select, textarea, [tabindex]')?.focus();
    return () => {
      document.removeEventListener('keydown', onKey);
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  const widths = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-5xl' };

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-void/80 p-4 pt-[8vh] backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          role="presentation"
        >
          <motion.div
            ref={ref}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            onClick={(event) => event.stopPropagation()}
            className={cn('panel w-full', widths[size])}
          >
            <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
              <div>
                <h2 className="text-sm font-semibold text-ink">{title}</h2>
                {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
              </div>
              <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close dialog">
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
            {footer ? <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  width = 'max-w-lg',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open ? (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-void/70 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 30, stiffness: 300 }}
            className={cn('fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-base', width)}
          >
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <h2 className="text-sm font-semibold text-ink">{title}</h2>
              <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close panel">
                <X className="h-4 w-4" />
              </Button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
          </motion.aside>
        </>
      ) : null}
    </AnimatePresence>
  );
}

/* -------------------------------------------------------------------- Toasts */

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant?: 'default' | 'success' | 'error' | 'warning';
}

const ToastContext = createContext<{ push: (toast: Omit<Toast, 'id'>) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = (toast: Omit<Toast, 'id'>) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((current) => [...current, { ...toast, id }]);
    setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 5200);
  };

  const styles = {
    default: 'border-line bg-panel',
    success: 'border-positive/30 bg-positive/10',
    error: 'border-critical/30 bg-critical/10',
    warning: 'border-warning/30 bg-warning/10',
  };

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2" role="status" aria-live="polite">
        <AnimatePresence>
          {toasts.map((toast) => (
            <motion.div
              key={toast.id}
              layout
              initial={{ opacity: 0, y: 16, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 24 }}
              className={cn('pointer-events-auto rounded-lg border px-4 py-3 shadow-panel backdrop-blur', styles[toast.variant ?? 'default'])}
            >
              <p className="text-sm font-medium text-ink">{toast.title}</p>
              {toast.description ? <p className="mt-0.5 text-xs text-muted">{toast.description}</p> : null}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error('useToast must be used inside ToastProvider');
  return context;
}

/* ------------------------------------------------------------------ Tooltip */

export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open ? (
        <span
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-raised px-2 py-1 text-2xs text-ink shadow-panel"
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}

/* ---------------------------------------------------------------- Progress */

export function ProgressBar({
  value,
  max = 100,
  className,
  barClassName,
}: {
  value: number;
  max?: number;
  className?: string;
  barClassName?: string;
}) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div
      className={cn('h-1.5 w-full overflow-hidden rounded-full bg-line', className)}
      role="progressbar"
      aria-valuenow={Math.round(value)}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <motion.div
        className={cn('h-full rounded-full bg-info', barClassName)}
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      />
    </div>
  );
}
