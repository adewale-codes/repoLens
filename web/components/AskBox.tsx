"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CITE_TITLE, linkifyCitations } from "@/lib/citations";
import { blobUrl } from "@/lib/github";
import type { AskResponse } from "@/lib/types";

export default function AskBox({
  repoId,
  commit,
  chunkCount,
  suggestions,
}: {
  repoId: string;
  commit: string;
  chunkCount: number;
  suggestions: string[];
}) {
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [result, setResult] = useState<AskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function ask(q: string) {
    const text = q.trim();
    if (text.length < 3 || pending) return;
    setQuestion(text);
    setPending(text);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repo_id: repoId, question: text }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Couldn't get an answer.");
      setResult(data as AskResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't get an answer.");
    } finally {
      setPending(null);
    }
  }

  return (
    <div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
        className="flex flex-col gap-2 sm:flex-row"
      >
        <label htmlFor="question" className="sr-only">
          Ask a question about {repoId}
        </label>
        <input
          id="question"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={`Ask anything about ${repoId}…`}
          maxLength={2000}
          disabled={pending !== null}
          className="min-h-12 flex-1 rounded-lg border border-border-strong bg-surface px-4 text-[15px] shadow-sm outline-none transition focus:border-brand focus:ring-4 focus:ring-brand/15 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={pending !== null || question.trim().length < 3}
          className="min-h-12 rounded-lg bg-brand px-6 text-[15px] font-medium text-white shadow-sm transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Reading the code…" : "Ask"}
        </button>
      </form>

      <div className="mt-3 flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => ask(s)}
            disabled={pending !== null}
            className="rounded-full border border-border bg-surface px-3 py-1 text-left text-xs text-muted transition-colors hover:border-brand-border hover:text-brand disabled:opacity-50"
          >
            {s}
          </button>
        ))}
      </div>

      {pending && <Pending question={pending} chunkCount={chunkCount} />}
      {error && (
        <p role="alert" className="mt-5 rounded-lg border border-danger-border bg-danger-bg px-4 py-3 text-sm text-danger-text">
          {error}
        </p>
      )}
      {result && <AnswerView result={result} commit={commit} />}
    </div>
  );
}

function Pending({ question, chunkCount }: { question: string; chunkCount: number }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="mt-5 rounded-xl border border-border bg-surface p-5 shadow-sm" aria-live="polite">
      <p className="text-sm font-medium">&ldquo;{question}&rdquo;</p>
      <p className="mt-2 flex items-center gap-2 text-sm text-muted">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-brand" />
        {seconds < 3
          ? `Searching ${chunkCount.toLocaleString()} code chunks and following imports…`
          : `Reading the most relevant code and writing a cited answer… ${seconds}s`}
      </p>
      <p className="mt-1 text-xs text-subtle">Answers usually take 20–60 seconds.</p>
    </div>
  );
}

const STATUS_UI = {
  not_found: {
    box: "border-notfound-border bg-notfound-bg",
    title: "Not found in this repository",
    text: "text-notfound-text",
  },
  partial: {
    box: "border-partial-border bg-partial-bg",
    title: "Partial answer: the code shown covers only part of this",
    text: "text-partial-text",
  },
  refused: { box: "border-danger-border bg-danger-bg", title: "No answer", text: "text-danger-text" },
} as const;

// The API appends a plain-text caveat when citations are unverified; the UI
// shows its own styled one instead.
const API_UNVERIFIED_NOTE = /\n+Note: \d+ citations? marked \[unverified\][^\n]*$/;

// The prompt asks the model to end with "Files consulted: a.py, b.py". Rendered
// as markdown, names like __init__.py turn into bold, so the line is pulled out
// and shown as file chips instead.
const FILES_CONSULTED = /\n*[*_]*Files consulted:?[*_]*:?\s*(.+?)\s*$/i;

function splitFilesConsulted(answer: string): { body: string; files: string[] } {
  const m = answer.match(FILES_CONSULTED);
  if (!m || m.index === undefined) return { body: answer, files: [] };
  const files = m[1]
    .split(/,\s*/)
    .map((f) => f.replace(/[`*]/g, "").replace(/\.$/, "").trim())
    .filter(Boolean);
  return { body: answer.slice(0, m.index).trimEnd(), files };
}

function AnswerView({ result, commit }: { result: AskResponse; commit: string }) {
  const { body: answer, files: consulted } = splitFilesConsulted(result.answer.replace(API_UNVERIFIED_NOTE, ""));
  const markdown = linkifyCitations(answer, result.citations, result.repo_id, commit);
  const unverified = result.citations.filter((c) => !c.valid).length;
  const status = result.answer_status;
  const banner = status && status !== "answered" ? STATUS_UI[status] : null;
  const files = new Set(result.chunks_used.map((c) => c.file));

  return (
    <article className="mt-5 overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
      <header className="border-b border-border px-5 py-3">
        <p className="text-sm font-medium">&ldquo;{result.question}&rdquo;</p>
      </header>

      {banner && (
        <div className={`flex gap-3 border-b px-5 py-3 ${banner.box}`} role="status">
          <StatusIcon status={status!} />
          <div className={`text-sm ${banner.text}`}>
            <p className="font-semibold">{banner.title}</p>
            {status === "not_found" && (
              <p className="mt-0.5">
                RepoLens searched {result.chunks_used.length} excerpts from {files.size} files and found no code for
                this. It doesn&apos;t guess. Here&apos;s what it looked at instead.
              </p>
            )}
          </div>
        </div>
      )}
      {status === "answered" && (
        <div className="flex items-center gap-2 border-b border-border bg-brand-soft/60 px-5 py-2 text-xs font-medium text-brand">
          <StatusIcon status="answered" />
          Answered from the code, with {result.citations.length} citation{result.citations.length === 1 ? "" : "s"}
          {unverified === 0 && result.citations.length > 0 ? ", all checked against the lines shown" : ""}
        </div>
      )}

      <div className="prose-answer px-5 py-4">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: MarkdownLink }}>
          {markdown}
        </ReactMarkdown>
        {consulted.length > 0 && (
          <div className="!mt-5 flex flex-wrap items-center gap-1.5 border-t border-border pt-3">
            <span className="mr-1 text-xs font-medium text-muted">Files consulted</span>
            {consulted.map((f) => (
              <a
                key={f}
                href={blobUrl(result.repo_id, commit, f)}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded border border-border bg-code-bg px-1.5 py-0.5 font-mono text-xs !text-foreground !no-underline hover:!border-brand-border hover:!text-brand"
              >
                {f}
              </a>
            ))}
          </div>
        )}
      </div>

      {unverified > 0 && (
        <p className="border-t border-danger-border bg-danger-bg px-5 py-2 text-xs text-danger-text">
          {unverified} citation{unverified === 1 ? "" : "s"} marked <strong>unverified</strong> point to lines that
          weren&apos;t in the code the model was shown. Treat {unverified === 1 ? "it" : "them"} as approximate.
        </p>
      )}

      <details className="group border-t border-border">
        <summary className="cursor-pointer select-none px-5 py-3 text-xs font-medium text-muted hover:text-foreground">
          Code read for this answer: {result.chunks_used.length} excerpts from {files.size} files
        </summary>
        <ul className="space-y-1 px-5 pb-4">
          {result.chunks_used.map((c) => (
            <li key={c.ref} className="flex flex-wrap items-baseline gap-x-2 text-xs">
              <a
                href={blobUrl(result.repo_id, commit, c.file, c.start_line, c.shown_through_line)}
                target="_blank"
                rel="noopener noreferrer"
                className="font-mono text-brand hover:underline"
              >
                {c.file}:{c.start_line}-{c.shown_through_line}
              </a>
              <span className="font-mono text-foreground/80">{c.symbol}</span>
              <span className="text-subtle">
                {c.retrieved_by === "graph" ? `via import graph: ${c.reason}` : "matched the question"}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}

function MarkdownLink({ href, title, children }: React.ComponentProps<"a">) {
  const isCite = title?.startsWith("cite:");
  if (!isCite) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    );
  }
  const state = title === CITE_TITLE.unverified ? "unverified" : title === CITE_TITLE.classIndex ? "index" : "ok";
  const hint =
    state === "unverified"
      ? "Unverified: these lines weren't in the code the model was shown"
      : state === "index"
        ? "Location from the class's member index (the code itself wasn't read)"
        : "Open these exact lines on GitHub";
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={hint}
      className={
        state === "unverified"
          ? "!text-danger-text !decoration-dashed"
          : state === "index"
            ? "!decoration-dotted"
            : "!no-underline hover:!underline"
      }
    >
      {children}
      {state === "unverified" && <sup className="ml-0.5 font-sans text-[10px] font-semibold">unverified</sup>}
    </a>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "answered") {
    return (
      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5 shrink-0" aria-hidden="true">
        <path d="M3 8.5l3 3 7-7" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (status === "not_found") {
    return (
      <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 text-notfound-text" aria-hidden="true" fill="none">
        <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.8" />
        <path d="M13 13l4 4M6.5 8.5h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" fill="none">
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.8" />
      <path d="M10 6v5M10 13.5v.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
