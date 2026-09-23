"""Parser registry: maps a file extension to the parser that chunks it."""

from . import javascript, python
from .base import Chunk, Import, ParsedFile

_BY_EXTENSION = {ext: mod for mod in (python, javascript) for ext in mod.EXTENSIONS}

SUPPORTED_EXTENSIONS = tuple(_BY_EXTENSION)


def parser_for(path: str):
    for ext, module in _BY_EXTENSION.items():
        if path.endswith(ext):
            return module
    return None


def parse_file(path: str, source: str) -> ParsedFile | None:
    """Parse a file, or return None if no parser handles its language."""
    module = parser_for(path)
    return module.parse(path, source) if module else None


__all__ = ["Chunk", "Import", "ParsedFile", "SUPPORTED_EXTENSIONS", "parse_file", "parser_for"]
