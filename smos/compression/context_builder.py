from __future__ import annotations

import re
from typing import Optional

from smos.memory.schemas import CompressedContext
from smos.memory.vector_store import VectorStore
from smos.llm.summarizer import compress_memories_full

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "security": [
        "auth", "authentication", "authorization", "token", "jwt", "oauth",
        "ssl", "tls", "encrypt", "password", "credential", "secret",
        "permission", "rbac", "cors", "xss", "csrf", "injection",
    ],
    "api": [
        "api", "rest", "graphql", "endpoint", "route", "request", "response",
        "http", "grpc", "webhook", "openapi", "swagger", "fastapi", "flask",
    ],
    "infra": [
        "kubernetes", "k8s", "docker", "container", "pod", "deployment",
        "service", "ingress", "helm", "terraform", "aws", "gcp", "azure",
        "cloud", "nginx", "loadbalancer",
    ],
    "db": [
        "database", "postgres", "postgresql", "mysql", "sqlite", "redis",
        "mongodb", "sql", "index", "query", "migration", "schema", "orm",
        "vector", "faiss", "chroma",
    ],
    "frontend": [
        "react", "vue", "angular", "component", "css", "html", "dom",
        "ui", "frontend", "browser", "javascript", "typescript", "webpack",
    ],
}

_CANDIDATE_MULTIPLIER = 4


def _classify_domain(query: str) -> Optional[str]:
    q = query.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in q)
        if score > 0:
            scores[domain] = score
    if not scores:
        return None
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] >= 2 else None


def _term_overlap(query: str, candidate: str) -> float:
    query_tokens = set(re.findall(r"\w+", query.lower()))
    cand_tokens = set(re.findall(r"\w+", candidate.lower()))
    if not query_tokens:
        return 0.0
    return len(query_tokens & cand_tokens) / len(query_tokens)


def _rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """70% FAISS cosine score + 30% query term overlap."""
    if len(candidates) <= top_k:
        return candidates
    for c in candidates:
        faiss_score = c.get("score", max(0.0, 1.0 - c["distance"]))
        term_score = _term_overlap(query, c["content"])
        c["_rerank_score"] = 0.7 * faiss_score + 0.3 * term_score
    candidates.sort(key=lambda c: c["_rerank_score"], reverse=True)
    return candidates[:top_k]


def build_compressed_context(
    query: str,
    k: int,
    store: VectorStore,
) -> CompressedContext:
    candidate_k = max(k * _CANDIDATE_MULTIPLIER, 20)

    domain = _classify_domain(query)
    if domain:
        domain_tags = _DOMAIN_KEYWORDS[domain]
        retrieved = store.query_domain(query, k=candidate_k, domain_tags=domain_tags)
        if len(retrieved) < k:
            retrieved = store.query_domain(query, k=candidate_k, domain_tags=None)
    else:
        retrieved = store.query_domain(query, k=candidate_k, domain_tags=None)

    if not retrieved:
        return CompressedContext(
            summary="No relevant memories found.",
            sources=[],
            confidence=0.0,
            mode="uncertain",
        )

    reranked = _rerank(query, retrieved, top_k=k)

    summary, confidence, mode = compress_memories_full(reranked, query)
    return CompressedContext(
        summary=summary,
        sources=[item["id"] for item in reranked],
        confidence=confidence,
        mode=mode,
    )
