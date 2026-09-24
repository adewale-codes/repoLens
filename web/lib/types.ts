// Shapes returned by the RepoLens API (see ../../schemas.py). Safe to import
// from client components: types only, no runtime values.

export type RepoStats = {
  files_indexed: number;
  chunks: number;
  chunks_by_kind: Record<string, number>;
  chunks_by_language: Record<string, number>;
  files_skipped: Record<string, number>;
  parse_errors: Record<string, string>;
  graph: { edges?: number; external?: number; unresolved?: number };
  timings_s: Record<string, number>;
};

export type Repo = {
  repo_id: string;
  url: string;
  commit: string;
  embed_model: string;
  dim: number;
  ingested_at: number; // unix seconds
  stats: RepoStats;
};

export type GraphEdge = { src: string; dst: string; spec: string; line: number };

export type ChunkSummary = {
  file: string;
  symbol: string;
  kind: string;
  start_line: number;
  end_line: number;
};

export type JobStatus = "queued" | "fetching" | "parsing" | "embedding" | "storing" | "complete" | "failed";

export type IngestJob = {
  job_id: string;
  repo_url: string;
  repo_id: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  progress: { chunks_embedded?: number; chunks_total?: number; repo_id?: string; commit?: string } | null;
  error: string | null;
  result: (RepoStats & { repo_id: string; commit: string }) | null;
};

export type Citation = {
  file: string;
  start: number;
  end: number;
  valid: boolean;
  basis: "excerpt" | "class_index" | null;
};

export type AnswerStatus = "answered" | "partial" | "not_found" | "refused";

export type ChunkUsed = {
  ref: number;
  file: string;
  start_line: number;
  end_line: number;
  shown_through_line: number;
  symbol: string;
  kind: string;
  retrieved_by: "vector" | "graph";
  reason: string;
  score: number;
};

export type AskResponse = {
  repo_id: string;
  commit: string;
  question: string;
  answer: string;
  answer_status: AnswerStatus | null;
  citations: Citation[];
  files_consulted: string[];
  chunks_used: ChunkUsed[];
  top_similarity: number;
  model: string | null;
};
