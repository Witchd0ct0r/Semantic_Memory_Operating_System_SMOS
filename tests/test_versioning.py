from __future__ import annotations

import tomllib
from pathlib import Path

import smos

_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_version_equals_package_version() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == smos.__version__


def test_health_reports_the_loaded_code_version(api, monkeypatch) -> None:
    client, _h, _app = api
    monkeypatch.setattr(smos, "__version__", "9.9.9")
    assert client.get("/api/v1/health").json()["version"] == "9.9.9"


def test_gitattributes_pins_lf_for_rebuild_files() -> None:
    lines = (_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for name in ("Dockerfile", "compose.yaml", "pyproject.toml", "smos/launcher.py"):
        assert any(line.startswith(name) and "eol=lf" in line for line in lines), name
