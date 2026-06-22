"""
S4 — Long-Term Compression Quality
Tests summarize_text() across document sizes (1KB, 5KB, 10KB, 50KB).
Measures: compression ratio, factual retention (keyword presence),
hallucination markers, and retrieval quality after compression.
LLM is tested live. Falls back gracefully if Ollama is unavailable.
"""
from __future__ import annotations

import sys
import tempfile
import time
import re
import statistics
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.summarizer import summarize_text
from memory.vector_store import VectorStore
from memory.schemas import MemoryObject
from memory.embeddings import embed_batch
import faiss
import numpy as np
import uuid as uuid_mod

# ---------------------------------------------------------------------------
# Document factory — generates documents of specified byte size
# ---------------------------------------------------------------------------
_BASE_FACTS = [
    ("PostgreSQL", "MVCC", "concurrent", "transaction", "isolation", "ACID"),
    ("Redis", "AOF", "persistence", "append-only", "durability", "snapshots"),
    ("Kubernetes", "pods", "scheduler", "resource", "limits", "namespaces"),
    ("TLS", "certificate", "ECDHE", "forward-secrecy", "handshake", "cipher"),
    ("FastAPI", "Pydantic", "OpenAPI", "async", "lifespan", "dependency"),
    ("Prometheus", "metrics", "scrape", "PromQL", "alertmanager", "recording"),
    ("React", "virtual-DOM", "reconciler", "fiber", "hooks", "state"),
    ("JWT", "claims", "signature", "HMAC-SHA256", "expiry", "issuer"),
]

_PARAGRAPH = """
{topic} is a widely used technology in modern distributed systems.
The core concept of {kw1} enables {kw2} operations to complete efficiently.
When engineers consider {kw3} requirements, they must account for {kw4}.
The design philosophy behind {topic} prioritizes {kw5} over simplicity.
Production deployments of {topic} commonly involve {kw6} configuration.
"""


def _make_document(target_bytes: int, fact_set: tuple) -> tuple[str, list[str]]:
    """Returns (document_text, list_of_expected_keywords)."""
    topic = fact_set[0]
    kw1, kw2, kw3, kw4, kw5, kw6 = fact_set[1], fact_set[2], fact_set[3], fact_set[4], fact_set[5], fact_set[1]
    base_para = _PARAGRAPH.format(
        topic=topic, kw1=kw1, kw2=kw2, kw3=kw3, kw4=kw4, kw5=kw5, kw6=kw6
    )
    # Pad to target size with topic-specific filler
    filler = (
        f"\nAdditional context: {topic} integrates with {kw1} via {kw2} mechanisms. "
        f"The {kw3} subsystem uses {kw4} protocols. Operators configure {kw5} and {kw6}. "
    )
    doc = base_para
    while len(doc.encode()) < target_bytes:
        doc += filler
    return doc[:target_bytes].decode("utf-8", errors="ignore") if isinstance(doc, bytes) else doc, list(fact_set)


TARGET_SIZES = [1_024, 5_120, 10_240, 51_200]  # 1KB, 5KB, 10KB, 50KB
SIZE_LABELS = ["1KB", "5KB", "10KB", "50KB"]

# ---------------------------------------------------------------------------
# Check LLM availability
# ---------------------------------------------------------------------------
print("Checking Ollama availability...")
llm_available = False
try:
    import openai
    client = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    resp = client.models.list()
    model_ids = [m.id for m in resp.data]
    llm_available = any("qwen" in m.lower() for m in model_ids)
    print(f"  Ollama models: {model_ids}")
    print(f"  qwen2.5:7b available: {llm_available}")
except Exception as e:
    print(f"  Ollama not available: {e}")
    print("  Running in MOCK mode — LLM compression tested via extractive fallback path only.")

# ---------------------------------------------------------------------------
# Run compression tests
# ---------------------------------------------------------------------------
results = []

for size_bytes, size_label in zip(TARGET_SIZES, SIZE_LABELS):
    fact_set = _BASE_FACTS[len(results) % len(_BASE_FACTS)]
    doc, keywords = _make_document(size_bytes, fact_set)
    actual_bytes = len(doc.encode("utf-8"))

    print(f"\n--- Testing {size_label} document ({actual_bytes:,} bytes) ---")
    print(f"    Keywords to retain: {keywords}")

    compress_times = []
    summaries = []

    # Run 3 times to check stability
    RUNS = 3
    for run in range(RUNS):
        t0 = time.perf_counter()
        try:
            summary = summarize_text(doc, context_hint=f"{size_label} document about {keywords[0]}")
        except Exception as e:
            summary = f"[ERROR: {e}]"
        elapsed_ms = (time.perf_counter() - t0) * 1000
        compress_times.append(elapsed_ms)
        summaries.append(summary)
        print(f"    Run {run+1}: {len(summary)} chars, {elapsed_ms:.0f}ms")

    # Metrics
    avg_summary_len = statistics.mean(len(s) for s in summaries)
    compression_ratio = round(actual_bytes / max(avg_summary_len, 1), 2)

    # Factual retention: how many keywords appear in at least one summary
    keyword_hits = []
    for kw in keywords:
        kw_lower = kw.lower().replace("-", " ")
        hits = sum(1 for s in summaries if kw_lower in s.lower() or kw.lower() in s.lower())
        keyword_hits.append(hits > 0)
    retention_rate = round(sum(keyword_hits) / len(keyword_hits), 3)

    # Stability: character-level overlap between runs
    if len(summaries) >= 2:
        set0 = set(summaries[0].lower().split())
        set1 = set(summaries[1].lower().split())
        overlap = len(set0 & set1) / max(len(set0 | set1), 1)
        stability = round(overlap, 3)
    else:
        stability = 1.0

    # Hallucination check: words in summary NOT present in source doc
    combined_source_words = set(re.findall(r'\b[a-z]{4,}\b', doc.lower()))
    hallucination_counts = []
    for s in summaries:
        s_words = set(re.findall(r'\b[a-z]{4,}\b', s.lower()))
        # Words in summary not in source (rough hallucination proxy)
        novel_words = s_words - combined_source_words
        # Filter common English stopwords that legitimately appear in summaries
        stopwords = {"this", "that", "with", "from", "have", "been", "they",
                     "will", "when", "what", "more", "also", "such", "than",
                     "like", "used", "uses", "into", "over", "about", "then",
                     "text", "provided", "above", "overall", "key", "main",
                     "here", "document", "section", "system", "technology",
                     "based", "note", "context", "using", "where", "these",
                     "there", "each", "their", "which", "both", "after",
                     "within", "through", "between", "across", "while"}
        novel_words -= stopwords
        hallucination_counts.append(len(novel_words))
    avg_novel = round(statistics.mean(hallucination_counts), 1)

    results.append({
        "size_label": size_label,
        "actual_bytes": actual_bytes,
        "avg_summary_chars": round(avg_summary_len, 0),
        "compression_ratio": compression_ratio,
        "retention_rate": retention_rate,
        "stability": stability,
        "avg_time_ms": round(statistics.mean(compress_times), 0),
        "p95_time_ms": round(sorted(compress_times)[int(len(compress_times)*0.95)], 0),
        "avg_novel_words": avg_novel,
        "keyword_hits": keyword_hits,
        "keywords": keywords,
    })

    print(f"    Compression ratio:  {compression_ratio}x")
    print(f"    Factual retention:  {retention_rate:.1%} ({sum(keyword_hits)}/{len(keywords)} keywords)")
    print(f"    Stability:          {stability:.3f}")
    print(f"    Novel words (proxy): {avg_novel}")
    print(f"    Avg time:           {statistics.mean(compress_times):.0f}ms")

# ---------------------------------------------------------------------------
# Post-compression retrieval quality
# ---------------------------------------------------------------------------
print("\n=== POST-COMPRESSION RETRIEVAL TEST ===")
comp_store = VectorStore(persist_path=Path(tempfile.mkdtemp(prefix="s4_")))

original_docs = []
compressed_docs = []
for r in results:
    fact_set = _BASE_FACTS[results.index(r) % len(_BASE_FACTS)]
    doc, _ = _make_document(r["actual_bytes"], fact_set)
    summary = summarize_text(doc[:1024], context_hint=fact_set[0])  # Use 1KB slice for speed
    original_docs.append(doc[:500])
    compressed_docs.append(summary)

# Store both and measure retrieval
from memory.embeddings import embed as _embed
import faiss as _faiss

for i, (orig, comp) in enumerate(zip(original_docs, compressed_docs)):
    comp_store.store(MemoryObject(type="doc", content=comp, timestamp=datetime.utcnow(),
                                  tags=[f"compressed,doc_{i}"]))

queries = [f["keywords"][0] for f in [
    {"keywords": kw} for kw in [r["keywords"] for r in results]
]]

hit = 0
for i, q in enumerate(queries):
    results_q = comp_store.query(q, k=3)
    retrieved_tags = [r["metadata"]["tags"] for r in results_q]
    if any(f"doc_{i}" in t for t in retrieved_tags):
        hit += 1

print(f"  Retrieval from compressed corpus: {hit}/{len(queries)} docs found in top-3")

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------
print("\n=== S4 COMPRESSION QUALITY RESULTS ===")
print(f"\n{'Size':<6}  {'Bytes':>8}  {'Summary':>8}  {'Ratio':>7}  "
      f"{'Retention':>10}  {'Stability':>10}  {'Novel':>6}  {'Avg(ms)':>8}")
print("-" * 80)
for r in results:
    print(f"{r['size_label']:<6}  {r['actual_bytes']:>8,}  "
          f"{int(r['avg_summary_chars']):>8,}  {r['compression_ratio']:>7.1f}x  "
          f"{r['retention_rate']:>10.1%}  {r['stability']:>10.3f}  "
          f"{r['avg_novel_words']:>6.1f}  {r['avg_time_ms']:>8.0f}")

print("\n=== KEY FINDINGS ===")
avg_retention = statistics.mean(r["retention_rate"] for r in results)
avg_compression = statistics.mean(r["compression_ratio"] for r in results)
avg_stability = statistics.mean(r["stability"] for r in results)
print(f"  Avg factual retention: {avg_retention:.1%}")
print(f"  Avg compression ratio: {avg_compression:.1f}x")
print(f"  Avg stability:         {avg_stability:.3f}")
print(f"  LLM mode:              {'LIVE' if llm_available else 'EXTRACTIVE FALLBACK'}")

# Flag degradation at 50KB
if len(results) >= 4:
    small = results[0]["retention_rate"]
    large = results[-1]["retention_rate"]
    if large < small - 0.1:
        print(f"  WARNING: Retention degrades at 50KB ({large:.1%} vs {small:.1%} at 1KB)")
    else:
        print(f"  Retention stable across sizes: {small:.1%} -> {large:.1%}")

print("\nS4 complete.")
