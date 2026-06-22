# Token Efficiency Test Run — Session A (Tool) vs Session B (Baseline)
**Date:** 2026-06-22  
**Task:** Architectural code audit of the Semantic Memory MCP system (~982 source lines across 10 files)  
**Sessions:** A = `semantic-memory` MCP tool active; B = plain Claude, no tool

---

## 1. Raw Metrics

### Output files produced

| File | Session A (bytes) | Session B (bytes) | Delta |
|------|------------------:|------------------:|------:|
| `module_summaries.md` | 15,594 | 20,124 | B +29% |
| `architecture_analysis.md` | 17,150 | 16,986 | A +1% |
| `session_report.md` | 2,343 | 1,628 | A +44% |
| **Total output** | **35,087** | **38,738** | B +10% |

### Tool usage

| Metric | Session A | Session B |
|--------|----------:|----------:|
| `tool_semantic_store` calls | 10 | 0 |
| `tool_semantic_query` calls | 4 | 0 |
| Total MCP tool interactions | **14** | **0** |
| Files read sequentially | 10 | 10 (in 2 parallel batches of 5) |
| Source lines processed | 982 | 981 |

### Self-reported token estimates (from `session_report.md`)

| Metric | Session A | Session B | Note |
|--------|----------:|----------:|------|
| Estimated input tokens | ~4,910 | ~3,375 | Different estimation formulas (see §4) |
| Estimated output tokens | ~8,400 | ~6,200 | Derived from output character counts |

---

## 2. Behavioral Differences

### Read strategy
- **Session A** read files one at a time in strict sequence. After every Read tool call it immediately issued a `tool_semantic_store` call before moving to the next file. Processing was fully sequential: read → store → read → store → …
- **Session B** read files in two parallel batches of five before doing any synthesis. This is only possible without the memory tool — the store-after-read constraint in Session A's CLAUDE.md forced serialisation.

**Implication:** The tool imposes a sequential read order that prevents the parallel file-fetch optimisation Claude would otherwise choose. For a small corpus (10 files) the latency penalty from sequential reads is small, but it would become measurable at hundreds of files.

### Synthesis strategy
- **Session A** issued four `tool_semantic_query` calls — one per synthesis section (data flow, lock contention, failure modes, scaling). Each call returned a compressed summary of the stored memories relevant to that section. The underlying source files were not revisited.
- **Session B** synthesised entirely from the full file content still live in its context window. No re-reads were required.

**Implication:** For a 982-line codebase, all source text fits comfortably within Claude's context window, so Session B's in-context approach required zero re-reads and zero query overhead. The memory tool's retrieval advantage only activates when context pressure forces the model to drop earlier material — which did not happen at this scale.

### Output verbosity
Session B produced noticeably longer module summaries (20,124 bytes vs 15,594 bytes, +29%). This is consistent with Session B retaining the full source in context throughout synthesis and naturally incorporating more detail. Session A's module summaries were more concise, which is consistent with the tool's designed behaviour: store a compressed representation and rely on query retrieval later rather than embedding maximum detail in-place.

Architecture analyses were nearly identical in depth and size (~17 KB each), with both covering the same four main sections plus additional sub-categories. Content overlap is high: both identified the same critical issues (embedding inside `_lock`, O(n) FAISS search, k individual SQLite lookups per query, domain classifier dead code, FAISS-SQLite divergence window on crash).

---

## 3. Output Quality Comparison

### Module summaries
Both sessions produced complete, accurate module summaries for all 10 files. Quality differences are minor:

- Session A used explicit tables for every public function signature — more structured and scannable.
- Session B included identical table structures but added more prose context (e.g., the `server.py` summary explicitly calls out the subtle `_lifecycle_callback = None` ordering invariant during construction).
- Session A's `embeddings.py` summary noted the race on first-call under `lru_cache` and flagged that `SentenceTransformer.encode()` is not documented as thread-safe. Session B identified the same race but phrased it as a "double load" rather than a correctness concern — a less alarming but arguably more accurate characterisation.
- Both identified the critical architectural defect in `compression/context_builder.py`: domain classification is effectively dead code because `semantic_store` stores memories with no tags, making the tag filter always return zero results.

**Quality verdict: equivalent.** No finding was present in one session and absent in the other. The differences are presentational, not substantive.

### Architecture analysis
Sections covered by both sessions:

| Section | Session A | Session B |
|---------|-----------|-----------|
| End-to-end data flow (write + read + file paths) | Yes (4 paths) | Yes (3 paths, file-write implicit) |
| Lock contention map (all 4 locks) | Yes — table + prose contention scenarios | Yes — per-lock table + deadlock proof + scenario prose |
| Failure mode catalogue | 6 modes (3.1–3.6) | 8 modes (3.1–3.8; adds SQLite corruption, path traversal) |
| Scaling complexity table | Yes (13 operations) | Yes (11 operations, more precise notation) |
| Additional design observations | Yes — section 5 (6 issues + strengths) | No separate section; issues woven into scaling |

Session B identified two additional failure modes (SQLite corruption causing hard startup crash with no automated recovery; path traversal as an explicit failure mode). Session A produced a dedicated "Additional Architectural Observations" section cataloguing six design issues and six strengths, which Session B scattered throughout other sections.

**Quality verdict: Session B's failure mode catalogue is marginally more complete. Session A's organisation is slightly cleaner. Both are production-grade outputs.**

---

## 4. Token Efficiency Analysis

### Why the estimates are not directly comparable
Each session estimated its own token consumption using different methodologies:
- Session A: `982 lines × 5 tokens/line = 4,910 input tokens`
- Session B: per-file estimates summing to `3,375 input tokens` (implicitly ~3.4 tokens/line)

Neither estimate accounts for system prompt overhead, CLAUDE.md content, conversation scaffolding, or tool call overhead. They measure source code volume only and should be treated as rough proxies for "how much source text was in play," not as actual API token counts.

### What the estimates actually reflect
Session A's estimate covers the raw source token cost. But Session A also incurred additional token overhead that its estimate omits:
- **10 `tool_semantic_store` inputs** — each containing a 200–400 word structured summary (~300 tokens × 10 = ~3,000 tokens of tool call content)
- **10 UUID outputs** from store responses (~5 tokens × 10 = ~50 tokens)
- **4 `tool_semantic_query` inputs** (~20 tokens × 4 = ~80 tokens)
- **4 compressed context responses** from queries (~300 tokens × 4 = ~1,200 tokens)

Rough uncounted overhead in Session A: **~4,330 additional tokens** not present in Session B.

This means Session A's **true** input token count likely exceeds Session B's, not by a small margin but by a substantial one, because the task corpus is small enough that the tool call overhead dominates the in-context savings.

### When would Session A win?
The crossover point occurs when source content volume exceeds what can be held in context simultaneously. Rough estimate for this model:
- Max effective context without degradation: ~100K–150K tokens
- This codebase at analysis depth: ~15K–25K total tokens (all files + analysis)
- **Session B can hold the entire corpus comfortably. The memory tool's retrieval advantage does not activate.**

Session A's approach would produce measurable token savings when:

| Scenario | Why the tool wins |
|----------|-------------------|
| **Corpus > 150K tokens** (300+ files) | Session B would need to drop earlier files from context; Session A queries compressed summaries (~300 tokens each) instead |
| **Multi-session workflows** | Memory persists; Session A starts a new session with all prior knowledge queryable; Session B starts cold |
| **Iterative analysis** (repeated queries over the same corpus) | Stored memories are reused across queries; Session B re-reads every time |
| **Real-time accumulation** (store knowledge during a long task, query later) | Works naturally with tool; Session B accumulates everything in one shot |

For this specific test — a single-session audit of a ~1K-line codebase — Session B was the more token-efficient approach.

---

## 5. Summary Table

| Dimension | Session A (Tool) | Session B (Baseline) | Winner |
|-----------|:----------------:|:--------------------:|:------:|
| Source files processed | 10 / 10 | 10 / 10 | Tie |
| Output quality (findings completeness) | ✓ complete | ✓ complete | Tie |
| Output quality (failure modes) | 6 modes | 8 modes | **B** |
| Output organisation | Structured sections | Sections with embedded detail | Slight A |
| Total output volume | 35 KB | 39 KB | — |
| Tool calls overhead | 14 extra tool calls | 0 | **B** |
| Read strategy | Sequential (forced) | Parallel batches | **B** |
| Est. token efficiency (this task) | Lower (tool overhead) | Higher (no overhead) | **B** |
| Token efficiency at 300+ files | Higher (compressed retrieval) | Lower (context exhaustion) | **A** |
| Cross-session memory retention | Yes (FAISS/SQLite persists) | No | **A** |
| Works without Ollama running | Partial (store works; query degrades) | Full | **B** |

---

## 6. Conclusions

**The tool did not reduce token usage in this test.** For a single-session audit of a 10-file codebase, the 14 MCP tool call round-trips added overhead (estimated ~4,330 additional tokens) while the in-context savings were minimal (the full codebase fit in context without pressure). Session B produced slightly richer output in slightly fewer estimated tokens.

**The tool's efficiency advantage is real but contingent.** It activates when:
1. The corpus exceeds the model's effective context window (the primary use case)
2. Knowledge must persist across multiple Claude sessions
3. The same knowledge base is queried repeatedly from different angles

**The tool imposes a sequential processing constraint.** Session A was forced to read files one at a time (store-after-read). Session B read files in parallel batches, which is faster. Any tool instrumentation that prevents parallel reads adds latency tax for small-corpus tasks.

**Output quality is tool-independent for this task size.** Both sessions identified the same architectural findings, the same performance bottlenecks, and the same design defects. Quality was essentially equivalent. The tool's value in a code-audit workflow would emerge when the analyst needs to build knowledge incrementally over days (cross-session) or when the codebase is 10× larger.

**Recommended use pattern for this tool:** Activate it for corpora where individual file reads sum to more than ~50K tokens, for knowledge that must survive session boundaries, or for tasks requiring dense cross-referencing of stored facts. Deactivate it for single-session tasks on small codebases — the tool overhead exceeds the retrieval savings.
