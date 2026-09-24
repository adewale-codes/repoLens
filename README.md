# RepoLens

Ask questions about a GitHub repository and get answers grounded in its actual
code, with `file:line` citations.

Phase 1 covers **Python** and **JavaScript/TypeScript**. Both are chunked along
real syntax boundaries (functions, classes, methods), not fixed line windows.

```
POST /ingest {"repo_url": "https://github.com/pallets/click"}   -> 202 {"job_id": ...}
  background job: fetch (shallow clone) -> filter -> parse/chunk -> import graph -> embed -> store
GET /ingest/{job_id}   -> queued | fetching | parsing | embedding (n/total chunks) | storing | complete | failed

POST /ask {"repo_id": "pallets/click", "question": "..."}
  embed question -> vector search + identifier boost -> expand via import graph
  -> bounded, line-numbered context -> Claude -> answer + citation check
```

## Running

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # .venv/bin/pip on macOS/Linux
cp .env.example .env                            # set ANTHROPIC_API_KEY (only /ask needs it)
.venv/Scripts/uvicorn main:app --reload
.venv/Scripts/pytest
```

Requires Python 3.11+ and `git` on PATH. The first ingest downloads the
embedding model (~640 MB) into `data/models`.

On Windows, don't build the venv from an Anaconda interpreter. Anaconda's
`python.exe` loads its own older MSVC runtime (14.29), and current onnxruntime
fails to import against it ("DLL initialization routine failed"). A python.org
interpreter uses the system runtime and works.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/ingest` | Queue an ingest (or a re-ingest, to pick up new commits). Returns `202` with a `job_id` right away. Returns `409` with the existing `job_id` if that repo already has a job in flight, and `422` for a URL that isn't a GitHub repo. |
| GET | `/ingest/{job_id}` | Job status: current stage, embedding progress (`chunks_embedded`/`chunks_total`), timestamps, `error` if it failed, and once complete, the ingest report (chunk counts by kind and language, skipped-file reasons, parse errors, graph stats, timings). |
| POST | `/ask` | Answer a question. Returns the answer, an `answer_status` (`answered` / `partial` / `not_found` / `refused`), each citation with a `valid` flag, the chunks used (with why each was retrieved), and `files_consulted`. |
| GET | `/repos` | List indexed repos. |
| GET | `/repos/{owner}/{name}` | One indexed repo: commit, embedding model, ingest stats. Case-insensitive; returns the canonical `repo_id`. |
| GET | `/repos/{owner}/{name}/graph?file=...` | Import edges, optionally for one file. |
| GET | `/repos/{owner}/{name}/chunks?file=...&include_text=true` | Chunk boundaries, for spot checks. |

## Website (`web/`, Phase 3)

A Next.js 16 (App Router) site on top of the API:

- **`/`** — paste a GitHub URL. If the repo is already indexed it opens straight away. Otherwise an ingest job starts, and the page shows its real stage (fetching, parsing, embedding with a chunk count, storing), the elapsed time, and a typical range. The job id goes in the URL (`/?job=…`), so a refresh keeps following it. The examples (click, express, ky) open instantly.
- **`/{owner}/{repo}`** — server-rendered, one shareable page per indexed repo:
  - an ask box (answers render with citations linked to the exact lines on GitHub at the indexed commit, and "not found" and "partial" answers get distinct banners);
  - an architecture overview;
  - the import graph (d3-force, colored by directory, with tests and examples hidden by default) plus a table of every edge;
  - copy / X / LinkedIn share buttons, and a generated OG image with the repo's stats.

  A repo that was never indexed returns a real 404 with an "Index it" button; mixed-case paths 308-redirect to the canonical one.

```bash
cd web
npm install
cp .env.example .env.local   # REPOLENS_API_URL (server-only), SITE_URL for absolute share/OG links
npm run dev                  # or: npm run build && npm start
```

`REPOLENS_API_URL` is read only on the server, never exposed as `NEXT_PUBLIC_`, the same convention as `WHYFAIL_API_URL` and `PACKAGESAFE_API_URL`. The browser only talks to the site's own `/api/*` route handlers, which proxy to the API.

Those handlers also apply per-IP rate limits: 10 questions per 10 minutes and 5 new ingests per hour. `/ask` spends Claude credits and an ingest occupies the single worker for minutes, so a public page needs some protection. The limits are in memory, so they're per instance and reset on restart.

## Deploying the API to Railway

Files: `backend.Dockerfile`, `railway.json`, `.dockerignore`, and `.env.example`, which lists every variable.

**Why this deploy needs a Volume when the other two projects didn't.** Everything RepoLens knows lives in `REPOLENS_DATA_DIR`:

- the SQLite index (every repo's chunks, embeddings and import graph, plus the ingest job table);
- the repo clones;
- the ~640 MB embedding model.

That's the product's data, not a cache. Railway's container filesystem is wiped on every deploy. Without a Volume, each redeploy deletes every indexed repo, and each one costs up to ~15 minutes of embedding to rebuild.

**Volumes can't be declared in `railway.json`.** Railway's config-as-code has no volume fields: its documented `build`/`deploy` keys cover builder, start command, health check, restart policy and so on, but not storage. Attaching the Volume is a dashboard step, in the same category as attaching Postgres was for PackageSafe and WhyDidThisFail. The image sets `REPOLENS_DATA_DIR=/data`, so mount the Volume at `/data`.

**Safety net.** At startup the API checks whether it's on Railway (`RAILWAY_SERVICE_ID`) and whether a Volume is mounted (`RAILWAY_VOLUME_MOUNT_PATH`). If data isn't on the Volume, it logs a warning. `GET /health` returns `storage.persistent`: `true`, `false`, or `null` off Railway.

### Manual steps in the Railway dashboard, in order

1. **Create the service.** New Project → Deploy from GitHub repo → this repository. Leave *Root Directory* empty: the build context must be the repo root, and the Dockerfile's `COPY` paths assume it.
2. **Confirm the builder.** In the service's Settings → Build, *Builder* must be **Dockerfile** and *Dockerfile Path* must be `backend.Dockerfile`. `railway.json` sets both, but on WhyDidThisFail Railway ignored that and auto-detected a different builder until it was set by hand here. If the first build log mentions Railpack/Nixpacks instead of your Dockerfile, set these two fields manually and redeploy.
3. **Attach a Volume.** Press ⌘K / Ctrl+K and choose to create a Volume, or right-click the project canvas. Connect it to this service and set the **mount path to `/data`**. Use at least the Hobby plan: Trial/Free volumes are capped at 0.5 GB, less than the embedding model alone. Hobby allows 5 GB, which holds the model plus a good number of indexed repos.
4. **Set variables** (service → Variables):
   - `ANTHROPIC_API_KEY`: required for `/ask`.
   - `REPOLENS_DATA_DIR=/data`: the image already defaults to this; set it explicitly so the config is visible and matches the mount path.
   - Optional overrides are listed in `.env.example`. Don't set `PORT`; Railway provides it, and the start command expands it through `sh -c`.
5. **Generate a public domain.** Settings → Networking → Generate Domain.
6. **Verify.** Run `curl https://<your-domain>/health` and expect `"status": "ok"` and `"storage": {"persistent": true, "volume_mount": "/data", ...}`. If `persistent` is `false`, the Volume isn't mounted at `REPOLENS_DATA_DIR`; fix that before ingesting anything.
7. **First run.** The first ingest (or first `/ask`) downloads the embedding model into `/data/models`, which adds a few minutes once. After that it's on the Volume and survives redeploys.

### Testing the image locally

```bash
docker build -f backend.Dockerfile -t repolens-api .
docker run --rm -d --name repolens -p 8000:8000 -v repolens-data:/data \
  -e ANTHROPIC_API_KEY=... repolens-api
curl http://127.0.0.1:8000/health

# Proves git, tree-sitter, onnxruntime, and SQLite on a mounted volume work
# inside the container: ingest a tiny real repo (11 chunks) and poll it.
curl -X POST http://127.0.0.1:8000/ingest -H 'content-type: application/json' \
  -d '{"repo_url": "https://github.com/sindresorhus/is-plain-obj"}'
curl http://127.0.0.1:8000/ingest/<job_id>   # repeat until "status": "complete"

# Persistence: restart the container on the same volume; the repo should still be listed.
docker rm -f repolens && docker run --rm -d --name repolens -p 8000:8000 -v repolens-data:/data repolens-api
curl http://127.0.0.1:8000/repos
```

### Operational notes

- **Keep a single replica.** Railway doesn't allow replicas on a service with a Volume, and ingestion is a single worker by design (Phase 2).
- **Redeploys have a little downtime** with a Volume attached. An ingest that's running during a redeploy is marked `failed` ("Interrupted…") on the next start. Resubmit it.
- **Memory.** Embedding peaked around 1.5 GB of RAM locally. Give the service at least 2 GB.
- **The website is a second Railway service** built from `web/`. Point its `REPOLENS_API_URL` at this service's URL, and set `SITE_URL` to its own public domain.

## Design decisions

**Fetching: shallow `git clone --depth 1`, not the GitHub tree API.** A clone
is one operation however many files the repo has. The tree API needs one
request per blob on top of the listing, and unauthenticated clients get 60
requests/hour. It also truncates large trees. The cost is that `git` must be
installed and the full snapshot is written to disk. (`services/fetch.py`)

**Filtering** (`services/filter.py`) skips vendored and build directories,
lockfiles, minified or generated files (by suffix and by average line length),
binaries (NUL bytes), non-UTF-8 files, and files over 400 KB. Every skip is
counted by reason in the ingest report.

**Chunking.** Python uses the stdlib `ast`. JS/TS uses **tree-sitter**
(official `tree-sitter-javascript` / `tree-sitter-typescript` grammars)
rather than `@babel/parser`. Babel would need a Node runtime and a subprocess
per file from inside a Python service. tree-sitter runs in-process, handles JS,
JSX, TS, and TSX, and still parses the rest of a file that has a syntax error.
The rules are the same in both parsers:

- One chunk per top-level function or class, including decorators and the
  comment or JSDoc block directly above it. For JS this also covers
  `const f = () => ...`, `exports.f = function ...`, and
  `app.handle = function ...`. For TS it also covers `interface`, `type`, and
  `enum`.
- A class longer than 150 lines becomes a header chunk (with a note listing
  its methods and their line ranges) plus one chunk per method (`Class.method`).
- Module-level code between definitions (imports, config, the
  `if __name__ == "__main__"` block) is grouped into `<module>` chunks that
  only break between statements. In JS, an oversized wrapper statement (a
  UMD/IIFE or a `describe(...)` block) is chunked inside its function body.
- A single function is never split. If one is too long, its embedding input
  is truncated, but the stored chunk and the citation range cover the whole
  function.

**Embeddings: `jina-embeddings-v2-base-code`, run locally via fastembed/ONNX.**
It's trained on paired English and code, needs no API key, and there's no
per-chunk network cost. Anthropic doesn't offer an embeddings endpoint; the
hosted option it recommends is Voyage (`voyage-code-3`), which is the upgrade
path. Each repo records which model built its index, because vectors from
different models can't be compared. (`services/embed.py`)

**Vector store: SQLite rows plus an in-memory numpy matrix, exact search.**
There's no Postgres dependency, and exact search keeps retrieval quality
measurable without approximate-index recall mixed in. At Phase 1 scale this is
fast: a 5k-chunk repo is a ~15 MB matrix and a query takes about a millisecond.
It stops being the right choice past roughly 100k chunks per repo, or with
many large repos resident at once. At that point, switch to pgvector with an
HNSW index. Only `services/store.py` would change.

**Dependency graph** (`services/graph.py`): file-to-file edges from each
file's own imports.

- Python modules are named by walking up through `__init__.py` packages, so
  src/ and flat layouts both work. Relative imports resolve against the
  file's package, and scripts resolve bare-name imports against their own
  directory.
- JS/TS resolves relative specifiers with extension and `index` probing,
  including TypeScript ESM's `./x.js` meaning `./x.ts`.
- Package imports are counted as external and produce no edge.

**Retrieval** (`services/retrieve.py`):

1. Rank every chunk by cosine similarity to the question.
2. Add a bonus to chunks whose symbol name appears in the question.
3. For the top 3 files, pull up to 4 chunks from the files they import or that
   import them. Neighbour chunks whose symbol is referenced by name in a top
   hit get priority (caller/callee).

Every returned chunk says why it was retrieved.

**Answering** (`services/answer.py`):

- The context is bounded (~15k tokens of code) and every line is prefixed
  with its real line number, so citations are copied rather than guessed.
- The system prompt tells Claude to use only the excerpts and to say so when
  they don't contain the answer.
- The prompt tells Claude to cite each excerpt by its own lines, never one
  range spanning several excerpts, and not to cite irrelevant excerpts when
  reporting that something isn't there. Both were real sources of bad
  citations in testing.
- After the call, every `file:line` citation is checked against the lines
  Claude was actually shown. Each is returned with `valid` and a `basis`:
  - `"excerpt"`: all code in the range was shown. Blank lines between
    excerpts don't count against a citation.
  - `"class_index"`: an exact member location from a split class's index,
    real but not read.
  - `null`: invalid. An invalid citation is also marked `[unverified]` inline
    in the answer, with a caveat, so the reader never sees an unchecked
    citation that looks like a checked one.
- The model is `claude-opus-5` with adaptive thinking. Server-side refusal
  fallback (`fallbacks: "default"`) is enabled, so a safety-classifier decline
  is retried on Anthropic's recommended fallback model instead of coming back
  empty.

**`/ingest` runs as a background job (Phase 2).** Fetch and parse take
seconds, but CPU embedding runs at roughly 0.3–0.7 s per chunk, so the repos
below took 7–15 minutes, far past any HTTP timeout. `POST /ingest` records a
job in the same SQLite database and returns `202`. A single daemon worker
thread runs jobs from an in-process FIFO queue and writes each stage to the
job row as it starts. Clients poll `GET /ingest/{job_id}`. The pipeline itself
is unchanged; it only reports its stage through an optional callback. Why this
design (full reasoning in `services/jobs.py`):

- **A thread, not asyncio:** the pipeline is synchronous, CPU-bound code, so
  asyncio would just wrap a thread anyway.
- **One worker:** embedding already saturates every core, so parallel ingests
  would each run at half speed with double the memory.
- **A daemon thread, not an executor:** a `ThreadPoolExecutor` would block
  server shutdown until the current embed finished.
- **No Celery/Redis:** they buy durability across restarts and machines at the
  cost of running a broker.

The trade-off is that queued and running jobs don't survive a restart. At
startup they're marked `failed` ("Interrupted … submit again"), so no client
polls a job that will never finish. The embedding cost is also the strongest
argument for a hosted embedding model.

## Phase 1 acceptance results

Tested against real repos: `pallets/click` (Python), `expressjs/express` (JS),
and `sindresorhus/ky` (TS).

| Repo | Files | Chunks | Import edges | Parse errors | Embed time |
|---|---|---|---|---|---|
| pallets/click | 86 | 1290 (855 functions, 197 methods, 101 classes, 13 split-class headers, 124 module) | 165 | 0 | 905 s |
| expressjs/express | 141 | 502 (136 functions, 366 module/test blocks) | 153 | 0 | 393 s |
| sindresorhus/ky | 87 | 456 (96 functions, 36 methods, 60 types, 2 interfaces, 10 class-level, 252 module) | 157 | 0 | 436 s |

- **Chunk boundaries** were spot-checked against the source. For example,
  `Command.make_context` is click `core.py:1355-1390`, from its `def` line
  through `return ctx`. click's 700-line `Command` class was split into a
  header with a member index plus one chunk per method.
- **Graph** edges were checked by hand against the source:
  - `lib/express.js` → `application`, `request`, `response`
  - `lib/application.js` → `view`, `utils`
  - click `decorators.py` → `core`, `globals`, `utils`
  - ky `index.ts` → `./core/Ky.js`, which is really `Ky.ts`

  All expected edges are present.
- **Retrieval**:
  - "Entry point" (click) → `Command.main` / `Command.__call__`.
  - "How does app.listen start the server" (express) → `app.listen` at
    0.95, far ahead of the next hit.
  - "How does ky retry" → `Ky.#retryFromError` and
    `Ky.#calculateRetryDelay`. Graph expansion also pulled in
    `calculateRetryTimingDelay` from a separate file.
  - Questions about things these repos don't contain (PostgreSQL, WebSockets,
    GraphQL) score 0.38–0.49 top similarity, against 0.66–0.81 for real
    questions.
- **Answering (`/ask`)** wasn't exercised against the live Claude API during
  Phase 1, because there were no API credentials in the build environment.
  Context building and citation checking are unit-tested.

## Known gaps / Phase 2+

- Jobs are in-process: they don't survive a restart (they're marked failed)
  and can't be spread across machines. A broker-backed queue would fix both
  if that's ever needed.
- No size cap on submitted repos: anyone can queue a very large repo, and it
  would hold the single worker for hours. A pre-clone size check (GitHub API
  `size`) or a chunk-count limit would fix this.
- Bare line ranges in answers (e.g. "`360-380`" after a cited file) aren't
  linked or verified; only `file:line` citations are.
- tsconfig `paths` aliases (`@/x`) and Python namespace-package edge cases in
  the graph.
- Incremental re-ingest: currently every ingest re-clones and re-embeds
  everything.
- pgvector backend for multi-repo or large-repo scale.
- Hybrid lexical search (BM25) alongside vectors. The identifier boost is a
  stopgap.
