from __future__ import annotations

import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from smos.update.config import UpdateConfig
from smos.update.unpack import ALLOWED_SUFFIXES, UnpackError, extract_smos, smoke_check

SHA = "b" * 40
TOP = f"repo-{SHA}"
CONFIG = UpdateConfig(repo="o/r", ca_bundle=None)


def _tar(tmp_path: Path, members: list, name: str = "a.tar.gz") -> Path:
    """members: (path, bytes) for files, (path, 'dir'), or (path, ('symlink'|'hardlink'|'char'|'fifo', target))."""
    path = tmp_path / name
    with tarfile.open(path, "w:gz") as tar:
        for member_path, content in members:
            info = tarfile.TarInfo(member_path)
            if content == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif isinstance(content, tuple):
                kind, target = content
                info.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                             "char": tarfile.CHRTYPE, "fifo": tarfile.FIFOTYPE}[kind]
                info.linkname = target
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return path


def _good(extra: list | None = None, version: str = "0.3.0") -> list:
    return [
        (TOP, "dir"), (f"{TOP}/README.md", b"readme"), (f"{TOP}/Dockerfile", b"FROM x"),
        (f"{TOP}/smos", "dir"),
        (f"{TOP}/smos/__init__.py", f'"""SMOS."""\n\n__version__ = "{version}"\n'.encode()),
        (f"{TOP}/smos/api", "dir"),
        (f"{TOP}/smos/api/app.py", b"x = 1\n"),
        (f"{TOP}/smos/admin/templates/dashboard.html", b"<html></html>"),
        (f"{TOP}/tests/test_x.py", b"def test(): pass\n"),
        *(extra or []),
    ]


def test_extracts_smos_only_and_returns_version(tmp_path) -> None:
    dest = tmp_path / "staging"
    version = extract_smos(_tar(tmp_path, _good()), SHA, dest, CONFIG)
    assert version == "0.3.0"
    assert (dest / "smos" / "__init__.py").is_file()
    assert (dest / "smos" / "api" / "app.py").read_text() == "x = 1\n"
    assert (dest / "smos" / "admin" / "templates" / "dashboard.html").is_file()
    assert sorted(p.name for p in dest.iterdir()) == ["smos"]  # nothing from outside smos/


def test_pycache_is_skipped_silently(tmp_path) -> None:
    dest = tmp_path / "staging"
    extract_smos(_tar(tmp_path, _good([(f"{TOP}/smos/__pycache__/app.cpython-312.pyc", b"\0")])), SHA, dest, CONFIG)
    assert not (dest / "smos" / "__pycache__").exists()


@pytest.mark.parametrize("bad", [
    f"{TOP}/smos/../../escape.py", "/abs/smos/x.py", f"{TOP}/smos/a/../../../x.py", f"{TOP}\\smos\\x.py",
    f"{TOP}/../x", f"{TOP}/README/../../x"])
def test_path_traversal_and_absolute_names_rejected(tmp_path, bad) -> None:
    dest = tmp_path / "staging"
    with pytest.raises(UnpackError):
        extract_smos(_tar(tmp_path, _good([(bad, b"evil")])), SHA, dest, CONFIG)
    assert not dest.exists() and not (tmp_path / "escape.py").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "char", "fifo"])
def test_links_and_devices_under_smos_rejected(tmp_path, kind) -> None:
    dest = tmp_path / "staging"
    with pytest.raises(UnpackError, match="not a regular file"):
        extract_smos(_tar(tmp_path, _good([(f"{TOP}/smos/evil", (kind, "/etc/passwd"))])), SHA, dest, CONFIG)
    assert not dest.exists()


def test_a_symlink_outside_smos_is_ignored(tmp_path) -> None:
    dest = tmp_path / "staging"
    extract_smos(_tar(tmp_path, _good([(f"{TOP}/docs/link", ("symlink", "/etc/passwd"))])), SHA, dest, CONFIG)
    assert (dest / "smos" / "__init__.py").exists()


@pytest.mark.parametrize("top", ["repo-main", f"repo-{'c' * 40}", "repo", f"{SHA}x"])
def test_wrong_top_directory_rejected(tmp_path, top) -> None:
    members = [(m.replace(TOP, top), c) for m, c in _good()]
    with pytest.raises(UnpackError, match="top-level"):
        extract_smos(_tar(tmp_path, members), SHA, tmp_path / "s", CONFIG)


def test_two_top_directories_rejected(tmp_path) -> None:
    with pytest.raises(UnpackError, match="top-level"):
        extract_smos(_tar(tmp_path, _good([(f"other-{SHA}/smos/x.py", b"")])), SHA, tmp_path / "s", CONFIG)


def test_missing_package_or_version_rejected(tmp_path) -> None:
    no_pkg = [(TOP, "dir"), (f"{TOP}/README.md", b"x")]
    with pytest.raises(UnpackError, match="smos/__init__.py"):
        extract_smos(_tar(tmp_path, no_pkg, "a.tgz"), SHA, tmp_path / "s1", CONFIG)
    no_version = [(f"{TOP}/smos/__init__.py", b'"""no version"""\n')]
    with pytest.raises(UnpackError, match="__version__"):
        extract_smos(_tar(tmp_path, no_version, "b.tgz"), SHA, tmp_path / "s2", CONFIG)
    odd_version = [(f"{TOP}/smos/__init__.py", b'__version__ = "0.3"\n')]
    with pytest.raises(UnpackError, match="__version__"):
        extract_smos(_tar(tmp_path, odd_version, "c.tgz"), SHA, tmp_path / "s3", CONFIG)


def test_file_count_size_and_total_caps(tmp_path) -> None:
    many = _good([(f"{TOP}/smos/f{i}.py", b"") for i in range(20)])
    with pytest.raises(UnpackError, match="too many files"):
        extract_smos(_tar(tmp_path, many, "m.tgz"), SHA, tmp_path / "s1", replace(CONFIG, max_files=10))
    big = _good([(f"{TOP}/smos/big.py", b"x" * 2000)])
    with pytest.raises(UnpackError, match="too large"):
        extract_smos(_tar(tmp_path, big, "b.tgz"), SHA, tmp_path / "s2", replace(CONFIG, max_file_bytes=1000))
    total = _good([(f"{TOP}/smos/a.py", b"x" * 600), (f"{TOP}/smos/b.py", b"x" * 600)])
    with pytest.raises(UnpackError, match="unpacks to more than"):
        extract_smos(_tar(tmp_path, total, "t.tgz"), SHA, tmp_path / "s3", replace(CONFIG, max_total_bytes=1000))


@pytest.mark.parametrize("name", ["smos/tool.exe", "smos/lib.so", "smos/x.sh", "smos/noextension", "smos/a.pyc"])
def test_disallowed_suffix_rejected(tmp_path, name) -> None:
    with pytest.raises(UnpackError, match="file type"):
        extract_smos(_tar(tmp_path, _good([(f"{TOP}/{name}", b"x")])), SHA, tmp_path / "s", CONFIG)


def test_allowed_suffixes_are_the_documented_ones() -> None:
    assert ALLOWED_SUFFIXES == {".py", ".html", ".css", ".js", ".json", ".txt", ".md"}


def test_error_leaves_no_dest_directory(tmp_path) -> None:
    dest = tmp_path / "staging"
    with pytest.raises(UnpackError):
        extract_smos(_tar(tmp_path, _good([(f"{TOP}/smos/zzz.exe", b"x")])), SHA, dest, CONFIG)
    assert not dest.exists()


def test_not_a_tarball_is_an_unpack_error(tmp_path) -> None:
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"this is not gzip")
    with pytest.raises(UnpackError, match="not a valid"):
        extract_smos(bad, SHA, tmp_path / "s", CONFIG)


def _package(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def test_smoke_check_passes_for_importable_package(tmp_path) -> None:
    staged = _package(tmp_path / "ok", {"smos/__init__.py": '__version__ = "9.9.9"\n', "smos/probe.py": "VALUE = 1\n"})
    smoke_check(staged, module="smos.probe", timeout=60)


def test_smoke_check_reports_syntax_error(tmp_path) -> None:
    staged = _package(tmp_path / "bad", {"smos/__init__.py": "", "smos/probe.py": "def broken(:\n"})
    with pytest.raises(UnpackError, match="SyntaxError"):
        smoke_check(staged, module="smos.probe", timeout=60)


def test_smoke_check_reports_missing_dependency(tmp_path) -> None:
    staged = _package(tmp_path / "dep", {"smos/__init__.py": "", "smos/probe.py": "import a_module_nobody_has\n"})
    with pytest.raises(UnpackError, match="a_module_nobody_has"):
        smoke_check(staged, module="smos.probe", timeout=60)


def test_smoke_check_times_out(tmp_path) -> None:
    staged = _package(tmp_path / "slow", {"smos/__init__.py": "", "smos/probe.py": "import time\ntime.sleep(30)\n"})
    with pytest.raises(UnpackError, match="took longer"):
        smoke_check(staged, module="smos.probe", timeout=1)
