from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = re.compile(r"^\s*(?:from|import)\s+(mcp|openai|smos\.llm|smos\.install|smos\.server)\b", re.M)


def test_no_mcp_or_openai_imports_in_package() -> None:
    offenders = [
        str(path.relative_to(_ROOT))
        for path in (_ROOT / "smos").rglob("*.py")
        if _FORBIDDEN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_removed_modules_are_gone() -> None:
    for relative in ["smos/server.py", "smos/install", "smos/llm", "smos/tools/file_tools.py"]:
        assert not (_ROOT / relative).exists(), relative


def test_pyproject_entry_point_and_deps() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "smos"
    assert project["version"] == "0.2.0"
    assert project["scripts"] == {"smos-server": "smos.launcher:main"}
    deps = " ".join(project["dependencies"]).lower()
    for wanted in ["fastapi", "uvicorn", "jinja2", "python-multipart", "itsdangerous"]:
        assert wanted in deps, wanted
    for banned in ["mcp", "openai"]:
        assert banned not in deps, banned
    assert "httpx" in " ".join(project["optional-dependencies"]["dev"]).lower()
    description = project["description"].lower()
    assert "mcp" not in description and "claude" not in description


def test_templates_are_package_data() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]
    assert "templates/*.html" in package_data["smos.admin"]
