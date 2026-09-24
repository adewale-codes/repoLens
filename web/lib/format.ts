const LANGUAGE_NAMES: Record<string, string> = {
  python: "Python",
  javascript: "JavaScript",
  typescript: "TypeScript",
};

/** "Python", "JavaScript & TypeScript", ... from the ingest report's chunks_by_language. */
export function languageNames(chunksByLanguage: Record<string, number>): string {
  return (
    Object.entries(chunksByLanguage)
      .sort((a, b) => b[1] - a[1])
      .map(([l]) => LANGUAGE_NAMES[l] ?? l)
      .join(" & ") || "Code"
  );
}

export function plural(n: number, word: string): string {
  return `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;
}
