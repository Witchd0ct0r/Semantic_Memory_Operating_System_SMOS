"""Turns a stored benchmark run into what the Benchmark pages show: tiles, verdict, charts, comparison and details."""
from __future__ import annotations

import logging
from typing import Callable, Optional, TypeVar

from smos.admin.charts import Series, _fmt_tick, bar_chart, data_table, stacked_bar, xy_chart
from smos.bench.analysis import METRIC_DEFINITIONS, compare_runs, hnsw_verdict, key_metrics
from smos.bench.stats import find_knee, peak_throughput, sustainable_step

log = logging.getLogger("smos.bench")
T = TypeVar("T")
SCHEMA = 1
_STAGE_NAMES = (("embed_ms", "embedding"), ("vector_ms", "vector search"), ("bm25_ms", "BM25"),
                ("graph_ms", "graph expansion"), ("sqlite_ms", "SQLite"), ("fusion_ms", "fusion"))
_MB = 1024 * 1024
_CHART_ORDER = ("latency", "load_rps", "load_latency", "stages", "hnsw_time", "hnsw_recall", "ingest", "startup",
                "memory_disk", "mixed", "quality")
_SEGMENTS_LABEL = "segments in the database"


def _fmt(value, digits: int = 1, unit: str = "") -> str:
    if value is None:
        return "–"
    return f"{value:,.{digits}f} {unit}".strip()


def _yes_no(value) -> str:
    return "–" if value is None else ("yes" if value else "no")


def _get(data, *keys):
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def _change_text(row: dict) -> str:
    return "–" if row["change_pct"] is None else f"{row['change_pct']:+.1f}%"


def _points(entries: list[dict], x_key: str, *path) -> list[tuple[float, float]]:
    out = []
    for entry in entries:
        value = _get(entry, *path)
        if value is not None and entry.get(x_key) is not None:
            out.append((float(entry[x_key]), float(value)))
    return out


class _Charts:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def add(self, key: str, title: str, definition: str, svg: str, headers: list[str], rows: list[list[str]]) -> None:
        self.items[key] = {"key": key, "title": title, "definition": definition, "svg": svg,
                           "table": data_table(headers, rows)}

    def xy(self, key, title, definition, series, *, x_label, y_unit, ticks, log, headers, rows,
           reference=None, marker=None) -> None:
        if any(s.points for s in series):
            self.add(key, title, definition,
                     xy_chart(series, title=title, x_label=x_label, y_unit=y_unit, x_ticks=ticks, x_log=log,
                              reference=reference, marker=marker), headers, rows)

    def ordered(self) -> list[dict]:
        return [self.items[k] for k in _CHART_ORDER if k in self.items]


def _build_charts(result: dict, budget: float) -> list[dict]:
    charts = _Charts()
    scaling = [e for e in result.get("scaling") or [] if isinstance(e, dict) and e.get("segments") is not None]
    sweep = [s for s in result.get("sweep") or [] if isinstance(s, dict) and s.get("clients") is not None]
    if scaling:
        _size_charts(charts, scaling, budget)
    if sweep:
        _load_charts(charts, sweep, budget)
    mixed = result.get("mixed")
    if isinstance(mixed, dict) and mixed.get("read_p95_alone_ms") is not None:
        charts.add(
            "mixed", "Reads while a writer is ingesting",
            "The same read load twice: alone, then while one client keeps ingesting new documents. The p95 is the latency "
            "of the successful reads; reads that failed are counted in the errors column.",
            bar_chart([("alone", mixed["read_p95_alone_ms"]), ("with a writer", mixed.get("read_p95_with_writer_ms"))],
                      title="Reads while a writer is ingesting (p95)", unit="ms"),
            ["Case", "Read p95 ms (successful reads)", "Read req/s", "Read errors", "Client-limited"],
            [["alone", _fmt(mixed.get("read_p95_alone_ms")), _fmt(mixed.get("read_rps_alone")),
              str(mixed.get("read_errors_alone", "–")), _yes_no(mixed.get("read_client_limited_alone"))],
             ["with a writer", _fmt(mixed.get("read_p95_with_writer_ms")), _fmt(mixed.get("read_rps_with_writer")),
              str(mixed.get("read_errors_with_writer", "–")), _yes_no(mixed.get("read_client_limited_with_writer"))]])
    return charts.ordered()


def _size_charts(charts: _Charts, scaling: list[dict], budget: float) -> None:
    sizes = [float(e["segments"]) for e in scaling]
    log = len(sizes) > 1 and min(sizes) > 0 and max(sizes) / min(sizes) >= 20
    common = {"x_label": _SEGMENTS_LABEL, "ticks": sizes, "log": log}
    size_col = lambda e: f"{e['segments']:,}"  # noqa: E731

    charts.xy(
        "latency", "Query latency against database size",
        "One client, one query at a time. " + METRIC_DEFINITIONS["p50"] + " " + METRIC_DEFINITIONS["p95"] + " "
        + METRIC_DEFINITIONS["p99"],
        [Series("p50", _points(scaling, "segments", "latency", "query", "p50"), "s0"),
         Series("p95", _points(scaling, "segments", "latency", "query", "p95"), "s1"),
         Series("p99", _points(scaling, "segments", "latency", "query", "p99"), "s3")],
        y_unit="ms", reference=("budget", budget),
        headers=["Segments", "p50 ms", "p95 ms", "p99 ms", "Cold first query ms"],
        rows=[[size_col(e), _fmt(_get(e, "latency", "query", "p50")), _fmt(_get(e, "latency", "query", "p95")),
               _fmt(_get(e, "latency", "query", "p99")), _fmt(_get(e, "latency", "cold_ms"))] for e in scaling],
        **common)

    stage_rows = [(_fmt_tick(e["segments"]),
                   [(name, float((e.get("stages") or {}).get(key) or 0.0)) for key, name in _STAGE_NAMES])
                  for e in scaling]
    if any(sum(v for _n, v in parts) > 0 for _l, parts in stage_rows):
        charts.add("stages", "Where a query spends its time", METRIC_DEFINITIONS["stages"],
                   stacked_bar(stage_rows, title="Where a query spends its time", unit="ms"),
                   ["Segments"] + [name + " ms" for _k, name in _STAGE_NAMES] + ["Total ms"],
                   [[size_col(e)] + [_fmt(_get(e, "stages", key)) for key, _n in _STAGE_NAMES]
                    + [_fmt(_get(e, "stages", "total_ms"))] for e in scaling])

    charts.xy(
        "hnsw_time", "Exact search against HNSW: time per search",
        "Milliseconds for one vector search over the whole database: the exact index SMOS uses against an HNSW index "
        "built from the same vectors (M=32, efSearch=64).",
        [Series("exact search", _points(scaling, "segments", "hnsw", "flat_ms"), "s0"),
         Series("HNSW", _points(scaling, "segments", "hnsw", "query_ms"), "s1")],
        y_unit="ms",
        headers=["Segments", "Exact ms", "HNSW ms", "HNSW build s", "HNSW size"],
        rows=[[size_col(e), _fmt(_get(e, "hnsw", "flat_ms"), 2), _fmt(_get(e, "hnsw", "query_ms"), 2),
               _fmt(_get(e, "hnsw", "build_s")), _fmt((_get(e, "hnsw", "mem_bytes") or 0) / _MB, 0, "MB")]
              for e in scaling], **common)

    recall10 = [(x, y * 100) for x, y in _points(scaling, "segments", "hnsw", "recall10")]
    recall50 = [(x, y * 100) for x, y in _points(scaling, "segments", "hnsw", "recall50")]
    charts.xy(
        "hnsw_recall", "HNSW recall against exact search",
        METRIC_DEFINITIONS["recall10"] + " " + METRIC_DEFINITIONS["recall50"],
        [Series("recall@10", recall10, "s0"), Series("recall@50", recall50, "s1")], y_unit="%",
        headers=["Segments", "Recall@10", "Recall@50"],
        rows=[[size_col(e), _fmt((_get(e, "hnsw", "recall10") or 0) * 100 if _get(e, "hnsw", "recall10") is not None else None, 1, "%"),
               _fmt((_get(e, "hnsw", "recall50") or 0) * 100 if _get(e, "hnsw", "recall50") is not None else None, 1, "%")]
              for e in scaling], **common)

    charts.xy("ingest", "Ingest speed against database size", METRIC_DEFINITIONS["ingest_seg_per_s"],
              [Series("segments/s", _points(scaling, "segments", "ingest", "seg_per_s"), "s0")], y_unit="seg/s",
              headers=["Segments", "Segments/s"],
              rows=[[size_col(e), _fmt(_get(e, "ingest", "seg_per_s"))] for e in scaling], **common)
    charts.xy("startup", "Start-up time against database size", METRIC_DEFINITIONS["startup_s"],
              [Series("start-up", _points(scaling, "segments", "startup_s"), "s0")], y_unit="s",
              headers=["Segments", "Start-up s"], rows=[[size_col(e), _fmt(_get(e, "startup_s"))] for e in scaling],
              **common)

    def mb(path: str):
        return [(x, y / _MB) for x, y in _points(scaling, "segments", path)]

    charts.xy("memory_disk", "Memory and disk against database size",
              "Server memory (resident set) after the latency probe, and bytes on disk of the whole store and of "
              "the search index.",
              [Series("server memory", mb("rss_bytes"), "s0"), Series("disk (store)", mb("disk_bytes"), "s1"),
               Series("search index", mb("index_bytes"), "s3")], y_unit="MB",
              headers=["Segments", "Server memory MB", "Disk MB", "Index MB"],
              rows=[[size_col(e), _fmt((e.get("rss_bytes") or 0) / _MB, 0), _fmt((e.get("disk_bytes") or 0) / _MB, 0),
                     _fmt((e.get("index_bytes") or 0) / _MB, 0)] for e in scaling], **common)

    def pct(name: str):
        return [(x, y * 100) for x, y in _points(scaling, "segments", "quality", name)]

    charts.xy(
        "quality", "Retrieval quality on synthetic text against database size",
        "Needle queries with a known target segment, on generated (synthetic) text: " + METRIC_DEFINITIONS["hit1"]
        + " " + METRIC_DEFINITIONS["hit10"] + " " + METRIC_DEFINITIONS["mrr"]
        + " Synthetic text says how quality changes with size, not how good SMOS is on your documents.",
        [Series("hit@1", pct("hit1"), "s0"), Series("hit@10", pct("hit10"), "s1"), Series("MRR", pct("mrr"), "s3")],
        y_unit="%", headers=["Segments", "hit@1", "hit@10", "MRR"],
        rows=[[size_col(e)] + [_fmt(None if _get(e, "quality", k) is None else _get(e, "quality", k) * 100, 1, "%")
                               for k in ("hit1", "hit10", "mrr")] for e in scaling], **common)


def _load_charts(charts: _Charts, sweep: list[dict], budget: float) -> None:
    clients = [float(s["clients"]) for s in sweep]
    knee = find_knee(sweep)
    common = {"x_label": "parallel clients", "ticks": clients, "log": len(clients) > 1 and max(clients) / min(clients) >= 20}
    rows = [[str(s["clients"]), _fmt(s.get("rps")), _fmt(s.get("p50_ms")), _fmt(s.get("p95_ms")), _fmt(s.get("p99_ms")),
             str(s.get("errors", "–")), _fmt((s.get("client_cpu_share") or 0) * 100, 0, "%"),
             _fmt(s.get("server_cpu_percent"), 0, "%"), "yes" if s.get("client_limited") else "no"] for s in sweep]
    headers = ["Clients", "Requests/s", "p50 ms", "p95 ms", "p99 ms", "Errors", "Client CPU (of one core)",
               "Server CPU (% of one core)", "Client-limited"]
    charts.xy("load_rps", "Throughput against parallel clients",
              METRIC_DEFINITIONS["peak_rps"] + " " + METRIC_DEFINITIONS["knee"],
              [Series("requests/s", _points(sweep, "clients", "rps"), "s2")], y_unit="req/s",
              marker=(f"knee: {knee} clients", float(knee)) if knee else None, headers=headers, rows=rows, **common)
    charts.xy("load_latency", "Latency against parallel clients",
              "Request latency as more clients ask at once. " + METRIC_DEFINITIONS["p95"],
              [Series("p50", _points(sweep, "clients", "p50_ms"), "s0"),
               Series("p95", _points(sweep, "clients", "p95_ms"), "s1"),
               Series("p99", _points(sweep, "clients", "p99_ms"), "s3")], y_unit="ms",
              reference=("budget", budget), headers=headers, rows=rows, **common)


def _clean(result: dict) -> dict:
    """The structure the builders walk: lists of dicts and a dict, whatever junk was stored."""
    cleaned = dict(result)
    for key in ("scaling", "sweep"):
        value = result.get(key)
        cleaned[key] = [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []
    cleaned["mixed"] = result.get("mixed") if isinstance(result.get("mixed"), dict) else {}
    return cleaned


def _guard(build: Callable[[], T], default: T, problems: list[str]) -> T:
    """Stored numbers can be odd (a future format, a damaged row): a part that cannot be drawn is left out."""
    try:
        return build()
    except Exception:  # noqa: BLE001 - any failure here must degrade the report, never break the page
        log.exception("benchmark report part could not be built")
        problems.append("part")
        return default


def _tiles(result: dict, comparison: list[dict]) -> list[dict]:
    change = {r["key"]: r for r in comparison}
    sweep = result.get("sweep") or []
    peak, sustained = peak_throughput(sweep), sustainable_step(sweep, float(result.get("budget_ms") or 200.0))
    largest = (result.get("scaling") or [{}])[-1]
    metrics = key_metrics(result)
    p95 = _get(largest, "latency", "query")

    def tile(key, label, value, hint):
        row = change.get(key)
        return {"key": key, "label": label, "value": value, "hint": hint,
                "change": _change_text(row) if row else "–", "flag": row["flag"] if row else "n/a"}

    return [
        tile("peak_rps", "Peak throughput", _fmt(metrics["peak_rps"], 0, "req/s"),
             f"at {peak['clients']} clients" if peak else "no usable load step"),
        tile("sustainable_rps", "Sustainable throughput", _fmt(metrics["sustainable_rps"], 0, "req/s"),
             f"at {sustained['clients']} clients, p95 within budget" if sustained else "no step stayed within budget"),
        tile("query_p95_ms", "Query p95", _fmt(metrics["query_p95_ms"], 0, "ms"),
             f"p50 {_fmt(_get(p95, 'p50'), 0)}, p99 {_fmt(_get(p95, 'p99'), 0)} ms at {largest.get('segments', '?'):,} segments"
             if isinstance(largest.get("segments"), int) else ""),
        tile("ingest_seg_per_s", "Ingest", _fmt(metrics["ingest_seg_per_s"], 0, "seg/s"), "at the largest size"),
    ]


def _message(run: dict, result: Optional[dict], complete: bool) -> str:
    status = run.get("status")
    if status == "failed":
        return f"This run failed: {run.get('error') or 'no reason was recorded'}"
    if status == "cancelled":
        return "This run was cancelled before it finished."
    if status == "interrupted":
        return "SMOS stopped or restarted while this run was in progress."
    if status == "running":
        return "This run is still in progress."
    if isinstance(result, dict) and result.get("schema") not in (None, SCHEMA):
        return (f"This report was written by a newer SMOS (report format {result.get('schema')}) and cannot be drawn "
                "here; the stored run details are shown.")
    if not complete:
        return "This run stored only part of its measurements; the charts below show what exists."
    return ""


def build_report(run: dict, previous: Optional[dict]) -> dict:
    raw = run.get("result") if isinstance(run.get("result"), dict) else None
    known = raw is not None and raw.get("schema") == SCHEMA
    result = _clean(raw) if known else raw
    budget = _number(run.get("budget_ms"), 200.0)
    has_data = bool(known and result["scaling"])
    complete = bool(has_data and run.get("status") == "completed" and result["sweep"])
    previous_raw = previous.get("result") if previous and isinstance(previous.get("result"), dict) else None
    previous_result = _clean(previous_raw) if previous_raw and previous_raw.get("schema") == SCHEMA else None
    problems: list[str] = []

    def comparison_rows() -> list[dict]:
        return [{**row, "change_text": _change_text(row)} for row in compare_runs(result, previous_result)]

    comparison = _guard(comparison_rows, [], problems) if has_data else []
    tiles = _guard(lambda: _tiles(result, comparison), [], problems) if has_data else []
    verdict = _guard(lambda: hnsw_verdict(result, budget), None, problems) if has_data else None
    charts = _guard(lambda: _build_charts(result, budget), [], problems) if has_data else []

    message = _message(run, raw, complete)
    if problems:
        message = (message + " " if message else "") + "Some numbers in this report could not be read and were left out."
    hardware = (raw or {}).get("hardware") or run.get("hardware") or {}
    details = {
        "id": run.get("id"), "status": run.get("status"), "version": run.get("smos_version"),
        "preset": run.get("preset"), "budget_ms": budget, "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"), "seed": (raw or {}).get("seed"),
        "corpus_fingerprint": (raw or {}).get("corpus_fingerprint"), "cleanup": (raw or {}).get("cleanup"),
        "hardware": hardware if isinstance(hardware, dict) else {},
        "resources": (raw or {}).get("resources") if isinstance((raw or {}).get("resources"), dict) else {},
        "error": run.get("error") or "",
    }
    warnings = (raw or {}).get("warnings")
    return {
        "run": run, "complete": complete and not problems, "message": message, "tiles": tiles, "verdict": verdict,
        "charts": charts, "comparison": comparison, "details": details,
        "warnings": [str(w) for w in warnings] if isinstance(warnings, list) else [],
        "definitions": METRIC_DEFINITIONS,
    }


def _number(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number > 0 else default


def history_rows(runs: list[dict]) -> list[dict]:
    """One table row per run (newest first). 'vs previous' compares with the next older completed run of the same preset."""
    rows = []
    for position, run in enumerate(runs):
        raw = run.get("result") if isinstance(run.get("result"), dict) and run["result"].get("schema") == SCHEMA else None
        result = _clean(raw) if raw and raw.get("scaling") else None
        problems: list[str] = []
        metrics = _guard(lambda: key_metrics(result), {}, problems) if result else {}
        verdict = (_guard(lambda: hnsw_verdict(result, _number(run.get("budget_ms"), 200.0))["level"], None, problems)
                   if result else None)
        flag = "n/a"
        if result and run.get("status") == "completed":
            older = next((r for r in runs[position + 1:] if r.get("preset") == run.get("preset")
                          and r.get("status") == "completed" and isinstance(r.get("result"), dict)
                          and r["result"].get("schema") == SCHEMA and r["result"].get("scaling")), None)
            if older:
                rows_ = _guard(lambda: compare_runs(result, _clean(older["result"])), [], problems)
                flags = [c["flag"] for c in rows_ if c["flag"] != "n/a"]
                flag = ("regression" if "regression" in flags else "slower" if "slower" in flags
                        else "better" if flags and "better" in flags else "ok" if flags else "n/a")
        rows.append({
            "id": run.get("id"), "started_at": run.get("started_at"), "version": run.get("smos_version"),
            "preset": run.get("preset"), "status": run.get("status"), "error": run.get("error") or "",
            "peak_rps": metrics.get("peak_rps"), "query_p95_ms": metrics.get("query_p95_ms"),
            "verdict": verdict, "vs_previous": flag,
        })
    return rows
