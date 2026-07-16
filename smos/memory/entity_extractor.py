from __future__ import annotations

import ast
import re
from typing import NamedTuple

_BACKTICK_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{1,60})`")
_MAX_ENTITIES = 50


class Entity(NamedTuple):
    name: str
    kind: str  # "function" | "class" | "import" | "reference"


def extract_entities(content: str) -> list[Entity]:
    """Extract code/concept entities from memory content.

    Two independent, best-effort sources:
      - AST walk (functions, classes, top-level import targets) — only
        fires when content parses as valid Python; yields nothing otherwise.
      - Backtick-wrapped spans (`` `like_this` ``) — works on any content,
        matching this project's own memory-file convention of naming code
        symbols in backticks.

    Deduplicates by (kind, name); caps output at 50 entities per call.
    """
    entities: list[Entity] = []
    seen: set[tuple[str, str]] = set()

    def _add(name: str, kind: str) -> None:
        key = (kind, name)
        if key in seen or len(entities) >= _MAX_ENTITIES:
            return
        seen.add(key)
        entities.append(Entity(name=name, kind=kind))

    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _add(node.name, "function")
            elif isinstance(node, ast.ClassDef):
                _add(node.name, "class")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    _add(alias.name.split(".")[0], "import")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    _add(node.module.split(".")[0], "import")
    except SyntaxError:
        pass

    for m in _BACKTICK_RE.finditer(content):
        _add(m.group(1), "reference")

    return entities
