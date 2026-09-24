import "server-only";

// Per-client fixed-window limits for the two expensive public actions:
// /api/ask spends Claude credits, and /api/ingest occupies the single ingest
// worker for minutes. In-memory, so the limits are per server instance and
// reset on restart. That's enough to stop casual abuse of a portfolio
// deployment, not a determined attacker behind many IPs.

type Window = { start: number; count: number };
const windows = new Map<string, Window>();

export function clientKey(req: Request): string {
  const forwarded = req.headers.get("x-forwarded-for");
  return forwarded?.split(",")[0].trim() || req.headers.get("x-real-ip") || "local";
}

/** Returns seconds until the window resets if the limit is hit, else null (and counts the hit). */
export function rateLimit(bucket: string, key: string, limit: number, windowMs: number): number | null {
  const id = `${bucket}:${key}`;
  const now = Date.now();
  const w = windows.get(id);
  if (!w || now - w.start >= windowMs) {
    windows.set(id, { start: now, count: 1 });
    return null;
  }
  if (w.count >= limit) return Math.ceil((w.start + windowMs - now) / 1000);
  w.count += 1;
  return null;
}
