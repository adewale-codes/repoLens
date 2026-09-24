"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { isValidRepoPath } from "@/lib/github";

// A repo path that was never indexed. Offer to index it rather than just a dead end.
export default function RepoNotFound() {
  const [owner, repo] = (usePathname() ?? "").split("/").filter(Boolean);
  const repoId = owner && repo && isValidRepoPath(owner, repo) ? `${owner}/${repo}` : null;

  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col items-center justify-center px-4 py-24 text-center">
      <p className="font-mono text-sm text-subtle">404</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">
        {repoId ? (
          <>
            <span className="font-mono">{repoId}</span> hasn&apos;t been indexed
          </>
        ) : (
          "Repository not found"
        )}
      </h1>
      <p className="mt-3 text-muted">
        RepoLens only has pages for repositories that have been indexed. Indexing reads the code and maps its imports,
        which takes a few minutes.
      </p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {repoId && (
          <Link
            href={`/?repo=${encodeURIComponent(repoId)}`}
            className="rounded-lg bg-brand px-5 py-2.5 text-sm font-medium text-white hover:bg-brand-hover"
          >
            Index {repoId}
          </Link>
        )}
        <Link href="/" className="rounded-lg border border-border-strong bg-surface px-5 py-2.5 text-sm font-medium hover:border-brand hover:text-brand">
          Browse indexed examples
        </Link>
      </div>
    </main>
  );
}
