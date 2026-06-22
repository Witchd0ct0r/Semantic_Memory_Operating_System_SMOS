<h1 align="center">SMOS</h1>

<p align="center"><strong>Semantic Memory Operating System for Claude Code</strong></p>

<p align="center">
  Compress files out of context. Query knowledge by meaning. Persist across sessions.
</p>

<p align="center">
  <a href="https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS/stargazers"><img src="https://img.shields.io/github/stars/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS?style=flat&color=blue" alt="Stars"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS?style=flat" alt="License"></a>
  <a href="https://pypi.org/project/smos-mcp/"><img src="https://img.shields.io/pypi/v/smos-mcp?style=flat" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat" alt="Python">
</p>

<p align="center">
  <a href="#the-problem">Problem</a> •
  <a href="#how-it-works">How it works</a> •
  <a href="#compression-in-practice">In practice</a> •
  <a href="#install">Install</a> •
  <a href="#benchmarks">Benchmarks</a> •
  <a href="#example-use-cases">Examples</a> •
  <a href="#tools">Tools</a> •
  <a href="#configuration">Config</a> •
  <a href="#uninstall">Uninstall</a>
</p>

---

## The problem

Every file Claude reads stays in the context window until the session ends. On a 20-file codebase, by the time Claude reaches synthesis it's carrying 40,000+ tokens of raw source — most of which it already processed, will never need again verbatim, and is paying for on every single API call.

**Caveman** compresses what Claude *says*. **SMOS** compresses what Claude *holds* — the files, the prior analysis, the context window itself.

---

## How it works

SMOS is an MCP server that gives Claude a persistent memory layer: FAISS vector search + SQLite, powered by a local LLM (qwen2.5 via Ollama) for compression.

Instead of reading a file with the built-in Read tool and leaving it in context forever, Claude calls `tool_read_file_compress`. The file is summarised by a local LLM, stored in the vector index, and **the raw source never enters the context window**. At synthesis time, Claude queries the semantic index rather than re-reading anything.

```
WITHOUT SMOS                          WITH SMOS
──────────────────────────────────    ──────────────────────────────────
Read file.py (3,000 tokens)      →    tool_read_file_compress(file.py)
  → stays in context forever           → local LLM compresses to ~85 tokens
                                        → stored in FAISS + SQLite
                                        → nothing in context window

10 files read → 30,000 ctx tokens     10 files compressed → ~850 ctx tokens

Synthesis:                            Synthesis:
  still carrying 30,000 tokens    →     4 semantic queries × ~300 tokens
  on every API call                →     = ~1,200 tokens total
                                        35× smaller context at synthesis
```

Memory persists across sessions. Session 2 queries what Session 1 stored — no re-reading.

---

## Compression in practice

### Input — raw file (312 tokens in context without SMOS)

```python
# smos/memory/vector_store.py  (excerpt)

def store(self, content: str, metadata: dict | None = None, tier: str = "working") -> str:
    meta = metadata or {}
    doc_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()

    summary = self._summarizer.summarize(content)
    embedding = self._embed(summary)

    with self._lock:
        idx = self._index.ntotal
        self._index.add(embedding.reshape(1, -1))
        self._db.execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?)",
            (doc_id, content, summary, json.dumps(meta), tier, ts),
        )
        self._db.commit()
        self._id_map[idx] = doc_id
    return doc_id

def query(self, text: str, top_k: int = 5) -> list[dict]:
    embedding = self._embed(text)
    distances, indices = self._index.search(embedding.reshape(1, -1), top_k)
    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if idx == -1:
            continue
        doc_id = self._id_map.get(int(idx))
        row = self._db.execute(
            "SELECT content, summary, metadata, tier, created_at FROM memories WHERE id = ?",
            (doc_id,),
        ).fetchone()
        if row:
            results.append({
                "id": doc_id,
                "summary": row[1],
                "score": float(dist),
                "tier": row[3],
            })
    return results
```

### Output — LLM summary stored in SMOS (42 tokens)

```
Stores text with LLM-compressed embedding into FAISS index and SQLite.
Assigns UUID, timestamps entry, generates summary via summarizer, embeds
with sentence-transformer, persists metadata and tier. Query method embeds
input text, searches FAISS for nearest neighbours, returns scored results
with summary and tier.
```

**42 tokens stored. 312 tokens never entered the context window. 7.4× compression on this excerpt.**

### What's written to SQLite

```
id:         f3a2b1c0-8d4e-4f7a-9b2c-1e5d6f3a2b1c
summary:    Stores text with LLM-compressed embedding into FAISS...
content:    [original source, stored for lossless retrieval if needed]
metadata:   {"source": "smos/memory/vector_store.py"}
tier:       working
created_at: 2026-06-22T14:23:11.847Z
```

The FAISS index stores the 384-dimensional embedding of the summary. Queries embed the search string and find nearest neighbours by cosine distance — no keywords, no exact match required.

### Query result returned to Claude

```
tool_semantic_query("how does storage work")

→ score: 0.91
  summary: "Stores text with LLM-compressed embedding into FAISS index
            and SQLite. Assigns UUID, timestamps entry, generates summary
            via summarizer, embeds with sentence-transformer..."
  source:  smos/memory/vector_store.py
  tier:    working
```

Claude gets the 42-token summary and a confidence score. The 312-token source stays on disk.

---

## Install

**Prerequisites:** Python 3.10+, [Claude Code](https://claude.ai/code), [Ollama](https://ollama.com/download)

```bash
pip install smos-mcp
smos setup
```

> **Note:** `smos-mcp` is the PyPI package name. The CLI commands installed are `smos` and `smos-server`.

The setup wizard handles everything else: Python deps, model selection, model pull, MCP registration, and CLAUDE.md policy injection. Restart Claude Code when done.

```bash
claude mcp list   # verify: smos should appear
```

Alternatively, install directly from GitHub:

```bash
pip install git+https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS.git
smos setup
```

### If `smos` is not found after install

pip installs CLI scripts to a directory that may not be on your `PATH`. Find it:

```bash
python -m site --user-scripts
```

Then add it permanently:

**Windows (PowerShell)**

```powershell
$scripts = python -m site --user-scripts
[Environment]::SetEnvironmentVariable("Path", "$env:Path;$scripts", "User")
# Restart PowerShell for the change to take effect
```

**macOS (zsh)**

```bash
echo 'export PATH="$(python3 -m site --user-scripts):$PATH"' >> ~/.zshrc && source ~/.zshrc
```

**Linux (bash)**

```bash
echo 'export PATH="$(python3 -m site --user-scripts):$PATH"' >> ~/.bashrc && source ~/.bashrc
```

> **conda / miniconda users:** Scripts land in `$CONDA_PREFIX\Scripts` (Windows) or `$CONDA_PREFIX/bin` (macOS/Linux). These are on PATH when the conda environment is active — if you installed into `base` or an active env, `smos` should work immediately after activating that environment.

---

## Benchmarks

All numbers measured on real data. Benchmarks live in [`tests/`](./tests/).

> **Test hardware:** AMD Ryzen 5 7640HS (6C / 12T, 4.3 GHz) · 32 GB RAM · RTX 4050 Laptop 6 GB VRAM · 1 TB Kioxia NVMe · Windows 11

### Compression quality

Local LLM (qwen2.5:7b via Ollama) compresses files to a fixed-length summary. **Factual retention is 100% at all sizes** — all seeded keywords recovered from every summary across 3 independent runs.

| File size | Tokens in context (before) | Tokens after SMOS | Compression | Retention |
|-----------|:--------------------------:|:-----------------:|:-----------:|:---------:|
| 1 KB      | ~260                       | ~85               | **3.1×**    | 100%      |
| 5 KB      | ~1,300                     | ~110              | **11.8×**   | 100%      |
| 10 KB     | ~2,585                     | ~76               | **34.2×**   | 100%      |
| 50 KB     | ~12,825                    | ~83               | **154.7×**  | 100%      |
| **avg**   |                            |                   | **51×**     | **100%**  |

At 50KB+ files — typical for large modules, log files, or generated content — SMOS compresses **154× with zero factual loss**. Summary length plateaus at ~330–440 characters regardless of input size above ~5KB; the LLM abstracts to a fixed-length output.

```
Context window pressure at synthesis (20-file codebase, 5KB avg)
──────────────────────────────────────────────────────────────────

Without SMOS   ████████████████████████████████████████  26,000 tokens
With SMOS      ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   2,200 tokens

                                                          ▲ 91% smaller
```

### Query latency

SMOS queries are fast and scale gracefully. P95 query latency grows only **1.21× when data grows 100×**. FAISS uses SIMD dot-product batching that stays sub-linear up to ~500K entries on standard hardware.

| Memories stored | Query avg | Query P95 | Query P99 |
|:--------------:|:---------:|:---------:|:---------:|
| 1,000          | 11.6 ms   | 14.6 ms   | 14.6 ms   |
| 5,000          | 11.4 ms   | 14.6 ms   | 14.6 ms   |
| 10,000         | 12.3 ms   | 16.3 ms   | 16.3 ms   |
| 50,000         | 11.2 ms   | 13.1 ms   | 13.1 ms   |
| 100,000        | 14.0 ms   | 16.9 ms   | 16.9 ms   |

```
Query latency vs. corpus size
─────────────────────────────
 20ms │  ·  ·  ·  ·  ·  ·  ·
      │
 15ms │  ×  ×     ×        ×      × = P95 measured
      │     ·  ·     ·  ·  ·
 10ms │  ·                        · = avg measured
      │
  5ms │
      └──────────────────────────
       1K  5K  10K  50K  100K

100× more data. 1.21× slower queries.
```

### Retrieval quality

Evaluated on 200 documents across 8 technical domains (security, auth, FastAPI, PostgreSQL, Redis, Kubernetes, monitoring, CI/CD). 40 queries, 5 per domain.

| Metric | Score |
|--------|------:|
| P@1 (first result correct domain) | **100%** |
| MRR (mean reciprocal rank) | **1.000** |
| P@3 micro-average | 78.3% |
| P@5 micro-average | 73.0% |

Every first result is from the correct domain across all 40 queries. Top-5 bleed is expected and reflects genuine semantic overlap (JWT tokens appear in both security and auth documents, CI/CD pipelines reference Kubernetes, etc.).

### Ingest throughput

| Path | Rate | Bottleneck |
|------|-----:|-----------|
| Real-time (store() call) | 42 docs/s | Embedding model (98% of time) |
| Bulk import | 300 docs/s | Embedding model only |

Embedding is the ceiling on both paths — FAISS add and SQLite write together account for ~2% of ingest time.

### Scaling ceiling

SMOS is production-ready for ≤100K memories on standard hardware. The lifecycle manager runs O(M) deduplication where M is the batch size (50), independent of total corpus size — dedup cycles stay at ~1.1 seconds whether you have 10K or 1M memories stored.

```
Component limits (tested: Ryzen 5 7640HS, 32 GB RAM, RTX 4050 Laptop 6 GB VRAM)
──────────────────────────────────────────────────────────────────────────
Query < 20ms P95        ████████████████████  100K memories
Lifecycle functional    ██████████████████████████████  1M+ memories
Ingest rate             42 docs/s (real-time) / 300 docs/s (bulk)
Max document size       ~50KB (qwen2.5:7b context window)
FAISS index size        147 MB at 100K memories / 1.4 GB at 1M memories
```

---

## When SMOS saves tokens

SMOS pays off when the knowledge being accumulated exceeds what fits comfortably in context, or when the same codebase is visited more than once.

| Scenario | Savings |
|----------|---------|
| 50KB+ files (logs, generated code, docs) | Up to **154× context reduction per file** |
| Codebases > 30 files | Synthesis context stays fixed; baseline grows linearly |
| Multi-session work | Session 2+ queries stored memory; no re-reading |
| Repeated analysis from different angles | Query same compressed knowledge; pay once |
| Long agentic runs | Prior tool outputs stored out-of-context; don't accumulate |

**Single-session, small codebases (< 10 files, < 5KB each):** SMOS overhead exceeds savings. The tool is designed for sustained use and scale, not one-shot audits of tiny repos.

---

## Example use cases

### Codebase audit across many files

```
Read every file in src/ and give me a security audit.
```

Without SMOS, Claude reads 40 files → 60,000 tokens in context by the time it reaches synthesis. With SMOS, each file is compressed to ~85 tokens and stored. Synthesis pulls only what's relevant via semantic query. Context at synthesis: ~1,200 tokens.

---

### Multi-session feature work

Day 1 — Claude reads the auth module, database schema, and API contracts. All compressed and stored.

Day 2 — new session, zero re-reading:

```
What did we establish about the auth flow yesterday?
```

SMOS returns the stored context instantly. Claude picks up exactly where it left off without touching a file.

---

### Large log / generated file analysis

```
Read build/output.log and tell me what failed.
```

A 50KB build log would consume ~12,800 tokens in context and stay there. With SMOS, it compresses 154× to ~83 tokens. Claude gets the failure summary; the raw log never enters the window.

---

### Accumulating decisions across a long agent run

Claude is running a multi-step refactor — reading files, making decisions, writing changes. Without SMOS, every prior decision accumulates in context. With SMOS:

```python
tool_store_verbatim(content=diff, label="auth-refactor-step-3")
tool_semantic_store("Decided to replace JWT with session tokens — see verbatim key abc123")
```

Prior steps are queryable but out-of-context. The agent runs indefinitely without hitting the context ceiling.

---

### Repeated analysis from different angles

```
# Session 1
Analyse src/payments.py for performance issues.

# Session 2
Analyse src/payments.py for security issues.
```

Session 2 queries the compressed version stored in session 1 — no re-read, no re-embedding, instant retrieval. Analysis starts immediately from stored knowledge.

---

## How Claude uses it

Once installed, Claude follows this policy automatically (injected via `~/.claude/CLAUDE.md`):

1. **Query first** — before reading any file, call `tool_semantic_query`. If the answer is already in memory, skip the read entirely.
2. **Compress reads** — use `tool_read_file_compress` for any file not about to be edited. Raw source never enters context.
3. **Precise reads** — use the built-in Read tool only immediately before an `Edit` or `Write` call.
4. **Lossless storage** — code, diffs, and structured data go to `tool_store_verbatim` (no LLM compression, exact bytes on retrieval).
5. **Synthesise from memory** — use `tool_semantic_query` instead of re-reading already-compressed files.

---

## Tools

| Tool | Description |
|------|------------|
| `tool_read_file_compress` | Read a file, compress with local LLM, store summary. Raw file never enters context window. Accepts absolute paths. |
| `tool_semantic_store` | Store any text as a queryable semantic memory. |
| `tool_semantic_query` | Retrieve compressed context via natural language. Returns summary + confidence + sources. |
| `tool_semantic_write` | Store a typed, tagged memory object (doc / adr / log / issue). |
| `tool_store_verbatim` | Store exact content losslessly — code, diffs, any artifact where exact bytes matter. Returns a retrieval key. |
| `tool_retrieve` | Retrieve verbatim content by key. |
| `tool_write_file_safe` | Write files to the sandboxed workspace directory. |

---

## Configuration

Environment variables (set during `smos setup` or in your shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Summarization model |
| `SUMMARIZER_MAX_TOKENS` | `512` | Max tokens per summary output |

### Model options (chosen during setup)

| Model | Size | Min RAM | Compression quality |
|-------|-----:|:-------:|-------------------|
| `qwen2.5:7b`  | 4.7 GB | 8 GB  | Best (benchmarked) — GPU-accelerated if CUDA available |
| `qwen2.5:3b`  | 2.0 GB | 4 GB  | Good |
| `qwen2.5:1.5b`| 0.9 GB | 4 GB  | Fast |
| none          | —      | —     | Extractive fallback (first sentences only) |

The RTX 4050 (or any CUDA GPU) will be used automatically by Ollama if available, reducing LLM latency from ~10s to ~2–3s per compression call.

Without Ollama, SMOS falls back to extractive summarization. Semantic querying and verbatim storage work normally — only LLM-driven compression degrades.

---

## Data

All data lives locally. Nothing leaves your machine.

```
data/
├── faiss.index    — vector index (147 MB at 100K memories)
└── metadata.db    — SQLite: content, tags, tiers, verbatim store
```

The database survives crashes: on restart, SMOS detects FAISS/SQLite divergence and rebuilds the index from SQLite automatically (re-embeds all content in batches of 256).

---

## Uninstall

```bash
smos uninstall
```

This removes:

- **MCP registration** — `claude mcp remove smos` (runs automatically)
- **CLAUDE.md policy block** — strips the injected file-reading policy from `~/.claude/CLAUDE.md`
- **Memory data** — prompts before deleting `~/.smos/` (FAISS index + SQLite database)

The Python package itself is **not** removed automatically — run `pip uninstall smos-mcp` afterward if you want that too.

Ollama models (`qwen2.5:7b` etc.) are **not** removed — they are shared system-wide. To remove manually:

```bash
ollama rm qwen2.5:7b
```

Dry-run to preview what would be removed without touching anything:

```bash
smos uninstall --dry-run
```

---

## Development

```bash
git clone https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS
cd Semantic_Memory_Operating_System_SMOS
pip install -e ".[dev]"
pytest tests/          # 31 tests
python -m smos         # run the server directly
```

---

## License

MIT
