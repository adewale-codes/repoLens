import "server-only";

// Server-only: reads REPOLENS_API_URL, which must not be exposed to the
// browser (no NEXT_PUBLIC_ prefix), for the same reason as WHYFAIL_API_URL and
// PACKAGESAFE_API_URL in the other two projects. The API base URL is an internal
// deployment detail, not something a client bundle should carry. Browser code
// reaches the API only through this app's /api/* route handlers.
//
// The default is 127.0.0.1 rather than localhost: on Windows, "localhost" tries
// IPv6 first, and the refused connection costs ~2 s per request.

const DEFAULT_API_URL = "http://127.0.0.1:8000";

export function getApiBaseUrl(): string {
  const configured = process.env.REPOLENS_API_URL || DEFAULT_API_URL;
  return configured.replace(/\/+$/, "");
}
