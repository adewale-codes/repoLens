"""Shared types for the language parsers.

Each parser turns one source file into syntax-aligned chunks plus the raw import
specifiers the file declares. Resolving those specifiers to repo files is the
dependency graph's job (services/graph.py), not the parser's.
"""

from dataclasses import dataclass, field

# A chunk longer than this many lines is split. Classes split into a summary
# plus their methods; module-level code splits at statement boundaries.
# A single function is never split.
MAX_CHUNK_LINES = 150


@dataclass
class Chunk:
    path: str  # repo-relative, forward slashes
    language: str
    kind: str  # function | method | class | class_summary | module | interface | type | enum
    symbol: str  # e.g. "parse_args", "Router.handle", "<module>"
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    text: str
    # Extra context that isn't source code, such as the member list of a split
    # class. It's included in the embedding input and shown to the model, but
    # never presented as quoted lines.
    note: str = ""


@dataclass
class Import:
    """One import statement, before it's resolved to a file."""

    spec: str  # "os.path", "./router", "..utils"; relative Python imports keep leading dots
    names: list[str] = field(default_factory=list)  # `from spec import a, b` -> ["a", "b"]
    line: int = 0


@dataclass
class ParsedFile:
    path: str
    language: str
    chunks: list[Chunk]
    imports: list[Import]
    error: str | None = None


def slice_lines(lines: list[str], start: int, end: int) -> str:
    """Lines start..end, 1-based and inclusive."""
    return "\n".join(lines[start - 1 : end])


def group_statements(spans: list[tuple[int, int]], max_lines: int = MAX_CHUNK_LINES) -> list[tuple[int, int]]:
    """Merge adjacent top-level statement spans into blocks of at most max_lines.

    Used for module-level code outside any function or class. Blocks only break
    between statements, so a statement is never cut in half. A single
    statement longer than max_lines gets a block to itself.
    """
    blocks: list[tuple[int, int]] = []
    for start, end in spans:
        if blocks and end - blocks[-1][0] + 1 <= max_lines:
            blocks[-1] = (blocks[-1][0], end)
        else:
            blocks.append((start, end))
    return blocks
