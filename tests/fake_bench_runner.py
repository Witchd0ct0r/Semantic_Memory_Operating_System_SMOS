"""A stand-in for `python -m smos.bench` used by the service tests. Behaviour is chosen with --mode."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--preset", required=True)
    parser.add_argument("--budget-ms", required=True)
    args = parser.parse_args()
    work = Path(args.work_dir)

    secret = os.environ.get("SMOS_BENCH_LAUNCH_SECRET", "")
    (work / "record.json").write_text(json.dumps({
        "argv": sys.argv, "has_secret": bool(secret),
        "secret_sha256": hashlib.sha256(secret.encode()).hexdigest() if secret else "",
    }), encoding="utf-8")
    (work / "progress.json").write_text(json.dumps(
        {"step": 1, "steps": 2, "label": "fake step", "percent": 50, "updated": time.time()}), encoding="utf-8")

    if args.mode == "die":
        os._exit(9)
    if args.mode == "crash":
        (work / "error.txt").write_text("boom: the fake runner crashed", encoding="utf-8")
        return 1
    if args.mode == "ok":
        from tests.benchdata import make_result

        time.sleep(0.3)
        (work / "data").mkdir(exist_ok=True)
        (work / "data" / "big.bin").write_bytes(b"x" * 1000)
        (work / "result.json").write_text(json.dumps(make_result()), encoding="utf-8")
        return 0
    if args.mode in ("sleep", "hang"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        (work / "grandchild.pid").write_text(str(child.pid), encoding="utf-8")
        time.sleep(600)
        return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
