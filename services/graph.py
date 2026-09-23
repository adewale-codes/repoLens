"""File-level import graph: "file A imports file B".

The parsers extract raw import specifiers. This module resolves them to files
in the repo. Imports that don't resolve are third-party or stdlib (`os`,
`react`, `express`). They're counted as external and produce no edge.

Python: a file's canonical module name comes from walking up through
directories that contain `__init__.py`, so `src/click/core.py` is
`click.core` in both src/ and flat layouts. Relative imports resolve against
the importing file's package. A file outside any package (a script) resolves bare-name imports
against its own directory first, matching Python's `sys.path[0]` behaviour.

JS/TS: relative specifiers (`./x`, `../y`) are resolved with Node/TypeScript
extension and `index` probing, including TypeScript's ESM convention of
writing `./x.js` for a file that's really `./x.ts`. Bare specifiers are
packages. tsconfig `paths` aliases (`@/components/...`) aren't resolved yet,
so they're counted as external too.
"""

import posixpath
from collections import Counter

from .parsers import Import
from .store import Edge

_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts")
_JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


class ModuleIndex:
    """Maps Python dotted module names to repo file paths."""

    def __init__(self, paths: list[str]):
        py_files = [p for p in paths if p.endswith((".py", ".pyi"))]
        package_dirs = {posixpath.dirname(p) for p in py_files if posixpath.basename(p) == "__init__.py"}
        self.package_dirs = package_dirs
        self.module_of: dict[str, str] = {}
        self.path_of: dict[str, str] = {}
        for path in py_files:
            name = _canonical_module(path, package_dirs)
            self.module_of[path] = name
            self.path_of.setdefault(name, path)
        # Fallback names for namespace packages (no __init__.py): the path from
        # the repo root, and from a src/ or lib/ directory.
        for path in py_files:
            dotted = _path_to_dotted(path)
            self.path_of.setdefault(dotted, path)
            for root in ("src.", "lib."):
                if dotted.startswith(root):
                    self.path_of.setdefault(dotted[len(root):], path)
        # .py wins over .pyi for the same module name.
        for path in py_files:
            if path.endswith(".py") and self.path_of[self.module_of[path]].endswith(".pyi"):
                self.path_of[self.module_of[path]] = path

    def longest_prefix(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            hit = self.path_of.get(".".join(parts[:i]))
            if hit:
                return hit
        return None


def _path_to_dotted(path: str) -> str:
    stem = path.rsplit(".", 1)[0]
    if stem.endswith("/__init__"):
        stem = stem[: -len("/__init__")]
    return stem.replace("/", ".")


def _canonical_module(path: str, package_dirs: set[str]) -> str:
    directory, filename = posixpath.split(path)
    parts = [filename.rsplit(".", 1)[0]]
    if parts[0] == "__init__":
        parts = []
    while directory and directory in package_dirs:
        directory, name = posixpath.split(directory)
        parts.insert(0, name)
    return ".".join(parts)


def resolve_python(src: str, imp: Import, index: ModuleIndex) -> list[str] | None:
    """Repo files an import refers to, or None if it's external."""
    if imp.spec.startswith("."):
        level = len(imp.spec) - len(imp.spec.lstrip("."))
        rest = imp.spec[level:]
        package = index.module_of.get(src, "").split(".")
        if not src.endswith("__init__.py") and not src.endswith("__init__.pyi"):
            package = package[:-1]  # a plain module's package is its parent
        if level - 1 > len(package):
            return []  # beyond the top-level package, so unresolvable
        base = package[: len(package) - (level - 1)]
        target = ".".join(p for p in [*base, rest] if p)
        # "from . import x" / "from .pkg import x" where x is a submodule
        hits = [index.path_of[f"{target}.{n}" if target else n] for n in imp.names
                if (f"{target}.{n}" if target else n) in index.path_of]
        if hits:
            return hits
        found = index.path_of.get(target) if target else None
        if found:
            return [found]
        # Namespace-package fallback: resolve against the file's directory.
        directory = posixpath.dirname(src)
        for _ in range(level - 1):
            directory = posixpath.dirname(directory)
        rel = posixpath.join(directory, *rest.split(".")) if rest else directory
        for candidate in (rel + ".py", rel + "/__init__.py"):
            if candidate in index.module_of:
                return [candidate]
        return []

    directory = posixpath.dirname(src)
    if directory not in index.package_dirs:
        # A script: its own directory is first on sys.path.
        head = imp.spec.split(".")[0]
        for candidate in (posixpath.join(directory, head + ".py"), posixpath.join(directory, head, "__init__.py")):
            if candidate in index.module_of and candidate != src:
                return [candidate]
    hits = [index.path_of[f"{imp.spec}.{n}"] for n in imp.names if f"{imp.spec}.{n}" in index.path_of]
    if hits:
        return hits
    found = index.longest_prefix(imp.spec)
    return [found] if found else None


def resolve_js(src: str, imp: Import, paths: set[str]) -> list[str] | None:
    spec = imp.spec
    if not spec.startswith("."):
        return None  # bare specifier: npm package or node builtin
    base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
    if base == ".":  # require('..') from a top-level dir: the repo root's index file
        return next(([f"index{e}"] for e in _JS_EXTENSIONS if f"index{e}" in paths), [])
    if base.startswith("../"):
        return []  # points outside the repo
    candidates = [base]
    root, ext = posixpath.splitext(base)
    candidates += [root + ts_ext for ts_ext in _JS_TO_TS.get(ext, ())]
    candidates += [base + e for e in _JS_EXTENSIONS]
    candidates += [f"{base}/index{e}" for e in _JS_EXTENSIONS]
    for candidate in candidates:
        if candidate in paths:
            return [candidate]
    return []


def build_graph(imports_by_file: dict[str, list[Import]]) -> tuple[list[Edge], dict]:
    """Resolve every file's imports to repo files and return (edges, stats)."""
    paths = set(imports_by_file)
    index = ModuleIndex(sorted(paths))
    edges: dict[tuple[str, str], Edge] = {}
    stats = Counter()
    for src in sorted(imports_by_file):
        is_python = src.endswith((".py", ".pyi"))
        for imp in imports_by_file[src]:
            targets = resolve_python(src, imp, index) if is_python else resolve_js(src, imp, paths)
            if targets is None:
                stats["external"] += 1
            elif not targets:
                stats["unresolved"] += 1
            for dst in targets or []:
                if dst != src and (src, dst) not in edges:
                    edges[(src, dst)] = Edge(src, dst, imp.spec, imp.line)
    stats["edges"] = len(edges)
    return list(edges.values()), dict(stats)
