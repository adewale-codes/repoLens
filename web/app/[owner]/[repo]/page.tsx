import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { cache } from "react";
import AskBox from "@/components/AskBox";
import DependencyGraph from "@/components/DependencyGraph";
import ShareButtons from "@/components/ShareButtons";
import { getChunks, getGraph, getRepo } from "@/lib/api";
import { directories, hubs, KIND_LABELS } from "@/lib/architecture";
import { suggestedQuestions } from "@/lib/examples";
import { blobUrl, isValidRepoPath, repoUrl } from "@/lib/github";
import { languageNames, plural } from "@/lib/format";
import { META_DESCRIPTION_MAX, repoDescription, SOCIAL_DESCRIPTION_MAX } from "@/lib/seo";
import { getBaseUrl } from "@/lib/site";
import type { Repo } from "@/lib/types";

// One fetch per request, shared by generateMetadata and the page.
const loadRepo = cache(async (owner: string, repo: string): Promise<Repo | null> => {
  if (!isValidRepoPath(owner, repo)) return null;
  return getRepo(`${owner}/${repo}`);
});

function languages(repo: Repo): string {
  return languageNames(repo.stats.chunks_by_language);
}

export async function generateMetadata(props: PageProps<"/[owner]/[repo]">): Promise<Metadata> {
  const { owner, repo: name } = await props.params;
  const repo = await loadRepo(owner, name);
  if (!repo) return { title: "Repository not indexed | RepoLens" };

  const title = `${repo.repo_id}: how it works | RepoLens`;
  const description = repoDescription(repo, META_DESCRIPTION_MAX);
  const socialDescription = repoDescription(repo, SOCIAL_DESCRIPTION_MAX);
  return {
    title,
    description,
    alternates: { canonical: `/${repo.repo_id}` },
    // siteName is repeated here because Next merges metadata shallowly: this
    // openGraph object replaces the root layout's, including its siteName.
    openGraph: {
      siteName: "RepoLens",
      title: `${repo.repo_id} on RepoLens`,
      description: socialDescription,
      type: "article",
      url: `/${repo.repo_id}`,
    },
    twitter: { card: "summary_large_image", title: `${repo.repo_id} on RepoLens`, description: socialDescription },
  };
}

export default async function RepoPage(props: PageProps<"/[owner]/[repo]">) {
  const { owner, repo: name } = await props.params;
  const repo = await loadRepo(owner, name);
  if (!repo) notFound();
  if (repo.repo_id !== `${owner}/${name}`) permanentRedirect(`/${repo.repo_id}`); // canonical casing

  const [edges, chunks, baseUrl] = await Promise.all([getGraph(repo.repo_id), getChunks(repo.repo_id), getBaseUrl()]);
  const shareUrl = `${baseUrl}/${repo.repo_id}`;
  const s = repo.stats;
  const dirs = directories(chunks);
  const hubFiles = hubs(edges);
  const kinds = Object.entries(s.chunks_by_kind).sort((a, b) => b[1] - a[1]);
  const maxKind = kinds[0]?.[1] ?? 1;
  const indexedOn = new Date(repo.ingested_at * 1000).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm text-muted">{languages(repo)} repository</p>
          <h1 className="mt-1 break-all font-mono text-2xl font-semibold tracking-tight sm:text-3xl">
            <a href={repoUrl(repo.repo_id)} target="_blank" rel="noopener noreferrer" className="hover:text-brand">
              {repo.repo_id}
            </a>
          </h1>
          <p className="mt-2 text-sm text-muted">
            Indexed at commit{" "}
            <a
              href={`${repoUrl(repo.repo_id)}/tree/${repo.commit}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-foreground hover:text-brand"
            >
              {repo.commit.slice(0, 7)}
            </a>{" "}
            on {indexedOn}
          </p>
        </div>
        <ShareButtons
          shareUrl={shareUrl}
          shareText={`How does ${repo.repo_id} work? Ask it anything on RepoLens: answers cite the exact lines, and the import graph is mapped.`}
        />
      </div>

      {/* Ask */}
      <section aria-labelledby="ask-heading" className="mt-8 rounded-2xl border border-border bg-surface p-5 shadow-sm sm:p-6">
        <h2 id="ask-heading" className="text-lg font-semibold">
          Ask about the code
        </h2>
        <p className="mt-1 text-sm text-muted">
          Answers come only from this repository&apos;s code at {repo.commit.slice(0, 7)}, with every file:line linked to
          GitHub. If the code doesn&apos;t contain the answer, RepoLens says so instead of guessing.
        </p>
        <div className="mt-4">
          <AskBox repoId={repo.repo_id} commit={repo.commit} chunkCount={s.chunks} suggestions={suggestedQuestions(repo.repo_id)} />
        </div>
      </section>

      {/* Overview */}
      <section aria-labelledby="overview-heading" className="mt-10">
        <h2 id="overview-heading" className="text-lg font-semibold">
          Architecture overview
        </h2>
        <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Source files indexed" value={s.files_indexed} />
          <Stat label="Functions, classes & blocks" value={s.chunks} />
          <Stat label="Internal imports" value={s.graph.edges ?? 0} />
          <Stat label="External imports" value={s.graph.external ?? 0} />
        </dl>

        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          <Card title="What the code is made of">
            <ul className="space-y-2">
              {kinds.map(([kind, n]) => (
                <li key={kind}>
                  <div className="flex justify-between text-sm">
                    <span>{KIND_LABELS[kind] ?? kind}</span>
                    <span className="tabular-nums text-muted">{n.toLocaleString()}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-surface-2">
                    <div className="h-full rounded-full bg-brand" style={{ width: `${Math.max(2, (n / maxKind) * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Where the code lives">
            <table className="w-full text-sm">
              <thead className="sr-only">
                <tr>
                  <th>Directory</th>
                  <th>Files</th>
                  <th>Chunks</th>
                </tr>
              </thead>
              <tbody>
                {dirs.slice(0, 8).map((d) => (
                  <tr key={d.dir} className="border-b border-border last:border-0">
                    <td className="py-1.5 pr-2 font-mono text-[13px]">{d.dir}</td>
                    <td className="py-1.5 text-right tabular-nums text-muted">{plural(d.files, "file")}</td>
                    <td className="py-1.5 pl-3 text-right tabular-nums text-muted">{d.chunks.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {dirs.length > 8 && <p className="mt-2 text-xs text-subtle">+ {dirs.length - 8} more directories</p>}
          </Card>

          <Card title="Most imported files">
            {hubFiles.length === 0 ? (
              <p className="text-sm text-muted">No internal imports between indexed files.</p>
            ) : (
              <ol className="space-y-1.5">
                {hubFiles.map((h) => (
                  <li key={h.file} className="flex items-baseline justify-between gap-3 text-sm">
                    <a
                      href={blobUrl(repo.repo_id, repo.commit, h.file)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="truncate font-mono text-[13px] hover:text-brand hover:underline"
                      title={h.file}
                    >
                      {h.file}
                    </a>
                    <span className="shrink-0 tabular-nums text-muted">imported by {h.importedBy}</span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </section>

      {/* Graph */}
      <section aria-labelledby="graph-heading" className="mt-10">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="graph-heading" className="text-lg font-semibold">
            Dependency graph
          </h2>
          <p className="text-sm text-muted">
            {edges.length} imports between files, resolved from each file&apos;s own import statements
          </p>
        </div>
        <div className="mt-4">
          {edges.length === 0 ? (
            <p className="rounded-xl border border-border bg-surface p-6 text-sm text-muted">
              No imports between this repository&apos;s own files were found.
            </p>
          ) : (
            <DependencyGraph edges={edges} repoId={repo.repo_id} commit={repo.commit} />
          )}
        </div>

        {edges.length > 0 && (
          <details className="mt-3 rounded-xl border border-border bg-surface">
            <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium text-muted hover:text-foreground">
              All {edges.length} imports as a table
            </summary>
            <div className="max-h-96 overflow-auto border-t border-border">
              <table className="w-full text-left text-[13px]">
                <thead className="sticky top-0 bg-surface-2 text-xs text-muted">
                  <tr>
                    <th className="px-4 py-2 font-medium">File</th>
                    <th className="px-4 py-2 font-medium">Imports</th>
                    <th className="px-4 py-2 font-medium">As written</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {edges.map((e) => (
                    <tr key={`${e.src}->${e.dst}`} className="border-t border-border">
                      <td className="px-4 py-1.5">
                        <a href={blobUrl(repo.repo_id, repo.commit, e.src, e.line)} target="_blank" rel="noopener noreferrer" className="hover:text-brand hover:underline">
                          {e.src}:{e.line}
                        </a>
                      </td>
                      <td className="px-4 py-1.5">{e.dst}</td>
                      <td className="px-4 py-1.5 text-muted">{e.spec}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </section>

      <p className="mt-10 text-xs text-subtle">
        Embeddings: {repo.embed_model}. {Object.values(s.files_skipped).reduce((a, b) => a + b, 0)} files skipped
        (non-code, vendored, generated, or too large).
      </p>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</dd>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-5">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {children}
    </div>
  );
}
