import { describe, expect, it } from 'vitest';
import {
  bandOf,
  caseStatusClass,
  decisionBgClass,
  formatCurrency,
  formatDuration,
  formatNumber,
  formatPercent,
  formatScore,
  riskBgClass,
  titleCase,
  truncate,
} from '@/lib/format';

describe('risk bands', () => {
  it.each([
    [0, 'LOW'],
    [39.9, 'LOW'],
    [40, 'MEDIUM'],
    [69.9, 'MEDIUM'],
    [70, 'HIGH'],
    [84.9, 'HIGH'],
    [85, 'CRITICAL'],
    [100, 'CRITICAL'],
  ])('maps %s to %s', (score, band) => {
    expect(bandOf(score as number)).toBe(band);
  });

  it('uses a distinct colour class per band', () => {
    const classes = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map(riskBgClass);
    expect(new Set(classes).size).toBe(4);
  });
});

describe('formatters', () => {
  it('renders currency and switches to compact notation for large sums', () => {
    expect(formatCurrency(1234.5, 'INR')).toContain('1,234.5');
    expect(formatCurrency(15_000_000, 'INR')).not.toContain('15,000,000.00');
  });

  it('handles missing values without throwing', () => {
    expect(formatCurrency(null)).toBe('--');
    expect(formatNumber(undefined)).toBe('--');
    expect(formatPercent(null)).toBe('--');
    expect(formatScore(undefined)).toBe('--');
    expect(formatDuration(null)).toBe('--');
  });

  it('scales duration units', () => {
    expect(formatDuration(0.4)).toContain('µs');
    expect(formatDuration(12)).toBe('12ms');
    expect(formatDuration(2500)).toBe('2.50s');
    expect(formatDuration(120_000)).toBe('2.0m');
  });

  it('formats percentages to the requested precision', () => {
    expect(formatPercent(12.3456, 2)).toBe('12.35%');
  });
});

describe('decision and case styling', () => {
  it('gives every decision its own treatment', () => {
    const classes = ['APPROVE', 'STEP_UP', 'MANUAL_REVIEW', 'DECLINE'].map(decisionBgClass);
    expect(new Set(classes).size).toBe(4);
  });

  it('falls back for an unknown value rather than throwing', () => {
    expect(decisionBgClass('SOMETHING_ELSE')).toContain('text-muted');
    expect(caseStatusClass('UNKNOWN')).toContain('text-muted');
  });
});

describe('text helpers', () => {
  it('title-cases snake case', () => {
    expect(titleCase('CONFIRMED_FRAUD')).toBe('Confirmed Fraud');
  });

  it('truncates with an ellipsis', () => {
    expect(truncate('abcdefghij', 5)).toBe('abcd…');
    expect(truncate('abc', 5)).toBe('abc');
  });
});
