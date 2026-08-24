/**
 * Chart wrappers.
 *
 * Recharts with a single shared theme so every chart in the platform reads as
 * one system: thin grid lines, muted axes, tabular tooltips, no chart junk.
 */
import { motion } from 'framer-motion';
import { TrendingDown, TrendingUp } from 'lucide-react';
import { type ReactNode } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { CHART_COLORS, formatNumber } from '@/lib/format';
import { cn } from '@/lib/utils';

const AXIS = {
  stroke: '#5C6980',
  fontSize: 10,
  tickLine: false,
  axisLine: false,
};

function ChartTooltip({ active, payload, label, formatter }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-line bg-raised/95 px-3 py-2 shadow-panel backdrop-blur">
      {label !== undefined ? <p className="mb-1 text-2xs text-faint">{label}</p> : null}
      {payload.map((entry: any) => (
        <div key={entry.dataKey ?? entry.name} className="flex items-center gap-2 text-xs">
          <span className="h-2 w-2 rounded-full" style={{ background: entry.color ?? entry.fill }} />
          <span className="text-muted">{entry.name}</span>
          <span className="tnum ml-auto text-ink">
            {formatter ? formatter(entry.value, entry.dataKey) : formatNumber(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

export interface SeriesSpec {
  key: string;
  name: string;
  color?: string;
  type?: 'line' | 'area' | 'bar';
  yAxis?: 'left' | 'right';
}

export function TrendChart({
  data,
  series,
  xKey,
  height = 240,
  formatter,
  stacked = false,
  showLegend = true,
}: {
  data: any[];
  series: SeriesSpec[];
  xKey: string;
  height?: number;
  formatter?: (value: number, key: string) => string;
  stacked?: boolean;
  showLegend?: boolean;
}) {
  if (!data?.length) {
    return <div className="flex h-[240px] items-center justify-center text-xs text-faint">No data in this window.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          {series.map((spec, index) => {
            const color = spec.color ?? CHART_COLORS[index % CHART_COLORS.length];
            return (
              <linearGradient key={spec.key} id={`fill-${spec.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                <stop offset="100%" stopColor={color} stopOpacity={0.02} />
              </linearGradient>
            );
          })}
        </defs>
        <CartesianGrid stroke="#1D2836" strokeDasharray="3 6" vertical={false} />
        <XAxis dataKey={xKey} {...AXIS} minTickGap={24} />
        <YAxis {...AXIS} width={48} tickFormatter={(value) => formatNumber(value)} />
        <Tooltip content={<ChartTooltip formatter={formatter} />} cursor={{ stroke: '#2A3849' }} />
        {showLegend && series.length > 1 ? (
          <Legend
            iconType="circle"
            iconSize={7}
            wrapperStyle={{ fontSize: 11, color: '#8A99AD', paddingTop: 8 }}
          />
        ) : null}
        {series.map((spec, index) => {
          const color = spec.color ?? CHART_COLORS[index % CHART_COLORS.length];
          return (
            <Area
              key={spec.key}
              type="monotone"
              dataKey={spec.key}
              name={spec.name}
              stroke={color}
              strokeWidth={1.8}
              fill={`url(#fill-${spec.key})`}
              stackId={stacked ? 'stack' : undefined}
              dot={false}
              activeDot={{ r: 3.5, strokeWidth: 0 }}
            />
          );
        })}
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function ForecastChart({
  history,
  forecast,
  height = 260,
}: {
  history: { date: string; value: number }[];
  forecast: { date: string; value: number; lower: number; upper: number }[];
  height?: number;
}) {
  // One continuous series: history, then the projection with its interval band.
  const data = [
    ...history.map((point) => ({ date: point.date, actual: point.value })),
    ...forecast.map((point) => ({
      date: point.date,
      forecast: point.value,
      band: [point.lower, point.upper] as [number, number],
    })),
  ];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="fill-actual" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38BDF8" stopOpacity={0.25} />
            <stop offset="100%" stopColor="#38BDF8" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#1D2836" strokeDasharray="3 6" vertical={false} />
        <XAxis dataKey="date" {...AXIS} minTickGap={30} />
        <YAxis {...AXIS} width={48} tickFormatter={(value) => formatNumber(value)} />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: '#2A3849' }} />
        <Area
          type="monotone"
          dataKey="band"
          name="95% interval"
          stroke="none"
          fill="#A78BFA"
          fillOpacity={0.14}
          isAnimationActive={false}
        />
        <Area
          type="monotone"
          dataKey="actual"
          name="Actual"
          stroke="#38BDF8"
          strokeWidth={1.8}
          fill="url(#fill-actual)"
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="forecast"
          name="Forecast"
          stroke="#A78BFA"
          strokeWidth={1.8}
          strokeDasharray="5 4"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function CategoryBars({
  data,
  xKey,
  yKey,
  height = 240,
  color,
  colorBy,
  horizontal = false,
  formatter,
}: {
  data: any[];
  xKey: string;
  yKey: string;
  height?: number;
  color?: string;
  colorBy?: (row: any, index: number) => string;
  horizontal?: boolean;
  formatter?: (value: number) => string;
}) {
  if (!data?.length) {
    return <div className="flex h-[200px] items-center justify-center text-xs text-faint">No data.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout={horizontal ? 'vertical' : 'horizontal'}
        margin={{ top: 8, right: 12, left: horizontal ? 8 : 0, bottom: 0 }}
      >
        <CartesianGrid stroke="#1D2836" strokeDasharray="3 6" vertical={horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" {...AXIS} tickFormatter={(value) => formatNumber(value)} />
            <YAxis type="category" dataKey={xKey} {...AXIS} width={110} />
          </>
        ) : (
          <>
            <XAxis dataKey={xKey} {...AXIS} interval={0} angle={-25} textAnchor="end" height={54} />
            <YAxis {...AXIS} width={48} tickFormatter={(value) => formatNumber(value)} />
          </>
        )}
        <Tooltip content={<ChartTooltip formatter={formatter ? (v: number) => formatter(v) : undefined} />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
        <Bar dataKey={yKey} radius={[3, 3, 0, 0]} maxBarSize={38}>
          {data.map((row, index) => (
            <Cell key={index} fill={colorBy ? colorBy(row, index) : color ?? CHART_COLORS[0]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DonutChart({
  data,
  nameKey,
  valueKey,
  height = 220,
  colors,
  centerLabel,
  centerValue,
}: {
  data: any[];
  nameKey: string;
  valueKey: string;
  height?: number;
  colors?: string[];
  centerLabel?: string;
  centerValue?: string;
}) {
  if (!data?.length) {
    return <div className="flex h-[200px] items-center justify-center text-xs text-faint">No data.</div>;
  }
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie
            data={data}
            dataKey={valueKey}
            nameKey={nameKey}
            innerRadius="62%"
            outerRadius="86%"
            paddingAngle={2}
            stroke="none"
          >
            {data.map((_row, index) => (
              <Cell key={index} fill={(colors ?? CHART_COLORS)[index % (colors ?? CHART_COLORS).length]} />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip />} />
        </PieChart>
      </ResponsiveContainer>
      {centerValue ? (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="tnum text-xl font-semibold text-ink">{centerValue}</span>
          {centerLabel ? <span className="label mt-1">{centerLabel}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Inline sparkline used inside KPI cards. */
export function Sparkline({
  data,
  dataKey = 'value',
  color = '#38BDF8',
  height = 34,
}: {
  data: any[];
  dataKey?: string;
  color?: string;
  height?: number;
}) {
  if (!data?.length) return <div style={{ height }} />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
        <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** KPI card with value, trend, sparkline and comparison period. */
export function KpiCard({
  label,
  value,
  changePct,
  comparison,
  sparkline,
  sparklineKey,
  invertTrend,
  accent = '#38BDF8',
  status,
  onClick,
  className,
}: {
  label: string;
  value: string;
  changePct?: number;
  comparison?: string;
  sparkline?: any[];
  sparklineKey?: string;
  invertTrend?: boolean;
  accent?: string;
  status?: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  const change = changePct ?? 0;
  const improving = invertTrend ? change < 0 : change > 0;
  const neutral = Math.abs(change) < 0.01;
  const trendColor = neutral ? 'text-muted' : improving ? 'text-positive' : 'text-critical';

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={onClick ? { y: -2 } : undefined}
      onClick={onClick}
      className={cn(
        'panel group relative overflow-hidden p-4',
        onClick && 'cursor-pointer transition-colors hover:border-line-strong',
        className,
      )}
    >
      <div
        className="absolute inset-x-0 top-0 h-px opacity-60"
        style={{ background: `linear-gradient(90deg, transparent, ${accent}, transparent)` }}
        aria-hidden
      />
      <div className="flex items-start justify-between gap-2">
        <p className="label">{label}</p>
        {status}
      </div>
      <p className="metric mt-2.5">{value}</p>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div>
          {changePct !== undefined ? (
            <p className={cn('tnum flex items-center gap-1 text-xs', trendColor)}>
              {neutral ? (
                <span className="text-muted">stable</span>
              ) : (
                <>
                  {change > 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                  {Math.abs(change).toFixed(1)}%
                </>
              )}
            </p>
          ) : null}
          {comparison ? <p className="mt-0.5 text-2xs text-faint">{comparison}</p> : null}
        </div>
        {sparkline?.length ? (
          <div className="h-9 w-24 opacity-80 transition-opacity group-hover:opacity-100">
            <Sparkline data={sparkline} dataKey={sparklineKey} color={accent} />
          </div>
        ) : null}
      </div>
    </motion.div>
  );
}

/** Heatmap grid (weekday x hour) used by the fraud heatmap screen. */
export function Heatmap({
  cells,
  maxValue,
  valueKey = 'fraud_rate',
  onSelect,
}: {
  cells: { day_of_week: number; hour: number; [key: string]: any }[];
  maxValue?: number;
  valueKey?: string;
  onSelect?: (cell: any) => void;
}) {
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const max = maxValue ?? Math.max(...cells.map((cell) => Number(cell[valueKey]) || 0), 0.0001);
  const lookup = new Map(cells.map((cell) => [`${cell.day_of_week}-${cell.hour}`, cell]));

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[720px]">
        <div className="mb-1 grid grid-cols-[42px_repeat(24,1fr)] gap-[2px]">
          <span />
          {Array.from({ length: 24 }).map((_, hour) => (
            <span key={hour} className="text-center text-[9px] text-faint">
              {hour % 3 === 0 ? hour : ''}
            </span>
          ))}
        </div>
        {days.map((day, dayIndex) => (
          <div key={day} className="mb-[2px] grid grid-cols-[42px_repeat(24,1fr)] items-center gap-[2px]">
            <span className="text-2xs text-faint">{day}</span>
            {Array.from({ length: 24 }).map((_, hour) => {
              const cell = lookup.get(`${dayIndex}-${hour}`);
              const value = Number(cell?.[valueKey]) || 0;
              const intensity = value / max;
              return (
                <button
                  key={hour}
                  type="button"
                  onClick={() => cell && onSelect?.(cell)}
                  title={
                    cell
                      ? `${day} ${hour}:00 · ${cell.transactions ?? 0} txns · ${value.toFixed(2)}% fraud`
                      : `${day} ${hour}:00 · no data`
                  }
                  className="h-5 rounded-[3px] border border-white/[0.03] transition-transform hover:scale-110"
                  style={{
                    background:
                      value > 0
                        ? `rgba(248,113,113,${0.08 + intensity * 0.85})`
                        : cell
                          ? 'rgba(56,189,248,0.06)'
                          : 'rgba(255,255,255,0.02)',
                  }}
                  aria-label={`${day} ${hour}:00, ${value.toFixed(2)} percent fraud`}
                />
              );
            })}
          </div>
        ))}
        <div className="mt-3 flex items-center gap-2 text-2xs text-faint">
          <span>lower</span>
          <div className="flex gap-[2px]">
            {[0.1, 0.3, 0.5, 0.7, 0.9].map((step) => (
              <span key={step} className="h-3 w-6 rounded-sm" style={{ background: `rgba(248,113,113,${step})` }} />
            ))}
          </div>
          <span>higher fraud rate</span>
        </div>
      </div>
    </div>
  );
}
