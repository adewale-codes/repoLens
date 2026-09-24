"use client";

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import { useMemo, useState } from "react";
import { groupOf, isAuxiliaryGroup } from "@/lib/architecture";
import { blobUrl } from "@/lib/github";
import type { GraphEdge } from "@/lib/types";

// Categorical slots validated all-pairs on the white card (see globals.css).
// Only three: a node-link graph puts every pair of colors next to each other,
// so further groups fold into a neutral "other" instead of new hues.
const SLOTS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const OTHER = "var(--series-other)";

type Node = SimulationNodeDatum & { id: string; group: string; inDeg: number; outDeg: number; r: number };
type Link = SimulationLinkDatum<Node> & { edge: GraphEdge };

const WIDTH = 900;
const MIN_HEIGHT = 340;
const MAX_HEIGHT = 620;
const FONT = 12;
const CHAR_W = 7.3; // Geist Mono at 12px

type Label = { x: number; y: number; anchor: "start" | "end" | "middle" };

/**
 * Direct labels without collisions. Most-imported files are placed first, each
 * trying right, left, above, then below its dot; a label that fits nowhere is
 * left for hover. Small graphs try every node, large ones the top 20.
 */
function placeLabels(nodes: Node[]): Map<string, Label> {
  const boxes: [number, number, number, number][] = nodes.map((n) => [
    (n.x ?? 0) - n.r - 2, (n.y ?? 0) - n.r - 2, (n.x ?? 0) + n.r + 2, (n.y ?? 0) + n.r + 2,
  ]);
  const overlaps = (b: [number, number, number, number]) =>
    b[0] < 2 || b[2] > WIDTH - 2 || boxes.some((o) => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]);
  const order = [...nodes].sort((a, b) => b.inDeg - a.inDeg || a.id.localeCompare(b.id));
  const candidates = nodes.length <= 30 ? order : order.slice(0, 20);
  const placed = new Map<string, Label>();
  for (const n of candidates) {
    const text = basename(n.id);
    const w = text.length * CHAR_W;
    const x = n.x ?? 0;
    const y = n.y ?? 0;
    const options: [Label, [number, number, number, number]][] = [
      [{ x: x + n.r + 5, y: y + 4, anchor: "start" }, [x + n.r + 4, y - 8, x + n.r + 6 + w, y + 7]],
      [{ x: x - n.r - 5, y: y + 4, anchor: "end" }, [x - n.r - 6 - w, y - 8, x - n.r - 4, y + 7]],
      [{ x, y: y - n.r - 6, anchor: "middle" }, [x - w / 2, y - n.r - 18, x + w / 2, y - n.r - 3]],
      [{ x, y: y + n.r + 15, anchor: "middle" }, [x - w / 2, y + n.r + 3, x + w / 2, y + n.r + 18]],
    ];
    const fit = options.find(([, box]) => !overlaps(box));
    if (fit) {
      placed.set(n.id, fit[0]);
      boxes.push(fit[1]);
    }
  }
  return placed;
}

export default function DependencyGraph({
  edges,
  repoId,
  commit,
}: {
  edges: GraphEdge[];
  repoId: string;
  commit: string;
}) {
  // Every group, largest first. Colors are fixed per group up front, so
  // toggling a group never repaints the others.
  const groups = useMemo(() => {
    const files = new Map<string, string>();
    for (const e of edges) {
      files.set(e.src, groupOf(e.src));
      files.set(e.dst, groupOf(e.dst));
    }
    const counts = new Map<string, number>();
    for (const g of files.values()) counts.set(g, (counts.get(g) ?? 0) + 1);
    const ordered = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    const colored = ordered.filter(([g]) => !isAuxiliaryGroup(g)).slice(0, SLOTS.length).map(([g]) => g);
    return ordered.map(([name, count]) => ({
      name,
      count,
      color: colored.includes(name) ? SLOTS[colored.indexOf(name)] : OTHER,
      auxiliary: isAuxiliaryGroup(name),
    }));
  }, [edges]);

  const [visible, setVisible] = useState<Set<string>>(() => {
    const source = groups.filter((g) => !g.auxiliary);
    const sourceFiles = source.reduce((n, g) => n + g.count, 0);
    // If hiding tests/examples would leave almost nothing, show everything.
    return new Set((sourceFiles >= 3 ? source : groups).map((g) => g.name));
  });
  const [hovered, setHovered] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const colorOf = useMemo(() => new Map(groups.map((g) => [g.name, g.color])), [groups]);

  const layout = useMemo(() => {
    const shown = edges.filter((e) => visible.has(groupOf(e.src)) && visible.has(groupOf(e.dst)));
    const nodes = new Map<string, Node>();
    const node = (id: string) => {
      let n = nodes.get(id);
      if (!n) {
        n = { id, group: groupOf(id), inDeg: 0, outDeg: 0, r: 5 };
        nodes.set(id, n);
      }
      return n;
    };
    for (const e of shown) {
      node(e.src).outDeg += 1;
      node(e.dst).inDeg += 1;
    }
    const list = [...nodes.values()].sort((a, b) => a.id.localeCompare(b.id));
    for (const n of list) n.r = 5 + 2.2 * Math.sqrt(n.inDeg);
    const links: Link[] = shown.map((e) => ({ source: e.src, target: e.dst, edge: e }));

    // Deterministic: d3-force seeds its own RNG, so server and client agree.
    // Small graphs get more room per node so labels fit between them.
    const spread = list.length <= 30 ? 1.6 : 1;
    const sim = forceSimulation(list)
      .force("link", forceLink<Node, Link>(links).id((d) => d.id).distance(70 * spread).strength(0.35))
      .force("charge", forceManyBody<Node>().strength(-220 * spread))
      .force("collide", forceCollide<Node>().radius((d) => d.r + 14))
      .force("x", forceX<Node>(0).strength(0.05))
      .force("y", forceY<Node>(0).strength(0.07))
      .stop();
    for (let i = 0; i < 360; i++) sim.tick();

    // Fit into the viewBox: the width is fixed and the height follows the graph's shape.
    const padX = 110; // room for labels at the edges
    const padY = 36;
    const xs = list.map((n) => n.x ?? 0);
    const ys = list.map((n) => n.y ?? 0);
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);
    const scale = Math.min((WIDTH - 2 * padX) / spanX, (MAX_HEIGHT - 2 * padY) / spanY, 2.5);
    const height = Math.max(MIN_HEIGHT, spanY * scale + 2 * padY);
    const offX = (WIDTH - spanX * scale) / 2;
    const offY = (height - spanY * scale) / 2;
    for (const n of list) {
      n.x = offX + ((n.x ?? 0) - minX) * scale;
      n.y = offY + ((n.y ?? 0) - minY) * scale;
    }

    return { nodes: list, links, byId: nodes, height, labels: placeLabels(list) };
  }, [edges, visible]);

  const focus = hovered ?? selected;
  const neighbors = useMemo(() => {
    if (!focus) return null;
    const set = new Set([focus]);
    for (const l of layout.links) {
      const s = (l.source as Node).id;
      const t = (l.target as Node).id;
      if (s === focus) set.add(t);
      if (t === focus) set.add(s);
    }
    return set;
  }, [focus, layout.links]);

  const detail = selected ? layout.byId.get(selected) : null;

  function toggle(name: string) {
    setSelected(null);
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Show or hide directories">
        {groups.map((g) => {
          const on = visible.has(g.name);
          return (
            <button
              key={g.name}
              type="button"
              onClick={() => toggle(g.name)}
              aria-pressed={on}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-xs transition-colors ${
                on ? "border-border-strong bg-surface text-foreground" : "border-border bg-surface-2 text-subtle line-through"
              }`}
            >
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: on ? g.color : "var(--border-strong)" }} />
              {g.name}
              <span className="text-subtle">{g.count}</span>
            </button>
          );
        })}
      </div>

      <div className="relative overflow-hidden rounded-xl border border-border bg-surface">
        <div className="overflow-x-auto">
        {layout.nodes.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted">No import edges between the directories selected above.</p>
        ) : (
          <svg
            viewBox={`0 0 ${WIDTH} ${layout.height}`}
            className="block h-auto w-full min-w-[640px]"
            role="img"
            aria-label={`Import graph: ${layout.nodes.length} files and ${layout.links.length} imports. The full list is in the table below.`}
            onClick={() => setSelected(null)}
          >
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0 0L10 5L0 10z" fill="var(--border-strong)" />
              </marker>
              <marker id="arrow-hi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0 0L10 5L0 10z" fill="var(--foreground)" />
              </marker>
            </defs>
            <g>
              {layout.links.map((l) => {
                const s = l.source as Node;
                const t = l.target as Node;
                const hi = focus !== null && (s.id === focus || t.id === focus);
                const dim = focus !== null && !hi;
                // Stop the line at the target's rim so the arrowhead stays visible.
                const dx = (t.x ?? 0) - (s.x ?? 0);
                const dy = (t.y ?? 0) - (s.y ?? 0);
                const len = Math.hypot(dx, dy) || 1;
                const x2 = (t.x ?? 0) - (dx / len) * (t.r + 3);
                const y2 = (t.y ?? 0) - (dy / len) * (t.r + 3);
                return (
                  <line
                    key={`${s.id}->${t.id}`}
                    x1={s.x}
                    y1={s.y}
                    x2={x2}
                    y2={y2}
                    stroke={hi ? "var(--foreground)" : "var(--border-strong)"}
                    strokeWidth={hi ? 1.6 : 1}
                    strokeOpacity={dim ? 0.25 : 1}
                    markerEnd={hi ? "url(#arrow-hi)" : "url(#arrow)"}
                  />
                );
              })}
            </g>
            <g>
              {layout.nodes.map((n) => {
                const dim = neighbors !== null && !neighbors.has(n.id);
                // Placed labels always; others appear for the hovered file and its neighbors.
                const label: Label | undefined =
                  layout.labels.get(n.id) ?? (neighbors?.has(n.id) ? { x: n.r + 5, y: 4, anchor: "start" } : undefined);
                const lx = label && layout.labels.has(n.id) ? label.x - (n.x ?? 0) : label?.x;
                const ly = label && layout.labels.has(n.id) ? label.y - (n.y ?? 0) : label?.y;
                return (
                  <g
                    key={n.id}
                    transform={`translate(${n.x},${n.y})`}
                    opacity={dim ? 0.3 : 1}
                    className="cursor-pointer"
                    onMouseEnter={() => setHovered(n.id)}
                    onMouseLeave={() => setHovered(null)}
                    onClick={(e) => {
                      e.stopPropagation();
                      setSelected(n.id === selected ? null : n.id);
                    }}
                  >
                    {/* Bigger invisible hit target than the mark. */}
                    <circle r={Math.max(n.r + 6, 12)} fill="transparent" />
                    <circle
                      r={n.r}
                      fill={colorOf.get(n.group) ?? OTHER}
                      stroke="var(--surface)"
                      strokeWidth={2}
                      className={n.id === selected ? "outline-none" : undefined}
                    />
                    {n.id === selected && <circle r={n.r + 4} fill="none" stroke="var(--foreground)" strokeWidth={1.5} />}
                    <title>{`${n.id}\nimported by ${n.inDeg} · imports ${n.outDeg}`}</title>
                    {label && (
                      <text
                        x={lx}
                        y={ly}
                        textAnchor={label.anchor}
                        fontSize={FONT}
                        fill="var(--foreground)"
                        stroke="var(--surface)"
                        strokeWidth={3}
                        paintOrder="stroke"
                        className="pointer-events-none font-mono"
                      >
                        {basename(n.id)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>
        )}
        </div>
        <p className="border-t border-border px-4 py-2 text-xs text-subtle">
          Arrows point from a file to the file it imports. Bigger dots are imported by more files. Hover to trace a
          file&apos;s connections; click to pin it.
        </p>
      </div>

      {detail && (
        <div className="mt-3 rounded-xl border border-border bg-surface p-4 text-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <a
              href={blobUrl(repoId, commit, detail.id)}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono font-medium text-brand hover:underline"
            >
              {detail.id}
            </a>
            <button type="button" onClick={() => setSelected(null)} className="text-xs text-muted hover:text-foreground">
              Close
            </button>
          </div>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <EdgeList
              title={`Imports (${detail.outDeg})`}
              items={layout.links.filter((l) => (l.source as Node).id === detail.id).map((l) => l.edge)}
              pick="dst"
              repoId={repoId}
              commit={commit}
            />
            <EdgeList
              title={`Imported by (${detail.inDeg})`}
              items={layout.links.filter((l) => (l.target as Node).id === detail.id).map((l) => l.edge)}
              pick="src"
              repoId={repoId}
              commit={commit}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function EdgeList({
  title,
  items,
  pick,
  repoId,
  commit,
}: {
  title: string;
  items: GraphEdge[];
  pick: "src" | "dst";
  repoId: string;
  commit: string;
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</p>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-subtle">None among the directories shown.</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {items.map((e) => (
            <li key={`${e.src}->${e.dst}`} className="font-mono text-xs">
              <a href={blobUrl(repoId, commit, e.src, e.line)} target="_blank" rel="noopener noreferrer" className="hover:text-brand hover:underline">
                {e[pick]}
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}
