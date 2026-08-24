/**
 * Interactive data lineage.
 *
 * The DAG is laid out in columns by dependency depth (a longest-path layering),
 * so upstream sources sit on the left and terminal datasets on the right.
 */
import { useQuery } from '@tanstack/react-query';
import { GitBranch, ShieldAlert } from 'lucide-react';
import { useMemo, useState } from 'react';
import { PageHeader } from '@/components/layout/AppShell';
import { Badge, ErrorState, Panel, PanelHeader, Skeleton } from '@/components/ui';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/format';

interface LineageNode {
  id: string;
  label: string;
  type: string;
  layer: string;
  row_count: number | null;
  quality_score: number | null;
  contains_pii: boolean;
}

interface LineageEdge {
  source: string;
  target: string;
  transformation: string;
  processor: string;
}

const LAYER_COLOR: Record<string, string> = {
  raw: '#8A99AD',
  bronze: '#FBBF24',
  silver: '#38BDF8',
  gold: '#34D399',
  processing: '#A78BFA',
};

export default function Lineage() {
  const [selected, setSelected] = useState<string | null>(null);
  const lineage = useQuery({ queryKey: ['lineage'], queryFn: () => api.get<{ nodes: LineageNode[]; edges: LineageEdge[] }>('/lineage') });

  const layout = useMemo(() => {
    if (!lineage.data) return { columns: [] as LineageNode[][], positions: new Map<string, { x: number; y: number }>() };
    const { nodes, edges } = lineage.data;

    // Longest-path layering: a node sits one column right of its deepest parent.
    const incoming = new Map<string, string[]>();
    nodes.forEach((node) => incoming.set(node.id, []));
    edges.forEach((edge) => incoming.get(edge.target)?.push(edge.source));

    const depth = new Map<string, number>();
    const resolve = (id: string, seen = new Set<string>()): number => {
      if (depth.has(id)) return depth.get(id)!;
      if (seen.has(id)) return 0; // cycle guard (the feedback loop edge)
      seen.add(id);
      const parents = incoming.get(id) ?? [];
      const value = parents.length ? Math.max(...parents.map((parent) => resolve(parent, seen))) + 1 : 0;
      depth.set(id, value);
      return value;
    };
    nodes.forEach((node) => resolve(node.id));

    const maxDepth = Math.max(...Array.from(depth.values()), 0);
    const columns: LineageNode[][] = Array.from({ length: maxDepth + 1 }, () => []);
    nodes.forEach((node) => columns[depth.get(node.id) ?? 0].push(node));

    const positions = new Map<string, { x: number; y: number }>();
    columns.forEach((column, columnIndex) => {
      column.forEach((node, rowIndex) => {
        positions.set(node.id, { x: columnIndex, y: rowIndex });
      });
    });
    return { columns, positions };
  }, [lineage.data]);

  if (lineage.isError) return <ErrorState error={lineage.error} onRetry={() => lineage.refetch()} />;

  const selectedEdges = (lineage.data?.edges ?? []).filter(
    (edge) => edge.source === selected || edge.target === selected,
  );

  const columnWidth = 220;
  const rowHeight = 92;
  const svgWidth = Math.max(layout.columns.length * columnWidth, 600);
  const svgHeight = Math.max(...layout.columns.map((column) => column.length), 1) * rowHeight + 40;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Data lineage"
        description="How a raw event becomes a decision: every transformation between the ingestion topic and the investigation case."
      />

      <Panel>
        <PanelHeader
          title="Lineage graph"
          subtitle={`${lineage.data?.nodes.length ?? 0} nodes · ${lineage.data?.edges.length ?? 0} edges`}
          icon={<GitBranch className="h-4 w-4" />}
        />
        <div className="overflow-x-auto p-4">
          {lineage.isLoading ? (
            <Skeleton className="h-[420px]" />
          ) : (
            <svg width={svgWidth} height={svgHeight} className="min-w-full" role="img" aria-label="Data lineage graph">
              {/* Edges */}
              {(lineage.data?.edges ?? []).map((edge, index) => {
                const from = layout.positions.get(edge.source);
                const to = layout.positions.get(edge.target);
                if (!from || !to) return null;
                const x1 = from.x * columnWidth + 172;
                const y1 = from.y * rowHeight + 36;
                const x2 = to.x * columnWidth + 8;
                const y2 = to.y * rowHeight + 36;
                const active = selected === edge.source || selected === edge.target;
                const midX = (x1 + x2) / 2;
                return (
                  <g key={index}>
                    <path
                      d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                      fill="none"
                      stroke={active ? '#38BDF8' : '#2A3849'}
                      strokeWidth={active ? 1.8 : 1}
                      strokeOpacity={selected && !active ? 0.2 : 0.8}
                      markerEnd="url(#arrow)"
                    />
                  </g>
                );
              })}
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#2A3849" />
                </marker>
              </defs>

              {/* Nodes */}
              {(lineage.data?.nodes ?? []).map((node) => {
                const position = layout.positions.get(node.id);
                if (!position) return null;
                const x = position.x * columnWidth + 8;
                const y = position.y * rowHeight + 12;
                const color = LAYER_COLOR[node.layer] ?? '#8A99AD';
                const active = selected === node.id;
                return (
                  <g
                    key={node.id}
                    transform={`translate(${x},${y})`}
                    onClick={() => setSelected(active ? null : node.id)}
                    className="cursor-pointer"
                  >
                    <rect
                      width={164}
                      height={48}
                      rx={8}
                      fill={active ? 'rgba(56,189,248,0.12)' : '#0C121B'}
                      stroke={active ? '#38BDF8' : '#1D2836'}
                      strokeWidth={active ? 1.6 : 1}
                    />
                    <rect x={0} y={0} width={3} height={48} rx={2} fill={color} />
                    <text x={12} y={20} className="text-[10px]" fill="#E8EEF6">
                      {node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label}
                    </text>
                    <text x={12} y={34} className="text-[9px]" fill="#5C6980">
                      {node.row_count !== null ? `${formatNumber(node.row_count)} rows` : node.type}
                      {node.contains_pii ? ' · PII' : ''}
                    </text>
                    <text x={148} y={20} className="text-[9px]" fill={color} textAnchor="end">
                      {node.layer}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
        </div>

        <div className="flex flex-wrap gap-3 border-t border-line px-4 py-2.5">
          {Object.entries(LAYER_COLOR).map(([layer, color]) => (
            <span key={layer} className="flex items-center gap-1.5 text-2xs text-muted">
              <span className="h-2 w-2 rounded-sm" style={{ background: color }} />
              {layer}
            </span>
          ))}
        </div>
      </Panel>

      {selected ? (
        <Panel>
          <PanelHeader title={selected} subtitle="Connected transformations" />
          <ul className="divide-y divide-line/60">
            {selectedEdges.map((edge, index) => (
              <li key={index} className="flex flex-wrap items-center gap-3 px-5 py-3">
                <span className="font-mono text-2xs text-muted">{edge.source}</span>
                <span className="text-faint">→</span>
                <span className="font-mono text-2xs text-ink">{edge.target}</span>
                <span className="text-xs text-muted">{edge.transformation}</span>
                <Badge className="ml-auto">{edge.processor}</Badge>
              </li>
            ))}
          </ul>
        </Panel>
      ) : (
        <div className="flex items-start gap-3 rounded-lg border border-line bg-surface px-4 py-3">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-muted" />
          <p className="text-xs text-muted">
            Select a node to see the transformations that write into it and the datasets it feeds. The edge from{' '}
            <span className="font-mono text-ink">cases</span> back to{' '}
            <span className="font-mono text-ink">transaction_features</span> is the analyst feedback loop that supplies
            labels for retraining.
          </p>
        </div>
      )}
    </div>
  );
}
