import { NextResponse } from "next/server";
import { apiFetch, getRepo } from "@/lib/api";
import { parseRepoInput } from "@/lib/github";
import { clientKey, rateLimit } from "@/lib/ratelimit";

// POST {repo: "<GitHub URL or owner/repo>"}
//   -> {state: "indexed", repo_id}          already indexed: go straight to its page
//   -> {state: "queued", repo_id, job_id}   ingestion started (or already running): poll the job
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { repo?: unknown } | null;
  const parsed = typeof body?.repo === "string" ? parseRepoInput(body.repo) : null;
  if (!parsed) {
    return NextResponse.json(
      { error: "That doesn't look like a GitHub repository. Try https://github.com/owner/repo." },
      { status: 400 }
    );
  }

  const repoId = `${parsed.owner}/${parsed.repo}`;
  const existing = await getRepo(repoId);
  if (existing) {
    return NextResponse.json({ state: "indexed", repo_id: existing.repo_id });
  }

  const retryAfter = rateLimit("ingest", clientKey(req), 5, 60 * 60 * 1000);
  if (retryAfter !== null) {
    return NextResponse.json(
      { error: `You've started several indexing jobs recently. Try again in ${Math.ceil(retryAfter / 60)} min.` },
      { status: 429, headers: { "Retry-After": String(retryAfter) } }
    );
  }

  const res = await apiFetch("/ingest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_url: `https://github.com/${repoId}` }),
  });
  const data = await res.json().catch(() => ({}));

  if (res.status === 202) {
    return NextResponse.json({ state: "queued", repo_id: repoId, job_id: data.job_id });
  }
  if (res.status === 409) {
    // Someone already started this repo; follow their job instead of failing.
    return NextResponse.json({ state: "queued", repo_id: repoId, job_id: data.detail?.job_id });
  }
  if (res.status === 422) {
    return NextResponse.json({ error: String(data.detail ?? "Invalid repository URL.") }, { status: 400 });
  }
  return NextResponse.json({ error: "The indexing service is unavailable right now." }, { status: 502 });
}
