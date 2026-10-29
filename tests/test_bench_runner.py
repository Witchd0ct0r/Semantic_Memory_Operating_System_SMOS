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
