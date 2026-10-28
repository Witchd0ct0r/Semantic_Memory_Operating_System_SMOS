from __future__ import annotations

from pathlib import Path

from smos import launcher
from smos.launcher import UpdatePaths


def _setup_overlay(paths: UpdatePaths, version: str, build_id: str) -> Path:
    overlay = paths.overlay_dir(version)
    (overlay / "smos").mkdir(parents=True)
    (overlay / "smos" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    launcher.write_json_atomic(paths.current, {"version": version, "sha": "a" * 40,
                                               "base_build_id": build_id, "applied_at": "t"})
    return overlay


def test_active_overlay_returns_current_when_build_matches(tmp_path) -> None:
    paths = UpdatePaths(tmp_path)
    overlay = _setup_overlay(paths, "0.3.0", "b1")
    assert launcher.active_overlay(paths, "b1") == overlay


def test_active_overlay_none_and_cleaned_when_build_id_differs(tmp_path) -> None:
    paths = UpdatePaths(tmp_path)
    overlay = _setup_overlay(paths, "0.3.0", "old-build")
    assert launcher.active_overlay(paths, "new-build") is None
    assert not overlay.exists() and not paths.current.exists()


def test_active_overlay_none_when_overlay_dir_missing(tmp_path) -> None:
    paths = UpdatePaths(tmp_path)
    launcher.write_json_atomic(paths.current, {"version": "0.3.0", "base_build_id": "b1"})
    assert launcher.active_overlay(paths, "b1") is None
    assert not paths.current.exists()


def test_active_overlay_none_without_current_file(tmp_path) -> None:
    assert launcher.active_overlay(UpdatePaths(tmp_path), "b1") is None


def _data(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    (data / "sub").mkdir(parents=True)
    (data / "metadata.db").write_text("meta")
    (data / "faiss.index").write_text("index")
    (data / "sub" / "x.bin").write_text("x")
    (data / "updates").mkdir()
    (data / "updates" / "keep.txt").write_text("keep")
    return data


def test_snapshot_copies_all_but_updates_and_marks_complete(tmp_path) -> None:
    data = _data(tmp_path)
    paths = UpdatePaths(data)
    launcher.snapshot_data(data, paths, "tok")
    assert (paths.snapshot / "metadata.db").read_text() == "meta"
    assert (paths.snapshot / "sub" / "x.bin").read_text() == "x"
    assert not (paths.snapshot / "updates").exists()
    assert (paths.snapshot / ".complete").read_text() == "tok"
    assert launcher.snapshot_matches(paths, "tok") and not launcher.snapshot_matches(paths, "other")


def test_snapshot_replaces_previous_snapshot(tmp_path) -> None:
    data = _data(tmp_path)
    paths = UpdatePaths(data)
    launcher.snapshot_data(data, paths, "one")
    (data / "metadata.db").write_text("meta2")
    (data / "sub" / "x.bin").unlink()
    launcher.snapshot_data(data, paths, "two")
    assert (paths.snapshot / "metadata.db").read_text() == "meta2"
    assert not (paths.snapshot / "sub" / "x.bin").exists()
    assert launcher.snapshot_matches(paths, "two")


def test_restore_removes_files_created_after_snapshot_and_keeps_updates(tmp_path) -> None:
    data = _data(tmp_path)
    paths = UpdatePaths(data)
    launcher.snapshot_data(data, paths, "tok")
    (data / "metadata.db").write_text("migrated")
    (data / "new.db").write_text("new")
    (data / "sub" / "x.bin").unlink()
    launcher.restore_data(data, paths)
    assert (data / "metadata.db").read_text() == "meta"
    assert (data / "sub" / "x.bin").read_text() == "x"
    assert not (data / "new.db").exists()
    assert not (data / ".complete").exists()
    assert (data / "updates" / "keep.txt").read_text() == "keep"
    assert paths.snapshot.exists()


def test_write_json_atomic_roundtrip_and_read_json_tolerates_garbage(tmp_path) -> None:
    target = tmp_path / "a" / "x.json"
    launcher.write_json_atomic(target, {"k": 1})
    assert launcher.read_json(target) == {"k": 1}
    target.write_text("{not json")
    assert launcher.read_json(target) is None
    assert launcher.read_json(tmp_path / "missing.json") is None


def test_load_build_info_reads_env_path_and_defaults_to_empty(tmp_path, monkeypatch) -> None:
    info = tmp_path / "bi.json"
    launcher.write_json_atomic(info, {"build_id": "abc", "manifest": {"Dockerfile": "x"}})
    monkeypatch.setenv("SMOS_BUILD_INFO", str(info))
    assert launcher.load_build_info()["build_id"] == "abc"
    monkeypatch.setenv("SMOS_BUILD_INFO", str(tmp_path / "none.json"))
    assert launcher.load_build_info() == {}
