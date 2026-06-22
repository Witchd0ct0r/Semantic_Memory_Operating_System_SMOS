"""
Compression Quality Benchmark
Tasks: embedding cosine sim, compression ratio, multi-doc retrieval quality.
"""
from __future__ import annotations

import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, r'C:\Private\semantic_memory')

import numpy as np
from memory.vector_store import VectorStore
from memory.embeddings import embed
from memory.schemas import MemoryObject


# ── helpers ────────────────────────────────────────────────────────────────────

def cosine_sim(a: list[float], b: list[float]) -> float:
    # vectors are already L2-normalised by embed()
    return float(np.dot(np.array(a), np.array(b)))


def make_store() -> VectorStore:
    return VectorStore(persist_path=Path(tempfile.mkdtemp()))


# ── Task 1: Embedding Cosine Similarity ────────────────────────────────────────

SIMILAR_PAIRS = [
    ("PostgreSQL database indexing", "SQL database index optimization"),
    ("Python async programming", "asyncio concurrent Python tasks"),
    ("Kubernetes pod deployment", "K8s container orchestration"),
    ("Redis caching strategy", "in-memory cache eviction policy"),
    ("JWT authentication token", "OAuth2 bearer token auth"),
]

DIFFERENT_PAIRS = [
    ("PostgreSQL database indexing", "React component lifecycle"),
    ("Python async programming", "Kubernetes pod deployment"),
    ("Redis caching strategy", "machine learning model training"),
    ("JWT authentication", "database backup strategy"),
    ("CI/CD pipeline automation", "CSS styling flexbox layout"),
]


def task1():
    sims_similar = [cosine_sim(embed(a), embed(b)) for a, b in SIMILAR_PAIRS]
    sims_different = [cosine_sim(embed(a), embed(b)) for a, b in DIFFERENT_PAIRS]
    avg_sim = float(np.mean(sims_similar))
    avg_diff = float(np.mean(sims_different))
    return avg_sim, avg_diff, avg_sim - avg_diff, sims_similar, sims_different


# ── Task 2: Compression Ratio ──────────────────────────────────────────────────

TEST_DOCS = [
    "PostgreSQL supports partial indexes that only index rows matching a WHERE clause. This reduces index size significantly. Queries that match the predicate can use the partial index efficiently. It is especially useful for sparse data like active users or pending orders.",
    "Redis uses an LRU eviction policy to remove least-recently-used keys when memory is full. You can configure maxmemory-policy in redis.conf. The allkeys-lru policy evicts any key while volatile-lru only targets keys with TTL set.",
    "Kubernetes Deployments manage ReplicaSets to ensure a desired number of pod replicas are running. Rolling updates replace pods gradually to avoid downtime. Rollbacks are supported via revision history. Resource limits prevent pods from starving the node.",
    "Python asyncio provides an event loop for running coroutines concurrently. The async/await syntax marks coroutines. Tasks wrap coroutines for scheduling. asyncio.gather runs multiple coroutines concurrently and collects results.",
    "JWT tokens consist of a header, payload, and signature separated by dots. The header specifies the algorithm. The payload contains claims like sub, exp, and iat. The signature verifies integrity using a secret or RSA key.",
    "CI/CD pipelines automate build, test, and deploy stages. GitHub Actions uses YAML workflow files in .github/workflows. Jobs run on runners and can be parallelized. Secrets are stored encrypted and injected as env vars.",
    "Docker multi-stage builds reduce final image size by copying only artifacts from builder stages. Each FROM instruction starts a new stage. The --target flag builds up to a specific stage. Alpine base images further reduce size.",
    "GraphQL queries let clients specify exactly which fields to return, avoiding over-fetching. Resolvers fetch data for each field. Schema definitions describe types and relationships. Mutations modify data on the server.",
    "Terraform uses HCL to declare infrastructure as code. Providers expose resources like AWS EC2 instances. State files track actual resource IDs. Plan shows changes before apply. Remote state backends enable team collaboration.",
    "nginx acts as a reverse proxy by forwarding client requests to upstream servers. Location blocks match URL patterns. Proxy headers like X-Forwarded-For pass client IP. Upstream blocks define server pools for load balancing.",
]


def task2():
    vs = make_store()
    stored = []
    for doc in TEST_DOCS:
        mem = MemoryObject(type="doc", content=doc)
        doc_id = vs.store(mem)
        stored.append((len(doc), doc_id))

    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False

    compression_ratios = []
    if ollama_ok:
        from llm.summarizer import summarize_text
        for i, (orig_chars, _) in enumerate(stored):
            try:
                summary = summarize_text(TEST_DOCS[i])
                comp_chars = len(summary)
                compression_ratios.append(orig_chars / comp_chars if comp_chars > 0 else 0)
            except Exception:
                compression_ratios.append(None)

    return stored, ollama_ok, compression_ratios


# ── Task 3: Multi-doc Retrieval Quality ────────────────────────────────────────

PG_DOCS = [
    "PostgreSQL uses MVCC for concurrent transactions. SQL queries can span multiple tables using JOINs. Index scans speed up WHERE clause filtering.",
    "Creating a composite index in PostgreSQL can accelerate multi-column SQL queries. The query planner decides whether to use the index based on statistics.",
    "PostgreSQL EXPLAIN ANALYZE reveals the actual query plan. Index-only scans avoid heap fetches for covered columns. Vacuum reclaims dead tuples.",
    "PostgreSQL foreign keys enforce referential integrity between tables. SQL CASCADE options control delete/update propagation. Index on FK columns improves JOIN performance.",
    "PostgreSQL full-text search uses tsvector and tsquery types. GIN indexes accelerate text search queries. SQL ranking functions order results by relevance.",
]

K8S_DOCS = [
    "Kubernetes pod scheduling places workloads on nodes based on resource requests. kubectl get pods lists running pods in the cluster.",
    "A Kubernetes deployment manifest defines replica count and pod template. kubectl apply -f deploys the manifest to the cluster. Rollouts track revision history.",
    "Kubernetes services expose pods via stable DNS names. ClusterIP, NodePort, and LoadBalancer are service types. kubectl describe service shows endpoint details.",
    "Kubernetes ConfigMaps store non-secret configuration. Pods mount ConfigMaps as volumes or env vars. kubectl create configmap generates them from files.",
    "Kubernetes horizontal pod autoscaler scales deployments based on CPU or custom metrics. kubectl get hpa shows current replica targets in the cluster.",
]

PY_DOCS = [
    "Python functions are first-class objects that can be passed as arguments. The def keyword defines a function. Lambda creates anonymous single-expression functions.",
    "Python classes use the class keyword. The __init__ method initializes instances. Inheritance is specified in parentheses. Module-level imports provide dependencies.",
    "Python modules are files with .py extension. The import statement loads a module. Packages are directories with __init__.py. Relative imports use dot notation.",
    "Python decorators wrap functions to add behavior. The @decorator syntax applies them. functools.wraps preserves the wrapped function metadata. Class decorators work similarly.",
    "Python type hints annotate function parameters and return values. The typing module provides generic types. mypy performs static type checking. Import annotations from __future__ for deferred evaluation.",
]

QUERIES = [
    ("PostgreSQL connection pooling", "postgresql"),
    ("SQL database index performance", "postgresql"),
    ("Kubernetes pod scheduling", "kubernetes"),
    ("kubectl apply deployment manifest", "kubernetes"),
    ("Python decorator pattern", "python"),
    ("Python module imports", "python"),
]

CLUSTER_KEYWORDS = {
    "postgresql": ["postgresql", "sql", "database", "index", "query"],
    "kubernetes": ["kubernetes", "pod", "deployment", "kubectl", "cluster"],
    "python": ["python", "function", "class", "module", "import"],
}


def classify_doc(content: str) -> str:
    c = content.lower()
    scores = {cluster: sum(1 for kw in kws if kw in c) for cluster, kws in CLUSTER_KEYWORDS.items()}
    return max(scores, key=lambda k: scores[k])


def task3():
    vs = make_store()
    for doc in PG_DOCS:
        vs.store(MemoryObject(type="doc", content=doc))
    for doc in K8S_DOCS:
        vs.store(MemoryObject(type="doc", content=doc))
    for doc in PY_DOCS:
        vs.store(MemoryObject(type="doc", content=doc))

    hits = 0
    query_results = []
    for query_text, expected in QUERIES:
        results = vs.query(query_text, k=1)
        if results:
            predicted = classify_doc(results[0]["content"])
            match = predicted == expected
        else:
            predicted, match = "NONE", False
        if match:
            hits += 1
        query_results.append((query_text, expected, predicted, match))

    return hits, query_results


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("COMPRESSION QUALITY BENCHMARK")
    print("=" * 60)

    # Task 1
    avg_sim, avg_diff, gap, sims_similar, sims_different = task1()
    print(f"\n[Task 1] Embedding Cosine Similarity")
    print(f"  Similar pairs:   {[round(x, 4) for x in sims_similar]}")
    print(f"  Different pairs: {[round(x, 4) for x in sims_different]}")

    # Task 2
    stored, ollama_ok, compression_ratios = task2()
    print(f"\n[Task 2] Compression Ratio")
    print(f"  Ollama available: {ollama_ok}")
    for i, (orig_chars, doc_id) in enumerate(stored):
        print(f"  Doc {i+1:02d}: original_chars={orig_chars}, id={doc_id[:8]}...")
    if ollama_ok and compression_ratios:
        valid = [r for r in compression_ratios if r is not None]
        avg_ratio = float(np.mean(valid)) if valid else None
    else:
        avg_ratio = None

    # Task 3
    hits, query_results = task3()
    print(f"\n[Task 3] Multi-doc Retrieval Quality")
    for query_text, expected, predicted, match in query_results:
        status = "HIT " if match else "MISS"
        print(f"  [{status}] '{query_text}' -> expected={expected}, got={predicted}")

    # Score
    score_embedding = (gap / 1.0) * 40
    score_retrieval = (hits / 6) * 60
    llm_bonus = 10 if (ollama_ok and avg_ratio and avg_ratio >= 2.0) else 0
    total_score = score_embedding + score_retrieval + llm_bonus

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  avg_similar_cosine_sim   = {avg_sim:.4f}")
    print(f"  avg_different_cosine_sim = {avg_diff:.4f}")
    print(f"  separation_gap           = {gap:.4f}")
    print(f"  compression_ratio        = {'N/A' if avg_ratio is None else f'{avg_ratio:.2f}x'}")
    print(f"  retrieval_hits           = {hits}/6")
    print(f"  Compression Score (0-100)= {total_score:.1f}")
    print(f"    breakdown: embedding={score_embedding:.1f} + retrieval={score_retrieval:.1f} + llm_bonus={llm_bonus}")
    print("=" * 60)


if __name__ == "__main__":
    main()
