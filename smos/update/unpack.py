"""Turn a downloaded release archive into a staged copy of the smos/ package, or refuse.

The archive is untrusted input. Nothing is ever extracted with tarfile's own extraction: every
member is checked, and only regular files of an allowed type under smos/ are written, by us.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path

from smos.update.config import UpdateConfig

ALLOWED_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".txt", ".md"}
_VERSION_LINE = re.compile(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$', re.M)
_MAX_MEMBERS = 100_000


class UnpackError(Exception):
    """The archive is not an acceptable release; the message is safe to show an admin."""


def _safe_parts(name: str) -> list[str]:
    if not name or name.startswith("/") or "\\" in name or ":" in name.split("/")[0]:
        raise UnpackError(f"unsafe path in archive: {name[:80]!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UnpackError(f"unsafe path in archive: {name[:80]!r}")
    return parts


def extract_smos(archive: Path, sha: str, dest: Path, config: UpdateConfig) -> str:
    """Write dest/smos/** from the archive and return the package's __version__ string."""
    dest = Path(dest)
    try:
        version = _extract(Path(archive), sha, dest, config)
    except UnpackError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except (tarfile.TarError, EOFError, zlib.error):
        shutil.rmtree(dest, ignore_errors=True)
        raise UnpackError("the release archive is not a valid tar.gz file") from None
    except OSError as err:
        shutil.rmtree(dest, ignore_errors=True)
        raise UnpackError(f"could not unpack the release archive: {err}") from None
    return version


def _extract(archive: Path, sha: str, dest: Path, config: UpdateConfig) -> str:
    top = None
    files = total = seen = 0
    dest_root = dest.resolve()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            seen += 1
            if seen > _MAX_MEMBERS:
                raise UnpackError("the release archive has too many entries")
            parts = _safe_parts(member.name)
            if not parts:
                continue
            if top is None:
                top = parts[0]
                if not top.endswith(f"-{sha}"):
                    raise UnpackError("unexpected top-level directory in the release archive")
            elif parts[0] != top:
                raise UnpackError("unexpected top-level directory in the release archive")
            rel = parts[1:]
            if not rel or rel[0] != "smos" or "__pycache__" in rel:
                continue
            target = dest.joinpath(*rel)
            if not target.resolve().is_relative_to(dest_root):
                raise UnpackError(f"unsafe path in archive: {member.name[:80]!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isreg():
                raise UnpackError(f"{'/'.join(rel)[:80]} is not a regular file")
            if target.suffix.lower() not in ALLOWED_SUFFIXES:
                raise UnpackError(f"file type not allowed in a release: {'/'.join(rel)[:80]}")
            files += 1
            if files > config.max_files:
                raise UnpackError("the release has too many files")
            if member.size > config.max_file_bytes:
                raise UnpackError(f"{'/'.join(rel)[:80]} is too large")
            total += member.size
            if total > config.max_total_bytes:
                raise UnpackError(f"the release unpacks to more than {config.max_total_bytes // (1024 * 1024)} MB")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            assert source is not None
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)

    init = dest / "smos" / "__init__.py"
    if not init.is_file():
        raise UnpackError("the release has no smos/__init__.py")
    match = _VERSION_LINE.search(init.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        raise UnpackError("smos/__init__.py has no __version__ = \"X.Y.Z\" line")
    return match.group(1)


_SMOKE = (
    "import importlib, os, sys\n"
    "staged, module = sys.argv[1], sys.argv[2]\n"
    "sys.path.insert(0, staged)\n"
    "loaded = importlib.import_module(module)\n"
    "assert os.path.realpath(loaded.__file__).startswith(os.path.realpath(staged)), 'imported from the wrong place'\n"
)


def smoke_check(staged: Path, module: str = "smos.api.app", timeout: float = 90.0) -> None:
    """Import the staged package in a throwaway interpreter (isolated mode, no data access), so a
    syntax error or a missing dependency is found before anything is switched."""
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _SMOKE, str(staged), module],
            capture_output=True, text=True, timeout=timeout, cwd=str(staged), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise UnpackError(f"importing the new code took longer than {timeout:g} s") from None
    if result.returncode != 0:
        lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
        raise UnpackError("the new code does not import: " + " | ".join(lines[-3:]))
