# repolens-mcp

An MCP server that lets AI agents (Claude Code, Claude Desktop, any MCP client)
ask questions about a GitHub repository's real code and get answers that cite
the exact lines. It's a thin wrapper over the live RepoLens API: agents can
check whether a repo is indexed, index it if it isn't, and ask it questions.
No account, no config, no local backend. It talks to the production API by
default.

It's a separate package from the `repolens-cli` terminal tool, so the CLI
keeps zero dependencies and only people who want agent integration install
the MCP SDK. Its API calls come from `repolens-cli`'s own modules, not a
reimplementation.

## Install

Requires Node >= 18.

### Claude Code

```bash
claude mcp add repolens -- npx -y repolens-mcp
```

This uses local/project scope by default. Add `--scope user` to make it
available in all your projects.

**On Windows PowerShell 5.1** (the default `powershell.exe`), that command
doesn't work as written. `claude` resolves to npm's `claude.ps1` shim, and
PowerShell drops a bare `--` passed to a `.ps1` script. Claude Code receives
`mcp add repolens npx -y repolens-mcp` and parses `-y` as its own option.
(Verified: the same arguments reach a `.ps1` script as
`[mcp] [add] [repolens] [npx] [-y] [repolens-mcp]`, with the `--` gone.) Either
of these works:

```powershell
claude mcp add repolens '--' npx -y repolens-mcp        # quote the --
claude.cmd mcp add repolens -- npx -y repolens-mcp      # or call the .cmd shim, which passes -- through
```

Or skip the command and add a `.mcp.json` at your project root (shared with
everyone who opens the project):

```json
{
  "mcpServers": {
    "repolens": {
      "command": "npx",
      "args": ["-y", "repolens-mcp"]
    }
  }
}
```

### Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config), then
restart Claude Desktop:

```json
{
  "mcpServers": {
    "repolens": {
      "command": "npx",
      "args": ["-y", "repolens-mcp"]
    }
  }
}
```

To point it at a local or self-hosted RepoLens API instead of production, add
an `env` block. This is the same `REPOLENS_API_URL` the CLI and the website
use:

```json
{
  "mcpServers": {
    "repolens": {
      "command": "npx",
      "args": ["-y", "repolens-mcp"],
      "env": { "REPOLENS_API_URL": "http://127.0.0.1:8000" }
    }
  }
}
```

### From a local checkout

```json
{
  "mcpServers": {
    "repolens": {
      "command": "node",
      "args": ["/absolute/path/to/repoLens/mcp/bin/repolens-mcp.js"]
    }
  }
}
```

In a checkout, run `npm install` and then `npm install --no-save ../cli`.
Until `repolens-cli` is published, the dependency has to come from the sibling
folder.

## Tools

Every tool queries the live RepoLens service, which indexes real repos
function by function. Every description says so, so agents don't treat it as a
static knowledge source.

| Tool | Arguments | What it does |
| --- | --- | --- |
| `check_repo_indexed` | `owner_repo` | Fast lookup: is the repo indexed? Returns `indexed`, the commit and size, and `next_step` (`ask_repo` or `ingest_repo`). The description tells agents to call it first. |
| `ask_repo` | `owner_repo`, `question` | Answers from the repo's indexed code, with `answer_status` (`answered` / `partial` / `not_found`) and citations (file, lines, verified flag, GitHub link at the indexed commit). Takes about 15-90 s. If the repo isn't indexed, it returns an error telling the agent to call `ingest_repo` with the exact `repo_url`. |
| `ingest_repo` | `repo_url`, optional `wait_seconds` (default 45, max 55), optional `reindex` | Indexes the repo. Returns `complete`, `in_progress` (with `job_id`, stage, and embedding counts), or `already_indexed`. See below. |

`owner_repo` and `repo_url` both accept `owner/repo` or a full
`https://github.com/owner/repo` URL.

### Why `ingest_repo` waits up to 45 seconds

Indexing takes about 1-3 minutes for a small repo and up to ~15 for a large one,
mostly spent embedding the code. A tool call can't block that long: MCP
clients time out tool calls, often around 60 s. So `ingest_repo` waits a
bounded time and then returns whatever state the job is in:

- **Done within the window** (common for small repos): it returns
  `status: "complete"` and the agent can ask right away. In testing,
  `pallets/markupsafe` (95 chunks) finished in about 35 s.
- **Still running**: it returns `status: "in_progress"` with the `job_id`,
  the current stage, and a typical total time. The job keeps running on the
  server.
- **To keep waiting, the agent calls `ingest_repo` again with the same
  `repo_url`.** That resumes the same job; it doesn't start a new one. The API
  refuses a second job for the same repo and hands back the running one. So
  "check back" needs no extra tool, and repeated calls are safe.
  `check_repo_indexed` also reports `indexed: true` once the job is done.
- `wait_seconds: 0` returns immediately with the `job_id`, for an agent that
  would rather not wait.
- A repo that's already indexed isn't re-indexed unless `reindex: true`,
  since that would tie up the server's single indexing worker for minutes.

While it waits, `ingest_repo` sends standard MCP progress notifications
(stage, then "embedding 48/95 code chunks"), if the client asks for them.

## A worked example

The tool calls below are from a real run. Claude (`claude-opus-5`) was given
only this server's tool list, with no system prompt and no tool names in the
request, and its calls ran against the production API.

**"How does the retry logic work in sindresorhus/ky?"** (not indexed at the time)

```
-> check_repo_indexed({"owner_repo": "sindresorhus/ky"})
   sindresorhus/ky is not indexed yet. Next step: call ingest_repo with repo_url
   "https://github.com/sindresorhus/ky", then ask_repo.
-> ingest_repo({"repo_url": "https://github.com/sindresorhus/ky"})
```

From there `ingest_repo` behaves as described above. For example, with
`pallets/markupsafe`:

```
-> ingest_repo({"repo_url": "https://github.com/pallets/markupsafe", "wait_seconds": 3})
   Still indexing pallets/markupsafe: embedding 0 of 95 code chunks. It keeps running on the
   server; a repo this size usually takes 1-3 minutes in total. To keep waiting, call
   ingest_repo again with the same repo_url: it resumes this same job (job 96d2ac22…) ...
-> ingest_repo({"repo_url": "pallets/markupsafe"})
   Indexed pallets/markupsafe at commit b2e4d9c: 12 files, 95 code chunks, 11 internal imports.
   It can be asked about now: call ask_repo.
```

**"How does markupsafe decide which characters to escape? The repo is github.com/pallets/markupsafe."** (indexed)

```
-> check_repo_indexed({"owner_repo": "pallets/markupsafe"})
   pallets/markupsafe is indexed (commit b2e4d9c, 12 files, 95 code chunks, Python, ...).
   Next step: call ask_repo with a question.
-> ask_repo({"owner_repo": "pallets/markupsafe", "question": "How does escaping work? Which
   characters are escaped and what are they replaced with? ..."})
   pallets/markupsafe @ b2e4d9c: PARTIAL ANSWER: ...
-> ask_repo({"owner_repo": "pallets/markupsafe", "question": "Where does __init__.py import
   _escape_inner, and what is the import/fallback logic ...?"})
   pallets/markupsafe @ b2e4d9c: ANSWERED: ...
```

Claude's final reply began: *"MarkupSafe doesn't 'decide' dynamically: it
escapes a fixed set of five characters, hard-coded as chained `str.replace`
calls,"* citing
[`src/markupsafe/_native.py:1-8`](https://github.com/pallets/markupsafe/blob/b2e4d9c7687be25695fffbe93a37622302b24fb1/src/markupsafe/_native.py#L1-L8).

## Verification

```bash
npm test    # node test/verify.js [owner/repo-to-ingest]
```

`test/verify.js` spawns the server as a real subprocess and drives it over
stdio with the MCP SDK's own `Client` and `StdioClientTransport`, against the
live API. It checks:

- the tool descriptions;
- `check_repo_indexed` for an indexed and a never-indexed repo;
- a real `ask_repo` answer with its citations;
- the not-indexed error;
- the full `ingest_repo` cycle: `in_progress`, then resuming the same job with
  progress notifications, then `complete`, `indexed: true`, and finally
  `already_indexed`.

The ingest step needs a small real repo that isn't indexed on the target API
yet (default `pallets/markupsafe`, which is now indexed on production), and it
indexes that repo as a side effect.

## Publishing

`repolens-cli` has to be published first, since this package depends on it
(`^0.1.0`).
