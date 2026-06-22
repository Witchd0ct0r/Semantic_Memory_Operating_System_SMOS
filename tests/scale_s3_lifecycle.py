"""
S3 — Lifecycle Stress
Exercises 10,000 insertions triggering multiple hot/warm/cold promotion cycles.
Measures: duplicate reduction, tier distribution stability, lifecycle timing,
race condition detection, and memory corruption checks.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import statistics
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.vector_store import VectorStore
from memory.lifecycle import LifecycleManager
from memory.schemas import MemoryObject

# ---------------------------------------------------------------------------
# Corpus: 5 "true" topics × high-similarity variations (triggers deduplication)
# + 5 "unique" topics × diverse variations (should survive deduplication)
# ---------------------------------------------------------------------------
_TRUE_TOPICS = [
    "PostgreSQL uses MVCC for concurrent transaction isolation.",
    "Redis implements a single-threaded event loop for high throughput.",
    "Kubernetes schedules pods based on resource requests and node affinity.",
    "JWT tokens encode base64-signed claims for stateless authentication.",
    "FastAPI uses Python type hints for automatic OpenAPI documentation.",
]
_UNIQUE_TOPICS = [
    "Gradient descent minimizes the loss function using parameter gradients.",
    "React reconciles the virtual DOM to minimize actual DOM mutations.",
    "TLS 1.3 eliminates RSA key exchange in favor of Diffie-Hellman.",
    "GitHub Actions triggers CI workflows on push and pull request events.",
    "Service mesh sidecars intercept all inbound and outbound pod traffic.",
]

TOTAL_INSERTS = 10_000
# Each "true" topic gets 40% of inserts across slight paraphrases
# Each "unique" topic gets 12% of inserts as distinct variations


def _gen_text(i: int) -> str:
    bucket = i % 10
    variant = i // 10
    if bucket < 5:
        base = _TRUE_TOPICS[bucket]
        return f"{base} [note: variation {variant} for benchmarking purposes]"
    else:
        base = _UNIQUE_TOPICS[bucket - 5]
        return f"{base} [variant {variant}, system={i}]"


# ---------------------------------------------------------------------------
# Instrumented lifecycle tracking
# ---------------------------------------------------------------------------
lifecycle_starts: list[float] = []
lifecycle_ends: list[float] = []
lifecycle_lock = threading.Lock()
insert_errors: list[str] = []


class InstrumentedLifecycle(LifecycleManager):
    def on_insert(self, insert_count: int) -> None:
        with lifecycle_lock:
            lifecycle_starts.append(time.perf_counter())
        threading.Thread(
            target=self._timed_run_cycle,
            daemon=True,
            name=f"lifecycle-{insert_count}",
        ).start()

    def _timed_run_cycle(self) -> None:
        t0 = time.perf_counter()
        super()._run_cycle()
        t1 = time.perf_counter()
        with lifecycle_lock:
            lifecycle_ends.append(t1 - t0)


# ---------------------------------------------------------------------------
# Run the stress
# ---------------------------------------------------------------------------
print(f"S3 Lifecycle Stress: {TOTAL_INSERTS:,} insertions")
print("Setting up store and lifecycle manager...")

data_dir = Path(tempfile.mkdtemp(prefix="s3_"))
store = VectorStore(persist_path=data_dir)
lifecycle = InstrumentedLifecycle(store)
store._lifecycle_callback = lifecycle.on_insert

print("Starting insertion run...")
t_run_start = time.perf_counter()

insert_times: list[float] = []
checkpoint_counts: dict[int, int] = {}  # insert_num -> store count

REPORT_INTERVAL = 1000

for i in range(TOTAL_INSERTS):
    text = _gen_text(i)
    try:
        t0 = time.perf_counter()
        store.store(MemoryObject(type="doc", content=text, timestamp=datetime.utcnow()))
        insert_times.append((time.perf_counter() - t0) * 1000)
    except Exception as exc:
        insert_errors.append(f"Insert {i}: {type(exc).__name__}: {exc}")

    if (i + 1) % REPORT_INTERVAL == 0:
        checkpoint_counts[i + 1] = store.count()
        elapsed = time.perf_counter() - t_run_start
        rate = (i + 1) / elapsed
        print(f"  [{i+1:>6,}] store.count={store.count():>6,}  "
              f"elapsed={elapsed:.1f}s  rate={rate:.1f} docs/s  "
              f"lifecycle_cycles={len(lifecycle_ends)}")

t_run_end = time.perf_counter()
total_run_sec = t_run_end - t_run_start

# Wait for any in-flight lifecycle threads
print("Waiting for lifecycle threads to complete...")
time.sleep(3)

# ---------------------------------------------------------------------------
# Final metrics
# ---------------------------------------------------------------------------
final_count = store.count()
dedup_removed = TOTAL_INSERTS - insert_errors.__len__() - final_count

# Tier distribution
tier_rows = store._db.execute("SELECT tier, COUNT(*) FROM memories GROUP BY tier").fetchall()
tier_dist = {row[0]: row[1] for row in tier_rows}

# Lifecycle timing
lc_times = lifecycle_ends[:]
lc_count = len(lc_times)

print("\n=== S3 LIFECYCLE STRESS RESULTS ===")
print(f"\n--- Insertion ---")
print(f"  Target inserts:   {TOTAL_INSERTS:,}")
print(f"  Actual inserts:   {TOTAL_INSERTS - len(insert_errors):,}")
print(f"  Insert errors:    {len(insert_errors)}")
print(f"  Total time:       {total_run_sec:.1f}s")
print(f"  Throughput:       {(TOTAL_INSERTS / total_run_sec):.1f} docs/s")

if insert_times:
    sv = sorted(insert_times)
    n = len(sv)
    print(f"  Insert avg:       {statistics.mean(sv):.1f}ms")
    print(f"  Insert p50:       {sv[n//2]:.1f}ms")
    print(f"  Insert p95:       {sv[int(n*0.95)]:.1f}ms")
    print(f"  Insert p99:       {sv[int(n*0.99)]:.1f}ms")
    print(f"  Insert max:       {sv[-1]:.1f}ms")

print(f"\n--- Deduplication ---")
print(f"  Docs inserted:    {TOTAL_INSERTS - len(insert_errors):,}")
print(f"  Docs remaining:   {final_count:,}")
print(f"  Docs removed:     {dedup_removed:,}")
dup_pct = round(dedup_removed / max(TOTAL_INSERTS, 1) * 100, 1)
print(f"  Reduction rate:   {dup_pct}%")
true_dups = TOTAL_INSERTS * 0.40  # 5 near-duplicate clusters × 40% each
print(f"  Expected near-dup content: ~{int(true_dups):,} docs across 5 clusters")

print(f"\n--- Tier Distribution ---")
total_tiered = sum(tier_dist.values())
for tier in ["hot", "warm", "cold"]:
    count = tier_dist.get(tier, 0)
    pct = round(count / max(total_tiered, 1) * 100, 1)
    print(f"  {tier:<5}: {count:>6,} ({pct}%)")

print(f"\n--- Lifecycle Cycles ---")
expected_cycles = TOTAL_INSERTS // 50
print(f"  Expected cycles:  {expected_cycles:,}")
print(f"  Completed cycles: {lc_count:,}")
if lc_times:
    sv_lc = sorted(lc_times)
    n_lc = len(sv_lc)
    print(f"  Cycle avg time:   {statistics.mean(sv_lc)*1000:.1f}ms")
    print(f"  Cycle p95 time:   {sv_lc[int(n_lc*0.95)]*1000:.1f}ms")
    print(f"  Cycle max time:   {sv_lc[-1]*1000:.1f}ms")

print(f"\n--- Race Condition Check ---")
# Verify SQLite integrity
integrity = store._db.execute("PRAGMA integrity_check").fetchone()[0]
print(f"  SQLite integrity: {integrity}")
print(f"  Insert errors:    {len(insert_errors)}")
if insert_errors:
    for e in insert_errors[:5]:
        print(f"    {e}")
consistency = store.count() == store._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
print(f"  FAISS/SQLite sync: {'OK' if consistency else 'MISMATCH'}")

print(f"\n--- Checkpoint Counts ---")
for k, v in sorted(checkpoint_counts.items()):
    print(f"  After {k:>6,} inserts: store.count()={v:,}")

print("\nS3 complete.")
