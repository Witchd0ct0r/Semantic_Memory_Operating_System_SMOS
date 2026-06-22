"""
S2 — Retrieval Quality Under Scale
Measures P@1, P@3, P@5, MRR, NDCG with a synthetic labeled corpus.
8 domains, 25 docs each (200 total), 5 queries per domain (40 queries).
Produces per-domain confusion matrix and cluster bleed analysis.
"""
from __future__ import annotations

import math
import sys
import tempfile
import time
from pathlib import Path

import faiss
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.embeddings import embed, embed_batch
from memory.vector_store import VectorStore
from memory.schemas import MemoryObject

import uuid as uuid_mod
from datetime import datetime

# ---------------------------------------------------------------------------
# Synthetic corpus — 8 domains, 25 docs each
# ---------------------------------------------------------------------------
DOMAINS = {
    "security": [
        "OAuth 2.0 authorization code flow with PKCE prevents authorization code interception attacks.",
        "TLS 1.3 eliminates RSA key exchange in favor of ephemeral Diffie-Hellman for forward secrecy.",
        "CSRF tokens must be tied to the user session and validated on every state-changing request.",
        "JWT signature verification uses the public key from the JWKS endpoint at /.well-known/jwks.",
        "SQL injection prevention requires parameterized queries, never string concatenation.",
        "Rate limiting on authentication endpoints prevents credential stuffing attacks.",
        "bcrypt with work factor 12 is the recommended algorithm for password hashing.",
        "Content Security Policy headers restrict which scripts can execute in the browser.",
        "SSRF protection requires validating and allowlisting all outbound HTTP targets.",
        "Session fixation is prevented by regenerating the session ID after successful login.",
        "HTTP Strict Transport Security forces all connections over HTTPS for the specified duration.",
        "Input validation at the API boundary prevents command injection and path traversal.",
        "XSS prevention requires HTML-escaping all user-supplied content before rendering.",
        "API keys must be rotated regularly and stored in environment variables, not source code.",
        "Zero-trust network architecture requires every request to be authenticated and authorized.",
        "Secrets scanning in CI pipelines catches credentials committed to source control.",
        "The principle of least privilege minimizes blast radius when credentials are compromised.",
        "Multi-factor authentication significantly reduces account takeover risk.",
        "Timing-safe string comparison prevents timing attacks on secret verification.",
        "Certificate pinning prevents MITM attacks on mobile applications.",
        "Penetration testing identifies exploitable vulnerabilities before attackers do.",
        "Dependency vulnerability scanning should run on every build in the CI pipeline.",
        "Key derivation functions like PBKDF2 add iteration cost to slow brute-force attacks.",
        "CORS misconfiguration allows malicious sites to make authenticated cross-origin requests.",
        "Audit logging captures all authentication events and privilege escalations.",
    ],
    "authentication": [
        "OIDC extends OAuth 2.0 with an ID token containing user identity claims.",
        "Refresh token rotation invalidates the previous token when a new one is issued.",
        "Biometric authentication uses fingerprint or face recognition as a second factor.",
        "SAML 2.0 uses XML assertions to exchange authentication data between IdP and SP.",
        "Passkeys replace passwords with device-bound public-key cryptography.",
        "Session tokens must be stored in HttpOnly cookies to prevent JavaScript access.",
        "Magic link authentication sends a one-time URL to the user's email address.",
        "Device authorization flow enables authentication on input-constrained devices.",
        "Continuous authentication verifies user identity throughout the session, not just at login.",
        "Social login delegates authentication to a trusted third-party identity provider.",
        "Account lockout after N failed attempts prevents automated brute-force attacks.",
        "Step-up authentication requires additional verification for high-risk operations.",
        "Hardware security keys provide phishing-resistant second-factor authentication.",
        "Single sign-on allows one login to access multiple related applications.",
        "Token expiry forces re-authentication, limiting the damage from stolen tokens.",
        "Federated identity management synchronizes identities across organizational boundaries.",
        "Password complexity requirements alone are less effective than password length.",
        "Recovery codes are one-time use backup codes for when MFA devices are unavailable.",
        "Mutual TLS authentication requires both client and server to present certificates.",
        "Authentication events should trigger notifications to users for anomaly detection.",
        "Credential stuffing uses breached username-password pairs against other services.",
        "Bot detection during login uses behavioral signals to distinguish humans from automation.",
        "Anonymous authentication allows access to public resources without identity disclosure.",
        "Adaptive authentication adjusts requirements based on risk signals like IP reputation.",
        "Identity proofing verifies real-world identity before issuing digital credentials.",
    ],
    "fastapi": [
        "FastAPI's dependency injection system resolves function parameters automatically.",
        "Pydantic v2 models validate request bodies and serialize response models in FastAPI.",
        "Background tasks in FastAPI run after the response is sent to the client.",
        "FastAPI generates interactive Swagger UI documentation at /docs automatically.",
        "Lifespan context managers replace startup and shutdown event handlers in FastAPI.",
        "APIRouter groups related endpoints and can be included with a prefix.",
        "FastAPI middleware intercepts every request and response for cross-cutting concerns.",
        "WebSocket endpoints in FastAPI maintain persistent bidirectional connections.",
        "Path parameters are declared with type annotations in the function signature.",
        "Query parameters with Optional type default to None when omitted.",
        "FastAPI integrates with SQLAlchemy async sessions via dependency injection.",
        "Response models strip extra fields and control the exact shape of JSON output.",
        "Status code overrides allow returning 201 Created for POST endpoints.",
        "File uploads use UploadFile and Form together in multipart requests.",
        "FastAPI test client wraps HTTPX for synchronous testing of async endpoints.",
        "Depends caches dependencies within a single request by default.",
        "Custom exception handlers convert domain errors to HTTP responses.",
        "Security utilities in FastAPI provide OAuth2 password and bearer token schemes.",
        "Streaming responses use StreamingResponse for large payloads or server-sent events.",
        "CORS middleware must be added before other middleware to handle preflight requests.",
        "FastAPI runs on any ASGI server; Uvicorn is the standard choice for production.",
        "Enum types in path parameters restrict accepted values and improve documentation.",
        "FastAPI's openapi_extra extends generated schema with custom fields.",
        "Concurrent requests are handled efficiently because FastAPI endpoints are async-native.",
        "Health check endpoints expose readiness and liveness probes for Kubernetes.",
    ],
    "postgresql": [
        "EXPLAIN ANALYZE reveals the actual execution plan and row estimates for a query.",
        "Partial indexes improve query performance by indexing only a subset of rows.",
        "Connection pooling with PgBouncer reduces overhead from frequent connect/disconnect.",
        "VACUUM reclaims dead tuple storage and prevents transaction ID wraparound.",
        "Logical replication streams row-level changes to subscribing databases.",
        "Window functions compute aggregates over sliding partitions without collapsing rows.",
        "GIN indexes accelerate full-text search and JSONB containment queries.",
        "BRIN indexes are compact and efficient for naturally ordered large columns like timestamps.",
        "CTEs with MATERIALIZED force eager evaluation, useful for query plan control.",
        "Row-level security policies restrict which rows each user can see or modify.",
        "Table partitioning splits large tables into smaller child tables by range or list.",
        "pg_stat_statements tracks execution statistics for all SQL statements.",
        "Write-ahead logging ensures durability by persisting changes before modifying data.",
        "Advisory locks coordinate application-level workflows without table locking.",
        "Generated columns compute derived values automatically on insert and update.",
        "Composite indexes serve queries filtering on multiple columns in order.",
        "Index-only scans avoid heap access when all needed columns are in the index.",
        "LATERAL JOIN iterates over a subquery once per row from the outer query.",
        "COPY command is far faster than INSERT for bulk data loading.",
        "Unlogged tables skip WAL for faster writes at the cost of crash recovery.",
        "Hot standby allows read-only queries on streaming replication replicas.",
        "Autovacuum dynamically adjusts frequency based on table modification rate.",
        "Table bloat from frequent updates accumulates dead tuples between vacuum runs.",
        "Postgres uses cost-based planning with statistics gathered by ANALYZE.",
        "Hash joins outperform nested loops for large unsorted datasets.",
    ],
    "redis": [
        "Redis Cluster shards data across 16384 hash slots distributed across nodes.",
        "Pub/sub messaging in Redis delivers messages to all subscribers in real time.",
        "Redis Streams provide a durable append-only log with consumer group semantics.",
        "Sorted sets enable leaderboards with O(log n) rank lookups.",
        "Lua scripting executes atomic multi-command operations server-side.",
        "Redis persistence modes: RDB snapshots versus AOF append-only files.",
        "Expiry with TTL is essential to prevent unbounded memory growth in caches.",
        "Redis Sentinel provides high availability through automatic failover.",
        "Pipeline batching reduces round trips by sending multiple commands at once.",
        "Keyspace notifications alert clients when keys expire or are modified.",
        "RESP3 protocol adds typed replies including attributes and out-of-band pushes.",
        "Memory optimization uses compact encodings like ziplist for small collections.",
        "WAIT command blocks until replicas acknowledge write propagation.",
        "Redis ACL controls per-user access to commands and key patterns.",
        "OBJECT ENCODING reveals the internal representation of a value.",
        "SCAN iterates keys without blocking in contrast to KEYS.",
        "Object eviction policies like allkeys-lru manage memory under pressure.",
        "Redis modules extend the server with custom data structures and commands.",
        "Replication is asynchronous by default, meaning recent writes may be lost on failover.",
        "OBJECT FREQ provides approximate access frequency for LFU eviction.",
        "Lazy expiration means expired keys are deleted only on access or by background sweep.",
        "HyperLogLog estimates unique element counts using only 12 KB of memory.",
        "Geospatial commands store and query coordinates using sorted set internals.",
        "MULTI/EXEC transactions execute a queued list of commands atomically.",
        "OBJECT IDLETIME reports seconds since the key was last accessed.",
    ],
    "kubernetes": [
        "Pod disruption budgets limit the number of voluntary disruptions during upgrades.",
        "Horizontal pod autoscaler scales deployment replicas based on CPU or custom metrics.",
        "Resource quotas cap total CPU and memory usage per namespace.",
        "Node affinity rules schedule pods on nodes with specific labels.",
        "Taints and tolerations control which pods can be scheduled on tainted nodes.",
        "ConfigMaps decouple configuration from container images.",
        "Secrets store sensitive data like passwords and API keys in base64-encoded form.",
        "Rolling updates replace pods gradually to achieve zero-downtime deployments.",
        "StatefulSets provide stable network identity and persistent storage per pod.",
        "DaemonSets ensure exactly one pod runs on every node in the cluster.",
        "Ingress controllers route external HTTP traffic to services based on host and path.",
        "Network policies control pod-to-pod communication with label selectors.",
        "Liveness probes restart containers that enter a failed state.",
        "Readiness probes gate traffic until the container is ready to serve.",
        "Service accounts provide pod identities for authenticating to the Kubernetes API.",
        "RBAC policies grant granular access to Kubernetes API resources.",
        "Persistent volume claims bind to volumes that outlive pod restarts.",
        "Custom resource definitions extend the Kubernetes API with domain objects.",
        "Operators encode operational knowledge as Kubernetes controllers.",
        "Helm charts package Kubernetes manifests as versioned deployable units.",
        "Vertical pod autoscaler recommends CPU and memory request adjustments.",
        "Priority classes influence scheduling order when cluster resources are scarce.",
        "Pod security admission enforces baseline and restricted pod security standards.",
        "Cluster autoscaler adds nodes when pods cannot be scheduled for lack of resources.",
        "Init containers run setup tasks before the main application container starts.",
    ],
    "monitoring": [
        "Prometheus uses a pull model, scraping /metrics endpoints at configurable intervals.",
        "Alertmanager deduplicates, groups, and routes alerts to receivers like PagerDuty.",
        "PromQL label matchers filter time series by exact value, prefix, or regex.",
        "Recording rules precompute expensive queries and store results as new metrics.",
        "Grafana dashboards visualize metrics, logs, and traces in a single pane.",
        "Rate function computes per-second change rate of a counter metric.",
        "Histogram quantile extracts percentiles from bucketed latency distributions.",
        "Exemplars link metric data points to specific trace IDs for correlation.",
        "Remote write forwards metrics from Prometheus to long-term storage backends.",
        "Service discovery dynamically discovers scrape targets from Kubernetes or Consul.",
        "Thanos extends Prometheus with global query and unlimited retention.",
        "Dead man's switch alerts fire when an alert pipeline goes silent.",
        "OpenTelemetry collector receives, processes, and exports telemetry data.",
        "Log aggregation with Loki indexes labels but not log content for efficiency.",
        "Distributed tracing with Jaeger visualizes request flows across microservices.",
        "Four golden signals: latency, traffic, errors, and saturation.",
        "SLO burn rate alerts detect fast error budget consumption early.",
        "Metric cardinality must be controlled to prevent Prometheus memory explosion.",
        "Pushgateway accepts metrics from batch jobs that cannot be scraped.",
        "Vector processes and routes log data with a rich transformation language.",
        "eBPF-based monitoring captures kernel-level metrics without application changes.",
        "Synthetic monitoring probes endpoints from external locations to detect outages.",
        "Baggage propagation carries contextual metadata across service boundaries in traces.",
        "Anomaly detection identifies unusual patterns in time-series metric data.",
        "Dashboard-as-code manages Grafana dashboards in version-controlled JSON.",
    ],
    "cicd": [
        "GitHub Actions workflows trigger on push, pull request, and schedule events.",
        "Matrix builds fan out jobs across multiple OS and language version combinations.",
        "Caching node_modules between runs significantly reduces CI pipeline duration.",
        "Branch protection rules require CI checks to pass before merging.",
        "Secrets are injected as environment variables and masked in log output.",
        "Reusable workflows share common CI logic across multiple repositories.",
        "Artifact upload persists build outputs between jobs in the same workflow.",
        "OIDC token exchange authenticates to cloud providers without stored credentials.",
        "Ephemeral runners execute each job in a fresh environment for isolation.",
        "Canary deployments route a small percentage of traffic to the new version first.",
        "Blue-green deployments maintain two environments and switch traffic atomically.",
        "Feature flags decouple deployment from feature release for incremental rollout.",
        "Semantic versioning automates release numbering based on commit message conventions.",
        "Dependency update bots open pull requests when new package versions are published.",
        "Container image scanning detects vulnerabilities before pushing to the registry.",
        "Integration tests run against a Docker Compose environment spun up in CI.",
        "Test coverage thresholds fail builds when coverage drops below acceptable levels.",
        "Parallelizing test suites across multiple runners reduces end-to-end CI time.",
        "Golden path templates standardize CI configuration across teams and projects.",
        "Trunk-based development keeps all engineers working on a single long-lived branch.",
        "Rollback automation detects deployment failures and reverts to the previous version.",
        "Change management gates require approval before deploying to production.",
        "Pipeline as code stores CI configuration alongside application source in version control.",
        "Smoke tests verify basic functionality immediately after each deployment.",
        "Signed commits and tag attestation provide a chain of custody for artifacts.",
    ],
}

QUERIES = {
    "security": [
        "How do I prevent SQL injection in a web application?",
        "What algorithm should I use for password hashing?",
        "How can I protect against XSS attacks?",
        "What is the principle of least privilege?",
        "How do I prevent CSRF in my API?",
    ],
    "authentication": [
        "How does OAuth 2.0 refresh token rotation work?",
        "What is the difference between OIDC and OAuth?",
        "How do passkeys replace traditional passwords?",
        "What is single sign-on and how is it implemented?",
        "How do I implement multi-factor authentication?",
    ],
    "fastapi": [
        "How does dependency injection work in FastAPI?",
        "How do I handle file uploads in FastAPI?",
        "How do I add middleware to a FastAPI application?",
        "How does FastAPI generate API documentation?",
        "How do I test a FastAPI endpoint?",
    ],
    "postgresql": [
        "How do I analyze slow PostgreSQL queries?",
        "How does table partitioning improve performance?",
        "What is the purpose of VACUUM in PostgreSQL?",
        "How do I implement row-level security?",
        "What are the best indexing strategies for PostgreSQL?",
    ],
    "redis": [
        "How does Redis Cluster distribute data across nodes?",
        "What is the difference between RDB and AOF persistence?",
        "How do I implement a leaderboard with Redis?",
        "How do Redis consumer groups work with Streams?",
        "What eviction policies does Redis support?",
    ],
    "kubernetes": [
        "How does horizontal pod autoscaling work?",
        "What is the difference between liveness and readiness probes?",
        "How do I configure network policies in Kubernetes?",
        "What are StatefulSets used for?",
        "How does Kubernetes RBAC control access?",
    ],
    "monitoring": [
        "How do I compute latency percentiles with Prometheus?",
        "What are the four golden signals of monitoring?",
        "How does distributed tracing work with Jaeger?",
        "How do SLO burn rate alerts work?",
        "How do I control metric cardinality in Prometheus?",
    ],
    "cicd": [
        "How do I cache dependencies in GitHub Actions?",
        "What is the difference between canary and blue-green deployment?",
        "How do OIDC tokens authenticate CI jobs to cloud providers?",
        "How do I parallelize tests in a CI pipeline?",
        "What is trunk-based development?",
    ],
}

DOMAIN_NAMES = list(DOMAINS.keys())
ALL_DOCS: list[tuple[str, str]] = []  # (content, domain)
for domain, docs in DOMAINS.items():
    for doc in docs:
        ALL_DOCS.append((doc, domain))

DOC_DOMAINS = [d for _, d in ALL_DOCS]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _precision_at_k(ranked_domains: list[str], relevant_domain: str, k: int) -> float:
    top_k = ranked_domains[:k]
    hits = sum(1 for d in top_k if d == relevant_domain)
    return hits / k


def _reciprocal_rank(ranked_domains: list[str], relevant_domain: str) -> float:
    for i, d in enumerate(ranked_domains, 1):
        if d == relevant_domain:
            return 1.0 / i
    return 0.0


def _dcg(ranked_domains: list[str], relevant_domain: str, k: int) -> float:
    total = 0.0
    for i, d in enumerate(ranked_domains[:k], 1):
        rel = 1 if d == relevant_domain else 0
        total += rel / math.log2(i + 1)
    return total


def _ndcg(ranked_domains: list[str], relevant_domain: str, k: int) -> float:
    # Ideal DCG: first hit at position 1
    ideal_dcg = 1.0 / math.log2(2)  # log2(1+1)
    actual_dcg = _dcg(ranked_domains, relevant_domain, k)
    return actual_dcg / ideal_dcg if ideal_dcg > 0 else 0.0


# ---------------------------------------------------------------------------
# Build index
# ---------------------------------------------------------------------------

print("Building retrieval quality corpus (200 docs, 8 domains)...")
data_dir = Path(tempfile.mkdtemp(prefix="s2_"))
store = VectorStore(persist_path=data_dir)

import faiss as _faiss

texts = [doc for doc, _ in ALL_DOCS]
all_vecs = []
t_idx_start = time.perf_counter()
batch_size = 64
for i in range(0, len(texts), batch_size):
    vecs = embed_batch(texts[i : i + batch_size])
    all_vecs.extend(vecs)

arr = np.array(all_vecs, dtype=np.float32)
_faiss.normalize_L2(arr)

from datetime import datetime as _dt
now_iso = _dt.utcnow().isoformat()
with store._lock:
    max_row = store._db.execute("SELECT MAX(row_id) FROM memories").fetchone()[0]
    base_id = max_row or 0
    rows = [
        (str(uuid_mod.uuid4()), "doc", text, now_iso, domain, "hot")
        for text, domain in ALL_DOCS
    ]
    store._db.executemany(
        "INSERT INTO memories (uuid, type, content, timestamp, tags, tier) VALUES (?,?,?,?,?,?)",
        rows,
    )
    store._db.commit()
    row_ids = np.arange(base_id + 1, base_id + 1 + len(texts), dtype=np.int64)
    store._index.add_with_ids(arr, row_ids)
    store._save_index()

t_idx_end = time.perf_counter()
print(f"Index built in {t_idx_end - t_idx_start:.2f}s. Total vectors: {store.count()}\n")

# ---------------------------------------------------------------------------
# Run queries and compute metrics
# ---------------------------------------------------------------------------

K_VALUES = [1, 3, 5, 10]

domain_metrics: dict[str, dict] = {d: {} for d in DOMAIN_NAMES}
confusion: dict[str, dict[str, int]] = {d: {d2: 0 for d2 in DOMAIN_NAMES} for d in DOMAIN_NAMES}

all_p1 = all_p3 = all_p5 = all_mrr = all_ndcg5 = 0.0
total_queries = 0

for domain, queries in QUERIES.items():
    d_p1 = d_p3 = d_p5 = d_mrr = d_ndcg5 = 0.0
    n_q = len(queries)

    for query in queries:
        t0 = time.perf_counter()
        results = store.query(query, k=25)
        latency_ms = (time.perf_counter() - t0) * 1000

        ranked_domains = []
        for r in results:
            tags = r["metadata"]["tags"]
            ranked_domains.append(tags if tags else "unknown")

        # Track confusion: what domains appear in top-5?
        for rd in ranked_domains[:5]:
            if rd in confusion[domain]:
                confusion[domain][rd] += 1

        d_p1 += _precision_at_k(ranked_domains, domain, 1)
        d_p3 += _precision_at_k(ranked_domains, domain, 3)
        d_p5 += _precision_at_k(ranked_domains, domain, 5)
        d_mrr += _reciprocal_rank(ranked_domains, domain)
        d_ndcg5 += _ndcg(ranked_domains, domain, 5)

    domain_metrics[domain] = {
        "P@1": round(d_p1 / n_q, 3),
        "P@3": round(d_p3 / n_q, 3),
        "P@5": round(d_p5 / n_q, 3),
        "MRR": round(d_mrr / n_q, 3),
        "NDCG@5": round(d_ndcg5 / n_q, 3),
    }
    all_p1 += d_p1
    all_p3 += d_p3
    all_p5 += d_p5
    all_mrr += d_mrr
    all_ndcg5 += d_ndcg5
    total_queries += n_q

micro_avg = {
    "P@1":    round(all_p1 / total_queries, 3),
    "P@3":    round(all_p3 / total_queries, 3),
    "P@5":    round(all_p5 / total_queries, 3),
    "MRR":    round(all_mrr / total_queries, 3),
    "NDCG@5": round(all_ndcg5 / total_queries, 3),
}

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

print(f"{'Domain':<18}  {'P@1':>6}  {'P@3':>6}  {'P@5':>6}  {'MRR':>6}  {'NDCG@5':>7}")
print("-" * 58)
for domain in DOMAIN_NAMES:
    m = domain_metrics[domain]
    print(f"{domain:<18}  {m['P@1']:>6.3f}  {m['P@3']:>6.3f}  {m['P@5']:>6.3f}  "
          f"{m['MRR']:>6.3f}  {m['NDCG@5']:>7.3f}")
print("-" * 58)
print(f"{'MICRO AVG':<18}  {micro_avg['P@1']:>6.3f}  {micro_avg['P@3']:>6.3f}  "
      f"{micro_avg['P@5']:>6.3f}  {micro_avg['MRR']:>6.3f}  {micro_avg['NDCG@5']:>7.3f}")

# Cluster bleed — domains with P@1 < 0.6
print("\n=== CLUSTER BLEED ANALYSIS ===")
bleed_threshold = 0.6
weak_domains = [(d, m) for d, m in domain_metrics.items() if m["P@1"] < bleed_threshold]
if weak_domains:
    for d, m in weak_domains:
        print(f"  {d}: P@1={m['P@1']} — potential bleed into:")
        bleeders = sorted(
            [(other, confusion[d][other]) for other in DOMAIN_NAMES if other != d and confusion[d][other] > 0],
            key=lambda x: -x[1],
        )
        for other, cnt in bleeders[:3]:
            print(f"    -> {other}: {cnt} top-5 hits")
else:
    print(f"  No domains below P@1={bleed_threshold} threshold.")

# Confusion matrix (abbreviated — top confusions only)
print("\n=== TOP CONFUSION PAIRS ===")
pairs = []
for d in DOMAIN_NAMES:
    for other in DOMAIN_NAMES:
        if other != d and confusion[d][other] > 0:
            pairs.append((d, other, confusion[d][other]))
pairs.sort(key=lambda x: -x[2])
for src, dst, cnt in pairs[:8]:
    print(f"  {src} -> {dst}: {cnt} cross-retrievals in top-5")

print("\n=== OVERALL RETRIEVAL SCORE ===")
score = round(
    (micro_avg["P@1"] * 25 + micro_avg["P@3"] * 25 + micro_avg["P@5"] * 20
     + micro_avg["MRR"] * 15 + micro_avg["NDCG@5"] * 15),
    1,
)
print(f"  Weighted score: {score}/100")
print(f"  Corpus: {len(ALL_DOCS)} docs, {total_queries} queries, 8 domains")

print("\nS2 complete.")
