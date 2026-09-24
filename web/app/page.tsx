import Link from "next/link";
import { Suspense } from "react";
import RepoSubmit from "@/components/RepoSubmit";
import { listRepos } from "@/lib/api";
import { languageNames } from "@/lib/format";
import type { Repo } from "@/lib/types";

// Already indexed and checked end to end in the Phase 1-2 acceptance runs,
// so these open instantly.
const EXAMPLES: { repoId: string; blurb: string; question: string }[] = [
  { repoId: "pallets/click", blurb: "Python CLI framework", question: "What does make_context do?" },
  { repoId: "expressjs/express", blurb: "Node.js web framework", question: "How does app.listen start the server?" },
  { repoId: "sindresorhus/ky", blurb: "TypeScript HTTP client", question: "How does ky decide when to retry?" },
];

async function indexedExamples(): Promise<{ repo: Repo; blurb: string; question: string }[]> {
  let repos: Repo[] = [];
  try {
    repos = await listRepos();
  } catch {
    return []; // API unreachable: the form still renders and reports errors on submit
  }
  return EXAMPLES.flatMap((ex) => {
    const repo = repos.find((r) => r.repo_id.toLowerCase() === ex.repoId.toLowerCase());
    return repo ? [{ repo, blurb: ex.blurb, question: ex.question }] : [];
  });
}

export default async function Home() {
  const examples = await indexedExamples();

  return (
    <main className="flex-1">
      <section className="mx-auto max-w-3xl px-4 pb-14 pt-16 text-center sm:pt-24">
        <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-brand-border bg-brand-soft px-3 py-1 text-xs font-medium text-brand">
          Python · JavaScript · TypeScript
        </p>
        <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
          Ask any GitHub repo how it works.
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-balance text-lg text-muted">
          RepoLens reads the code function by function, maps which files import which, and answers your questions
          with citations to the exact lines. If the answer isn&apos;t in the code, it says so.
        </p>
        <div className="mx-auto mt-8 max-w-2xl">
          <Suspense fallback={<div className="h-12 rounded-lg border border-border bg-surface" />}>
            <RepoSubmit />
          </Suspense>
        </div>
      </section>

      {examples.length > 0 && (
        <section className="mx-auto max-w-5xl px-4 pb-16" aria-labelledby="examples-heading">
          <div className="mb-4 flex items-baseline justify-between">
            <h2 id="examples-heading" className="text-sm font-semibold uppercase tracking-wide text-muted">
              Try one that&apos;s already indexed
            </h2>
            <span className="text-xs text-subtle">Opens instantly, no wait</span>
          </div>
          <ul className="grid gap-3 sm:grid-cols-3">
            {examples.map(({ repo, blurb, question }) => {
              return (
                <li key={repo.repo_id}>
                  <Link
                    href={`/${repo.repo_id}`}
                    className="group flex h-full flex-col rounded-xl border border-border bg-surface p-4 shadow-sm transition hover:border-brand-border hover:shadow-md"
                  >
                    <span className="font-mono text-[15px] font-medium group-hover:text-brand">{repo.repo_id}</span>
                    <span className="mt-0.5 text-sm text-muted">{blurb}</span>
                    <span className="mt-3 text-xs text-subtle tabular-nums">
                      {languageNames(repo.stats.chunks_by_language)} · {repo.stats.files_indexed} files · {repo.stats.chunks.toLocaleString()} chunks ·{" "}
                      {repo.stats.graph.edges ?? 0} imports
                    </span>
                    <span className="mt-3 border-t border-border pt-3 text-sm text-foreground/80">
                      Try: &ldquo;{question}&rdquo;
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="border-t border-border bg-surface">
        <div className="mx-auto grid max-w-5xl gap-8 px-4 py-14 sm:grid-cols-3">
          <Feature title="Syntax-aware, not line windows">
            Code is split along real function and class boundaries with Python&apos;s own parser and tree-sitter, so
            every answer is built from whole units of code.
          </Feature>
          <Feature title="Citations that are checked">
            Every file:line in an answer links to the exact lines on GitHub, and is checked against what the model
            was actually shown. Anything unchecked is flagged.
          </Feature>
          <Feature title="A map of the codebase">
            Each repository page shows which files import which, so you can see the architecture before reading a
            single line.
          </Feature>
        </div>
      </section>
    </main>
  );
}

function Feature({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-muted">{children}</p>
    </div>
  );
}
