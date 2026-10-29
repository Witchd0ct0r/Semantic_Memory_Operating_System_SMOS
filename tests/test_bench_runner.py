from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import psutil
import pytest

from smos.bench import runner
from smos.bench.corpus import CorpusBuilder
from smos.bench.presets import SMOKE


@pytest.fixture(scope="module")
def smoke_run():
    base = Path(tempfile.mkdtemp(prefix="smosrun_"))
    work = base / "bench-tmp" / "1"
    work.mkdir(parents=True)
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    result = runner.run(work, SMOKE, 200.0, seed=5)
    after = {p.pid for p in psutil.Process().children(recursive=True)}
    yield result, work, after - before
    shutil.rmtree(base, ignore_errors=True)


def test_run_smoke_produces_complete_result(smoke_run) -> None:
    result, work, _leftover = smoke_run
    assert result["schema"] == 1 and result["preset"] == "smoke" and result["seed"] == 5
    assert result["budget_ms"] == 200.0 and result["sizes"] == [40, 90]
    assert len(result["scaling"]) == 2
    for entry, size in zip(result["scaling"], SMOKE.sizes):
        assert entry["segments"] >= size
        for key in ("ingest", "stages", "hnsw", "quality", "latency", "startup_s", "disk_bytes", "index_bytes",
                    "rss_bytes"):
            assert key in entry and entry[key] is not None, key
        assert entry["latency"]["query"]["n"] > 0 and entry["startup_s"] > 0
        assert entry["stages"]["total_ms"] > 0
    assert [s["clients"] for s in result["sweep"]] == [1, 2]
    assert all(s["requests"] > 0 for s in result["sweep"])
    assert {"read_p95_alone_ms", "read_p95_with_writer_ms", "writer_docs"} <= set(result["mixed"])
    assert {"cpu_model", "cpu_cores", "mem_total", "faiss", "python", "embedding_model"} <= set(result["hardware"])
    assert isinstance(result["warnings"], list) and result["started_at"] < result["finished_at"]
    json.dumps(result)  # must be serialisable
    assert json.loads((work / "result.json").read_text(encoding="utf-8"))["schema"] == 1


def test_corpus_fingerprint_matches_the_seeded_corpus(smoke_run) -> None:
    result, _work, _leftover = smoke_run
    docs = CorpusBuilder(5).next_documents(SMOKE.sizes[-1])
    expected = hashlib.sha1(json.dumps(docs, sort_keys=True).encode()).hexdigest()
    assert result["corpus_fingerprint"] == expected


def test_progress_file_reaches_100_percent(smoke_run) -> None:
    _result, work, _leftover = smoke_run
    progress = json.loads((work / "progress.json").read_text(encoding="utf-8"))
    assert progress["percent"] == 100 and progress["step"] == progress["steps"]


def test_runner_leaves_no_server_process(smoke_run) -> None:
    _result, _work, leftover = smoke_run
    assert leftover == set()


def test_run_refuses_a_work_dir_that_already_holds_data(tmp_path) -> None:
    (tmp_path / "data").mkdir()
    with pytest.raises(RuntimeError):
        runner.run(tmp_path, SMOKE, 200.0)


# ---- the command line only runs when the admin page launched it -----------------------------------------------

def _run_dir(tmp_path: Path, secret: str = "s3cret", parent: str = "bench-tmp") -> Path:
    work = tmp_path / parent / "1"
    work.mkdir(parents=True)
    (work / "launch.sha256").write_text(hashlib.sha256(secret.encode()).hexdigest(), encoding="utf-8")
    return work


def _args(work: Path, preset: str = "smoke") -> list[str]:
    return ["--work-dir", str(work), "--preset", preset, "--budget-ms", "200"]


def test_cli_refuses_without_launch_secret(tmp_path) -> None:
    work = _run_dir(tmp_path)
    assert runner.main(_args(work), environ={}) == 2
    assert sorted(p.name for p in work.iterdir()) == ["launch.sha256"]  # nothing written


def test_cli_refuses_wrong_secret(tmp_path) -> None:
    work = _run_dir(tmp_path)
    assert runner.main(_args(work), environ={"SMOS_BENCH_LAUNCH_SECRET": "nope"}) == 2
    assert sorted(p.name for p in work.iterdir()) == ["launch.sha256"]


def test_cli_refuses_work_dir_outside_bench_tmp(tmp_path) -> None:
    work = _run_dir(tmp_path, parent="elsewhere")
    assert runner.main(_args(work), environ={"SMOS_BENCH_LAUNCH_SECRET": "s3cret"}) == 2
    assert sorted(p.name for p in work.iterdir()) == ["launch.sha256"]


def test_cli_refuses_missing_hash_file(tmp_path) -> None:
    work = tmp_path / "bench-tmp" / "1"
    work.mkdir(parents=True)
    assert runner.main(_args(work), environ={"SMOS_BENCH_LAUNCH_SECRET": "s3cret"}) == 2


def test_cli_exit_code_and_error_file_on_failure(tmp_path) -> None:
    work = _run_dir(tmp_path)
    assert runner.main(_args(work, preset="nonsense"), environ={"SMOS_BENCH_LAUNCH_SECRET": "s3cret"}) == 1
    assert "nonsense" in (work / "error.txt").read_text(encoding="utf-8")


def test_check_launch_authorisation_accepts_matching_secret(tmp_path) -> None:
    work = _run_dir(tmp_path)
    runner.check_launch_authorisation(work, {"SMOS_BENCH_LAUNCH_SECRET": "s3cret"})  # does not raise


def test_parent_watchdog_fires_when_the_parent_dies() -> None:
    import subprocess
    import sys
    import threading

    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    fired = threading.Event()
    runner.start_parent_watchdog(parent.pid, fired.set, interval=0.05)
    assert not fired.wait(0.3)  # the parent is alive: nothing happens
    parent.kill()
    parent.wait(timeout=10)
    assert fired.wait(5)


def test_parent_watchdog_ignores_a_live_parent() -> None:
    import os
    import threading

    fired = threading.Event()
    runner.start_parent_watchdog(os.getpid(), fired.set, interval=0.05)
    assert not fired.wait(0.4)


def test_clean_run_has_an_empty_errors_section(smoke_run) -> None:
    # Regression guard for the shared-connection defect (docs/benchmark-verification.md section 8): a real server under
    # mixed read/write load used to answer an occasional query with 500 in about 3 runs of 10.
    result, _work, _leftover = smoke_run
    errors = result["errors"]
    assert {"total", "groups", "more_groups", "server"} <= set(errors)
    assert errors["total"] == 0 and errors["groups"] == [], errors
    assert not any("request(s) failed" in w for w in result["warnings"])


def test_failed_requests_reach_the_result_and_a_warning(monkeypatch, tmp_path) -> None:
    from smos.bench import httpload

    original = httpload.mixed_step

    def failing(*args, errors=None, **kwargs):
        errors.record("mixed: writer", "document ingest", "http 500", "synthetic failure", 1.0)
        return original(*args, errors=errors, **kwargs)

    monkeypatch.setattr(httpload, "mixed_step", failing)
    work = tmp_path / "bench-tmp" / "1"
    work.mkdir(parents=True)
    result = runner.run(work, SMOKE, 200.0, seed=5)
    assert result["errors"]["total"] >= 1
    assert any(g["example"] == "synthetic failure" for g in result["errors"]["groups"])
    assert any("request(s) failed" in w for w in result["warnings"])
