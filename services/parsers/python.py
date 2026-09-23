"""Python chunking with the stdlib `ast` module.

Chunking rules:
- A top-level function is one chunk, covering its decorators and any comment
  block directly above it. Nested functions stay inside their parent.
- A class of at most MAX_CHUNK_LINES lines is one chunk. A longer class becomes
  a header chunk (the class line, docstring, and class attributes, plus a note
  listing its methods) and one chunk per method, named "Class.method".
- Module-level statements between definitions (imports, constants, the
  `if __name__ == "__main__":` block) are grouped into "<module>" chunks.
  Groups only break between statements.
"""

import ast

from .base import MAX_CHUNK_LINES, Chunk, Import, ParsedFile, group_statements, slice_lines

LANGUAGE = "python"
EXTENSIONS = (".py", ".pyi")

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def parse(path: str, source: str) -> ParsedFile:
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as e:  # ValueError: source contains null bytes
        return ParsedFile(path, LANGUAGE, [], [], error=f"{type(e).__name__}: {e}")

    lines = source.splitlines()
    chunks: list[Chunk] = []
    _chunk_body(tree.body, lines, path, prefix="", chunks=chunks, floor=0)
    chunks.sort(key=lambda c: c.start_line)
    return ParsedFile(path, LANGUAGE, chunks, _imports(tree))


def _start_line(node: ast.stmt, lines: list[str], floor: int) -> int:
    """First line of a node, including decorators and a comment block directly above.

    `floor` is the last line already claimed by an earlier chunk, so a chunk
    never reaches back into it.
    """
    start = node.lineno
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        start = min(start, *(d.lineno for d in decorators))
    while start - 1 > floor and lines[start - 2].lstrip().startswith("#"):
        start -= 1
    return start


def _chunk_body(body: list[ast.stmt], lines: list[str], path: str, prefix: str, chunks: list[Chunk], floor: int) -> None:
    """Chunk a module body or the body of a class that is being split."""
    run: list[tuple[int, int]] = []  # consecutive non-definition statements

    def flush_run() -> None:
        symbol = f"{prefix}<body>" if prefix else "<module>"
        kind = "class_body" if prefix else "module"
        for start, end in group_statements(run):
            chunks.append(Chunk(path, LANGUAGE, kind, symbol, start, end, slice_lines(lines, start, end)))
        run.clear()

    for node in body:
        if not isinstance(node, _DEFS):
            run.append((node.lineno, node.end_lineno))
            floor = node.end_lineno
            continue
        flush_run()
        start = _start_line(node, lines, floor)
        end = node.end_lineno
        name = f"{prefix}{node.name}"
        if isinstance(node, ast.ClassDef):
            _chunk_class(node, start, lines, path, name, chunks)
        else:
            kind = "method" if prefix else "function"
            chunks.append(Chunk(path, LANGUAGE, kind, name, start, end, slice_lines(lines, start, end)))
        floor = end
    flush_run()


def _chunk_class(node: ast.ClassDef, start: int, lines: list[str], path: str, name: str, chunks: list[Chunk]) -> None:
    end = node.end_lineno
    if end - start + 1 <= MAX_CHUNK_LINES:
        chunks.append(Chunk(path, LANGUAGE, "class", name, start, end, slice_lines(lines, start, end)))
        return

    # Too big for one chunk. The header runs from the class line up to the first
    # method; everything after that is chunked like a module body.
    first_def = next((i for i, n in enumerate(node.body) if isinstance(n, _DEFS)), None)
    if first_def is None:  # a huge class with no methods (e.g. a long enum)
        first_def = 1
        header_end = node.body[0].end_lineno
    else:
        floor = node.body[first_def - 1].end_lineno if first_def else node.lineno
        # Leave the first method's decorators and leading comment to the method.
        header_end = max(floor, _start_line(node.body[first_def], lines, floor) - 1)
    while header_end > start and not lines[header_end - 1].strip():
        header_end -= 1
    members = [
        f"{n.name} (L{n.lineno}-{n.end_lineno})" for n in node.body if isinstance(n, _DEFS)
    ]
    chunk = Chunk(path, LANGUAGE, "class_summary", name, start, header_end, slice_lines(lines, start, header_end))
    chunk.note = f"Class {name} spans L{start}-{end}. Members: " + ", ".join(members)
    chunks.append(chunk)
    _chunk_body(node.body[first_def:], lines, path, prefix=f"{name}.", chunks=chunks, floor=header_end)


def _imports(tree: ast.Module) -> list[Import]:
    """Every import in the file, including ones inside functions."""
    found: list[Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(Import(spec=alias.name, line=node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            spec = "." * node.level + (node.module or "")
            found.append(Import(spec=spec, names=[a.name for a in node.names], line=node.lineno))
    return found
