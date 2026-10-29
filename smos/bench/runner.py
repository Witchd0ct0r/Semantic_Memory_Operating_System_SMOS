"""The benchmark runner: grows a throwaway store through the preset's sizes and measures it.

It is started only by the admin page (see `check_launch_authorisation`), never by hand. All its files live in the run
directory it is given; the live service reads `progress.json` while it runs and `result.json` when it ends.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import os
import secrets
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

import faiss
import psutil

from smos.admin.metrics import read_hardware
from smos.bench import httpload, phases
from smos.bench.corpus import CorpusBuilder
from smos.bench.presets import PRESETS, SMOKE, Preset
from smos.bench.stats import find_knee
from smos.launcher import write_json_atomic

RSS_KEY = "largest_post_probe_rss_bytes"  # one sample per size, after the latency probe: not a true peak
SECRET_ENV = "SMOS_BENCH_LAUNCH_SECRET"
HASH_FILE = "launch.sha256"
REFUSAL = "benchmarks are started from the admin page"
_PROFILE_PROBES, _PROFILE_REPEATS = 40, 2
_LATENCY_QUERIES = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def hardware_info() -> dict:
    from smos.memory.embeddings import _MODEL_NAME

    live = read_hardware()
    return {
        "cpu_model": _cpu_model(), "cpu_cores": psutil.cpu_count() or 1, "cpu_cores_allocated": live["cpu_cores"],
        "mem_total": live["mem_limit"], "platform": platform.platform(), "faiss": faiss.__version__,
        "python": platform.python_version(), "embedding_model": _MODEL_NAME,
    }


def release_model() -> None:
    """Free this process's copy of the embedding model, so it and the benchmark's server never both hold one."""
    from smos.memory import embeddings

    embeddings._get_model.cache_clear()
    gc.collect()


def _open_and_warm(data_dir: Path):
    """Open the store and load the model now, so loading it is not counted in the timed ingest."""
    from smos.memory.embeddings import warm_up

    store = phases.open_store(data_dir)
    warm_up()
    return store


class _Progress:
    def __init__(self, work_dir: Path, steps: int) -> None:
        self._path, self.steps, self.step = work_dir / "progress.json", steps, 0
        self.update("starting")

    def update(self, label: str, advance: bool = False) -> None:
        if advance:
            self.step += 1
        write_json_atomic(self._path, {
            "step": self.step, "steps": self.steps, "label": label,
            "percent": round(100 * self.step / self.steps) if self.steps else 100, "updated": time.time(),
        })


def run(work_dir: Path, preset: Preset, budget_ms: float, seed: int = 1) -> dict:
    """Measure SMOS on this machine with `preset`; returns the result and writes `work_dir/result.json`."""
    work_dir = Path(work_dir)
    data_dir = work_dir / "data"
    if data_dir.exists():
        raise RuntimeError(f"{data_dir} already exists; a run needs an empty directory")
    work_dir.mkdir(parents=True, exist_ok=True)

    progress = _Progress(work_dir, len(preset.sizes) * 4 + len(preset.clients) + 1)
    builder = CorpusBuilder(seed)
    probes = builder.probes(preset.probe_count, max_index=preset.sizes[0])
    warnings: list[str] = []
    scaling: list[dict] = []
    sweep: list[dict] = []
    mixed: dict = {}
    peak_rss = 0
    started_at = _now()

    for position, size in enumerate(preset.sizes):
        last = position == len(preset.sizes) - 1
        label = f"{size} segments"

        progress.update(f"{label}: ingesting")
        store = _open_and_warm(data_dir)
        try:
            ingest = phases.ingest_to(store, builder, size)
            progress.update(f"{label}: ingested", advance=True)
            if ingest["quarantined"]:
                warnings.append(f"{ingest['quarantined']} generated document(s) were quarantined at {size} segments")
            stages = phases.profile_stages(store, probes[:_PROFILE_PROBES], repeats=_PROFILE_REPEATS)
            progress.update(f"{label}: stage profile done", advance=True)
            hnsw = phases.compare_hnsw(store, probes)
            progress.update(f"{label}: HNSW comparison done", advance=True)
            segments = store.count()
        finally:
            store.close()
            release_model()
        footprint = phases.store_footprint(data_dir)

        progress.update(f"{label}: starting a server")
        server = httpload.BenchServer(data_dir, httpload.free_port())
        entry = {"segments": segments, "ingest": ingest, "stages": stages, "hnsw": hnsw, **footprint}
        try:
            entry["startup_s"] = server.start()
            process = psutil.Process(server.pid)
            entry["quality"] = httpload.quality_probes(server.base_url, server.token, probes)
            entry["latency"] = httpload.latency_probe(server.base_url, server.token, probes, _LATENCY_QUERIES)
            entry["rss_bytes"] = process.memory_info().rss
            peak_rss = max(peak_rss, entry["rss_bytes"])
            progress.update(f"{label}: latency measured", advance=True)

            if last:
                for clients in preset.clients:
                    progress.update(f"{label}: {clients} parallel client(s)")
                    process.cpu_percent(None)
                    step = httpload.run_step(server.base_url, server.token, clients, preset.step_seconds, probes)
                    step["server_cpu_percent"] = process.cpu_percent(None)
                    sweep.append(step)
                    if step["client_limited"]:
                        warnings.append(f"{clients} client(s): the load generator was the limit; step not counted")
                    progress.update(f"{label}: {clients} client(s) done", advance=True)
                knee = find_knee(sweep) or preset.clients[len(preset.clients) // 2]
                progress.update(f"{label}: mixed read/write load")
                mixed = httpload.mixed_step(server.base_url, server.token, knee, preset.step_seconds, probes, builder)
                progress.update("finishing", advance=True)
        finally:
            server.stop()
        scaling.append(entry)

    docs = CorpusBuilder(seed).next_documents(preset.sizes[-1])
    result = {
        "schema": 1, "preset": preset.name, "seed": seed, "budget_ms": budget_ms, "sizes": list(preset.sizes),
        "scaling": scaling, "sweep": sweep, "mixed": mixed, "hardware": hardware_info(),
        "resources": {RSS_KEY: peak_rss, "final_disk_bytes": scaling[-1]["disk_bytes"]},
        "corpus_fingerprint": hashlib.sha1(json.dumps(docs, sort_keys=True).encode()).hexdigest(),
        "warnings": warnings, "started_at": started_at, "finished_at": _now(),
    }
    write_json_atomic(work_dir / "result.json", result)
    progress.update("done")
    return result


def check_launch_authorisation(work_dir: Path, environ: Mapping[str, str]) -> None:
    """Exit with code 2 unless the admin page launched this process: right secret, run directory under bench-tmp/."""
    work_dir = Path(work_dir)
    expected = None
    try:
        expected = (work_dir / HASH_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    presented = environ.get(SECRET_ENV, "")
    ok = (
        work_dir.parent.name == "bench-tmp"
        and expected
        and presented
        and secrets.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), expected)
    )
    if not ok:
        print(REFUSAL, file=sys.stderr)
        raise SystemExit(2)


def start_parent_watchdog(parent_pid: int, on_orphan: Callable[[], None], interval: float = 2.0) -> threading.Thread:
    """Call `on_orphan` once the process that started us is gone (SMOS killed hard): nobody would stop this run."""

    def alive() -> bool:
        try:
            return psutil.pid_exists(parent_pid) and psutil.Process(parent_pid).status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def watch() -> None:
        while alive():
            time.sleep(interval)
        on_orphan()

    thread = threading.Thread(target=watch, daemon=True, name="parent-watchdog")
    thread.start()
    return thread


def _orphaned() -> None:
    for child in psutil.Process().children(recursive=True):
        try:
            child.kill()
        except psutil.Error:
            pass
    os._exit(130)


def main(argv: Optional[list[str]] = None, environ: Optional[Mapping[str, str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smos.bench", description="Started by the SMOS admin page only.")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--preset", required=True)
    parser.add_argument("--budget-ms", type=float, required=True)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    work_dir = Path(args.work_dir)
    try:
        check_launch_authorisation(work_dir, os.environ if environ is None else environ)
    except SystemExit as refused:
        return int(refused.code or 2)

    start_parent_watchdog(os.getppid(), _orphaned)

    def interrupted(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        preset = SMOKE if args.preset == "smoke" else PRESETS.get(args.preset)
        if preset is None:
            raise ValueError(f"unknown preset {args.preset!r}")
        run(work_dir, preset, args.budget_ms, args.seed)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        text = traceback.format_exc()
        print(text, file=sys.stderr)
        try:
            (work_dir / "error.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
        return 1
