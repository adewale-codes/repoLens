import "server-only";
import { headers } from "next/headers";

/**
 * Absolute base URL for this deployment, used for share links and OG
 * metadata. Prefers the SITE_URL env var (set this in production); falls
 * back to the incoming request's host for local dev / preview deploys.
 */
export async function getBaseUrl(): Promise<string> {
  if (process.env.SITE_URL) {
    return process.env.SITE_URL.replace(/\/+$/, "");
  }

  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (/^(localhost|127\.0\.0\.1)/.test(host) ? "http" : "https");
  return `${proto}://${host}`;
}
