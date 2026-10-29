"""Guards the guide against drift: links resolve, endpoints named exist, every endpoint is documented."""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GUIDE = sorted((_ROOT / "docs" / "guide").glob("*.md"))
_CHECKED = _GUIDE + [_ROOT / "README.md", _ROOT / "docs" / "API.md"]
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_OPERATION = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/api/v1/[A-Za-z0-9_/{}.\-]*[A-Za-z0-9_}])")
_NOT_OPERATIONS = {"/api/v1/docs"}


def _slug(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.M)}
    found |= set(re.findall(r'<a id="([^"]+)"', text))
    return found


def _normalise(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def test_guide_pages_exist() -> None:
    names = {p.name for p in _GUIDE}
    assert {"README.md", "concepts.md", "parser-author-guide.md", "query-guide.md", "agent-playbook.md",
            "troubleshooting-and-reporting.md", "known-limits-and-roadmap.md"} <= names


def test_internal_links_resolve() -> None:
    problems = []
    for page in _CHECKED:
        for target in _LINK.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            destination = page if not file_part else (page.parent / file_part).resolve()
            if not destination.exists():
                problems.append(f"{page.name}: {target} (missing file)")
            elif anchor and destination.suffix == ".md" and anchor not in _anchors(destination):
                problems.append(f"{page.name}: {target} (missing anchor)")
    assert problems == []


def _openapi_operations(api) -> set[tuple[str, str]]:
    _client, _headers, app = api
    return {(method.upper(), _normalise(path))
            for path, item in app.openapi()["paths"].items() for method in item}


def test_endpoints_named_in_the_guide_exist(api) -> None:
    real = _openapi_operations(api)
    problems = []
    for page in _GUIDE:
        for method, path in _OPERATION.findall(page.read_text(encoding="utf-8")):
            if path in _NOT_OPERATIONS or path == "/api/v1/openapi.json":
                continue
            if (method, _normalise(path)) not in real:
                problems.append(f"{page.name}: {method} {path}")
    assert problems == []


def test_every_endpoint_is_documented_in_the_guide(api) -> None:
    real = _openapi_operations(api)
    mentioned = {(m, _normalise(p)) for page in _GUIDE
                 for m, p in _OPERATION.findall(page.read_text(encoding="utf-8"))}
    assert sorted(real - mentioned) == []


def test_operation_pattern_knows_every_http_method() -> None:
    sample = "PATCH /api/v1/a, HEAD /api/v1/b, OPTIONS /api/v1/c, GET /api/v1/d/{id}"
    assert [m for m, _p in _OPERATION.findall(sample)] == ["PATCH", "HEAD", "OPTIONS", "GET"]
