'use strict';

// Tool handlers for repolens-mcp. The API calls come from the published
// `repolens-cli` package rather than being re-implemented: its lib/ has zero
// dependencies of its own, already defaults to the production API, and
// follows REPOLENS_API_URL. That's the same reuse whyfail-mcp does with
// `whyfail`.
//
// Reused as is: api.js (URL resolution, repo parsing, GET /repos),
// ingest.js (submit, attach on 409, bounded polling via maxWaitMs), and
// format.js's githubBlob. Not reused: the CLI's terminal formatting, and
// ask.js's not-indexed error text, which tells a human to run a shell
// command. An agent should be told to call ingest_repo instead, so
// ask_repo does its own indexed check first.
const { ToolError, getRepo, parseRepo, resolveApiUrl } = require('repolens-cli/lib/api.js');
const { ask } = require('repolens-cli/lib/ask.js');
const { ingest } = require('repolens-cli/lib/ingest.js');
const { githubBlob } = require('repolens-cli/lib/format.js');

// Bounded wait for ingest_repo (see README "Why ingest_repo waits up to 45 seconds").
const DEFAULT_WAIT_SECONDS = 45;
const MAX_WAIT_SECONDS = 55;
// Whole-job seconds per chunk seen on real repos: gives a typical range, not a countdown.
const SECONDS_PER_CHUNK = [0.7, 1.6];

function apiUrl() {
  return resolveApiUrl(null);
}

const LANGUAGE_NAMES = { python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript' };

function languageText(languages) {
  return languages.map((l) => LANGUAGE_NAMES[l] || l).join(' & ');
}

function languagesOf(stats) {
  return Object.keys(stats.chunks_by_language || {});
}

function repoSummary(repo) {
  const s = repo.stats;
  return {
    repo_id: repo.repo_id,
    commit: repo.commit,
    files_indexed: s.files_indexed,
    chunks: s.chunks,
    languages: languagesOf(s),
    internal_imports: (s.graph && s.graph.edges) || 0,
    indexed_at: new Date(repo.ingested_at * 1000).toISOString(),
  };
}

function notIndexedText(repoId) {
  return (
    `${repoId} is NOT indexed in RepoLens yet, so it can't be asked about. ` +
    `To fix: call ingest_repo with repo_url "https://github.com/${repoId}" ` +
    `(indexing takes about 1-3 minutes for a small repo, up to ~15 for a large one), ` +
    `then call ask_repo again.`
  );
}

// ------------------------------------------------------ check_repo_indexed

async function checkRepoIndexed({ owner_repo }) {
  const { repoId } = parseRepo(owner_repo);
  const repo = await getRepo(apiUrl(), repoId);
  if (!repo) {
    return {
      text: `${repoId} is not indexed yet. Next step: call ingest_repo with repo_url "https://github.com/${repoId}", then ask_repo.`,
      structured: { indexed: false, repo_id: repoId, next_step: 'ingest_repo' },
    };
  }
  const summary = repoSummary(repo);
  return {
    text:
      `${repo.repo_id} is indexed (commit ${repo.commit.slice(0, 7)}, ${summary.files_indexed} files, ` +
      `${summary.chunks} code chunks, ${languageText(summary.languages)}, indexed ${summary.indexed_at.slice(0, 10)}). ` +
      `Next step: call ask_repo with a question.`,
    structured: { indexed: true, ...summary, next_step: 'ask_repo' },
  };
}

// ---------------------------------------------------------------- ask_repo

const STATUS_TEXT = {
  answered: 'ANSWERED: the indexed code answers this question.',
  partial: 'PARTIAL ANSWER: the code found answers only part of this question; the answer says what is missing.',
  not_found: "NOT FOUND: the repo's indexed code doesn't contain an answer. The service reports this rather than guessing.",
  refused: 'NO ANSWER: the model declined this question.',
};

async function askRepo({ owner_repo, question }) {
  const { repoId } = parseRepo(owner_repo);
  const base = apiUrl();
  const repo = await getRepo(base, repoId);
  if (!repo) {
    // Not an exception: the agent's next step is clear, and it's spelled out.
    return { notIndexed: true, text: notIndexedText(repoId) };
  }
  const { result } = await ask(repo.repo_id, question, { apiUrl: base });
  const citations = result.citations.map((c) => ({
    file: c.file,
    start: c.start,
    end: c.end,
    verified: c.valid,
    basis: c.basis,
    url: githubBlob(result.repo_id, result.commit, c.file, c.start, c.end),
  }));
  const lines = [
    `${result.repo_id} @ ${result.commit.slice(0, 7)}: ${STATUS_TEXT[result.answer_status] || STATUS_TEXT.answered}`,
    '',
    result.answer,
  ];
  if (citations.length) {
    lines.push('', 'Citations (file:lines, each linked to GitHub at the indexed commit):');
    for (const c of citations) {
      const range = c.end !== c.start ? `${c.start}-${c.end}` : `${c.start}`;
      lines.push(`- ${c.file}:${range}${c.verified ? '' : ' (UNVERIFIED: outside the code the model was shown)'} ${c.url}`);
    }
  }
  return {
    text: lines.join('\n'),
    structured: {
      repo_id: result.repo_id,
      commit: result.commit,
      question: result.question,
      answer_status: result.answer_status || 'answered',
      answer: result.answer,
      citations,
      files_consulted: result.files_consulted,
    },
  };
}

// ------------------------------------------------------------- ingest_repo

function typicalMinutes(total) {
  if (!total) return null;
  const lo = Math.max(1, Math.round((total * SECONDS_PER_CHUNK[0]) / 60));
  return [lo, Math.max(lo + 1, Math.ceil((total * SECONDS_PER_CHUNK[1]) / 60))];
}

/**
 * @param {{ repo_url: string, wait_seconds?: number, reindex?: boolean }} args
 * @param {(job: object) => void} [onProgress] called after every poll
 */
async function ingestRepo({ repo_url, wait_seconds = DEFAULT_WAIT_SECONDS, reindex = false }, onProgress) {
  const { repoId } = parseRepo(repo_url);
  const waitSeconds = Math.max(0, Math.min(MAX_WAIT_SECONDS, wait_seconds));
  const out = await ingest(repoId, {
    apiUrl: apiUrl(),
    reindex,
    maxWaitMs: waitSeconds * 1000,
    onProgress,
  });

  if (out.alreadyIndexed) {
    const summary = repoSummary(out.alreadyIndexed);
    return {
      text:
        `${summary.repo_id} is already indexed (commit ${summary.commit.slice(0, 7)}, ${summary.chunks} code chunks); ` +
        `nothing was re-indexed. Call ask_repo now. (Pass reindex: true only to pick up newer commits.)`,
      structured: { status: 'already_indexed', job_id: null, ...summary },
    };
  }

  const { job, attached } = out;
  if (job.status === 'failed') {
    throw new ToolError(`Indexing ${job.repo_id} failed: ${job.error}`);
  }
  if (job.status === 'complete') {
    const r = job.result;
    return {
      text:
        `Indexed ${r.repo_id} at commit ${r.commit.slice(0, 7)}: ${r.files_indexed} files, ${r.chunks} code chunks, ` +
        `${(r.graph && r.graph.edges) || 0} internal imports. It can be asked about now: call ask_repo.`,
      structured: {
        status: 'complete',
        job_id: job.job_id,
        repo_id: r.repo_id,
        commit: r.commit,
        files_indexed: r.files_indexed,
        chunks: r.chunks,
        languages: languagesOf(r),
        internal_imports: (r.graph && r.graph.edges) || 0,
        resumed_existing_job: attached,
      },
    };
  }

  // Still running after the bounded wait. The job continues on the server.
  const p = job.progress || {};
  const typical = typicalMinutes(p.chunks_total);
  const stageText =
    job.status === 'embedding' && p.chunks_total
      ? `embedding ${p.chunks_embedded || 0} of ${p.chunks_total} code chunks`
      : job.status === 'queued'
        ? 'queued behind another repo (jobs run one at a time)'
        : job.status;
  return {
    text:
      `Still indexing ${job.repo_id}: ${stageText}. It keeps running on the server` +
      `${typical ? `; a repo this size usually takes ${typical[0]}-${typical[1]} minutes in total` : ''}. ` +
      `To keep waiting, call ingest_repo again with the same repo_url: it resumes this same job ` +
      `(job ${job.job_id}) rather than starting over. Or call check_repo_indexed later; it reports ` +
      `indexed once the job is done. ask_repo won't work until then.`,
    structured: {
      status: 'in_progress',
      job_id: job.job_id,
      repo_id: job.repo_id,
      stage: job.status,
      chunks_embedded: p.chunks_embedded ?? null,
      chunks_total: p.chunks_total ?? null,
      typical_total_minutes: typical,
      resumed_existing_job: attached,
    },
  };
}

module.exports = {
  checkRepoIndexed,
  askRepo,
  ingestRepo,
  ToolError,
  DEFAULT_WAIT_SECONDS,
  MAX_WAIT_SECONDS,
};
