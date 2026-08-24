/**
 * Fraud network visualisation.
 *
 * A small force-directed layout (repulsion + spring attraction + centring)
 * implemented directly against `requestAnimationFrame`, so the graph is
 * interactive without pulling in a heavyweight dependency. Nodes are coloured
 * by entity kind and sized by degree; risk is shown as a ring.
 */
import { motion } from 'framer-motion';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RISK_COLORS, bandOf } from '@/lib/format';
import { cn } from '@/lib/utils';

export interface GraphNode {
  id: string;
  kind: string;
  entity_id: string;
  label: string;
  risk_score: number;
  risk_band?: string;
  degree: number;
  transaction_count?: number;
  total_amount?: number;
  fraud_count?: number;
  is_root?: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
  amount?: number;
  fraud_count?: number;
}

interface Positioned extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

// eslint-disable-next-line react-refresh/only-export-components
export const KIND_COLORS: Record<string, string> = {
  CUSTOMER: '#38BDF8',
  MERCHANT: '#A78BFA',
  DEVICE: '#FBBF24',
  IP: '#2DD4BF',
  ACCOUNT: '#60A5FA',
  UNKNOWN: '#8A99AD',
};

export function NetworkGraph({
  nodes,
  edges,
  height = 460,
  onSelect,
  selectedId,
  className,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  height?: number;
  onSelect?: (node: GraphNode) => void;
  selectedId?: string | null;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 800, height });
  const [positions, setPositions] = useState<Positioned[]>([]);
  const [hovered, setHovered] = useState<string | null>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const dragState = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const frameRef = useRef<number>();

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setSize({ width: Math.max(320, rect.width), height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [height]);

  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    edges.forEach((edge) => {
      if (!map.has(edge.source)) map.set(edge.source, new Set());
      if (!map.has(edge.target)) map.set(edge.target, new Set());
      map.get(edge.source)!.add(edge.target);
      map.get(edge.target)!.add(edge.source);
    });
    return map;
  }, [edges]);

  // Force simulation: runs for a bounded number of ticks, then settles.
  useEffect(() => {
    if (!nodes.length) {
      setPositions([]);
      return;
    }
    const { width, height: h } = size;
    const centreX = width / 2;
    const centreY = h / 2;

    const items: Positioned[] = nodes.map((node, index) => {
      const angle = (index / nodes.length) * Math.PI * 2;
      const radius = node.is_root ? 0 : Math.min(width, h) * 0.32;
      return {
        ...node,
        x: centreX + Math.cos(angle) * radius + (Math.random() - 0.5) * 24,
        y: centreY + Math.sin(angle) * radius + (Math.random() - 0.5) * 24,
        vx: 0,
        vy: 0,
      };
    });

    const index = new Map(items.map((item) => [item.id, item]));
    const linkDistance = Math.min(width, h) / Math.max(3, Math.sqrt(nodes.length));
    let alpha = 1;
    let ticks = 0;
    const maxTicks = 320;

    const step = () => {
      alpha *= 0.985;
      ticks += 1;

      // Repulsion (naive O(n^2) is fine at the sizes we cap the graph to).
      for (let i = 0; i < items.length; i += 1) {
        for (let j = i + 1; j < items.length; j += 1) {
          const a = items[i];
          const b = items[j];
          let dx = b.x - a.x;
          let dy = b.y - a.y;
          const distance = Math.sqrt(dx * dx + dy * dy) || 0.01;
          if (distance > linkDistance * 4) continue;
          const force = (linkDistance * linkDistance) / (distance * distance) * 0.6 * alpha;
          dx /= distance;
          dy /= distance;
          a.vx -= dx * force;
          a.vy -= dy * force;
          b.vx += dx * force;
          b.vy += dy * force;
        }
      }

      // Spring attraction along edges.
      edges.forEach((edge) => {
        const a = index.get(edge.source);
        const b = index.get(edge.target);
        if (!a || !b) return;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distance = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = ((distance - linkDistance) / distance) * 0.06 * alpha * Math.min(edge.weight, 4);
        a.vx += dx * force;
        a.vy += dy * force;
        b.vx -= dx * force;
        b.vy -= dy * force;
      });

      // Gravity toward the centre keeps the layout on screen.
      items.forEach((item) => {
        item.vx += (centreX - item.x) * 0.012 * alpha;
        item.vy += (centreY - item.y) * 0.012 * alpha;
        if (item.is_root) {
          item.vx += (centreX - item.x) * 0.06;
          item.vy += (centreY - item.y) * 0.06;
        }
        item.vx *= 0.82;
        item.vy *= 0.82;
        item.x = Math.max(28, Math.min(width - 28, item.x + item.vx));
        item.y = Math.max(28, Math.min(h - 28, item.y + item.vy));
      });

      setPositions(items.map((item) => ({ ...item })));
      if (ticks < maxTicks && alpha > 0.02) {
        frameRef.current = requestAnimationFrame(step);
      }
    };

    frameRef.current = requestAnimationFrame(step);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [nodes, edges, size]);

  const positionIndex = useMemo(() => new Map(positions.map((node) => [node.id, node])), [positions]);

  const onWheel = useCallback((event: React.WheelEvent) => {
    event.preventDefault();
    setTransform((current) => {
      const k = Math.min(3, Math.max(0.4, current.k * (event.deltaY > 0 ? 0.92 : 1.08)));
      return { ...current, k };
    });
  }, []);

  const highlighted = hovered ?? selectedId ?? null;
  const neighbours = highlighted ? adjacency.get(highlighted) ?? new Set<string>() : null;

  return (
    <div ref={containerRef} className={cn('relative overflow-hidden rounded-xl border border-line bg-surface', className)}>
      <svg
        width="100%"
        height={height}
        role="img"
        aria-label={`Entity network with ${nodes.length} nodes and ${edges.length} connections`}
        onWheel={onWheel}
        onMouseDown={(event) => {
          dragState.current = { x: event.clientX, y: event.clientY, ox: transform.x, oy: transform.y };
        }}
        onMouseMove={(event) => {
          if (!dragState.current) return;
          setTransform((current) => ({
            ...current,
            x: dragState.current!.ox + (event.clientX - dragState.current!.x),
            y: dragState.current!.oy + (event.clientY - dragState.current!.y),
          }));
        }}
        onMouseUp={() => {
          dragState.current = null;
        }}
        onMouseLeave={() => {
          dragState.current = null;
          setHovered(null);
        }}
        className="cursor-grab active:cursor-grabbing"
      >
        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
          {edges.map((edge, i) => {
            const a = positionIndex.get(edge.source);
            const b = positionIndex.get(edge.target);
            if (!a || !b) return null;
            const active =
              !highlighted || edge.source === highlighted || edge.target === highlighted;
            return (
              <line
                key={`${edge.source}-${edge.target}-${i}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={edge.fraud_count ? '#F87171' : '#2A3849'}
                strokeWidth={Math.min(1 + Math.log2(edge.weight + 1) * 0.7, 3.5)}
                strokeOpacity={active ? (edge.fraud_count ? 0.55 : 0.4) : 0.08}
              />
            );
          })}

          {positions.map((node) => {
            const color = KIND_COLORS[node.kind] ?? KIND_COLORS.UNKNOWN;
            const riskColor = RISK_COLORS[bandOf(node.risk_score)];
            const radius = node.is_root ? 16 : Math.min(6 + Math.sqrt(node.degree) * 2.4, 15);
            const dimmed = highlighted && node.id !== highlighted && !neighbours?.has(node.id);
            return (
              <g
                key={node.id}
                transform={`translate(${node.x},${node.y})`}
                opacity={dimmed ? 0.16 : 1}
                className="cursor-pointer transition-opacity"
                onMouseEnter={() => setHovered(node.id)}
                onClick={(event) => {
                  event.stopPropagation();
                  onSelect?.(node);
                }}
              >
                {node.risk_score >= 70 ? (
                  <circle r={radius + 6} fill="none" stroke={riskColor} strokeOpacity={0.35} strokeWidth={1.5} />
                ) : null}
                <circle r={radius} fill={color} fillOpacity={0.18} stroke={color} strokeWidth={1.6} />
                {node.is_root || selectedId === node.id ? (
                  <circle r={radius + 3.5} fill="none" stroke="#E8EEF6" strokeWidth={1.2} strokeOpacity={0.7} />
                ) : null}
                {node.risk_score >= 40 ? (
                  <circle
                    r={radius}
                    fill="none"
                    stroke={riskColor}
                    strokeWidth={2.2}
                    strokeDasharray={2 * Math.PI * radius}
                    strokeDashoffset={2 * Math.PI * radius * (1 - Math.min(node.risk_score, 100) / 100)}
                    transform="rotate(-90)"
                  />
                ) : null}
                {(node.is_root || radius > 10 || highlighted === node.id) && (
                  <text
                    y={radius + 12}
                    textAnchor="middle"
                    className="pointer-events-none fill-current text-[9px]"
                    style={{ fill: '#8A99AD' }}
                  >
                    {node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {/* Legend */}
      <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-2">
        {Object.entries(KIND_COLORS)
          .filter(([kind]) => nodes.some((node) => node.kind === kind))
          .map(([kind, color]) => (
            <span
              key={kind}
              className="glass inline-flex items-center gap-1.5 rounded px-2 py-1 text-2xs uppercase tracking-wide text-muted"
            >
              <span className="h-2 w-2 rounded-full" style={{ background: color }} />
              {kind}
            </span>
          ))}
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 text-2xs text-faint">
        scroll to zoom · drag to pan · click a node for detail
      </div>

      {hovered ? <NodeTooltip node={positionIndex.get(hovered)} transform={transform} /> : null}
    </div>
  );
}

function NodeTooltip({ node, transform }: { node?: Positioned; transform: { x: number; y: number; k: number } }) {
  if (!node) return null;
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      className="pointer-events-none absolute z-10 min-w-[180px] rounded-lg border border-line bg-raised px-3 py-2 shadow-panel"
      style={{
        left: node.x * transform.k + transform.x + 18,
        top: node.y * transform.k + transform.y - 8,
      }}
    >
      <p className="text-2xs uppercase tracking-wide text-faint">{node.kind}</p>
      <p className="truncate text-sm text-ink">{node.label}</p>
      <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-2xs text-muted">
        <span>Risk</span>
        <span className="tnum text-right" style={{ color: RISK_COLORS[bandOf(node.risk_score)] }}>
          {node.risk_score.toFixed(1)}
        </span>
        <span>Connections</span>
        <span className="tnum text-right">{node.degree}</span>
        {node.transaction_count ? (
          <>
            <span>Transactions</span>
            <span className="tnum text-right">{node.transaction_count}</span>
          </>
        ) : null}
        {node.fraud_count ? (
          <>
            <span>Fraud</span>
            <span className="tnum text-right text-critical">{node.fraud_count}</span>
          </>
        ) : null}
      </div>
    </motion.div>
  );
}
