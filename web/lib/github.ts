// GitHub URL helpers. Pure functions, safe on client and server.

// Same shape the API accepts (services/fetch.py), plus bare "owner/repo".
const NAME = "[A-Za-z0-9_.-]+";
const REPO_INPUT = new RegExp(
  `^(?:(?:https?://)?(?:www\\.)?github\\.com/)?(${NAME})/(${NAME}?)(?:\\.git)?/?(?:[?#].*)?$`
);
const SEGMENT = new RegExp(`^${NAME}$`);

/** "https://github.com/pallets/click", "github.com/pallets/click.git" or "pallets/click" -> {owner, repo}. */
export function parseRepoInput(input: string): { owner: string; repo: string } | null {
  const trimmed = input.trim().replace(/\/(tree|blob)\/.*$/, "");
  const m = trimmed.match(REPO_INPUT);
  if (!m || !m[2] || m[1] === "." || m[1] === "..") return null;
  return { owner: m[1], repo: m[2] };
}

/** True if both path segments could be a GitHub owner/repo (guards the dynamic route). */
export function isValidRepoPath(owner: string, repo: string): boolean {
  return SEGMENT.test(owner) && SEGMENT.test(repo) && ![owner, repo].some((s) => s === "." || s === "..");
}

export function repoUrl(repoId: string): string {
  return `https://github.com/${repoId}`;
}

/** A link to exact lines of a file at the indexed commit, so it never drifts. */
export function blobUrl(repoId: string, commit: string, file: string, start?: number, end?: number): string {
  const path = file.split("/").map(encodeURIComponent).join("/");
  const anchor = start ? (end && end !== start ? `#L${start}-L${end}` : `#L${start}`) : "";
  return `https://github.com/${repoId}/blob/${commit}/${path}${anchor}`;
}
