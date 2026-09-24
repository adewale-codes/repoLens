import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";

// GET -> the job's live status from the API (stage, embedding progress, error, result).
export async function GET(_req: Request, ctx: RouteContext<"/api/ingest/[jobId]">) {
  const { jobId } = await ctx.params;
  if (!/^[0-9a-f]{32}$/.test(jobId)) {
    return NextResponse.json({ error: "Unknown job." }, { status: 404 });
  }
  const res = await apiFetch(`/ingest/${jobId}`);
  if (res.status === 404) {
    return NextResponse.json({ error: "Unknown job." }, { status: 404 });
  }
  if (!res.ok) {
    return NextResponse.json({ error: "The indexing service is unavailable right now." }, { status: 502 });
  }
  return NextResponse.json(await res.json(), { headers: { "Cache-Control": "no-store" } });
}
