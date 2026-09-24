import { blobUrl } from "./github";
import type { Citation } from "./types";

// Turns the answer's `file:line` citations into markdown links to the exact
// lines on GitHub, at the indexed commit. The pattern matches the API's
// (services/answer.py _CITATION), so every match corresponds to an entry in the
// response's `citations` list, which says whether it was verified.

const CITE =
  "([A-Za-z0-9_./@+\\-]+\\.(?:py|pyi|js|jsx|mjs|cjs|ts|tsx|mts|cts)):(\\d+)(?:\\s*[-–]\\s*(\\d+))?";
const IN_BACKTICKS = new RegExp("`" + CITE + "`( \\[unverified\\])?", "g");
const BARE = new RegExp("(?<![\\w/.])" + CITE + "( \\[unverified\\])?", "g");
const FENCE = /(```[\s\S]*?```)/g;

/** Link titles carry the verification state to the markdown renderer. */
export const CITE_TITLE = { verified: "cite:verified", classIndex: "cite:class_index", unverified: "cite:unverified" };

export function linkifyCitations(markdown: string, citations: Citation[], repoId: string, commit: string): string {
  const byKey = new Map(citations.map((c) => [`${c.file}:${c.start}-${c.end}`, c]));
  const tokens: string[] = [];

  function toToken(file: string, start: string, end: string | undefined, original: string): string {
    const clean = file.replace(/^\.\//, "");
    const s = Number(start);
    const e = end ? Number(end) : s;
    const c = byKey.get(`${clean}:${s}-${e}`);
    if (!c) return original; // not a citation the API checked: leave the text alone
    const title = !c.valid ? CITE_TITLE.unverified : c.basis === "class_index" ? CITE_TITLE.classIndex : CITE_TITLE.verified;
    const label = `${clean}:${s}${e !== s ? `-${e}` : ""}`;
    tokens.push(`[\`${label}\`](<${blobUrl(repoId, commit, clean, s, e)}> "${title}")`);
    return `\u0000${tokens.length - 1}\u0000`;
  }

  // Leave fenced code blocks untouched; they're code, not prose citations.
  return markdown
    .split(FENCE)
    .map((part) => {
      if (part.startsWith("```")) return part;
      return part
        .replace(IN_BACKTICKS, (m, f, s, e) => toToken(f, s, e, m))
        .replace(BARE, (m, f, s, e) => toToken(f, s, e, m))
        .replace(/\u0000(\d+)\u0000/g, (_, i) => tokens[Number(i)]);
    })
    .join("");
}
