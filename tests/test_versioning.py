from __future__ import annotations

import tomllib
from pathlib import Path

import smos

_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_takes_its_version_from_the_package() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in data["project"]
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "smos.__version__"}


def test_rebuild_files_carry_no_version_number() -> None:
    """The rebuild fingerprint covers these files, so a version literal in any of them would make every
    release look like it needs a rebuilt image (the v0.3.0 defect)."""
    from smos.update.manifest import REBUILD_PATHS

    for name in REBUILD_PATHS:
        path = _ROOT / name
        if path.is_dir():
            continue
        assert smos.__version__ not in path.read_text(encoding="utf-8"), name


def test_compose_image_tag_is_not_versioned() -> None:
    lines = [l.strip() for l in (_ROOT / "compose.yaml").read_text(encoding="utf-8").splitlines()]
    assert "image: smos:${SMOS_IMAGE_TAG:-latest}" in lines


def test_rebuild_files_name_no_stale_smos_version() -> None:
    """Catches a version left behind from an earlier release, not only today's."""
    import re

    pattern = re.compile(r"smos[:\- ]v?\d+\.\d+(\.\d+)?", re.I)
    for name in ("Dockerfile", "compose.yaml", "smos/launcher.py"):
        found = pattern.search((_ROOT / name).read_text(encoding="utf-8"))
        assert found is None, (name, found and found.group(0))


def test_release_that_only_bumps_the_version_needs_no_rebuild(tmp_path) -> None:
    from smos.update import manifest

    def snapshot(version: str) -> dict[str, str]:
        root = tmp_path / version
        (root / "smos").mkdir(parents=True)
        (root / "smos" / "__init__.py").write_text(f'__version__ = "{version}"\n')
        entries = {}
        for name in manifest.REBUILD_PATHS:
            source = _ROOT / name
            if source.is_dir():
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            entries[name] = target
        return manifest.build_manifest(entries)

    assert manifest.differing(snapshot("0.3.1"), snapshot("0.3.2")) == []


def test_health_reports_the_loaded_code_version(api, monkeypatch) -> None:
    client, _h, _app = api
    monkeypatch.setattr(smos, "__version__", "9.9.9")
    assert client.get("/api/v1/health").json()["version"] == "9.9.9"


def test_gitattributes_pins_lf_for_rebuild_files() -> None:
    lines = (_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for name in ("Dockerfile", "compose.yaml", "pyproject.toml", "smos/launcher.py"):
        assert any(line.startswith(name) and "eol=lf" in line for line in lines), name
