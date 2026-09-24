import { languageNames } from "./format";
import type { Repo } from "./types";

// Length budgets for repo page descriptions. Google shows roughly 150-160
// characters of a meta description; social cards on mobile cut around 125.
export const META_DESCRIPTION_MAX = 155;
export const SOCIAL_DESCRIPTION_MAX = 125;

/**
 * A description of an indexed repo that fits within `max` characters.
 *
 * Rather than cutting one long string (which can end mid-word), it tries
 * complete sentences from richest to plainest and returns the first that
 * fits, so the result always reads as whole sentences. Long owner/repo names
 * fall through to the shorter variants.
 */
export function repoDescription(repo: Repo, max: number): string {
  const s = repo.stats;
  const id = repo.repo_id;
  const lang = languageNames(s.chunks_by_language);
  const n = (x: number) => x.toLocaleString("en-US");
  const files = `${n(s.files_indexed)} file${s.files_indexed === 1 ? "" : "s"}`;
  const imports = `${n(s.graph.edges ?? 0)} import${s.graph.edges === 1 ? "" : "s"} mapped`;

  const leads = [
    `Ask ${id} how its code works and get answers that cite the exact lines.`,
    `Ask ${id} how its code works, with answers citing exact lines.`,
  ];
  const stats = [
    `${lang} · ${files} · ${n(s.chunks)} functions, classes & blocks · ${imports}.`,
    `${lang} · ${files} · ${imports}.`,
    `${lang} · ${files}.`,
  ];
  const candidates = [
    ...leads.flatMap((lead) => stats.map((stat) => `${lead} ${stat}`)),
    ...leads,
    `Ask this ${lang} repo how its code works, with answers citing exact lines.`,
    "Ask this repo how its code works, with answers citing exact lines.",
  ];
  const fit = candidates.find((c) => c.length <= max);
  if (fit) return fit;

  // Unreachable for any real budget (the last candidate is 67 characters),
  // but never return something over the limit: cut at a word boundary.
  const last = candidates[candidates.length - 1];
  return last.slice(0, last.lastIndexOf(" ", max - 1)).replace(/[,\s]+$/, "") + ".";
}
