"""Fetch a GitHub repo's source.

We use a shallow `git clone --depth 1` rather than the GitHub REST tree API:

- One clone is one network operation, whatever the file count. The tree API
  lists paths but you then fetch every blob separately, and an unauthenticated
  client gets 60 requests/hour, which a medium repo exceeds on its own.
- The recursive tree endpoint truncates at 100k entries / 7 MB, and needs
  fallback pagination when it does.
- Clones need no token for public repos and aren't counted against the REST
  rate limit.

The trade-off is that git must be installed on the host and the whole snapshot
lands on disk, including files the filter will throw away. At portfolio scale,
that's cheaper than juggling rate limits.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_GITHUB_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)

CLONE_TIMEOUT_SECONDS = 300


class FetchError(Exception):
    pass


@dataclass
class FetchedRepo:
    repo_id: str  # "owner/name"
    url: str
    commit: str
    root: Path


def parse_github_url(url: str) -> tuple[str, str]:
    match = _GITHUB_URL.match(url.strip())
    if not match:
        raise FetchError(f"Not a GitHub repository URL: {url!r} (expected https://github.com/<owner>/<repo>)")
    return match["owner"], match["name"]


def fetch_repo(url: str, repos_dir: Path) -> FetchedRepo:
    owner, name = parse_github_url(url)
    repo_id = f"{owner}/{name}"
    dest = repos_dir / owner / name
    if dest.exists():
        _rmtree(dest)  # re-ingest always takes a fresh snapshot of the default branch
    dest.parent.mkdir(parents=True, exist_ok=True)

    clone_url = f"https://github.com/{owner}/{name}.git"
    _git(["clone", "--depth", "1", "--single-branch", "--no-tags", clone_url, str(dest)], cwd=None)
    commit = _git(["rev-parse", "HEAD"], cwd=dest).strip()
    return FetchedRepo(repo_id=repo_id, url=clone_url, commit=commit, root=dest)


def _git(args: list[str], cwd: Path | None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            # Never prompt for credentials: a private or missing repo should fail, not hang.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as e:
        raise FetchError("git is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise FetchError(f"git {args[0]} timed out after {CLONE_TIMEOUT_SECONDS}s") from e
    if result.returncode != 0:
        raise FetchError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _rmtree(path: Path) -> None:
    # Git marks pack files read-only, which makes shutil.rmtree fail on Windows.
    def on_error(func, p, _exc):
        Path(p).chmod(stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=on_error)
    else:
        shutil.rmtree(path, onerror=on_error)
