import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import { isValidRepoPath } from "@/lib/github";
import { clientKey, rateLimit } from "@/lib/ratelimit";

// Answers take 20-90 s (retrieval plus Claude with adaptive thinking).
export const maxDuration = 180;

// POST {repo_id: "owner/repo", question} -> the API's /ask response.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { repo_id?: unknown; question?: unknown } | null;
  const repoId = typeof body?.repo_id === "string" ? body.repo_id : "";
  const question = typeof body?.question === "string" ? body.question.trim() : "";
  const [owner, repo] = repoId.split("/");

  if (!owner || !repo || !isValidRepoPath(owner, repo)) {
    return NextResponse.json({ error: "Unknown repository." }, { status: 400 });
  }
  if (question.length < 3 || question.length > 2000) {
    return NextResponse.json({ error: "Ask a question between 3 and 2000 characters." }, { status: 400 });
  }

  const retryAfter = rateLimit("ask", clientKey(req), 10, 10 * 60 * 1000);
  if (retryAfter !== null) {
    return NextResponse.json(
      { error: `That's a lot of questions in a short time. Try again in ${Math.ceil(retryAfter / 60)} min.` },
      { status: 429, headers: { "Retry-After": String(retryAfter) } }
    );
  }

  const res = await apiFetch("/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repo_id: repoId, question }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok) return NextResponse.json(data);

  const message =
    res.status === 404
      ? "This repository isn't indexed."
      : res.status === 429
        ? "The answering service is busy. Try again in a moment."
        : "Couldn't get an answer right now. Please try again.";
  return NextResponse.json({ error: message }, { status: res.status === 404 ? 404 : 502 });
}
