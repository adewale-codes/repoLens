from textwrap import dedent

from services.parsers import parse_file
from services.parsers.base import MAX_CHUNK_LINES


def spans(parsed):
    return [(c.kind, c.symbol, c.start_line, c.end_line) for c in parsed.chunks]


def test_python_functions_classes_and_module_code():
    source = dedent('''\
        """Module docstring."""
        import os
        from .util import helper

        # Adds numbers.
        @decorator
        def add(a, b):
            def inner():
                return a
            return a + b


        class Small:
            x = 1

            def method(self):
                return self.x


        if __name__ == "__main__":
            add(1, 2)
        ''')
    parsed = parse_file("pkg/mod.py", source)
    assert parsed.error is None
    assert spans(parsed) == [
        ("module", "<module>", 1, 3),
        ("function", "add", 5, 10),  # leading comment and decorator included, nested def kept inside
        ("class", "Small", 13, 17),
        ("module", "<module>", 20, 21),
    ]
    assert [(i.spec, i.names) for i in parsed.imports] == [("os", []), (".util", ["helper"])]


def test_python_large_class_is_split_into_header_and_methods():
    methods = "\n".join(f"    def m{i}(self):\n" + "        x = 1\n" * 20 for i in range(10))
    source = f"class Big:\n    \"\"\"Doc.\"\"\"\n    attr = 1\n\n{methods}\n"
    parsed = parse_file("big.py", source)
    kinds = [(c.kind, c.symbol) for c in parsed.chunks]
    assert kinds[0] == ("class_summary", "Big")
    assert parsed.chunks[0].end_line == 3  # class line, docstring, attr; no method bodies
    assert "m0 (L5-" in parsed.chunks[0].note
    assert kinds[1:] == [("method", f"Big.m{i}") for i in range(10)]
    for chunk in parsed.chunks:
        assert chunk.end_line - chunk.start_line + 1 <= MAX_CHUNK_LINES


def test_python_syntax_error_is_reported_not_raised():
    parsed = parse_file("py2.py", "print 'hello'\n")
    assert parsed.chunks == [] and parsed.error.startswith("SyntaxError")


def test_javascript_definitions_and_requires():
    source = dedent('''\
        'use strict';
        var Router = require('./router');
        const { a, b } = require('./util');

        /**
         * Create an app.
         */
        function createApplication() {
          return {};
        }

        app.handle = function handle(req, res) {
          return 1;
        };

        const arrow = (x) => x * 2;

        module.exports = createApplication;
        ''')
    parsed = parse_file("lib/express.js", source)
    assert parsed.error is None
    assert spans(parsed) == [
        ("module", "<module>", 1, 3),
        ("function", "createApplication", 5, 10),  # JSDoc included
        ("function", "app.handle", 12, 14),
        ("function", "arrow", 16, 16),
        ("module", "<module>", 18, 18),
    ]
    assert [(i.spec, i.names) for i in parsed.imports] == [("./router", ["Router"]), ("./util", ["a", "b"])]


def test_typescript_exports_interfaces_and_imports():
    source = dedent('''\
        import type { Options } from './types.js';
        import ky from 'ky';
        export * from './errors';

        export interface Hooks {
          before: () => void;
        }

        export type Input = string | URL;

        export default class Client {
          private base: string;
          constructor(base: string) { this.base = base; }
          get(path: string): Promise<Response> { return fetch(this.base + path); }
        }

        export const helper = async (x: number): Promise<number> => x;
        ''')
    parsed = parse_file("source/client.ts", source)
    assert parsed.language == "typescript" and parsed.error is None
    assert [(k, s) for k, s, *_ in spans(parsed)] == [
        ("module", "<module>"),
        ("interface", "Hooks"),
        ("type", "Input"),
        ("class", "Client"),
        ("function", "helper"),
    ]
    assert [i.spec for i in parsed.imports] == ["./types.js", "ky", "./errors"]


def test_tsx_parses_jsx():
    source = "export function App() {\n  return <div className=\"x\">hi</div>;\n}\n"
    parsed = parse_file("src/App.tsx", source)
    assert parsed.error is None
    assert spans(parsed) == [("function", "App", 1, 3)]


def test_oversized_iife_is_chunked_inside():
    body = "\n".join(f"  function f{i}() {{\n" + "    var x = 1;\n" * 20 + "  }" for i in range(10))
    source = f"(function (root) {{\n  'use strict';\n{body}\n  root.lib = f0;\n}})(this);\n"
    parsed = parse_file("umd.js", source)
    symbols = [c.symbol for c in parsed.chunks]
    assert [f"f{i}" for i in range(10)] == [s for s in symbols if s != "<module>"]
    for chunk in parsed.chunks:
        assert chunk.end_line - chunk.start_line + 1 <= MAX_CHUNK_LINES


def test_nested_oversized_describe_blocks_are_descended():
    tests = "\n".join(f"    it('t{i}', function () {{\n" + "      assert(1);\n" * 20 + "    });" for i in range(10))
    source = f"describe('res', function () {{\n  describe('.json()', function () {{\n{tests}\n  }});\n}});\n"
    parsed = parse_file("test/res.json.js", source)
    assert len(parsed.chunks) > 2
    for chunk in parsed.chunks:
        assert chunk.end_line - chunk.start_line + 1 <= MAX_CHUNK_LINES
