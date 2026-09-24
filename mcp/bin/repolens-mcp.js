#!/usr/bin/env node
'use strict';

const { McpServer } = require('@modelcontextprotocol/sdk/server/mcp.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const { z } = require('zod');

const { askRepo, checkRepoIndexed, ingestRepo, ToolError, DEFAULT_WAIT_SECONDS, MAX_WAIT_SECONDS } = require('../lib/tools');
const pkg = require('../package.json');

const LIVE =
  'This queries the live RepoLens service, which indexes real GitHub repositories function by function; ' +
  "it is not a static or offline knowledge source, so results reflect the repo's actual code at the indexed commit.";

const OWNER_REPO = z
  .string()
  .describe('The GitHub repository as "owner/repo" (e.g. "pallets/click") or a full https://github.com/owner/repo URL.');

const server = new McpServer({ name: 'repolens-mcp', version: pkg.version });

function errorResult(err) {
  const message = err instanceof ToolError ? err.message : `Unexpected error: ${err.message}`;
  return { content: [{ type: 'text', text: message }], isError: true };
}

// ------------------------------------------------------ check_repo_indexed

server.registerTool(
  'check_repo_indexed',
  {
    description:
      'Check whether a GitHub repository is already indexed in RepoLens, so it can be asked about with ask_repo. ' +
      'Fast (one lookup). Call this first whenever the user asks how a GitHub repo works, where something is ' +
      'handled in its code, or what a function in it does: if it returns indexed=false, call ingest_repo before ' +
      'ask_repo. Also reports the indexed commit and size. ' +
      LIVE,
    inputSchema: { owner_repo: OWNER_REPO },
    outputSchema: {
      indexed: z.boolean(),
      repo_id: z.string(),
      next_step: z.enum(['ask_repo', 'ingest_repo']),
      commit: z.string().optional(),
      files_indexed: z.number().optional(),
      chunks: z.number().optional(),
      languages: z.array(z.string()).optional(),
      internal_imports: z.number().optional(),
      indexed_at: z.string().optional(),
    },
  },
  async (args) => {
    try {
      const { text, structured } = await checkRepoIndexed(args);
      return { content: [{ type: 'text', text }], structuredContent: structured };
    } catch (err) {
      return errorResult(err);
    }
  }
);

// ---------------------------------------------------------------- ask_repo

server.registerTool(
  'ask_repo',
  {
    description:
      "Answer a question about a GitHub repository's code, e.g. \"how does the retry logic work in this repo\", " +
      '"where is authentication handled", "what does function X do", "what\'s the main entry point". Answers are ' +
      'generated from the repo\'s actual indexed source and cite exact file:line locations (with GitHub links at ' +
      'the indexed commit). Each answer has an answer_status: "answered", "partial" (only part of the question is ' +
      'covered by the code), or "not_found" (the code doesn\'t contain it; the service says so rather than ' +
      'guessing). Treat not_found as a real answer, not a failure. Takes about 15-90 seconds. The repo must be ' +
      'indexed first: if it isn\'t, this returns an error telling you to call ingest_repo (use check_repo_indexed ' +
      'to find out up front). ' +
      LIVE,
    inputSchema: {
      owner_repo: OWNER_REPO,
      question: z.string().min(3).max(2000).describe("A natural-language question about the repository's code."),
    },
    outputSchema: {
      repo_id: z.string(),
      commit: z.string(),
      question: z.string(),
      answer_status: z.enum(['answered', 'partial', 'not_found', 'refused']),
      answer: z.string(),
      citations: z.array(
        z.object({
          file: z.string(),
          start: z.number(),
          end: z.number(),
          verified: z.boolean(),
          basis: z.string().nullable(),
          url: z.string(),
        })
      ),
      files_consulted: z.array(z.string()),
    },
  },
  async (args) => {
    try {
      const out = await askRepo(args);
      if (out.notIndexed) return { content: [{ type: 'text', text: out.text }], isError: true };
      return { content: [{ type: 'text', text: out.text }], structuredContent: out.structured };
    } catch (err) {
      return errorResult(err);
    }
  }
);

// ------------------------------------------------------------- ingest_repo

const STAGE_ORDER = { queued: 0, fetching: 1, parsing: 2, embedding: 3, storing: 4, complete: 5 };

server.registerTool(
  'ingest_repo',
  {
    description:
      'Index a GitHub repository in RepoLens so questions can be asked about it with ask_repo. Only needed when ' +
      'check_repo_indexed says indexed=false (an already-indexed repo is not re-indexed unless reindex=true). ' +
      'INDEXING IS SLOW: about 1-3 minutes for a small repo, up to ~15 minutes for a large one (mostly embedding ' +
      `the code). This tool waits up to ${DEFAULT_WAIT_SECONDS} seconds by default (wait_seconds, max ` +
      `${MAX_WAIT_SECONDS}; 0 = return immediately). If indexing finishes in that time it returns status ` +
      '"complete". Otherwise it returns status "in_progress" with the job_id and current stage, and the job keeps ' +
      'running on the server. To keep waiting, call ingest_repo again with the same repo_url: it resumes the same ' +
      'job instead of starting a new one. Tell the user it may take a few minutes. Supports Python and ' +
      'JavaScript/TypeScript code. ' +
      LIVE,
    inputSchema: {
      repo_url: z.string().describe('The repository: a https://github.com/owner/repo URL or "owner/repo".'),
      wait_seconds: z
        .number()
        .int()
        .min(0)
        .max(MAX_WAIT_SECONDS)
        .optional()
        .describe(`How long to wait for indexing to finish before returning (default ${DEFAULT_WAIT_SECONDS}).`),
      reindex: z
        .boolean()
        .optional()
        .describe('Re-index a repo that is already indexed, to pick up newer commits. Default false.'),
    },
    outputSchema: {
      status: z.enum(['complete', 'in_progress', 'already_indexed']),
      job_id: z.string().nullable(),
      repo_id: z.string(),
      commit: z.string().optional(),
      files_indexed: z.number().optional(),
      chunks: z.number().optional(),
      languages: z.array(z.string()).optional(),
      internal_imports: z.number().optional(),
      indexed_at: z.string().optional(),
      stage: z.string().optional(),
      chunks_embedded: z.number().nullable().optional(),
      chunks_total: z.number().nullable().optional(),
      typical_total_minutes: z.array(z.number()).nullable().optional(),
      resumed_existing_job: z.boolean().optional(),
    },
  },
  async (args, extra) => {
    // If the client asked for progress (a progressToken), report each poll as
    // a standard MCP progress notification: stage plus embedding counts.
    const token = extra && extra._meta && extra._meta.progressToken;
    let lastProgress = -1;
    const onProgress = (job) => {
      if (token === undefined) return;
      const p = job.progress || {};
      const embedding = job.status === 'embedding' && p.chunks_total;
      const progress = embedding ? 3 + (p.chunks_embedded || 0) : STAGE_ORDER[job.status] ?? 0;
      if (progress <= lastProgress) return; // progress must only increase
      lastProgress = progress;
      extra
        .sendNotification({
          method: 'notifications/progress',
          params: {
            progressToken: token,
            progress,
            ...(p.chunks_total ? { total: p.chunks_total + 4 } : {}),
            message: embedding ? `embedding ${p.chunks_embedded || 0}/${p.chunks_total} code chunks` : job.status,
          },
        })
        .catch(() => {});
    };
    try {
      const { text, structured } = await ingestRepo(args, onProgress);
      return { content: [{ type: 'text', text }], structuredContent: structured };
    } catch (err) {
      return errorResult(err);
    }
  }
);

async function main() {
  await server.connect(new StdioServerTransport());
  // stdout carries MCP protocol messages; anything human-readable goes to stderr.
  console.error(`repolens-mcp ${pkg.version} running on stdio`);
}

main().catch((err) => {
  console.error('repolens-mcp fatal error:', err);
  process.exit(1);
});
