# RepoLens

Ask questions about a GitHub repository and get answers grounded in its actual
code, with `file:line` citations.

Phase 1 covers **Python** and **JavaScript/TypeScript**. Both are chunked along
real syntax boundaries (functions, classes, methods), not fixed line windows.

```
POST /ingest {"repo_url": "https://github.com/pallets/click"}
  fetch (shallow clone) -> filter -> parse/chunk -> import graph -> embed -> store

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
| POST | `/ingest` | Index a repo. Returns chunk counts by kind and language, skipped-file reasons, parse errors, graph stats, and timings. |
| POST | `/ask` | Answer a question. Returns the answer, each citation with a `valid` flag, the chunks used (with why each was retrieved), and `files_consulted`. |
| GET | `/repos` | List indexed repos. |
| GET | `/repos/{owner}/{name}/graph?file=...` | Import edges, optionally for one file. |
| GET | `/repos/{owner}/{name}/chunks?file=...&include_text=true` | Chunk boundaries, for spot checks. |

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
- After the call, every `file:line` citation is checked against the lines
  Claude was actually shown, and returned with `valid: true/false`.
- The model is `claude-opus-5` with adaptive thinking. Server-side refusal
  fallback (`fallbacks: "default"`) is enabled, so a safety-classifier decline
  is retried on Anthropic's recommended fallback model instead of coming back
  empty.

**`/ingest` is synchronous.** Fetch and parse take seconds, but CPU
embedding runs at roughly 0.3–0.7 s per chunk, so the repos below took 7–15
minutes. Phase 2 should return a job id and run the pipeline in a worker. The
embedding cost is also the strongest argument for a hosted embedding model.

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

- Async ingestion with job polling.
- tsconfig `paths` aliases (`@/x`) and Python namespace-package edge cases in
  the graph.
- Incremental re-ingest: currently every ingest re-clones and re-embeds
  everything.
- pgvector backend for multi-repo or large-repo scale.
- Hybrid lexical search (BM25) alongside vectors. The identifier boost is a
  stopgap.
