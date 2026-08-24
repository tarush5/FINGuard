import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Badge, Button, EmptyState, Pagination, ProgressBar } from '@/components/ui';
import { RiskOrb, RiskPill } from '@/components/viz/RiskOrb';
import { DecisionTrace } from '@/components/viz/DecisionTrace';

describe('Button', () => {
  it('renders its label', () => {
    render(<Button>Run simulation</Button>);
    expect(screen.getByRole('button', { name: 'Run simulation' })).toBeInTheDocument();
  });

  it('is disabled while loading', () => {
    render(<Button loading>Training</Button>);
    expect(screen.getByRole('button')).toBeDisabled();
  });
});

describe('RiskOrb', () => {
  it('shows the score and band, and is described for screen readers', () => {
    render(<RiskOrb score={87.4} band="CRITICAL" />);
    expect(screen.getByText('87.4')).toBeInTheDocument();
    expect(screen.getByText('CRITICAL')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      expect.stringContaining('87.4 out of 100'),
    );
  });

  it('derives the band when one is not supplied', () => {
    render(<RiskOrb score={45} />);
    expect(screen.getByText('MEDIUM')).toBeInTheDocument();
  });
});

describe('RiskPill', () => {
  it('renders the score to one decimal', () => {
    render(<RiskPill score={12.34} />);
    expect(screen.getByText('12.3')).toBeInTheDocument();
  });
});

describe('DecisionTrace', () => {
  const stages = [
    { stage: 'FEATURES', duration_ms: 2.5, summary: '35 features computed', detail: { notable: { amount: 1000 } } },
    {
      stage: 'RULES',
      duration_ms: 0.5,
      summary: '2 of 15 rules triggered',
      detail: { triggered: [{ code: 'R-VEL-001', name: 'Velocity', risk_points: 25 }] },
    },
    { stage: 'MODEL', duration_ms: 7.1, summary: 'Fraud probability 0.84', detail: { model_version: 'Fraud-XGB-v1' } },
    { stage: 'GRAPH', duration_ms: 0.8, summary: 'Graph risk 0.25', detail: { signals: [] } },
    { stage: 'RISK', duration_ms: 0, summary: 'Final score 85.6/100', detail: { components: {}, top_factors: [] } },
    { stage: 'DECISION', duration_ms: 6, summary: 'DECLINE', detail: { decision: 'DECLINE' } },
  ];

  it('renders every pipeline stage with its measured latency', () => {
    render(<DecisionTrace stages={stages} />);
    expect(screen.getByText('Feature engineering')).toBeInTheDocument();
    expect(screen.getByText('Rule engine')).toBeInTheDocument();
    expect(screen.getByText('Machine learning')).toBeInTheDocument();
    expect(screen.getByText('Graph intelligence')).toBeInTheDocument();
    expect(screen.getByText('Ensemble risk')).toBeInTheDocument();
    expect(screen.getByText('Decision engine')).toBeInTheDocument();
    expect(screen.getByText('7.10ms')).toBeInTheDocument();
  });
});

describe('Pagination', () => {
  it('summarises the result count when there is a single page', () => {
    render(<Pagination page={1} pages={1} total={7} onPage={() => {}} />);
    expect(screen.getByText(/7 result/)).toBeInTheDocument();
  });

  it('disables previous on the first page', () => {
    render(<Pagination page={1} pages={4} total={100} onPage={() => {}} />);
    expect(screen.getByLabelText('Previous page')).toBeDisabled();
    expect(screen.getByLabelText('Next page')).toBeEnabled();
  });
});

describe('ProgressBar', () => {
  it('exposes accessible progress attributes', () => {
    render(<ProgressBar value={42} />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });
});

describe('EmptyState and Badge', () => {
  it('renders guidance text', () => {
    render(<EmptyState title="No cases" description="Nothing to review." />);
    expect(screen.getByText('No cases')).toBeInTheDocument();
    expect(screen.getByText('Nothing to review.')).toBeInTheDocument();
  });

  it('renders badge content', () => {
    render(<Badge>critical</Badge>);
    expect(screen.getByText('critical')).toBeInTheDocument();
  });
});
