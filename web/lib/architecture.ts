import type { ChunkSummary, GraphEdge } from "./types";

// An architecture summary derived from the real index: where the code lives,
// what it's made of, and which files everything else depends on.

export type DirSummary = { dir: string; files: number; chunks: number };
export type Hub = { file: string; importedBy: number; imports: number };

/** Group key for a file: its top-level directory, or two levels for src-style layouts. */
export function groupOf(file: string): string {
  const parts = file.split("/");
  if (parts.length === 1) return "(root)";
  if (["src", "lib", "source", "packages", "apps"].includes(parts[0]) && parts.length > 2) {
    return `${parts[0]}/${parts[1]}`;
  }
  return parts[0];
}

const NON_SOURCE = /^(tests?|__tests__|spec|specs|examples?|docs?|benchmarks?|bench|scripts|fixtures|e2e)$/i;

/** Tests, examples, docs and the like: hidden by default in the graph. */
export function isAuxiliaryGroup(group: string): boolean {
  return NON_SOURCE.test(group.split("/")[0]);
}

export function directories(chunks: ChunkSummary[]): DirSummary[] {
  const byDir = new Map<string, { files: Set<string>; chunks: number }>();
  for (const c of chunks) {
    const dir = groupOf(c.file);
    const entry = byDir.get(dir) ?? { files: new Set<string>(), chunks: 0 };
    entry.files.add(c.file);
    entry.chunks += 1;
    byDir.set(dir, entry);
  }
  return [...byDir.entries()]
    .map(([dir, v]) => ({ dir, files: v.files.size, chunks: v.chunks }))
    .sort((a, b) => b.chunks - a.chunks || a.dir.localeCompare(b.dir));
}

export function hubs(edges: GraphEdge[], limit = 6): Hub[] {
  const importedBy = new Map<string, number>();
  const imports = new Map<string, number>();
  for (const e of edges) {
    importedBy.set(e.dst, (importedBy.get(e.dst) ?? 0) + 1);
    imports.set(e.src, (imports.get(e.src) ?? 0) + 1);
  }
  return [...importedBy.entries()]
    .map(([file, n]) => ({ file, importedBy: n, imports: imports.get(file) ?? 0 }))
    .sort((a, b) => b.importedBy - a.importedBy || a.file.localeCompare(b.file))
    .slice(0, limit);
}

export const KIND_LABELS: Record<string, string> = {
  function: "Functions",
  method: "Methods",
  class: "Classes",
  class_summary: "Large-class headers",
  class_body: "Class bodies",
  module: "Module-level code",
  interface: "Interfaces",
  type: "Type aliases",
  enum: "Enums",
};
