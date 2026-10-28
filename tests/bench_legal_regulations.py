"""
bench_legal_regulations.py — pre-Phase-1 vs current, on a regulation-text corpus.

Standalone script (NOT pytest-collected — see tests/test_exact_term_recall.py's
docstring for why files in tests/ named bench_*.py aren't picked up by `pytest
tests/`). Run directly: `python tests/bench_legal_regulations.py`

`tests/bench_full_evolution.py` answers "how much better is retrieval now vs
pre-Phase-1" using a code/identifier corpus (camelCase symbols, backtick-wrapped
entities). This script asks the same question for a domain with no code syntax:
ingested law/regulation text — realistic-shaped but entirely fictional citations,
defined terms, and provisions (not sourced from any real statute).

Two dimensions, same monkeypatch-based no-drift methodology as the other bench_
scripts in this repo:

  1. CITATION / DEFINED-TERM RECALL — the legal-text analogue of exact-term code
     recall. A citation like "Rule 7-C.1" or a defined term like "Covered
     Institution" plays the same role a function name like "AuthMiddleware" plays
     for code: an exact, compact token a lawyer would search for verbatim, that
     dense embeddings don't privilege over paraphrased-but-related prose. This
     tests whether BM25's contribution (Phase 1) transfers to legal text as-is —
     it should, since it's a lexical mechanism, not code-specific.

  2. CROSS-REFERENCE RECALL VIA THE ENTITY GRAPH — a provision that *defines* a
     term and a separate, topically-unrelated provision that *uses* the same term
     are linked only through that shared term, the same shape as the code-domain
     graph-rescue scenarios. Critically, `smos/memory/entity_extractor.py` only
     extracts entities two ways: a Python `ast` walk (never matches prose) and
     backtick-wrapped spans (`` `LikeThis` ``, matching *this project's own*
     memory-file convention, not any legal-document convention). Ingested law
     text is plain prose — no backticks, no Python syntax. This script therefore
     runs the graph dimension twice: once on unmarked prose (what a real legal
     corpus looks like today) and once with defined terms backtick-wrapped
     (simulating an ingestion-time convention that doesn't exist yet), to show
     whether the gap is "the graph doesn't help legal text" or "the graph would
     help, but nothing currently marks the terms it looks for."
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from smos.compression.context_builder import build_context
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.ingest.adapters.legal import LegalAdapter
from smos.ingest.transformers.markdown_transformer import markdown_to_payload
from smos.tools.ingest_tools import do_ingest_document_schema

# ---------------------------------------------------------------------------
# 1. Citation / defined-term recall
#
# Each citation gets a defining sentence plus ~40 paraphrase noise sentences
# that discuss the same regulatory topic without ever using the literal
# citation string — same corpus-difficulty rationale as
# test_exact_term_recall.py's noise generation (embeddings correlate too
# strongly with token overlap unless noise is dense and topically adjacent).
# ---------------------------------------------------------------------------

import itertools
import random


def _generate_noise(subjects, verbs, objects, tails, seed, n):
    rng = random.Random(seed)
    combos = list(itertools.product(subjects, verbs, objects, tails))
    rng.shuffle(combos)
    phrases = [f"{s} {v} {o} {t}" for s, v, o, t in combos]
    return list(dict.fromkeys(phrases))[:n]


_RECORD_RETENTION_NOISE = _generate_noise(
    subjects=["the compliance office", "the recordkeeping unit", "a covered entity",
              "the internal audit team", "the retention policy", "the filing department"],
    verbs=["must retain", "is required to preserve", "shall keep", "needs to archive",
           "is obligated to store", "must maintain"],
    objects=["transaction records", "client account statements", "trade confirmations",
             "settlement documentation", "audit trail data", "customer correspondence"],
    tails=["for the statutory retention period", "in a durable and accessible format",
           "subject to periodic examination", "for regulatory inspection purposes",
           "under the applicable recordkeeping rule", "for at least the minimum required duration"],
    seed=11, n=40,
)

_DISCLOSURE_NOISE = _generate_noise(
    subjects=["the issuer", "a related party", "the disclosure committee", "the board",
              "an affiliated entity", "the reporting entity"],
    verbs=["must disclose", "is required to report", "shall file notice of", "must publicly state",
           "needs to submit disclosure of", "is obligated to reveal"],
    objects=["related-party transactions", "conflicts of interest", "affiliate dealings",
             "material ownership stakes", "insider arrangements", "cross-holdings"],
    tails=["above the applicable threshold", "within the disclosure window",
           "to the relevant regulator", "in the periodic filing", "prior to closing",
           "as part of the annual report"],
    seed=12, n=40,
)

_BIOMETRIC_NOISE = _generate_noise(
    subjects=["the data processor", "the platform operator", "a third-party vendor",
              "the identity verification service", "the onboarding system", "the mobile app"],
    verbs=["may not transfer", "is prohibited from exporting", "must not relay",
           "shall not move", "cannot forward", "is barred from transmitting"],
    objects=["fingerprint templates", "facial recognition data", "voiceprint records",
             "biometric identifiers", "retina scan data", "biometric authentication logs"],
    tails=["outside the designated processing zone", "to a third country",
           "without explicit consent", "beyond the approved jurisdiction",
           "without an adequacy determination", "to an unauthorized recipient"],
    seed=13, n=40,
)

_CITATIONS = [
    (
        "Rule 7-C.1",
        "Rule 7-C.1 requires a covered entity to retain transaction records for seven years.",
        _RECORD_RETENTION_NOISE,
    ),
    (
        "Section 14.2(a)(3)",
        "Section 14.2(a)(3) sets the disclosure threshold for related-party transactions at ten percent.",
        _DISCLOSURE_NOISE,
    ),
    (
        "Article 9(2)(b)",
        "Article 9(2)(b) restricts the transfer of biometric data outside the processing zone.",
        _BIOMETRIC_NOISE,
    ),
]


def _seed_citation_corpus(store: VectorStore) -> dict[str, str]:
    ids: dict[str, str] = {}
    for citation, content, _noise in _CITATIONS:
        ids[citation] = store.store(MemoryObject(type="doc", content=content))
    for _citation, _content, noise in _CITATIONS:
        for n in noise:
            store.store(MemoryObject(type="doc", content=n))
    return ids


def _citation_recall(store: VectorStore, ids: dict[str, str]) -> float:
    hits = 0
    for citation, _content, _noise in _CITATIONS:
        result = build_context(citation, k=3, store=store)
        if ids[citation] in result.sources:
            hits += 1
    return hits / len(_CITATIONS)


def _citation_comparison(tmp_root: Path) -> tuple[float, float]:
    n_noise = sum(len(noise) for _, _, noise in _CITATIONS)
    print("=" * 72)
    print(f"1. CITATION / DEFINED-TERM RECALL ({len(_CITATIONS)} citations, k=3, "
          f"{n_noise} noise docs)")
    print("=" * 72)

    store = VectorStore(persist_path=tmp_root / "citations")
    ids = _seed_citation_corpus(store)

    real_bm25_query = store.bm25.query
    real_graph_traverse = store.graph.traverse
    store.bm25.query = lambda text, k: []
    store.graph.traverse = lambda *a, **k: []
    pre_phase1 = _citation_recall(store, ids)

    store.bm25.query = real_bm25_query
    store.graph.traverse = real_graph_traverse
    now = _citation_recall(store, ids)

    print(f"Pre-Phase-1 (vector-only): {pre_phase1:.2f} ({int(pre_phase1 * len(_CITATIONS))}/{len(_CITATIONS)})")
    print(f"Now (BM25 hybrid + graph): {now:.2f} ({int(now * len(_CITATIONS))}/{len(_CITATIONS)})")
    print()
    return pre_phase1, now


# ---------------------------------------------------------------------------
# 2. Cross-reference recall via the entity graph — with and without backticks
# ---------------------------------------------------------------------------

_NOISE_DOCS = [
    "Quarterly examination schedules are published on the regulator's website.",
    "The compliance calendar lists all upcoming filing deadlines.",
    "Staff training on the new reporting portal begins next month.",
    "The help desk handles technical issues with the e-filing system.",
    "Regional offices coordinate joint examinations twice per year.",
    "The public comment period for proposed rule changes lasts sixty days.",
]

_CROSS_REF_SCENARIOS = [
    {
        "name": "Covered Institution / stress test",
        "term": "Covered Institution",
        "query": "asset threshold definition",
        "anchor": "{term} means any entity that holds more than ten billion dollars in assets under management.",
        "target": "Annual stress-test results for a {term} must be submitted no later than March 31.",
    },
    {
        "name": "Qualified Custodian / audit",
        "term": "Qualified Custodian",
        "query": "custody standard requirements",
        "anchor": "A {term} is a bank or trust company that meets the segregation-of-assets standard.",
        "target": "Independent audits of a {term} occur on a surprise, unannounced basis.",
    },
    {
        "name": "Material Nonpublic Information / trading halt",
        "term": "Material Nonpublic Information",
        "query": "definition of confidential trading data",
        "anchor": "{term} refers to information not yet disseminated to the public that a reasonable investor would consider important.",
        "target": "Trading is halted for any employee in possession of {term} until public disclosure occurs.",
    },
]


def _fmt(text: str, term: str, backtick: bool) -> str:
    wrapped = f"`{term}`" if backtick else term
    return text.format(term=wrapped)


def _run_cross_ref_scenario(scenario: dict, tmp_root: Path, backtick: bool, graph_enabled: bool) -> bool:
    suffix = f"{scenario['name']}_{backtick}_{graph_enabled}"
    store = VectorStore(persist_path=tmp_root / f"xref_{abs(hash(suffix))}")
    if not graph_enabled:
        store.graph.traverse = lambda *a, **k: []

    target_id = store.store(MemoryObject(type="doc", content=_fmt(scenario["target"], scenario["term"], backtick)))
    store.store(MemoryObject(type="doc", content=_fmt(scenario["anchor"], scenario["term"], backtick)))
    for noise in _NOISE_DOCS:
        store.store(MemoryObject(type="doc", content=noise))

    result = build_context(scenario["query"], k=1, store=store)
    return target_id in result.sources


def _cross_ref_comparison(tmp_root: Path, backtick: bool) -> tuple[float, float]:
    n = len(_CROSS_REF_SCENARIOS)
    label = "backtick-marked terms" if backtick else "plain prose (no backticks)"
    print("=" * 72)
    print(f"2. CROSS-REFERENCE RECALL VIA GRAPH — {label} ({n} scenarios, k=1)")
    print("=" * 72)
    print(f"{'Scenario':<42} {'Graph off':<12} {'Graph on'}")
    print("-" * 72)

    off_hits = 0
    on_hits = 0
    for scenario in _CROSS_REF_SCENARIOS:
        off_ok = _run_cross_ref_scenario(scenario, tmp_root, backtick, graph_enabled=False)
        on_ok = _run_cross_ref_scenario(scenario, tmp_root, backtick, graph_enabled=True)
        off_hits += off_ok
        on_hits += on_ok
        print(f"{scenario['name']:<42} {'FOUND' if off_ok else 'missed':<12} {'FOUND' if on_ok else 'missed'}")

    off_recall = off_hits / n
    on_recall = on_hits / n
    print("-" * 72)
    print(f"Graph off: {off_recall:.2f} ({off_hits}/{n})")
    print(f"Graph on:  {on_recall:.2f} ({on_hits}/{n})")
    print()
    return off_recall, on_recall


def _run_adapter_scenario(scenario: dict, tmp_root: Path, graph_enabled: bool) -> bool:
    suffix = f"adapter_{scenario['name']}_{graph_enabled}"
    store = VectorStore(persist_path=tmp_root / f"adapter_{abs(hash(suffix))}")
    if not graph_enabled:
        store.graph.traverse = lambda *a, **k: []

    target_text = _fmt(scenario["target"], scenario["term"], backtick=False)
    anchor_text = _fmt(scenario["anchor"], scenario["term"], backtick=False)

    target_payload = markdown_to_payload(target_text, system="legal-bench", native_id=f"target-{suffix}")
    target_result = do_ingest_document_schema(target_payload, store, adapter=LegalAdapter())
    target_id = target_result["segment_ids"][0]

    anchor_payload = markdown_to_payload(anchor_text, system="legal-bench", native_id=f"anchor-{suffix}")
    do_ingest_document_schema(anchor_payload, store, adapter=LegalAdapter())

    for noise in _NOISE_DOCS:
        store.store(MemoryObject(type="doc", content=noise))

    result = build_context(scenario["query"], k=1, store=store)
    return target_id in result.sources


def _adapter_comparison(tmp_root: Path) -> tuple[float, float]:
    n = len(_CROSS_REF_SCENARIOS)
    print("=" * 72)
    print(f"3. CROSS-REFERENCE RECALL VIA LEGAL ADAPTER (schema-driven ingest, "
          f"plain prose, {n} scenarios, k=1)")
    print("=" * 72)
    print(f"{'Scenario':<42} {'Graph off':<12} {'Graph on'}")
    print("-" * 72)

    off_hits = 0
    on_hits = 0
    for scenario in _CROSS_REF_SCENARIOS:
        off_ok = _run_adapter_scenario(scenario, tmp_root, graph_enabled=False)
        on_ok = _run_adapter_scenario(scenario, tmp_root, graph_enabled=True)
        off_hits += off_ok
        on_hits += on_ok
        print(f"{scenario['name']:<42} {'FOUND' if off_ok else 'missed':<12} {'FOUND' if on_ok else 'missed'}")

    off_recall = off_hits / n
    on_recall = on_hits / n
    print("-" * 72)
    print(f"Graph off: {off_recall:.2f} ({off_hits}/{n})")
    print(f"Graph on:  {on_recall:.2f} ({on_hits}/{n})")
    print()
    return off_recall, on_recall


def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="smos_bench_legal_"))
    try:
        cite_pre, cite_now = _citation_comparison(tmp_root)
        plain_off, plain_on = _cross_ref_comparison(tmp_root, backtick=False)
        marked_off, marked_on = _cross_ref_comparison(tmp_root, backtick=True)
        adapter_off, adapter_on = _adapter_comparison(tmp_root)

        print("=" * 72)
        print("SUMMARY")
        print("=" * 72)
        print(f"Citation/defined-term recall:              {cite_pre:.0%} -> {cite_now:.0%}")
        print(f"Cross-reference recall, plain prose:       {plain_off:.0%} -> {plain_on:.0%} (graph off -> on)")
        print(f"Cross-reference recall, backtick-marked:   {marked_off:.0%} -> {marked_on:.0%} (graph off -> on)")
        print(f"Cross-reference recall, legal adapter:      {adapter_off:.0%} -> {adapter_on:.0%} (graph off -> on)")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
