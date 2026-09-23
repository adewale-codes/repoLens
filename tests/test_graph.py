from pathlib import Path

from services.filter import skip_reason
from services.graph import build_graph
from services.parsers import Import


def edges_of(imports):
    edges, stats = build_graph(imports)
    return {(e.src, e.dst) for e in edges}, stats


def test_python_src_layout_absolute_relative_and_submodule_imports():
    imports = {
        "src/pkg/__init__.py": [Import(".core", ["Thing"])],
        "src/pkg/core.py": [Import("os"), Import("pkg.util", ["helper"])],
        "src/pkg/util.py": [],
        "src/pkg/sub/__init__.py": [],
        "src/pkg/sub/deep.py": [Import("..", ["util"]), Import("..core", ["Thing"])],
        "tests/test_core.py": [Import("pkg", ["core"])],
        "scripts/run.py": [Import("helpers")],
        "scripts/helpers.py": [],
    }
    edges, stats = edges_of(imports)
    assert edges == {
        ("src/pkg/__init__.py", "src/pkg/core.py"),
        ("src/pkg/core.py", "src/pkg/util.py"),
        ("src/pkg/sub/deep.py", "src/pkg/util.py"),
        ("src/pkg/sub/deep.py", "src/pkg/core.py"),
        ("tests/test_core.py", "src/pkg/core.py"),
        ("scripts/run.py", "scripts/helpers.py"),  # script importing its neighbour
    }
    assert stats["external"] == 1  # os


def test_js_relative_resolution_with_extension_and_index_probing():
    imports = {
        "lib/express.js": [Import("./router"), Import("./utils"), Import("debug")],
        "lib/router/index.js": [Import("../utils.js")],
        "lib/utils.js": [],
        "src/a.ts": [Import("./b.js"), Import("@/alias")],
        "src/b.ts": [],
        "index.js": [Import("./lib/express")],
        "test/app.js": [Import("..")],
    }
    edges, stats = edges_of(imports)
    assert edges == {
        ("index.js", "lib/express.js"),
        ("test/app.js", "index.js"),  # require('..') is the package root
        ("lib/express.js", "lib/router/index.js"),
        ("lib/express.js", "lib/utils.js"),
        ("lib/router/index.js", "lib/utils.js"),
        ("src/a.ts", "src/b.ts"),  # TS ESM: "./b.js" means b.ts
    }
    assert stats["external"] == 2  # debug, @/alias (tsconfig paths aren't resolved yet)


def test_filter_rules():
    assert skip_reason(Path("node_modules/x/index.js"), 10) == "excluded_dir"
    assert skip_reason(Path("package-lock.json"), 10) == "lockfile"
    assert skip_reason(Path("dist/app.min.js"), 10) == "excluded_dir"
    assert skip_reason(Path("static/app.min.js"), 10) == "generated"
    assert skip_reason(Path("README.md"), 10) == "unsupported_language"
    assert skip_reason(Path("src/big.py"), 10_000_000) == "too_large"
    assert skip_reason(Path("src/a.py"), 10, b"\x00\x01") == "binary"
    assert skip_reason(Path("src/a.js"), 9000, b"x" * 9000) == "minified"
    assert skip_reason(Path("src/a.py"), 12, b"x = 1\ny = 2\n") is None
