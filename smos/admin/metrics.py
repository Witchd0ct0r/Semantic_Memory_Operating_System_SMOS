from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil

log = logging.getLogger("smos.metrics")

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_V1_UNLIMITED = 1 << 60  # cgroup v1 reports "no limit" as a number close to 2**63


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _int(text: Optional[str]) -> Optional[int]:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _host_cores() -> float:
    return float(psutil.cpu_count() or os.cpu_count() or 1)


def _cpu_percent(usage_us: Optional[int], cores: float, previous: Optional[dict], now: float) -> Optional[float]:
    if usage_us is None or not previous or previous.get("usage") is None:
        return None
    elapsed_us = (now - previous["t"]) * 1_000_000
    if elapsed_us <= 0:
        return None
    return max(0.0, (usage_us - previous["usage"]) / (elapsed_us * cores) * 100.0)


def read_hardware(
    cgroup_root: Path = _CGROUP_ROOT,
    previous: Optional[dict] = None,
    now: Optional[float] = None,
) -> dict:
    """The container's own CPU and memory view (cgroup v2, then v1), else the host's via psutil.

    `cpu_percent` is a share of the allocated cores and needs the `cpu_state` of a previous reading.
    """
    now = time.time() if now is None else now
    host_mem = psutil.virtual_memory()
    cgroup_root = Path(cgroup_root)

    used = limit = None
    cores = _host_cores()
    usage_us: Optional[int] = None

    if (cgroup_root / "memory.current").exists():  # cgroup v2
        used = _int(_read(cgroup_root / "memory.current"))
        raw_limit = _read(cgroup_root / "memory.max")
        limit = None if raw_limit in (None, "max") else _int(raw_limit)
        cpu_max = (_read(cgroup_root / "cpu.max") or "max 100000").split()
        if len(cpu_max) == 2 and cpu_max[0] != "max" and int(cpu_max[1]) > 0:
            cores = int(cpu_max[0]) / int(cpu_max[1])
        for line in (_read(cgroup_root / "cpu.stat") or "").splitlines():
            if line.startswith("usage_usec"):
                usage_us = _int(line.split()[1])
    elif (cgroup_root / "memory" / "memory.usage_in_bytes").exists():  # cgroup v1
        used = _int(_read(cgroup_root / "memory" / "memory.usage_in_bytes"))
        raw_limit = _int(_read(cgroup_root / "memory" / "memory.limit_in_bytes"))
        limit = raw_limit if raw_limit is not None and raw_limit < _V1_UNLIMITED else None
        quota = _int(_read(cgroup_root / "cpu" / "cpu.cfs_quota_us"))
        period = _int(_read(cgroup_root / "cpu" / "cpu.cfs_period_us"))
        if quota and quota > 0 and period and period > 0:
            cores = quota / period
        ns = _int(_read(cgroup_root / "cpuacct" / "cpuacct.usage"))
        usage_us = ns // 1000 if ns is not None else None
    else:  # no cgroup (development): the host's numbers
        used = int(host_mem.used)
        times = psutil.cpu_times()
        usage_us = int((times.user + times.system) * 1_000_000)

    if used is None:
        used = int(host_mem.used)
    if limit is None or limit > host_mem.total:
        limit = int(host_mem.total)

    return {
        "mem_used": int(used),
        "mem_limit": int(limit),
        "cpu_cores": float(cores),
        "cpu_percent": _cpu_percent(usage_us, cores, previous, now),
        "cpu_state": {"usage": usage_us, "t": now},
    }


class Sampler:
    """Writes one dashboard sample per interval. A failed sample is logged and skipped."""

    def __init__(self, app, interval: float = 60.0, cgroup_root: Path = _CGROUP_ROOT) -> None:
        self._app = app
        self._interval = interval
        self._cgroup_root = cgroup_root
        self._state: Optional[dict] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def sample_once(self) -> bool:
        try:
            state = self._app.state
            hardware = read_hardware(self._cgroup_root, self._state)
            self._state = hardware["cpu_state"]
            stats = state.store.stats()
            sizes = state.store.file_sizes()
            disk = shutil.disk_usage(state.data_dir)
            admin_path = Path(state.data_dir) / "admin.db"
            admin_bytes = sum(
                p.stat().st_size
                for p in (admin_path, admin_path.with_name("admin.db-wal"))
                if p.exists()
            )
            row = (
                datetime.now(timezone.utc).isoformat(),
                hardware["cpu_percent"], hardware["cpu_cores"],
                hardware["mem_used"], hardware["mem_limit"],
                disk.used, disk.total,
                sizes["store_db"], sizes["index"], admin_bytes,
                stats["documents"], stats["segments"],
            )
            with state.admin_lock:
                state.admin_db.execute(
                    "INSERT OR REPLACE INTO samples (ts, cpu_percent, cpu_cores, mem_used, mem_limit, "
                    "disk_used, disk_total, store_db_bytes, index_bytes, admin_db_bytes, documents, segments) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
                state.admin_db.commit()
            return True
        except Exception:  # noqa: BLE001 - metrics must never break the service
            log.exception("dashboard sample failed")
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="smos-sampler")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
