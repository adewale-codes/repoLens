"""Decide which files in a cloned repo are worth parsing.

Checks run cheapest first: path rules (excluded directories, lockfiles,
minified bundles), then extension, then size, then content sniffing (binary,
minified). Every skip is counted by reason, so an ingest report can show why a
file was dropped.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from config import settings

from .parsers import SUPPORTED_EXTENSIONS

EXCLUDED_DIRS = {
    ".git", "node_modules", "bower_components", "jspm_packages", "vendor", "third_party",
    "dist", "build", "out", "target", "coverage", ".next", ".nuxt", ".svelte-kit", ".turbo",
    ".cache", ".parcel-cache", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".nox", "site-packages", ".eggs", "htmlcov",
    ".idea", ".vscode", "storybook-static",
}

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "npm-shrinkwrap.json",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "Cargo.lock", "Gemfile.lock",
    "composer.lock", "go.sum",
}

GENERATED_SUFFIXES = (".min.js", ".min.mjs", ".bundle.js", ".map", "_pb2.py", "_pb2_grpc.py", ".pb.ts")

SNIFF_BYTES = 8192
MINIFIED_AVG_LINE = 300  # a bundle averages hundreds or thousands of characters per line


@dataclass
class FilterResult:
    kept: list[Path] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)


def skip_reason(rel: Path, size: int, head: bytes | None = None) -> str | None:
    """Why this file should be skipped, or None to keep it.

    `head` is the file's first bytes. Pass None to run only the path and size checks.
    """
    parts = rel.parts
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in parts[:-1]):
        return "excluded_dir"
    name = rel.name
    if name in LOCKFILES:
        return "lockfile"
    if name.endswith(GENERATED_SUFFIXES):
        return "generated"
    if not name.endswith(SUPPORTED_EXTENSIONS):
        return "unsupported_language"
    if size == 0:
        return "empty"
    if size > settings.max_file_bytes:
        return "too_large"
    if head is not None:
        if b"\x00" in head:
            return "binary"
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            # A multi-byte character cut off at the sniff boundary is fine.
            if e.start < len(head) - 4:
                return "not_utf8"
        if head.count(b"\n") <= 2 and len(head) >= SNIFF_BYTES:
            return "minified"
        lines = head.count(b"\n") + 1
        if len(head) / lines > MINIFIED_AVG_LINE:
            return "minified"
    return None


def filter_repo(root: Path) -> FilterResult:
    result = FilterResult()
    stack = [root]
    while stack:
        directory = stack.pop()
        for entry in sorted(directory.iterdir()):
            rel = entry.relative_to(root)
            if entry.is_symlink():
                result.skipped["symlink"] += 1
                continue
            if entry.is_dir():
                if entry.name in EXCLUDED_DIRS or entry.name.endswith(".egg-info"):
                    result.skipped["excluded_dir"] += 1  # count the directory, don't walk it
                else:
                    stack.append(entry)
                continue
            size = entry.stat().st_size
            reason = skip_reason(rel, size)
            if reason is None:
                with entry.open("rb") as f:
                    reason = skip_reason(rel, size, f.read(SNIFF_BYTES))
            if reason:
                result.skipped[reason] += 1
            else:
                result.kept.append(entry)
    result.kept.sort()
    return result
