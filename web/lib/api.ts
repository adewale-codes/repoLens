import "server-only";
import { getApiBaseUrl } from "./config";
import type { ChunkSummary, GraphEdge, Repo } from "./types";

// Typed server-side client for the RepoLens API. Every call is uncached:
// a re-ingest changes a repo's data, and a page must never show a stale 404
// for a repo that has since been indexed.

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown
  ) {
    super(`RepoLens API returned ${status}`);
  }
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${getApiBaseUrl()}${path}`, { cache: "no-store", ...init });
}

async function getJson<T>(path: string): Promise<T | null> {
  const res = await apiFetch(path);
  if (res.status === 404) return null;
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return (await res.json()) as T;
}

const repoPath = (repoId: string) => repoId.split("/").map(encodeURIComponent).join("/");

/** An indexed repo (case-insensitive lookup; the result carries the canonical id), or null. */
export function getRepo(repoId: string): Promise<Repo | null> {
  return getJson<Repo>(`/repos/${repoPath(repoId)}`);
}

export async function getGraph(repoId: string): Promise<GraphEdge[]> {
  const body = await getJson<{ edges: GraphEdge[] }>(`/repos/${repoPath(repoId)}/graph`);
  return body?.edges ?? [];
}

export async function getChunks(repoId: string): Promise<ChunkSummary[]> {
  return (await getJson<ChunkSummary[]>(`/repos/${repoPath(repoId)}/chunks`)) ?? [];
}

export async function listRepos(): Promise<Repo[]> {
  return (await getJson<Repo[]>("/repos")) ?? [];
}
