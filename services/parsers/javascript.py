"""JavaScript / TypeScript chunking with tree-sitter.

We use tree-sitter instead of @babel/parser because it runs in-process (no Node
subprocess per file from a Python service), the official JS, TS, and TSX
grammars ship as prebuilt wheels, and it tolerates syntax errors: a file with
one bad construct still yields a tree for everything else.

Chunking rules follow the Python parser:
- One chunk per top-level definition: `function f`, `class C`,
  `const f = () => ...`, `exports.f = function ...`, `app.use = function ...`,
  and in TypeScript `interface`, `type`, and `enum`. Each chunk includes its
  `export` keyword and the JSDoc or comment block directly above it.
- A class longer than MAX_CHUNK_LINES becomes a header chunk plus one chunk per
  method, named "Class.method".
- Other top-level statements are grouped into "<module>" chunks. Groups only
  break between statements. If one statement is itself oversized, such as a
  UMD/IIFE wrapper or a `describe(...)` block, we chunk the function body
  inside it the same way.
"""

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript
import tree_sitter_typescript

from .base import MAX_CHUNK_LINES, Chunk, Import, ParsedFile, group_statements, slice_lines

EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")

_LANGS = {
    "javascript": Language(tree_sitter_javascript.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
}

_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
_CLASS_VALUES = {"class", "class_expression"}
_DECL_KINDS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "function_signature": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
_FUNCTION_BODY_PARENTS = _FUNCTION_VALUES | {"function_declaration", "method_definition"}
_COMMENT_PREFIXES = ("//", "/*", "*", "*/")


def grammar_for(path: str) -> str:
    if path.endswith(".tsx"):
        return "tsx"
    if path.endswith((".ts", ".mts", ".cts")):
        return "typescript"
    return "javascript"


def parse(path: str, source: str) -> ParsedFile:
    grammar = grammar_for(path)
    language = "typescript" if grammar in ("typescript", "tsx") else "javascript"
    tree = Parser(_LANGS[grammar]).parse(source.encode("utf-8"))
    lines = source.splitlines()

    chunks: list[Chunk] = []
    _Chunker(path, language, lines, chunks).statements(tree.root_node.named_children, floor=0)
    chunks.sort(key=lambda c: c.start_line)

    # tree-sitter always returns a tree. Report errors without discarding chunks.
    error = "partial parse: syntax errors present" if tree.root_node.has_error else None
    return ParsedFile(path, language, chunks, _imports(tree.root_node), error=error)


def _start(node: Node) -> int:
    return node.start_point[0] + 1


def _end(node: Node) -> int:
    return node.end_point[0] + 1


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


class _Chunker:
    def __init__(self, path: str, language: str, lines: list[str], out: list[Chunk]):
        self.path, self.language, self.lines, self.out = path, language, lines, out

    def emit(self, kind: str, symbol: str, start: int, end: int, note: str = "") -> None:
        text = slice_lines(self.lines, start, end)
        self.out.append(Chunk(self.path, self.language, kind, symbol, start, end, text, note))

    def lead_start(self, node: Node, floor: int) -> int:
        """Node start, extended up over a comment/JSDoc block directly above it."""
        start = _start(node)
        while start - 1 > floor and self.lines[start - 2].lstrip().startswith(_COMMENT_PREFIXES):
            start -= 1
        return start

    def statements(self, nodes: list[Node], floor: int, prefix: str = "") -> None:
        run: list[tuple[int, int]] = []

        def flush() -> None:
            for start, end in group_statements(run):
                self.emit("module", "<module>", start, end)
            run.clear()

        for node in nodes:
            if node.type == "comment":
                continue  # comments attach to the following definition or run
            definition = _classify(node)
            if definition is None:
                if _end(node) - _start(node) + 1 > MAX_CHUNK_LINES and self.descend(node, floor, flush):
                    floor = _end(node)
                    continue
                run.append((self.lead_start(node, floor) if not run else _start(node), _end(node)))
                floor = _end(node)
                continue
            flush()
            kind, name, decl = definition
            start, end = self.lead_start(node, floor), _end(node)
            if kind == "class" and end - start + 1 > MAX_CHUNK_LINES:
                self.split_class(decl, prefix + name, start, end)
            else:
                self.emit(kind, prefix + name, start, end)
            floor = end
        flush()

    def descend(self, node: Node, floor: int, flush) -> bool:
        """Chunk inside an oversized non-definition statement, such as an IIFE wrapper.

        Returns False if the statement contains no function body to split on.
        """
        body = _largest_function_body(node)
        if body is None or not body.named_children:
            return False
        flush()
        inner = body.named_children
        head_end = _start(inner[0]) - 1
        if head_end >= _start(node):
            self.emit("module", "<module>", self.lead_start(node, floor), head_end)
        self.statements(inner, floor=max(floor, head_end))
        tail_start = _end(inner[-1]) + 1
        if tail_start <= _end(node):
            self.emit("module", "<module>", tail_start, _end(node))
        return True

    def split_class(self, decl: Node, name: str, start: int, end: int) -> None:
        body = decl.child_by_field_name("body")
        members = [m for m in (body.named_children if body else []) if m.type != "comment"]
        methods = [m for m in members if m.type in ("method_definition", "method_signature", "abstract_method_signature")]
        if not methods:
            self.emit("class", name, start, end)
            return

        first = methods[0]
        header_end = max(start, self.lead_start(first, _start(decl)) - 1)
        listing = ", ".join(f"{_text(m.child_by_field_name('name'))} (L{_start(m)}-{_end(m)})" for m in methods)
        self.emit("class_summary", name, start, header_end, note=f"Class {name} spans L{start}-{end}. Methods: {listing}")

        floor, run = header_end, []
        for member in members:
            if _start(member) <= header_end:
                continue
            if member in methods:
                for s, e in group_statements(run):
                    self.emit("class_body", f"{name}.<body>", s, e)
                run = []
                m_start = self.lead_start(member, floor)
                self.emit("method", f"{name}.{_text(member.child_by_field_name('name'))}", m_start, _end(member))
            else:
                run.append((_start(member), _end(member)))
            floor = _end(member)
        for s, e in group_statements(run):
            self.emit("class_body", f"{name}.<body>", s, e)


def _classify(node: Node) -> tuple[str, str, Node] | None:
    """If a top-level statement is a named definition, return (kind, name, declaration node)."""
    target = node
    if node.type == "export_statement":
        decl = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        if decl is not None:
            target = decl
        elif value is not None:  # export default <expression>
            if value.type in _FUNCTION_VALUES:
                return "function", _text(value.child_by_field_name("name")) or "default", value
            if value.type in _CLASS_VALUES:
                return "class", _text(value.child_by_field_name("name")) or "default", value
            return None
        else:
            return None
    if target.type == "ambient_declaration" and target.named_children:  # `declare function f(): T`
        target = target.named_children[0]

    if target.type in _DECL_KINDS:
        return _DECL_KINDS[target.type], _text(target.child_by_field_name("name")), target

    if target.type in ("lexical_declaration", "variable_declaration"):
        declarators = [c for c in target.named_children if c.type == "variable_declarator"]
        if len(declarators) == 1:
            value = declarators[0].child_by_field_name("value")
            name = _text(declarators[0].child_by_field_name("name"))
            if value is not None and _is_function_like(value):
                return "function", name, value
            if value is not None and value.type in _CLASS_VALUES:
                return "class", name, value
        return None

    if target.type == "expression_statement" and target.named_children:
        expr = target.named_children[0]
        if expr.type == "assignment_expression":
            right = expr.child_by_field_name("right")
            left = _text(expr.child_by_field_name("left"))
            if right is not None and _is_function_like(right):
                return "function", left, right
            if right is not None and right.type in _CLASS_VALUES:
                return "class", left, right
    return None


def _is_function_like(value: Node) -> bool:
    """A function value, including one wrapped in a call such as memo(() => ...) or forwardRef(...)."""
    if value.type in _FUNCTION_VALUES:
        return True
    if value.type == "call_expression":
        args = value.child_by_field_name("arguments")
        return args is not None and any(a.type in _FUNCTION_VALUES for a in args.named_children)
    return False


def _largest_function_body(node: Node) -> Node | None:
    """The biggest function body (statement_block) within a few levels of `node`."""
    best, stack = None, [(node, 0)]
    while stack:
        current, depth = stack.pop()
        if current.type == "statement_block" and current.parent is not None and current.parent.type in _FUNCTION_BODY_PARENTS:
            if best is None or current.end_byte - current.start_byte > best.end_byte - best.start_byte:
                best = current
            continue
        if depth < 6:
            stack.extend((child, depth + 1) for child in current.named_children)
    return best


def _string_value(node: Node | None) -> str | None:
    if node is None or node.type not in ("string", "template_string"):
        return None
    fragments = [c for c in node.named_children if c.type == "string_fragment"]
    if node.type == "template_string" and any(c.type == "template_substitution" for c in node.named_children):
        return None  # dynamic path; can't resolve statically
    return _text(fragments[0]) if fragments else ""


def _imports(root: Node) -> list[Import]:
    found: list[Import] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type in ("import_statement", "export_statement"):
            spec = _string_value(node.child_by_field_name("source"))
            if spec is not None:
                found.append(Import(spec=spec, names=_imported_names(node), line=_start(node)))
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn is None or args is None or not args.named_children:
                continue
            if (fn.type == "identifier" and _text(fn) == "require") or fn.type == "import":
                spec = _string_value(args.named_children[0])
                if spec is not None:
                    found.append(Import(spec=spec, names=_required_names(node), line=_start(node)))
    found.sort(key=lambda i: i.line)
    return found


def _imported_names(node: Node) -> list[str]:
    names: list[str] = []
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        if n.type in ("import_specifier", "export_specifier"):
            names.append(_text(n.child_by_field_name("name")))
        elif n.type == "identifier" and n.parent is not None and n.parent.type == "import_clause":
            names.append(_text(n))  # default import
        elif n.type in ("import_clause", "named_imports", "export_clause", "namespace_import"):
            stack.extend(n.named_children)
    return names


def _required_names(call: Node) -> list[str]:
    """Names bound by `const x = require(...)` or `const { a, b } = require(...)`."""
    parent = call.parent
    if parent is None or parent.type != "variable_declarator":
        return []
    target = parent.child_by_field_name("name")
    if target is None:
        return []
    if target.type == "identifier":
        return [_text(target)]
    return [_text(c) for c in target.named_children if c.type in ("shorthand_property_identifier_pattern", "identifier")]
