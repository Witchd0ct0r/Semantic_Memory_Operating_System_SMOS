"""Turns a benchmark result into the things a reader acts on: the HNSW verdict and the comparison with the last run."""
from __future__ import annotations

from typing import Optional

from smos.bench.stats import peak_throughput, project_size_at_budget, sustainable_step

PROJECTION_HEADROOM = 3.0   # the budget must be this many times the largest measured size away
VECTOR_SHARE_MIN = 0.25     # vector search must be at least this share of a query for HNSW to help
REGRESSION_PCT = 20.0       # more than this much worse is a regression
SLOWER_PCT = 10.0           # more than this much worse is "slower"
SMALL_RANGE_SEGMENTS = 10_000
DEFAULT_BUDGET_MS = 200.0

DELETE_CAVEAT = ("HNSW cannot delete vectors in place, and SMOS deletes and re-adds every segment of a document "
                 "on each re-ingest, so a switch needs soft deletion and background rebuilds.")

_STAGES = (
    ("embed_ms", "embedding"),
    ("vector_ms", "vector search"),
    ("bm25_ms", "BM25"),
    ("graph_ms", "graph expansion"),
    ("sqlite_ms", "SQLite"),
    ("fusion_ms", "fusion"),
)

METRIC_DEFINITIONS: dict[str, str] = {
    "peak_rps": "Highest requests per second any load step reached (steps limited by the test client are excluded).",
    "sustainable_rps": "Highest requests per second of a step with no errors and p95 latency within the budget.",
    "knee": "Client count after which one more step adds less than 10% throughput.",
    "query_p95_ms": "95% of queries finished faster than this, one client, at the largest database size.",
    "p50": "Median: half of the requests were faster than this.",
    "p95": "95% of the requests were faster than this.",
    "p99": "99% of the requests were faster than this.",
    "cold_ms": "The first query after the server started (model and index not yet warm).",
    "ingest_seg_per_s": "Segments stored per second while ingesting documents, at the largest size.",
    "startup_s": "Seconds from starting the server process until the health check answered.",
    "stages": "Average time per query spent in each step: embedding the question, vector search, BM25, graph expansion, "
              "SQLite lookups and fusion.",
    "recall10": "Share of the exact top-10 that HNSW also returned, averaged over the probe queries.",
    "recall50": "Share of the exact top-50 that HNSW also returned.",
    "hit1": "Share of needle queries whose target segment was the first result (synthetic text).",
    "hit10": "Share of needle queries whose target segment was in the top 10 (synthetic text).",
    "mrr": "Mean reciprocal rank of the target segment over the needle queries (synthetic text).",
}

# key -> (label, higher_is_better)
_COMPARED: dict[str, tuple[str, bool]] = {
    "peak_rps": ("Peak requests/s", True),
    "sustainable_rps": ("Sustainable requests/s", True),
    "query_p95_ms": ("Query p95 at the largest size (ms)", False),
    "ingest_seg_per_s": ("Ingest segments/s", True),
    "startup_s": ("Startup at the largest size (s)", False),
}


def _largest(result: dict) -> Optional[dict]:
    scaling = result.get("scaling") or []
    return scaling[-1] if scaling else None


def _p95(entry: dict) -> Optional[float]:
    return ((entry.get("latency") or {}).get("query") or {}).get("p95")


def key_metrics(result: dict) -> dict[str, Optional[float]]:
    largest = _largest(result) or {}
    budget = float(result.get("budget_ms") or DEFAULT_BUDGET_MS)
    sweep = result.get("sweep") or []
    peak, sustained = peak_throughput(sweep), sustainable_step(sweep, budget)
    return {
        "peak_rps": peak["rps"] if peak else None,
        "sustainable_rps": sustained["rps"] if sustained else None,
        "query_p95_ms": _p95(largest),
        "ingest_seg_per_s": (largest.get("ingest") or {}).get("seg_per_s"),
        "startup_s": largest.get("startup_s"),
    }


def compare_runs(current: dict, previous: Optional[dict]) -> list[dict]:
    now = key_metrics(current)
    before = key_metrics(previous) if previous else {}
    rows = []
    for key, (label, higher_is_better) in _COMPARED.items():
        cur, prev = now.get(key), before.get(key)
        row = {"key": key, "label": label, "previous": prev, "current": cur, "change_pct": None, "flag": "n/a"}
        if cur is not None and prev:
            change = (cur - prev) / prev * 100.0
            worse = -change if higher_is_better else change
            row["change_pct"] = change
            if worse > REGRESSION_PCT:
                row["flag"] = "regression"
            elif worse > SLOWER_PCT:
                row["flag"] = "slower"
            elif worse < -SLOWER_PCT:
                row["flag"] = "better"
            else:
                row["flag"] = "ok"
        rows.append(row)
    return rows


def _dominant_stage(stages: dict) -> tuple[Optional[str], float]:
    total = float(stages.get("total_ms") or 0.0)
    best, share = None, 0.0
    for key, name in _STAGES:
        value = float(stages.get(key) or 0.0)
        if total > 0 and value / total > share:
            best, share = name, value / total
    return best, share


def hnsw_verdict(result: dict, budget_ms: float) -> dict:
    scaling = [e for e in (result.get("scaling") or []) if _p95(e) is not None]
    largest = scaling[-1] if scaling else None
    if len(scaling) < 3 or largest is None:
        return {"level": "insufficient",
                "headline": "Not enough database sizes were measured to judge whether HNSW is needed. "
                            "Run a larger preset.",
                "facts": {"budget_ms": budget_ms, "caveat": DELETE_CAVEAT}}

    p95 = _p95(largest)
    stages = largest.get("stages") or {}
    total = float(stages.get("total_ms") or 0.0)
    vector_share = float(stages.get("vector_ms") or 0.0) / total if total > 0 else 0.0
    dominant, dominant_share = _dominant_stage(stages)
    projected = project_size_at_budget([(float(e["segments"]), float(_p95(e))) for e in scaling], budget_ms)
    hnsw = largest.get("hnsw") or {}
    facts = {
        "p95_ms": p95, "budget_ms": budget_ms, "segments": largest["segments"],
        "vector_share": vector_share, "dominant_stage": dominant, "dominant_share": dominant_share,
        "projected_segments": projected,
        "range_small": largest["segments"] < SMALL_RANGE_SEGMENTS,
        "recall10": hnsw.get("recall10"), "recall50": hnsw.get("recall50"),
        "hnsw_build_s": hnsw.get("build_s"), "hnsw_mem_bytes": hnsw.get("mem_bytes"),
        "caveat": DELETE_CAVEAT,
    }
    if p95 <= budget_ms:
        if projected is None or projected >= PROJECTION_HEADROOM * largest["segments"]:
            level = "not_needed"
            headline = (f"HNSW is not needed on this device: query p95 is {p95:.0f} ms at {largest['segments']} "
                        f"segments against a {budget_ms:.0f} ms budget, and vector search is "
                        f"{vector_share * 100:.0f}% of a query.")
        else:
            level = "watch"
            headline = (f"Keep an eye on it: p95 is within the budget at {largest['segments']} segments, but the "
                        f"measured growth reaches {budget_ms:.0f} ms at about {projected:.0f} segments.")
    elif vector_share >= VECTOR_SHARE_MIN:
        level = "consider"
        headline = (f"Consider HNSW: p95 is {p95:.0f} ms against a {budget_ms:.0f} ms budget and vector search "
                    f"takes {vector_share * 100:.0f}% of a query.")
    else:
        level = "not_hnsw"
        headline = (f"HNSW would not fix this: p95 is {p95:.0f} ms against a {budget_ms:.0f} ms budget, but vector "
                    f"search is only {vector_share * 100:.0f}% of a query; the time goes to {dominant} "
                    f"({dominant_share * 100:.0f}%).")
    if facts["range_small"]:
        headline += " The measured size range is small, so the projection is rough."
    return {"level": level, "headline": headline, "facts": facts}
