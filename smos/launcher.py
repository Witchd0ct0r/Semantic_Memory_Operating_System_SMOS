"""Container supervisor for SMOS and the on-disk state of the self-updater.

Standard library only, on purpose. This file is part of the image and is never replaced by an
update overlay (the supervisor runs before any overlay is on the path), so the formats and
behaviour here are frozen until the image is rebuilt. A release that changes this file is
therefore reported as "needs rebuild" by the updater.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional

EXIT_RESTART = 75  # the app exits with this code to ask the supervisor for a restart
_BUILD_INFO_DEFAULT = "/opt/smos/build-info.json"


class UpdatePaths:
    """Where the updater keeps its files, under <data dir>/updates."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "updates"
        self.current = self.root / "current.json"
        self.current_prev = self.root / "current.json.prev"
        self.pending = self.root / "pending.json"
        self.last_result = self.root / "last-result.json"
        self.snapshot = self.root / "snapshot"

    def overlay_dir(self, version: str) -> Path:
        return self.root / "overlay" / version

    def staging_dir(self, version: str) -> Path:
        return self.root / f"staging-{version}"


def read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json_atomic(path: Path, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_build_info() -> dict:
    return read_json(Path(os.environ.get("SMOS_BUILD_INFO", _BUILD_INFO_DEFAULT))) or {}


def active_overlay(paths: UpdatePaths, build_id: Optional[str]) -> Optional[Path]:
    """The code overlay to run, or None for the code baked into the image.

    An overlay made for a different image build (the image was rebuilt since) or whose
    directory is gone is stale: it and its pointer are deleted."""
    current = read_json(paths.current)
    if current is None:
        return None
    version = str(current.get("version", ""))
    overlay = paths.overlay_dir(version) if version else None
    if overlay is not None and overlay.is_dir() and current.get("base_build_id") == build_id:
        return overlay
    if overlay is not None:
        shutil.rmtree(overlay, ignore_errors=True)
    paths.current.unlink(missing_ok=True)
    return None


def _data_entries(data_dir: Path) -> list[Path]:
    return [p for p in Path(data_dir).iterdir() if p.name != "updates"]


def _copy_entry(src: Path, dst_dir: Path) -> None:
    target = dst_dir / src.name
    if src.is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)


def snapshot_data(data_dir: Path, paths: UpdatePaths, token: str) -> None:
    """Replace the snapshot with a copy of everything in the data dir except updates/."""
    shutil.rmtree(paths.snapshot, ignore_errors=True)
    paths.snapshot.mkdir(parents=True)
    for entry in _data_entries(data_dir):
        _copy_entry(entry, paths.snapshot)
    (paths.snapshot / ".complete").write_text(token, encoding="utf-8")


def snapshot_matches(paths: UpdatePaths, token: str) -> bool:
    try:
        return (paths.snapshot / ".complete").read_text(encoding="utf-8") == token
    except OSError:
        return False


def restore_data(data_dir: Path, paths: UpdatePaths) -> None:
    """Put the data dir back exactly as the snapshot has it (updates/ is left alone)."""
    for entry in _data_entries(data_dir):
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in paths.snapshot.iterdir():
        if entry.name != ".complete":
            _copy_entry(entry, Path(data_dir))
