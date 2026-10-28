from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from smos.update import manifest

_ROOT = Path(__file__).resolve().parents[1]
_HAS_GIT = shutil.which("git") is not None


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_blob_id_matches_git() -> None:
    assert manifest.blob_id(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
    assert manifest.blob_id(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


@pytest.mark.skipif(not _HAS_GIT, reason="git not installed")
def test_tree_id_matches_git_write_tree(tmp_path) -> None:
    root = tmp_path / "t"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a-b").mkdir()          # sorts between "a" and "a.txt" only if directories sort as "name/"
    (root / "a.txt").write_text("one\n")
    (root / "a" / "b" / "deep.bin").write_bytes(bytes(range(256)))
    (root / "a" / "x.py").write_text("print(1)\n")
    (root / "a-b" / "f").write_text("f")
    (root / "z").write_text("")
    (root / "empty").mkdir()        # git does not track empty directories
    gitdir = tmp_path / "gitdir"   # kept outside the hashed directory
    base = ["--git-dir", str(gitdir), "--work-tree", str(root)]
    _git("init", "-q", "--bare", str(gitdir), cwd=tmp_path)
    _git(*base, "-c", "core.autocrlf=false", "add", "-A", cwd=tmp_path)
    assert manifest.tree_id(root) == _git(*base, "write-tree", cwd=tmp_path)


def _clean(paths: list[str]) -> bool:
    if not _HAS_GIT:
        return False
    try:
        return _git("status", "--porcelain", "--", *paths, cwd=_ROOT) == ""
    except subprocess.CalledProcessError:
        return False


@pytest.mark.skipif(not (_ROOT / ".git").exists(), reason="not a git checkout")
def test_repo_rebuild_files_match_git_head() -> None:
    """Guards against line-ending and .dockerignore drift between the files and what GitHub reports."""
    if not _clean(list(manifest.REBUILD_PATHS)):
        pytest.skip("rebuild-relevant files have uncommitted changes")
    computed = manifest.build_manifest({p: _ROOT / p for p in manifest.REBUILD_PATHS})
    for path in manifest.REBUILD_PATHS:
        assert computed[path] == _git("rev-parse", f"HEAD:{path}", cwd=_ROOT), path


def test_differing_reports_changed_and_missing() -> None:
    image = {"Dockerfile": "a", "compose.yaml": "b", "models": "c"}
    target = {"Dockerfile": "a", "compose.yaml": "CHANGED", "pyproject.toml": "new"}
    assert manifest.differing(image, target) == ["compose.yaml", "models", "pyproject.toml"]
    assert manifest.differing(image, dict(image)) == []


def test_write_build_info_has_random_build_id_and_manifest(tmp_path) -> None:
    f = tmp_path / "Dockerfile"
    f.write_text("FROM x\n")
    out = tmp_path / "info.json"
    first = manifest.write_build_info(out, {"Dockerfile": f})
    second = manifest.write_build_info(out, {"Dockerfile": f})
    on_disk = json.loads(out.read_text())
    assert on_disk["manifest"] == {"Dockerfile": manifest.blob_id(b"FROM x\n")}
    assert first["build_id"] != second["build_id"] and on_disk["build_id"] == second["build_id"]
    assert len(on_disk["build_id"]) == 32


def test_cli_writes_build_info(tmp_path) -> None:
    f = tmp_path / "pyproject.toml"
    f.write_text("[project]\n")
    d = tmp_path / "models"
    d.mkdir()
    (d / "m.bin").write_bytes(b"1")
    out = tmp_path / "bi.json"
    code = manifest.main(["--out", str(out), f"pyproject.toml={f}", f"models={d}"])
    assert code == 0
    info = json.loads(out.read_text())
    assert set(info["manifest"]) == {"pyproject.toml", "models"}
    assert info["manifest"]["models"] == manifest.tree_id(d)
