"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { IngestJob, JobStatus } from "@/lib/types";

const POLL_MS = 2000;
// Whole-job seconds per chunk seen on real repos (click 0.74, express 0.78, ky 0.96,
// itsdangerous 1.5). Used for a typical range, not a countdown.
const SECONDS_PER_CHUNK: [number, number] = [0.7, 1.6];

type Phase =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "tracking"; jobId: string }
  | { kind: "opening"; repoId: string; fresh: boolean };

export default function RepoSubmit() {
  const router = useRouter();
  const params = useSearchParams();
  const resumeJob = params.get("job");

  // Prefilled (not auto-submitted) when arriving from a not-indexed repo page.
  const [input, setInput] = useState(() => params.get("repo") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>(() =>
    resumeJob && /^[0-9a-f]{32}$/.test(resumeJob) ? { kind: "tracking", jobId: resumeJob } : { kind: "idle" }
  );

  async function submit(repo: string) {
    setError(null);
    setPhase({ kind: "checking" });
    try {
      const res = await fetch("/api/ingest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repo }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Something went wrong.");
      if (data.state === "indexed") {
        setPhase({ kind: "opening", repoId: data.repo_id, fresh: false });
        router.push(`/${data.repo_id}`);
        return;
      }
      // Put the job in the URL so a refresh (or a second tab) keeps following it.
      router.replace(`/?job=${data.job_id}`, { scroll: false });
      setPhase({ kind: "tracking", jobId: data.job_id });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setPhase({ kind: "idle" });
    }
  }

  const busy = phase.kind !== "idle";

  return (
    <div className="w-full">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (input.trim()) submit(input);
        }}
        className="flex flex-col gap-2 sm:flex-row"
      >
        <label htmlFor="repo-url" className="sr-only">
          GitHub repository URL
        </label>
        <input
          id="repo-url"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="https://github.com/owner/repo"
          autoComplete="off"
          spellCheck={false}
          disabled={busy}
          className="min-h-12 flex-1 rounded-lg border border-border-strong bg-surface px-4 font-mono text-[15px] shadow-sm outline-none transition focus:border-brand focus:ring-4 focus:ring-brand/15 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="min-h-12 rounded-lg bg-brand px-6 text-[15px] font-medium text-white shadow-sm transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {phase.kind === "checking" ? "Checking…" : "Explore repo"}
        </button>
      </form>

      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-danger-border bg-danger-bg px-4 py-2.5 text-sm text-danger-text">
          {error}
        </p>
      )}

      {phase.kind === "opening" && (
        <p className="mt-3 text-sm text-muted">
          {phase.fresh ? "Indexed." : "Already indexed."} Opening {phase.repoId}…
        </p>
      )}

      {phase.kind === "tracking" && (
        <IngestProgress
          jobId={phase.jobId}
          onComplete={(repoId) => {
            setPhase({ kind: "opening", repoId, fresh: true });
            router.push(`/${repoId}`);
          }}
          onReset={() => {
            router.replace("/", { scroll: false });
            setPhase({ kind: "idle" });
          }}
        />
      )}
    </div>
  );
}

const STEPS: { status: JobStatus; label: string }[] = [
  { status: "fetching", label: "Fetching" },
  { status: "parsing", label: "Parsing" },
  { status: "embedding", label: "Embedding" },
  { status: "storing", label: "Storing" },
];

function IngestProgress({
  jobId,
  onComplete,
  onReset,
}: {
  jobId: string;
  onComplete: (repoId: string) => void;
  onReset: () => void;
}) {
  const [job, setJob] = useState<IngestJob | null>(null);
  const [lost, setLost] = useState<string | null>(null);
  const [eta, setEta] = useState("");
  // When this page started following the job, for the elapsed-time display.
  const followedSince = useRef<number | null>(null);
  const completed = useRef(false);
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const res = await fetch(`/api/ingest/${jobId}`, { cache: "no-store" });
        if (res.status === 404) {
          setLost("This indexing job doesn't exist (the link may be from an old session).");
          return;
        }
        if (res.ok) {
          const next = (await res.json()) as IngestJob;
          if (cancelled) return;
          setJob(next);
          const now = Date.now();
          // Prefer the server start time; fall back to when this page began polling.
          followedSince.current ??= next.started_at ? Math.min(Date.parse(next.started_at), now) : now;
          setEta(estimate(next, followedSince.current, now));
          if (next.status === "complete") {
            if (!completed.current) {
              completed.current = true;
              onCompleteRef.current(next.result?.repo_id ?? next.repo_id);
            }
            return;
          }
          if (next.status === "failed") return;
        }
      } catch {
        // transient network error: keep polling
      }
      if (!cancelled) timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId]);

  if (lost) {
    return (
      <Panel>
        <p className="text-sm text-muted">{lost}</p>
        <ResetButton onReset={onReset} label="Start over" />
      </Panel>
    );
  }
  if (!job) {
    return (
      <Panel>
        <p className="text-sm text-muted">Connecting to the indexing job…</p>
      </Panel>
    );
  }

  if (job.status === "failed") {
    return (
      <Panel tone="danger">
        <p className="text-sm font-medium text-danger-text">Indexing {job.repo_id} failed.</p>
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-danger-text/90">{job.error}</pre>
        <ResetButton onReset={onReset} label="Try another repository" />
      </Panel>
    );
  }

  const stepIndex = STEPS.findIndex((s) => s.status === job.status);
  const total = job.progress?.chunks_total;
  const done = job.progress?.chunks_embedded ?? 0;
  const pct = total ? Math.round((done / total) * 100) : 0;

  return (
    <Panel>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium">
          Indexing <span className="font-mono">{job.repo_id}</span>
        </p>
        <p className="text-xs text-muted">{eta}</p>
      </div>

      <ol className="mt-4 grid grid-cols-4 gap-2" aria-label="Indexing stages">
        {STEPS.map((s, i) => {
          const state =
            job.status === "complete" || i < stepIndex ? "done" : i === stepIndex ? "active" : "pending";
          return (
            <li key={s.status} className="flex flex-col gap-1.5">
              <span
                className={`h-1.5 rounded-full ${
                  state === "done" ? "bg-brand" : state === "active" ? "animate-pulse bg-brand/60" : "bg-border"
                }`}
              />
              <span
                className={`text-xs ${state === "pending" ? "text-subtle" : "font-medium text-foreground"}`}
                aria-current={state === "active" ? "step" : undefined}
              >
                {s.label}
              </span>
            </li>
          );
        })}
      </ol>

      <p className="mt-4 text-sm text-muted" aria-live="polite">
        {job.status === "queued" && "Waiting in line. Another repository is being indexed, and jobs run one at a time."}
        {job.status === "fetching" && "Cloning the repository from GitHub…"}
        {job.status === "parsing" && "Splitting the code into functions and classes, and mapping imports…"}
        {job.status === "embedding" && total !== undefined && (
          <>
            Embedding code chunks: <span className="font-medium text-foreground tabular-nums">{done.toLocaleString()}</span> of{" "}
            <span className="tabular-nums">{total.toLocaleString()}</span> ({pct}%)
          </>
        )}
        {job.status === "storing" && "Saving the index…"}
        {job.status === "complete" && "Done. Opening the repository page…"}
      </p>

      {job.status === "embedding" && total !== undefined && (
        <div
          className="mt-2 h-2 overflow-hidden rounded-full bg-surface-2"
          role="progressbar"
          aria-valuenow={done}
          aria-valuemin={0}
          aria-valuemax={total}
        >
          <div className="h-full rounded-full bg-brand transition-[width] duration-700" style={{ width: `${pct}%` }} />
        </div>
      )}

      <p className="mt-4 text-xs text-subtle">
        Large repositories can take 10–15 minutes, mostly spent embedding. You can leave this page open, or bookmark
        it and come back. The link keeps following the job.
      </p>
    </Panel>
  );
}

/**
 * Elapsed time plus a typical total, not a countdown. Chunks are embedded
 * shortest-first, so the last ones are the slowest and a rate measured early
 * always underestimates. The range comes from the total chunk count and the
 * per-chunk speeds seen on real repos.
 */
function estimate(job: IngestJob, startedAt: number, now: number): string {
  const elapsed = formatDuration((now - startedAt) / 1000);
  const total = job.progress?.chunks_total;
  if (job.status === "queued") return `Queued · ${elapsed}`;
  if (job.status === "storing") return `Almost done · ${elapsed}`;
  if (!total) return `${elapsed} elapsed`;
  const lo = Math.max(1, Math.round((total * SECONDS_PER_CHUNK[0]) / 60));
  const hi = Math.max(lo + 1, Math.ceil((total * SECONDS_PER_CHUNK[1]) / 60));
  return `${elapsed} elapsed · usually ${lo}–${hi} min for ${total.toLocaleString()} chunks`;
}

function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function Panel({ children, tone }: { children: React.ReactNode; tone?: "danger" }) {
  return (
    <div
      className={`mt-4 rounded-xl border p-5 text-left ${
        tone === "danger" ? "border-danger-border bg-danger-bg" : "border-border bg-surface shadow-sm"
      }`}
    >
      {children}
    </div>
  );
}

function ResetButton({ onReset, label }: { onReset: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onReset}
      className="mt-3 rounded-md border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium hover:border-brand hover:text-brand"
    >
      {label}
    </button>
  );
}
