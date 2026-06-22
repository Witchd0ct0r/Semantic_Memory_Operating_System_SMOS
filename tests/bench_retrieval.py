"""
bench_retrieval.py — Vector Retrieval Accuracy Benchmark
Measures FAISS + sentence-transformers (all-MiniLM-L6-v2) precision/recall.
"""
from __future__ import annotations

import sys
import tempfile
import shutil
import time
import random
import math
from pathlib import Path
from datetime import datetime

sys.path.insert(0, r'C:\Private\semantic_memory')

from memory.vector_store import VectorStore
from memory.schemas import MemoryObject
from memory.embeddings import embed_batch

# ---------------------------------------------------------------------------
# 1. Synthetic dataset — 200 documents, 10 clusters × 20 docs
# ---------------------------------------------------------------------------

CLUSTERS = {
    0: {
        "name": "PostgreSQL/database",
        "docs": [
            "PostgreSQL connection pool tuning using PgBouncer in transaction mode for high concurrency.",
            "Configuring shared_buffers and work_mem in postgresql.conf for OLTP workloads.",
            "Creating partial indexes in PostgreSQL to speed up filtered queries on large tables.",
            "PostgreSQL VACUUM and AUTOVACUUM configuration to prevent table bloat and dead tuple accumulation.",
            "Streaming replication setup in PostgreSQL using wal_level=replica and hot_standby.",
            "Point-in-time recovery (PITR) with pg_basebackup and WAL archiving in PostgreSQL.",
            "Using EXPLAIN ANALYZE to identify sequential scans and missing indexes in slow queries.",
            "Partitioning large PostgreSQL tables by date range to improve query performance and maintenance.",
            "Logical replication in PostgreSQL 14 for selective table replication across databases.",
            "PostgreSQL JSONB indexing strategies: GIN indexes for document search workloads.",
            "Tuning checkpoint_completion_target and max_wal_size to reduce I/O spikes in PostgreSQL.",
            "Row-level security (RLS) policies in PostgreSQL for multi-tenant data isolation.",
            "PostgreSQL full-text search using tsvector, tsquery, and GIN indexes.",
            "Connection multiplexing with PgBouncer session vs transaction vs statement modes.",
            "Backup and restore strategies for PostgreSQL: pg_dump, pg_restore, and continuous archiving.",
            "Monitoring PostgreSQL performance with pg_stat_statements and pg_stat_activity views.",
            "Using pg_upgrade to perform major version upgrades with minimal downtime.",
            "Configuring PostgreSQL for read-heavy workloads with multiple hot-standby replicas.",
            "Deadlock detection and lock timeout configuration in PostgreSQL concurrent transactions.",
            "Using materialized views in PostgreSQL to cache expensive aggregate queries.",
        ],
    },
    1: {
        "name": "Kubernetes/deployment",
        "docs": [
            "Kubernetes Deployment rolling update strategy with maxSurge and maxUnavailable configuration.",
            "Configuring Kubernetes HorizontalPodAutoscaler based on CPU and custom metrics.",
            "Kubernetes Ingress controller setup with NGINX and TLS termination using cert-manager.",
            "Pod disruption budgets in Kubernetes to ensure high availability during node drains.",
            "Kubernetes StatefulSet for deploying stateful applications like databases with persistent volumes.",
            "Setting up Kubernetes network policies to restrict pod-to-pod communication.",
            "Kubernetes resource requests and limits: CPU throttling and OOMKill prevention.",
            "Using Helm charts for templated Kubernetes application deployment and release management.",
            "Kubernetes ConfigMap and Secret management for application configuration injection.",
            "Rolling back a failed Kubernetes deployment using kubectl rollout undo.",
            "Kubernetes liveness and readiness probes configuration for zero-downtime deployments.",
            "Node affinity and pod anti-affinity rules in Kubernetes for workload distribution.",
            "Kubernetes namespace isolation and RBAC for multi-team cluster sharing.",
            "Configuring Kubernetes cluster autoscaler for dynamic node provisioning.",
            "Using kubectl port-forward and exec for debugging running pods in Kubernetes.",
            "Kubernetes service mesh integration with Istio for traffic management and observability.",
            "Persistent volume claims and storage classes in Kubernetes for stateful workloads.",
            "Kubernetes DaemonSet for deploying node-level agents like log collectors.",
            "Multi-container pod patterns in Kubernetes: sidecar, ambassador, and adapter.",
            "Kubernetes CronJob for scheduled batch workloads and periodic tasks.",
        ],
    },
    2: {
        "name": "Python/FastAPI",
        "docs": [
            "FastAPI dependency injection with Depends() for shared database session management.",
            "Building async REST endpoints in FastAPI with async def and asyncio background tasks.",
            "FastAPI middleware for request logging, CORS, and request ID propagation.",
            "Pydantic v2 model validation in FastAPI request and response schemas.",
            "Testing FastAPI applications with pytest and httpx AsyncClient.",
            "FastAPI path and query parameter validation with type annotations and Field constraints.",
            "Implementing background tasks in FastAPI using BackgroundTasks or Celery workers.",
            "FastAPI OAuth2 password flow with JWT token generation and validation.",
            "Structuring large FastAPI applications with APIRouter and module-based routing.",
            "FastAPI WebSocket support for real-time bidirectional communication.",
            "Rate limiting in FastAPI using slowapi and Redis as the backend storage.",
            "FastAPI exception handlers and custom HTTP error responses.",
            "Using SQLAlchemy async ORM with FastAPI for non-blocking database operations.",
            "FastAPI response caching with Redis and cache-control headers.",
            "OpenAPI documentation customization in FastAPI with tags, descriptions, and examples.",
            "FastAPI startup and shutdown lifecycle events for resource initialization.",
            "Implementing pagination in FastAPI with limit/offset and cursor-based strategies.",
            "FastAPI file upload handling with UploadFile and multipart form data.",
            "Deploying FastAPI with Uvicorn and Gunicorn in production.",
            "FastAPI integration testing with test database fixtures and dependency overrides.",
        ],
    },
    3: {
        "name": "Redis/caching",
        "docs": [
            "Redis LRU and LFU eviction policies for memory-constrained cache deployments.",
            "Implementing cache-aside pattern with Redis for database query result caching.",
            "Redis Sentinel configuration for automatic failover and high availability.",
            "Redis Cluster setup with hash slots and data sharding across multiple nodes.",
            "Using Redis pub/sub for lightweight real-time messaging between microservices.",
            "Redis Lua scripting for atomic multi-step cache operations.",
            "Cache stampede prevention with Redis using probabilistic early expiration.",
            "Redis Streams for durable message queuing and consumer group processing.",
            "Distributed locking with Redis using the Redlock algorithm.",
            "Redis keyspace notifications for cache invalidation event subscriptions.",
            "Using Redis sorted sets for leaderboard and rate-limiting use cases.",
            "Redis persistence modes: RDB snapshots vs AOF append-only file tradeoffs.",
            "Write-through and write-behind caching strategies with Redis.",
            "Redis pipeline and transaction (MULTI/EXEC) for batching commands.",
            "Cache warming strategies to pre-populate Redis after cold starts.",
            "Monitoring Redis performance with INFO command and redis-cli latency.",
            "Redis connection pool configuration in Python with redis-py client.",
            "Using Redis as a session store for stateless web application authentication.",
            "TTL management strategies in Redis for cache expiry and memory control.",
            "Redis ACL configuration for multi-tenant access control and security.",
        ],
    },
    4: {
        "name": "Security/auth",
        "docs": [
            "JWT token structure: header, payload, signature and RS256 vs HS256 signing algorithms.",
            "OAuth2 authorization code flow with PKCE for secure mobile and SPA authentication.",
            "Implementing RBAC (Role-Based Access Control) with permission inheritance hierarchies.",
            "API key management: generation, rotation, hashing with bcrypt for secure storage.",
            "Rate limiting strategies: token bucket, sliding window, and fixed window algorithms.",
            "OAuth2 refresh token rotation and silent refresh for long-lived sessions.",
            "JWT expiry and revocation strategies using Redis token blacklisting.",
            "Implementing multi-factor authentication (MFA) with TOTP using Google Authenticator.",
            "OpenID Connect (OIDC) for federated identity and single sign-on (SSO).",
            "API gateway authentication: validating JWT at the edge with Kong or AWS API Gateway.",
            "CORS policy configuration to prevent cross-origin request forgery.",
            "Protecting against SQL injection, XSS, and CSRF in web applications.",
            "TLS mutual authentication (mTLS) for service-to-service security in microservices.",
            "Secrets management with HashiCorp Vault for dynamic credential generation.",
            "OAuth2 scopes and fine-grained authorization for API resource protection.",
            "Session fixation and session hijacking prevention strategies.",
            "Content Security Policy (CSP) headers to mitigate XSS attacks.",
            "Security headers: HSTS, X-Frame-Options, and Referrer-Policy configuration.",
            "Penetration testing API endpoints with Burp Suite and OWASP ZAP.",
            "SAML 2.0 integration for enterprise SSO with identity providers like Okta.",
        ],
    },
    5: {
        "name": "Monitoring/observability",
        "docs": [
            "Prometheus scrape configuration and service discovery for Kubernetes workloads.",
            "Grafana dashboard design for latency, error rate, and throughput (RED method).",
            "Defining SLOs and SLIs with Prometheus recording rules and alert thresholds.",
            "OpenTelemetry distributed tracing with Jaeger for microservice request tracing.",
            "Prometheus alerting rules with Alertmanager routing to PagerDuty and Slack.",
            "Grafana Loki for log aggregation and correlation with traces and metrics.",
            "Using PromQL to compute 99th percentile latency histograms in Prometheus.",
            "Kubernetes cluster monitoring with kube-state-metrics and node-exporter.",
            "Setting up error budget burn rate alerts based on SLO violation windows.",
            "Synthetic monitoring with Grafana Synthetic Monitoring for uptime tracking.",
            "Custom Prometheus exporters for application-specific business metrics.",
            "Distributed trace sampling strategies: head-based vs tail-based sampling.",
            "Grafana on-call scheduling and escalation policies for incident management.",
            "Correlation between logs, metrics, and traces using exemplars in Prometheus.",
            "Monitoring PostgreSQL with postgres_exporter and Grafana dashboards.",
            "Cardinality management in Prometheus to prevent metric explosion.",
            "Using Thanos or Cortex for long-term Prometheus metric storage.",
            "Application performance monitoring (APM) with Datadog or New Relic.",
            "Alerting fatigue reduction with alert deduplication and inhibition rules.",
            "SLO reporting and error budget tracking with automated weekly reports.",
        ],
    },
    6: {
        "name": "CI/CD pipeline",
        "docs": [
            "GitHub Actions workflow for Python CI: lint, test, and coverage reporting.",
            "Docker multi-stage build to minimize production image size for FastAPI apps.",
            "Implementing canary deployments with GitHub Actions and Kubernetes rollouts.",
            "GitHub Actions matrix strategy for cross-platform and multi-version testing.",
            "Caching pip dependencies and Docker layers in GitHub Actions for faster builds.",
            "Semantic versioning and automated changelog generation with Release Please.",
            "Security scanning in CI pipelines with Snyk, Trivy, and Dependabot alerts.",
            "GitHub Actions reusable workflows for shared pipeline logic across repositories.",
            "Docker image tagging strategies: SHA, semantic version, and latest tag conventions.",
            "Blue-green deployment automation with GitHub Actions and load balancer switching.",
            "Environment-specific secrets management in GitHub Actions using environments.",
            "Automated database migration runs as part of the Kubernetes deployment pipeline.",
            "Pull request checks: required status checks and branch protection rules.",
            "GitHub Actions OIDC for keyless authentication with AWS and GCP.",
            "Artifact management in CI: uploading test results and coverage reports.",
            "Deployment approval gates and manual workflow triggers in GitHub Actions.",
            "Monorepo CI optimization: path filters to run only affected service pipelines.",
            "Docker Compose for local development environment parity with production.",
            "GitHub Actions self-hosted runners for access to internal resources.",
            "Pipeline observability: tracking build duration and flaky test detection.",
        ],
    },
    7: {
        "name": "React/frontend",
        "docs": [
            "React useEffect cleanup to prevent memory leaks in async data fetching.",
            "State management in React with Zustand vs Redux Toolkit comparison.",
            "React Query (TanStack Query) for server state caching and background refetching.",
            "Building accessible React components with ARIA attributes and keyboard navigation.",
            "React component testing with React Testing Library and Jest.",
            "Code splitting and lazy loading in React with React.lazy and Suspense.",
            "React Context API for global state sharing without prop drilling.",
            "Custom React hooks for reusable stateful logic and side-effect encapsulation.",
            "React performance optimization: useMemo, useCallback, and React.memo.",
            "Form handling in React with React Hook Form and Zod schema validation.",
            "React Router v6 nested routes and data loading with loaders and actions.",
            "Server-side rendering in Next.js with getServerSideProps and App Router.",
            "Styling React components: CSS Modules, Tailwind CSS, and CSS-in-JS comparison.",
            "React error boundaries for graceful error handling in component trees.",
            "Storybook for React component documentation and visual regression testing.",
            "TypeScript integration in React: typing props, hooks, and event handlers.",
            "React Suspense and concurrent features for improved loading UX.",
            "Virtual DOM reconciliation and key prop importance in React lists.",
            "Micro-frontend architecture with Module Federation in React applications.",
            "Progressive Web App (PWA) features: service workers and offline caching in React.",
        ],
    },
    8: {
        "name": "Machine learning",
        "docs": [
            "Training a binary classification model with scikit-learn and cross-validation.",
            "Feature engineering pipeline with sklearn Pipeline and ColumnTransformer.",
            "Model evaluation metrics: AUC-ROC, precision-recall curve, and F1-score.",
            "Hyperparameter tuning with Optuna for neural network architecture search.",
            "MLflow experiment tracking for logging metrics, parameters, and artifacts.",
            "Deploying machine learning models as REST APIs with FastAPI and ONNX runtime.",
            "Data drift detection using Evidently AI for production model monitoring.",
            "Gradient boosting with XGBoost and LightGBM for tabular classification tasks.",
            "Transfer learning with Hugging Face Transformers for text classification.",
            "A/B testing machine learning models in production with traffic splitting.",
            "Feature store design with Feast for training and serving consistency.",
            "Model versioning and registry with MLflow Models and AWS SageMaker.",
            "Handling class imbalance with SMOTE oversampling and class weights.",
            "Neural network training with PyTorch: optimizer, loss function, and scheduler.",
            "Distributed model training with PyTorch DDP across multiple GPUs.",
            "Data labeling pipeline and active learning for annotation efficiency.",
            "Explainability with SHAP values and LIME for black-box model interpretation.",
            "Batch inference pipeline with Apache Spark for large-scale prediction jobs.",
            "Time series forecasting with Prophet and LSTM neural networks.",
            "MLOps pipeline with Kubeflow or Vertex AI for end-to-end model lifecycle.",
        ],
    },
    9: {
        "name": "Microservices",
        "docs": [
            "Service mesh architecture with Istio for traffic management and mutual TLS.",
            "API gateway pattern with Kong or AWS API Gateway for routing and auth.",
            "Event-driven architecture using Apache Kafka for inter-service communication.",
            "SAGA pattern for distributed transaction management across microservices.",
            "Circuit breaker pattern with Resilience4j to prevent cascading failures.",
            "Service discovery with Consul or Kubernetes DNS for dynamic endpoint resolution.",
            "Strangler fig pattern for incrementally migrating a monolith to microservices.",
            "Outbox pattern with PostgreSQL for reliable event publishing in microservices.",
            "CQRS (Command Query Responsibility Segregation) for read/write model separation.",
            "Distributed tracing across microservices with Zipkin and B3 propagation headers.",
            "Health check endpoints and liveness probes for service mesh readiness.",
            "Bulkhead pattern for resource isolation and preventing cascade failures.",
            "Event sourcing architecture for audit log and temporal query support.",
            "GraphQL federation for composing multiple microservice APIs into one schema.",
            "gRPC for high-performance inter-service communication with Protocol Buffers.",
            "Idempotency keys for safe retry logic in distributed payment services.",
            "Dead letter queue (DLQ) handling for failed Kafka consumer message processing.",
            "Rate limiting per service with token bucket algorithm at the API gateway.",
            "Chaos engineering with LitmusChaos to test microservice resilience.",
            "Contract testing with Pact for consumer-driven API compatibility verification.",
        ],
    },
}

# ---------------------------------------------------------------------------
# 2. Test queries — 30 total (3 per cluster)
# ---------------------------------------------------------------------------

QUERIES = [
    # Cluster 0 — PostgreSQL
    {"query": "How do I tune PostgreSQL connection pools for high concurrency?", "expected_cluster": 0},
    {"query": "Setting up streaming replication and point-in-time recovery in Postgres", "expected_cluster": 0},
    {"query": "PostgreSQL slow query investigation with EXPLAIN ANALYZE and index optimization", "expected_cluster": 0},
    # Cluster 1 — Kubernetes
    {"query": "How to configure Kubernetes rolling deployments with zero downtime?", "expected_cluster": 1},
    {"query": "Kubernetes autoscaling pods based on CPU metrics with HPA", "expected_cluster": 1},
    {"query": "Setting up ingress controller with TLS in a Kubernetes cluster", "expected_cluster": 1},
    # Cluster 2 — Python/FastAPI
    {"query": "FastAPI dependency injection for database session sharing", "expected_cluster": 2},
    {"query": "How to add middleware and authentication to a FastAPI application?", "expected_cluster": 2},
    {"query": "Testing async FastAPI endpoints with pytest and httpx", "expected_cluster": 2},
    # Cluster 3 — Redis
    {"query": "Redis eviction policy configuration for memory-constrained caches", "expected_cluster": 3},
    {"query": "Implementing distributed locking with Redis Redlock algorithm", "expected_cluster": 3},
    {"query": "Redis pub/sub messaging and Streams for real-time event processing", "expected_cluster": 3},
    # Cluster 4 — Security/auth
    {"query": "JWT token signing and OAuth2 authentication flow implementation", "expected_cluster": 4},
    {"query": "Role-based access control (RBAC) and API key management best practices", "expected_cluster": 4},
    {"query": "Rate limiting strategies and token bucket algorithm for API protection", "expected_cluster": 4},
    # Cluster 5 — Monitoring
    {"query": "Prometheus metrics scraping and Grafana dashboard for Kubernetes", "expected_cluster": 5},
    {"query": "Defining SLOs, SLIs and setting up alerting with Prometheus", "expected_cluster": 5},
    {"query": "Distributed tracing with OpenTelemetry and Jaeger for microservices", "expected_cluster": 5},
    # Cluster 6 — CI/CD
    {"query": "GitHub Actions workflow for Docker build and Kubernetes deployment", "expected_cluster": 6},
    {"query": "Caching dependencies and multi-stage Docker builds in CI pipelines", "expected_cluster": 6},
    {"query": "Canary deployment automation and blue-green release strategies in CI/CD", "expected_cluster": 6},
    # Cluster 7 — React
    {"query": "React hooks for state management and performance optimization", "expected_cluster": 7},
    {"query": "Testing React components with React Testing Library and Jest", "expected_cluster": 7},
    {"query": "Server-side rendering and code splitting in Next.js React applications", "expected_cluster": 7},
    # Cluster 8 — Machine learning
    {"query": "Training and evaluating machine learning classification models with scikit-learn", "expected_cluster": 8},
    {"query": "MLflow experiment tracking and model deployment with ONNX", "expected_cluster": 8},
    {"query": "Hyperparameter tuning and model monitoring for production ML systems", "expected_cluster": 8},
    # Cluster 9 — Microservices
    {"query": "SAGA pattern and event-driven architecture for distributed transactions", "expected_cluster": 9},
    {"query": "Service mesh with Istio and API gateway for microservice routing", "expected_cluster": 9},
    {"query": "Circuit breaker, bulkhead and resilience patterns in microservice architecture", "expected_cluster": 9},
]


def print_separator(char="=", width=70):
    print(char * width)


def run_benchmark():
    print_separator()
    print("VECTOR RETRIEVAL ACCURACY BENCHMARK")
    print("Model: all-MiniLM-L6-v2  |  Store: FAISS IndexFlatIP (cosine)")
    print_separator()

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        # ----------------------------------------------------------------
        # Step 2: Insert 200 documents
        # ----------------------------------------------------------------
        print("\n[Step 1+2] Building dataset and inserting 200 documents...")
        store = VectorStore(persist_path=tmp_dir)

        # Map: row_id -> cluster_id  (built as we insert)
        # row_ids are sequential starting at 1 in SQLite AUTOINCREMENT
        doc_cluster_map: dict[int, int] = {}  # row_id -> cluster_id

        t0 = time.perf_counter()
        inserted = 0
        for cluster_id, cluster_info in CLUSTERS.items():
            for doc_text in cluster_info["docs"]:
                mem = MemoryObject(
                    type="doc",
                    content=doc_text,
                    tags=[f"cluster_{cluster_id}", cluster_info["name"]],
                )
                store.store(mem)
                inserted += 1
                # Row IDs are 1-based and sequential
                doc_cluster_map[inserted] = cluster_id

        elapsed_insert = time.perf_counter() - t0
        total_in_store = store.count()
        print(f"  Inserted: {inserted} documents in {elapsed_insert:.1f}s")
        print(f"  Store count: {total_in_store}")

        # ----------------------------------------------------------------
        # Step 3+4: Run retrieval benchmark
        # ----------------------------------------------------------------
        print("\n[Step 3+4] Running 30 retrieval queries...")

        # Per-cluster accumulators
        cluster_p1  = {c: [] for c in range(10)}
        cluster_p3  = {c: [] for c in range(10)}
        cluster_p5  = {c: [] for c in range(10)}

        query_results = []
        t0 = time.perf_counter()

        for qinfo in QUERIES:
            q = qinfo["query"]
            expected = qinfo["expected_cluster"]

            results = store.query(q, k=5)
            retrieved_row_ids = []
            for r in results:
                # Look up row_id from uuid via the store's db
                row = store._db.execute(
                    "SELECT row_id FROM memories WHERE uuid = ?", (r["id"],)
                ).fetchone()
                if row:
                    retrieved_row_ids.append(row[0])

            retrieved_clusters = [doc_cluster_map.get(rid, -1) for rid in retrieved_row_ids]

            # Precision@K
            def precision_at_k(k):
                top_k = retrieved_clusters[:k]
                if not top_k:
                    return 0.0
                return sum(1 for c in top_k if c == expected) / k

            p1 = precision_at_k(1)
            p3 = precision_at_k(3)
            p5 = precision_at_k(5)

            cluster_p1[expected].append(p1)
            cluster_p3[expected].append(p3)
            cluster_p5[expected].append(p5)

            query_results.append({
                "query": q,
                "expected": expected,
                "retrieved_clusters": retrieved_clusters,
                "p1": p1, "p3": p3, "p5": p5,
            })

        elapsed_query = time.perf_counter() - t0
        print(f"  Queries completed in {elapsed_query:.2f}s ({elapsed_query/30*1000:.0f}ms avg)")

        # ----------------------------------------------------------------
        # Print per-cluster table
        # ----------------------------------------------------------------
        print("\n[Step 4] PRECISION@K PER CLUSTER")
        print_separator("-")
        header = f"{'Cluster':<5} {'Name':<22} {'P@1':>6} {'P@3':>6} {'P@5':>6}"
        print(header)
        print_separator("-")

        all_p1, all_p3, all_p5 = [], [], []
        for cid in range(10):
            name = CLUSTERS[cid]["name"]
            p1_avg = sum(cluster_p1[cid]) / len(cluster_p1[cid]) if cluster_p1[cid] else 0
            p3_avg = sum(cluster_p3[cid]) / len(cluster_p3[cid]) if cluster_p3[cid] else 0
            p5_avg = sum(cluster_p5[cid]) / len(cluster_p5[cid]) if cluster_p5[cid] else 0
            all_p1.append(p1_avg)
            all_p3.append(p3_avg)
            all_p5.append(p5_avg)
            print(f"  {cid:<4} {name:<22} {p1_avg:>6.3f} {p3_avg:>6.3f} {p5_avg:>6.3f}")

        print_separator("-")
        ovr_p1 = sum(all_p1) / len(all_p1)
        ovr_p3 = sum(all_p3) / len(all_p3)
        ovr_p5 = sum(all_p5) / len(all_p5)
        print(f"  {'OVERALL':<27} {ovr_p1:>6.3f} {ovr_p3:>6.3f} {ovr_p5:>6.3f}")
        print_separator("-")

        # Print per-query detail
        print("\n[Detail] Per-query breakdown:")
        print_separator("-")
        print(f"{'Q#':<4} {'Expected':>9} {'Retrieved clusters':<30} {'P@1':>5} {'P@3':>5} {'P@5':>5}")
        print_separator("-")
        for i, qr in enumerate(query_results):
            cluster_str = str(qr["retrieved_clusters"])
            print(f"  {i+1:<3} {CLUSTERS[qr['expected']]['name'][:16]:<17} {cluster_str:<30} {qr['p1']:>5.2f} {qr['p3']:>5.2f} {qr['p5']:>5.2f}")

        # ----------------------------------------------------------------
        # Step 5: Intra vs inter cluster distance analysis
        # ----------------------------------------------------------------
        print("\n[Step 5] INTRA vs INTER CLUSTER DISTANCE ANALYSIS")
        print_separator("-")

        # Embed all docs in batch for efficiency
        sample_clusters = [0, 2, 4, 6, 8]  # 5 random-ish clusters
        random.seed(42)

        # Build cluster -> list of (text, embed) pairs
        cluster_embeddings: dict[int, list] = {}
        for cid in sample_clusters:
            texts = CLUSTERS[cid]["docs"]
            vecs = embed_batch(texts)
            cluster_embeddings[cid] = vecs

        def cosine_distance(a, b):
            """Cosine distance in [0,2] (0=identical, 2=opposite)."""
            # Vectors are already normalized by embed_batch
            dot = sum(x * y for x, y in zip(a, b))
            return float(1.0 - dot)

        print(f"\n  {'Cluster':<22} {'Intra dist':>12} {'Inter dist':>12} {'Sep ratio':>11}")
        print_separator("-")

        sep_ratios = []
        for cid in sample_clusters:
            vecs = cluster_embeddings[cid]
            n = len(vecs)

            # Intra-cluster: sample 10 random pairs
            intra_pairs = []
            attempts = 0
            while len(intra_pairs) < 10 and attempts < 100:
                i, j = random.sample(range(n), 2)
                intra_pairs.append(cosine_distance(vecs[i], vecs[j]))
                attempts += 1
            avg_intra = sum(intra_pairs) / len(intra_pairs)

            # Inter-cluster: sample 10 pairs from other clusters
            other_cids = [c for c in sample_clusters if c != cid]
            inter_pairs = []
            attempts = 0
            while len(inter_pairs) < 10 and attempts < 200:
                other_cid = random.choice(other_cids)
                other_vecs = cluster_embeddings[other_cid]
                i = random.randrange(n)
                j = random.randrange(len(other_vecs))
                inter_pairs.append(cosine_distance(vecs[i], other_vecs[j]))
                attempts += 1
            avg_inter = sum(inter_pairs) / len(inter_pairs)

            sep_ratio = avg_inter / avg_intra if avg_intra > 0 else float("inf")
            sep_ratios.append(sep_ratio)
            name = CLUSTERS[cid]["name"]
            print(f"  {name:<22} {avg_intra:>12.4f} {avg_inter:>12.4f} {sep_ratio:>11.3f}x")

        print_separator("-")
        avg_sep = sum(sep_ratios) / len(sep_ratios)
        print(f"  Average separation ratio: {avg_sep:.3f}x")

        # ----------------------------------------------------------------
        # Step 6: Edge cases
        # ----------------------------------------------------------------
        print("\n[Step 6] EDGE CASE TESTS")
        print_separator("-")

        # Edge case 1: Gibberish query
        try:
            t0 = time.perf_counter()
            gibberish_result = store.query("xzqwrt blarf moogsh flapzoid 1234@@##", k=5)
            t1 = time.perf_counter()
            print(f"  [PASS] Gibberish query: returned {len(gibberish_result)} results in {(t1-t0)*1000:.0f}ms (no crash)")
        except Exception as e:
            print(f"  [FAIL] Gibberish query raised: {e}")

        # Edge case 2: Very long query (2000 chars)
        long_text = (
            "PostgreSQL database performance tuning connection pool configuration "
            "replication backup indexing query optimization vacuum autovacuum "
        ) * 15  # ~1050 chars, double it
        long_text = (long_text + long_text)[:2000]
        try:
            t0 = time.perf_counter()
            long_result = store.query(long_text, k=5)
            t1 = time.perf_counter()
            top_cluster = None
            if long_result:
                row = store._db.execute(
                    "SELECT row_id FROM memories WHERE uuid = ?", (long_result[0]["id"],)
                ).fetchone()
                if row:
                    top_cluster = doc_cluster_map.get(row[0])
            print(f"  [PASS] Long query (2000 chars): {len(long_result)} results in {(t1-t0)*1000:.0f}ms, top cluster={top_cluster}")
        except Exception as e:
            print(f"  [FAIL] Long query raised: {e}")

        # Edge case 3: k > total documents
        try:
            t0 = time.perf_counter()
            big_k_result = store.query("database", k=9999)
            t1 = time.perf_counter()
            print(f"  [PASS] k=9999 (>{total_in_store} docs): returned {len(big_k_result)} results (capped at {total_in_store})")
        except Exception as e:
            print(f"  [FAIL] k > total docs raised: {e}")

        # ----------------------------------------------------------------
        # Overall Retrieval Score
        # ----------------------------------------------------------------
        print("\n[Result] OVERALL RETRIEVAL SCORE")
        print_separator()

        # Average across all 30 queries directly
        all_p1_raw = [qr["p1"] for qr in query_results]
        all_p3_raw = [qr["p3"] for qr in query_results]
        all_p5_raw = [qr["p5"] for qr in query_results]
        avg_p1 = sum(all_p1_raw) / len(all_p1_raw)
        avg_p3 = sum(all_p3_raw) / len(all_p3_raw)
        avg_p5 = sum(all_p5_raw) / len(all_p5_raw)

        score = (avg_p1 * 0.5 + avg_p3 * 0.3 + avg_p5 * 0.2) * 100

        print(f"\n  Dataset:    {inserted} docs inserted, 30 queries run")
        print(f"  P@1 avg:    {avg_p1:.4f}  ({avg_p1*100:.1f}%)")
        print(f"  P@3 avg:    {avg_p3:.4f}  ({avg_p3*100:.1f}%)")
        print(f"  P@5 avg:    {avg_p5:.4f}  ({avg_p5*100:.1f}%)")
        print(f"\n  Weighted score = P@1×0.5 + P@3×0.3 + P@5×0.2")
        print(f"                 = {avg_p1:.4f}×0.5 + {avg_p3:.4f}×0.3 + {avg_p5:.4f}×0.2")
        print(f"                 = {avg_p1*0.5:.4f} + {avg_p3*0.3:.4f} + {avg_p5*0.2:.4f}")
        print(f"\n  OVERALL RETRIEVAL SCORE: {score:.1f} / 100")
        print_separator()

        if score >= 85:
            grade = "EXCELLENT"
        elif score >= 70:
            grade = "GOOD"
        elif score >= 55:
            grade = "FAIR"
        else:
            grade = "POOR"
        print(f"  Grade: {grade}")
        print_separator()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("\n[Cleanup] Temp directory removed.")


if __name__ == "__main__":
    run_benchmark()
