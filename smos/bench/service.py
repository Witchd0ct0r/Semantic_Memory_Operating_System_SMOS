"""Starts, supervises and cleans up benchmark runs inside the live service.

A run is a separate process (`python -m smos.bench`) working in `<data dir>/bench-tmp/<run id>/`. While it runs, `/api/v1`
answers 503 (the flag `app.state.benchmark_active`, checked in the app's gate). Every way a run can end ends in the same
cleanup: processes killed, directory removed and checked, result stored, flag cleared.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import psutil

import smos
from smos.admin import benchmarks
from smos.admin.metrics import read_hardware
from smos.bench.presets import PRESETS, Preset

log = logging.getLogger("smos.bench")

BENCH_DIR = "bench-tmp"
HASH_FILE = "launch.sha256"        # also checked by smos.bench.runner.check_launch_authorisation
SECRET_ENV = "SMOS_BENCH_LAUNCH_SECRET"
MAX_BUDGET_MS = 60_000.0
_DRAIN_POLL_S = 0.02
_RMTREE_ATTEMPTS = 5

_rmtree = shutil.rmtree  # a module attribute so tests can make deletion fail


class BenchmarkRefused(Exception):
    """The run was not started; the message is safe to show an admin."""


def default_command(work_dir: Path, preset: str, budget_ms: float) -> list[str]:
    return [sys.executable, "-m", "smos.bench", "--work-dir", str(work_dir), "--preset", preset,
            "--budget-ms", repr(float(budget_ms))]


def _is_link(path: Path) -> bool:
    """A symbolic link or, on Windows, a junction or any other reparse point (which `Path.is_symlink` misses)."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _inside(candidate: str, base: Path) -> bool:
    """True when `candidate` is `base` or lies below it (whole path components, never a string prefix)."""
    try:
        path = Path(os.path.normcase(os.path.abspath(candidate)))
        root = Path(os.path.normcase(os.path.abspath(base)))
    except (OSError, ValueError):
        return False
    return path == root or root in path.parents


def safe_run_dir(data_dir: Path, run_id: int) -> Path:
    """`<data dir>/bench-tmp/<run id>`, and nothing else: the id must be a positive integer, bench-tmp must be a real
    directory directly inside the data directory (no link or junction), and so must the run directory if it exists."""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError(f"not a benchmark run id: {run_id!r}")
    data = Path(data_dir)
    base = data / BENCH_DIR
    if _is_link(base):
        raise ValueError("bench-tmp is a link")
    if base.exists() and base.resolve().parent != data.resolve():
        raise ValueError(f"{base} does not lie directly inside {data}")
    path = base / str(run_id)
    if path.exists() or _is_link(path):
        if _is_link(path) or path.resolve() != base.resolve() / str(run_id):
            raise ValueError(f"{path} does not lie inside {base}")
    return path


def requirements(preset: Preset) -> tuple[int, int]:
    """(free disk bytes, free memory bytes) a run of this preset needs; estimates with a margin. Memory counts the
    measuring process and the benchmark's own server, each holding a copy of the embedding model (the runner frees its copy
    before the server starts, but the allocator may not give it back at once)."""
    segments = preset.sizes[-1]
    return int(segments * 8 * 1024 * 1.5) + 200 * 1024 * 1024, int(2.2 * 1024 ** 3) + segments * 6 * 1024


def _default_free_disk(path: Path) -> int:
    return shutil.disk_usage(path).free


def _default_available_ram() -> int:
    live = read_hardware()
    return int(min(psutil.virtual_memory().available, live["mem_limit"] - live["mem_used"]))


def _tail(path: Path, limit: int = 600) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:]
    except OSError:
        return ""


@dataclass
class _Active:
    run_id: int
    preset: str
    budget_ms: float
    started_at: float
    run_dir: Path
    process: subprocess.Popen
    cancel: threading.Event
    finishing: bool = False


class BenchmarkService:
    def __init__(
        self,
        app,
        data_dir: Path,
        command_factory: Callable[[Path, str, float], list[str]] = default_command,
        drain_timeout: float = 30.0,
        free_disk: Callable[[Path], int] = _default_free_disk,
        available_ram: Callable[[], int] = _default_available_ram,
        wall_cap: Callable[[Preset], float] = lambda preset: preset.wall_cap_s,
        poll_interval: float = 0.25,
    ) -> None:
        self.app, self.data_dir = app, Path(data_dir)
        self._command, self._drain_timeout = command_factory, drain_timeout
        self._free_disk, self._available_ram = free_disk, available_ram
        self._wall_cap, self._poll = wall_cap, poll_interval
        self._lock = threading.RLock()
        self._active: Optional[_Active] = None

    # ------------------------------------------------------------------ public
    def start(self, admin: str, preset: str, budget_ms: float) -> int:
        spec = PRESETS.get(preset) if isinstance(preset, str) else None
        if spec is None:
            raise ValueError(f"unknown preset {preset!r}")
        try:
            budget = float(budget_ms)
        except (TypeError, ValueError):
            raise ValueError("the latency budget must be a number") from None
        if not (0 < budget <= MAX_BUDGET_MS):
            raise ValueError(f"the latency budget must be between 0 and {MAX_BUDGET_MS:.0f} ms")

        with self._lock:
            # the update interlock: checking for an update and setting our flag is one step, as it is in Updater.begin
            with getattr(self.app.state, "transition_lock", None) or nullcontext():
                self._check_can_start(spec)
                self.app.state.benchmark_active = True
            active = None
            try:
                self._drain()
                active = self._launch(admin, spec, budget)
                self._active = active
                try:
                    threading.Thread(target=self._watch, args=(active, spec), daemon=True,
                                     name="benchmark-watch").start()
                except BaseException as err:
                    self._abandon(active, f"the supervisor could not be started: {err}")
                    raise BenchmarkRefused(f"The benchmark could not be started: {err}") from None
            except BaseException:
                self._active = None
                self.app.state.benchmark_active = False
                raise
            return active.run_id

    def cancel(self) -> bool:
        with self._lock:
            active = self._active
        if active is None or active.cancel.is_set():
            return False
        active.cancel.set()
        return True

    def status(self) -> dict:
        with self._lock:
            active = self._active
        if active is None:
            return {"state": "idle", "run_id": None, "preset": None, "progress": None, "started_at": None}
        progress = None
        try:
            progress = json.loads((active.run_dir / "progress.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return {"state": "finishing" if active.finishing else "running", "run_id": active.run_id,
                "preset": active.preset, "progress": progress, "started_at": active.started_at}

    def describe_requirements(self, preset: Preset) -> dict:
        """What a run of `preset` needs and what is free now, for the Run panel."""
        need_disk, need_ram = requirements(preset)
        return {"need_disk": need_disk, "need_ram": need_ram,
                "free_disk": self._free_disk(self.data_dir), "free_ram": self._available_ram()}

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._active is None:
                    return True
            time.sleep(0.02)
        return False

    def close(self) -> None:
        self.cancel()
        self.wait_idle(15)

    def recover_on_startup(self) -> None:
        """After a crash or restart: no run can still be going, so mark stored ones interrupted and remove leftovers."""
        try:
            with self.app.state.admin_lock:
                interrupted = benchmarks.mark_running_interrupted(self.app.state.admin_db)
            if interrupted:
                log.warning("marked %d benchmark run(s) interrupted by a restart", interrupted)
        except Exception:
            log.exception("could not mark interrupted benchmark runs")
        base = self.data_dir / BENCH_DIR
        self._kill_orphans(base)
        if not base.is_dir():
            return
        for entry in base.iterdir():
            if not (entry.is_dir() and not entry.is_symlink() and entry.name.isdigit()):
                log.warning("leaving %s alone: not a benchmark run directory", entry)
                continue
            try:
                _rmtree(safe_run_dir(self.data_dir, int(entry.name)))
            except (ValueError, OSError):
                log.exception("could not remove the stale benchmark directory %s", entry)

    @staticmethod
    def _kill_orphans(base: Path) -> None:
        """A hard kill of SMOS leaves a run's processes behind (they are separate processes): end them.

        Only a Python process counts, and only one that is plainly a benchmark's: `python -m smos.bench` working inside
        bench-tmp/, or a SMOS server whose data directory is inside bench-tmp/. A process that merely mentions the path
        (a shell, an editor) is never touched, and neither is this process or any ancestor of it."""
        if not base.is_dir() or not any(base.iterdir()):
            return  # no run left anything behind, so there is nothing to look for (the scan reads every process)
        protected = {os.getpid()}
        try:
            protected.update(p.pid for p in psutil.Process().parents())
        except psutil.Error:
            pass
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            if proc.pid in protected:
                continue
            try:
                if not (proc.info["name"] or "").lower().startswith("python"):
                    continue
                cmd = proc.info["cmdline"] or []
                belongs = "smos.bench" in cmd and any(_inside(arg, base) for arg in cmd)
                if not belongs and "smos.api.main" in cmd:
                    data_dir = proc.environ().get("SMOS_DATA_DIR", "")
                    belongs = bool(data_dir) and _inside(data_dir, base)
                if not belongs:
                    continue
                victims = proc.children(recursive=True) + [proc]
            except psutil.Error:
                continue
            log.warning("ending orphaned benchmark process %d", proc.pid)
            for victim in victims:
                if victim.pid in protected:
                    continue
                try:
                    victim.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs(victims, timeout=5)

    # ------------------------------------------------------------------ starting
    def _check_can_start(self, spec: Preset) -> None:
        state = self.app.state
        if self._active is not None:
            raise BenchmarkRefused("A benchmark is already running.")
        with state.admin_lock:
            stored_running = benchmarks.has_running(state.admin_db)
        if stored_running:
            raise BenchmarkRefused("A benchmark is already running.")
        if getattr(state, "maintenance", False) or getattr(state, "closing", False):
            raise BenchmarkRefused("SMOS is restarting for an update; try again afterwards.")
        updater = getattr(getattr(state, "update", None), "updater", None)
        if updater is not None and updater.state() != "idle":
            raise BenchmarkRefused("An update is in progress; run the benchmark afterwards.")
        need_disk, need_ram = requirements(spec)
        have_disk = self._free_disk(self.data_dir)
        if have_disk < need_disk:
            raise BenchmarkRefused(f"Not enough free disk space: need about {need_disk // 2 ** 20} MB, "
                                   f"have {have_disk // 2 ** 20} MB.")
        have_ram = self._available_ram()
        if have_ram < need_ram:
            raise BenchmarkRefused(f"Not enough free memory: need about {need_ram // 2 ** 20} MB, "
                                   f"have {have_ram // 2 ** 20} MB.")

    def _drain(self) -> None:
        deadline = time.monotonic() + self._drain_timeout
        while getattr(self.app.state, "api_inflight", 0) > 0:
            if time.monotonic() >= deadline:
                raise BenchmarkRefused("API requests are still in progress; try again in a moment.")
            time.sleep(_DRAIN_POLL_S)

    def _launch(self, admin: str, spec: Preset, budget: float) -> _Active:
        with self.app.state.admin_lock:
            run_id = benchmarks.create_run(self.app.state.admin_db, preset=spec.name, budget_ms=budget,
                                           version=smos.__version__)
        run_dir = safe_run_dir(self.data_dir, run_id)
        try:
            run_dir.mkdir(parents=True)
            secret = secrets.token_urlsafe(32)
            (run_dir / HASH_FILE).write_text(hashlib.sha256(secret.encode()).hexdigest(), encoding="utf-8")
            env = dict(os.environ)
            env[SECRET_ENV] = secret
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            with open(run_dir / "runner.log", "ab") as log_file:
                process = subprocess.Popen(
                    self._command(run_dir, spec.name, budget), env=env, stdin=subprocess.DEVNULL,
                    stdout=log_file, stderr=log_file, creationflags=flags,
                    start_new_session=(os.name != "nt"))
        except BaseException as err:
            self._remove_dir(run_dir)
            with self.app.state.admin_lock:
                benchmarks.finish_run(self.app.state.admin_db, run_id, status="failed", result=None,
                                      hardware=None, error=f"could not start the runner: {err}")
            if isinstance(err, OSError):
                raise BenchmarkRefused(f"The benchmark could not be started: {err}") from None
            raise
        log.info("benchmark %d (%s) started by %s", run_id, spec.name, admin)
        return _Active(run_id, spec.name, budget, time.time(), run_dir, process, threading.Event())

    # ------------------------------------------------------------------ supervising and cleanup
    def _watch(self, active: _Active, spec: Preset) -> None:
        status, error, result, family = "failed", "", None, {}
        cleanup = "not run"
        try:
            deadline = active.started_at + self._wall_cap(spec)
            timed_out = False
            while True:
                self._remember_family(active.process, family)
                if active.process.poll() is not None:
                    break
                if active.cancel.is_set():
                    break
                if time.time() > deadline:
                    timed_out = True
                    break
                time.sleep(self._poll)
            active.finishing = True
            if active.process.poll() is None:
                self._kill_family(active.process, family)
            code = active.process.returncode
            result = self._read_json(active.run_dir / "result.json")
            if active.cancel.is_set():
                status = "cancelled"
            elif timed_out:
                error = f"The run exceeded its time limit ({self._wall_cap(spec) / 60:.0f} min); try a smaller preset."
            elif code == 0 and result is not None:
                status = "completed"
            else:
                error = (_tail(active.run_dir / "error.txt") or _tail(active.run_dir / "runner.log")
                         or f"The runner exited with code {code}.")
                if code is not None and f"code {code}" not in error:
                    error = f"The runner exited with code {code}. {error}".strip()
        except Exception as err:  # the supervisor itself failed: still clean up and end maintenance
            log.exception("benchmark supervisor error")
            status, error = "failed", f"supervisor error: {err}"
        finally:
            try:
                self._kill_family(active.process, family)
                cleanup = self._cleanup(active, family)
            except Exception as err:
                log.exception("benchmark cleanup error")
                cleanup = f"NOT verified: cleanup raised {err!r}"
            self._store(active, status, result, error, cleanup)
            with self._lock:
                self._active = None
                self.app.state.benchmark_active = False
            log.info("benchmark %d ended: %s; cleanup %s", active.run_id, status, cleanup)

    @staticmethod
    def _remember_family(process: subprocess.Popen, family: dict) -> None:
        try:
            parent = psutil.Process(process.pid)
            for proc in [parent, *parent.children(recursive=True)]:
                family.setdefault(proc.pid, proc)
        except psutil.Error:
            pass

    @staticmethod
    def _kill_family(process: subprocess.Popen, family: dict) -> None:
        BenchmarkService._remember_family(process, family)
        victims = [p for p in family.values() if _running(p)]
        for proc in victims:
            try:
                proc.terminate()
            except psutil.Error:
                pass
        _gone, alive = psutil.wait_procs(victims, timeout=3)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=5)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _abandon(self, active: "_Active", reason: str) -> None:
        """A launched run that nothing supervises: end its processes, delete its directory, record it as failed."""
        try:
            self._kill_family(active.process, {})
        except Exception:
            log.exception("could not stop the runner of benchmark %d", active.run_id)
        self._remove_dir(active.run_dir)
        try:
            with self.app.state.admin_lock:
                benchmarks.finish_run(self.app.state.admin_db, active.run_id, status="failed", result=None,
                                      hardware=None, error=reason)
        except Exception:
            log.exception("could not record the failed start of benchmark %d", active.run_id)

    def _remove_dir(self, run_dir: Path) -> Optional[str]:
        """Delete the run directory; returns an error text if something is left."""
        problem: Optional[str] = None
        for _attempt in range(_RMTREE_ATTEMPTS):
            try:
                _rmtree(run_dir)
                problem = None
                break
            except FileNotFoundError:
                problem = None
                break
            except OSError as err:
                problem = str(err)
                time.sleep(0.2)
        return problem

    def _cleanup(self, active: _Active, family: dict) -> str:
        leftovers = []
        problem = self._remove_dir(safe_run_dir(self.data_dir, active.run_id))
        if active.run_dir.exists():
            leftovers.append(f"directory {active.run_dir} still exists" + (f" ({problem})" if problem else ""))
        leftovers += [f"process {pid} still running" for pid, proc in family.items() if _running(proc)]
        if leftovers:
            log.warning("benchmark %d cleanup incomplete: %s", active.run_id, "; ".join(leftovers))
            return "NOT verified: " + "; ".join(leftovers)
        return "verified"

    def _store(self, active: _Active, status: str, result: Optional[dict], error: str, cleanup: str) -> None:
        stored = dict(result) if isinstance(result, dict) and status == "completed" else {"schema": 1, "partial": True}
        stored["cleanup"] = cleanup
        hardware = stored.get("hardware") if isinstance(stored.get("hardware"), dict) else None
        try:
            with self.app.state.admin_lock:
                benchmarks.finish_run(self.app.state.admin_db, active.run_id, status=status, result=stored,
                                      hardware=hardware, error=error)
                benchmarks.prune(self.app.state.admin_db)
            return
        except Exception as err:
            log.exception("could not store benchmark run %d", active.run_id)
            reason = f"The result could not be stored ({err}); the run itself ended as {status}. {error}".strip()
        for _attempt in range(3):  # leave a final row, so a stuck 'running' row never blocks the next run
            try:
                with self.app.state.admin_lock:
                    benchmarks.finish_run(self.app.state.admin_db, active.run_id, status="failed",
                                          result={"schema": 1, "partial": True, "cleanup": cleanup},
                                          hardware=None, error=reason)
                return
            except Exception:
                log.exception("could not record the final state of benchmark %d", active.run_id)
                time.sleep(0.1)

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None


def _running(proc: psutil.Process) -> bool:
    try:
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False
