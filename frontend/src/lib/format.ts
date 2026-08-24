/** Presentation helpers: currency, numbers, dates and risk semantics. */

export type RiskBand = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type Decision = 'APPROVE' | 'STEP_UP' | 'MANUAL_REVIEW' | 'DECLINE' | 'PENDING';

const currencyFormatters = new Map<string, Intl.NumberFormat>();

function currencyFormatter(currency: string, compact: boolean): Intl.NumberFormat {
  const key = `${currency}:${compact}`;
  let formatter = currencyFormatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency,
      notation: compact ? 'compact' : 'standard',
      maximumFractionDigits: compact ? 2 : 2,
      minimumFractionDigits: compact ? 0 : 2,
    });
    currencyFormatters.set(key, formatter);
  }
  return formatter;
}

export function formatCurrency(value: number | null | undefined, currency = 'INR', compact = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  // Large sums are unreadable in full; switch to compact notation automatically.
  const useCompact = compact || Math.abs(value) >= 1_000_000;
  return currencyFormatter(currency, useCompact).format(value);
}

export function formatNumber(value: number | null | undefined, options: Intl.NumberFormatOptions = {}): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  const compact = Math.abs(value) >= 100_000 && !options.maximumFractionDigits;
  return new Intl.NumberFormat('en-IN', {
    notation: compact ? 'compact' : 'standard',
    maximumFractionDigits: compact ? 2 : 0,
    ...options,
  }).format(value);
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return `${value.toFixed(digits)}%`;
}

export function formatScore(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return value.toFixed(digits);
}

export function formatMetric(value: number, format: string, currency = 'INR'): string {
  switch (format) {
    case 'currency':
      return formatCurrency(value, currency, true);
    case 'percent':
      return formatPercent(value, 2);
    case 'score':
      return formatScore(value, 1);
    default:
      return formatNumber(value);
  }
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString('en-GB', { hour12: false });
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  const ranges: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, 'second'],
    [3600, 'minute'],
    [86400, 'hour'],
    [604800, 'day'],
    [2629800, 'week'],
    [31557600, 'month'],
  ];
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
  let previous = 1;
  for (const [limit, unit] of ranges) {
    if (Math.abs(seconds) < limit) {
      return formatter.format(-Math.round(seconds / previous), unit);
    }
    previous = limit;
  }
  return formatter.format(-Math.round(seconds / 31557600), 'year');
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '--';
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 2 : 0)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  return `${(ms / 60_000).toFixed(1)}m`;
}

export function bandOf(score: number): RiskBand {
  if (score >= 85) return 'CRITICAL';
  if (score >= 70) return 'HIGH';
  if (score >= 40) return 'MEDIUM';
  return 'LOW';
}

export const RISK_COLORS: Record<RiskBand, string> = {
  LOW: '#34D399',
  MEDIUM: '#FBBF24',
  HIGH: '#FB923C',
  CRITICAL: '#F87171',
};

export const DECISION_COLORS: Record<string, string> = {
  APPROVE: '#34D399',
  STEP_UP: '#FBBF24',
  MANUAL_REVIEW: '#FB923C',
  DECLINE: '#F87171',
  PENDING: '#8A99AD',
};

export const CHART_COLORS = ['#38BDF8', '#34D399', '#A78BFA', '#FBBF24', '#FB923C', '#F87171', '#60A5FA', '#2DD4BF'];

export function riskTextClass(band: string): string {
  return (
    {
      LOW: 'text-positive',
      MEDIUM: 'text-warning',
      HIGH: 'text-high',
      CRITICAL: 'text-critical',
    }[band] ?? 'text-muted'
  );
}

export function riskBgClass(band: string): string {
  return (
    {
      LOW: 'bg-positive/10 text-positive border-positive/25',
      MEDIUM: 'bg-warning/10 text-warning border-warning/25',
      HIGH: 'bg-high/10 text-high border-high/25',
      CRITICAL: 'bg-critical/10 text-critical border-critical/25',
    }[band] ?? 'bg-line/50 text-muted border-line'
  );
}

export function decisionBgClass(decision: string): string {
  return (
    {
      APPROVE: 'bg-positive/10 text-positive border-positive/25',
      STEP_UP: 'bg-warning/10 text-warning border-warning/25',
      MANUAL_REVIEW: 'bg-high/10 text-high border-high/25',
      DECLINE: 'bg-critical/10 text-critical border-critical/25',
    }[decision] ?? 'bg-line/50 text-muted border-line'
  );
}

export function statusBgClass(status: string): string {
  return (
    {
      HEALTHY: 'bg-positive/10 text-positive border-positive/25',
      PASS: 'bg-positive/10 text-positive border-positive/25',
      SUCCESS: 'bg-positive/10 text-positive border-positive/25',
      WARNING: 'bg-warning/10 text-warning border-warning/25',
      WARN: 'bg-warning/10 text-warning border-warning/25',
      CRITICAL: 'bg-critical/10 text-critical border-critical/25',
      FAIL: 'bg-critical/10 text-critical border-critical/25',
      FAILED: 'bg-critical/10 text-critical border-critical/25',
      RUNNING: 'bg-info/10 text-info border-info/25',
      INFO: 'bg-info/10 text-info border-info/25',
    }[status] ?? 'bg-line/50 text-muted border-line'
  );
}

export function caseStatusClass(status: string): string {
  return (
    {
      NEW: 'bg-info/10 text-info border-info/25',
      INVESTIGATING: 'bg-warning/10 text-warning border-warning/25',
      ESCALATED: 'bg-high/10 text-high border-high/25',
      CONFIRMED_FRAUD: 'bg-critical/10 text-critical border-critical/25',
      FALSE_POSITIVE: 'bg-positive/10 text-positive border-positive/25',
      RESOLVED: 'bg-line/60 text-muted border-line',
    }[status] ?? 'bg-line/50 text-muted border-line'
  );
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split(/[\s_]+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function truncate(value: string, length = 24): string {
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
}
