/**
 * Risk Orb - the platform's signature risk visualisation.
 *
 * A circular gauge whose arc, colour and glow follow the risk band, with the
 * score counting up on change. The displayed number is React state seeded with
 * the real value, so it is correct even when the count-up animation cannot run
 * (reduced-motion preference, or a tab that is not compositing frames).
 */
import { motion, useReducedMotion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { RISK_COLORS, bandOf, type RiskBand } from '@/lib/format';
import { cn } from '@/lib/utils';

export function RiskOrb({
  score,
  band,
  size = 168,
  label = 'Risk score',
  sublabel,
  className,
}: {
  score: number;
  band?: RiskBand | string;
  size?: number;
  label?: string;
  sublabel?: string;
  className?: string;
}) {
  const resolvedBand = (band as RiskBand) ?? bandOf(score);
  const color = RISK_COLORS[resolvedBand] ?? RISK_COLORS.LOW;
  const reduceMotion = useReducedMotion();

  const stroke = Math.max(6, size * 0.05);
  const radius = size / 2 - stroke * 1.6;
  const circumference = 2 * Math.PI * radius;
  const target = Math.max(0, Math.min(100, score));

  const [displayed, setDisplayed] = useState(target);

  useEffect(() => {
    if (reduceMotion) {
      setDisplayed(target);
      return;
    }
    const from = displayed;
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 700);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplayed(from + (target - from) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    // Guarantee the final value even if frames stop being delivered.
    const settle = setTimeout(() => setDisplayed(target), 900);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(settle);
    };
    // `displayed` is intentionally excluded: it is the animation's start point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, reduceMotion]);

  const dashOffset = circumference * (1 - displayed / 100);

  return (
    <div className={cn('relative flex flex-col items-center justify-center', className)}>
      <div className="relative" style={{ width: size, height: size }}>
        {/* Ambient glow, intensity follows the band. */}
        <div
          className="absolute inset-0 rounded-full blur-2xl"
          style={{ background: color, opacity: resolvedBand === 'CRITICAL' ? 0.22 : 0.12 }}
          aria-hidden
        />
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="relative -rotate-90"
          role="img"
          aria-label={`${label}: ${target.toFixed(1)} out of 100, ${resolvedBand}`}
        >
          <defs>
            <linearGradient id={`orb-${resolvedBand}`} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor={color} stopOpacity="0.95" />
              <stop offset="100%" stopColor={color} stopOpacity="0.45" />
            </linearGradient>
          </defs>
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#1D2836" strokeWidth={stroke} />
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={`url(#orb-${resolvedBand})`}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={false}
            animate={{ strokeDashoffset: dashOffset }}
            transition={{ duration: reduceMotion ? 0 : 0.7, ease: [0.22, 1, 0.36, 1] }}
          />
          {/* Band threshold ticks at 40 / 70 / 85. */}
          {[40, 70, 85].map((threshold) => {
            const angle = (threshold / 100) * 2 * Math.PI;
            const inner = radius - stroke * 0.75;
            const outer = radius + stroke * 0.75;
            return (
              <line
                key={threshold}
                x1={size / 2 + inner * Math.cos(angle)}
                y1={size / 2 + inner * Math.sin(angle)}
                x2={size / 2 + outer * Math.cos(angle)}
                y2={size / 2 + outer * Math.sin(angle)}
                stroke="#2A3849"
                strokeWidth={1.5}
              />
            );
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="tnum font-semibold leading-none tracking-tight" style={{ fontSize: size * 0.26, color }}>
            {displayed.toFixed(1)}
          </span>
          <span className="mt-1.5 text-2xs font-medium uppercase tracking-[0.18em]" style={{ color }}>
            {resolvedBand}
          </span>
        </div>
      </div>
      {label ? <p className="label mt-3">{label}</p> : null}
      {sublabel ? <p className="mt-1 text-xs text-muted">{sublabel}</p> : null}
    </div>
  );
}

/** Compact inline variant for table rows and list items. */
export function RiskPill({ score, band }: { score: number; band?: string }) {
  const resolved = (band as RiskBand) ?? bandOf(score);
  const color = RISK_COLORS[resolved] ?? RISK_COLORS.LOW;
  const bounded = Math.min(100, Math.max(0, score));
  return (
    <span className="inline-flex items-center gap-2">
      <span className="relative h-6 w-6 shrink-0">
        <svg viewBox="0 0 24 24" className="-rotate-90" aria-hidden>
          <circle cx="12" cy="12" r="9" fill="none" stroke="#1D2836" strokeWidth="3" />
          <circle
            cx="12"
            cy="12"
            r="9"
            fill="none"
            stroke={color}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={2 * Math.PI * 9}
            strokeDashoffset={2 * Math.PI * 9 * (1 - bounded / 100)}
          />
        </svg>
      </span>
      <span className="tnum text-sm font-medium" style={{ color }}>
        {score.toFixed(1)}
      </span>
    </span>
  );
}
