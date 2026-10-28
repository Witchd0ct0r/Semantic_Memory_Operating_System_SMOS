"""Git object ids of the files that, when they change, need a rebuilt image.

The image build records them next to a random build id (`/opt/smos/build-info.json`); the updater
compares them with the same entries of a release's commit (read from the GitHub trees API), so
"needs rebuild" is decided without downloading anything large.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Mapping, Optional, Sequence

REBUILD_PATHS = ("pyproject.toml", "Dockerfile", "compose.yaml", "models", "smos/launcher.py")


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def file_id(path: Path, normalize_eol: bool = False) -> str:
    """Blob id of a file. Text files are committed with LF (.gitattributes), so a checkout or build
    context with CRLF line endings must hash the same; binary files (the model) are hashed as they are."""
    data = Path(path).read_bytes()
    return blob_id(data.replace(b"\r\n", b"\n") if normalize_eol else data)


def _tree_entries(directory: Path) -> list[tuple[bytes, bytes, str]]:
    """(sort key, git entry bytes, ...) for each tracked-looking child; empty directories are skipped."""
    entries: list[tuple[bytes, bytes]] = []
    for child in directory.iterdir():
        name = child.name.encode("utf-8")
        if child.is_dir():
            if not any(child.iterdir()):
                continue
            digest = bytes.fromhex(tree_id(child))
            entries.append((name + b"/", b"40000 " + name + b"\0" + digest))
        elif child.is_file():
            digest = bytes.fromhex(file_id(child))
            entries.append((name, b"100644 " + name + b"\0" + digest))
    entries.sort(key=lambda item: item[0])
    return entries  # type: ignore[return-value]


def tree_id(directory: Path) -> str:
    body = b"".join(entry for _key, entry in _tree_entries(Path(directory)))
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def build_manifest(entries: Mapping[str, Path]) -> dict[str, str]:
    """repo path -> git object id (blob for a file, tree for a directory)."""
    return {name: tree_id(path) if Path(path).is_dir() else file_id(path, normalize_eol=True)
            for name, path in entries.items()}


def differing(image: Mapping[str, str], target: Mapping[str, str]) -> list[str]:
    """Repo paths whose id differs between the image and the target, or that exist on only one side."""
    return sorted(name for name in set(image) | set(target) if image.get(name) != target.get(name))


def write_build_info(out: Path, entries: Mapping[str, Path]) -> dict:
    info = {"build_id": uuid.uuid4().hex, "manifest": build_manifest(entries)}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Write the image's build-info.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("entries", nargs="+", help="REPO_PATH=LOCAL_PATH")
    args = parser.parse_args(argv)
    mapping = {}
    for item in args.entries:
        repo_path, _, local = item.partition("=")
        if not repo_path or not local:
            parser.error(f"expected REPO_PATH=LOCAL_PATH, got {item!r}")
        mapping[repo_path] = Path(local)
    write_build_info(args.out, mapping)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
