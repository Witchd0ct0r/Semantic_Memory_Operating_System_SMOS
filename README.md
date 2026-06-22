# SMOS — Semantic Memory Operating System

Persistent semantic memory for Claude Code. Store knowledge across sessions, compress files out of the context window, and retrieve everything by meaning — not by re-reading.

## Install

**Prerequisites:** Python 3.10+, [Claude Code CLI](https://claude.ai/code), [Ollama](https://ollama.com/download)

```bash
pip install smos-mcp
smos setup
```

The setup wizard installs dependencies, pulls the local LLM, registers the MCP server globally in Claude Code, and injects the file-reading policy into your `~/.claude/CLAUDE.md`.

Restart Claude Code when done. Verify with:

```bash
claude mcp list
```

---

## What it does

SMOS gives Claude a persistent memory layer backed by FAISS (vector search) and SQLite. Instead of holding large files in the context window, Claude compresses them on read, stores the summaries, and retrieves relevant knowledge on demand via natural language queries.

```
Without SMOS                    With SMOS
─────────────────────────────   ─────────────────────────────
Read file (2,000 tokens) →      tool_read_file_compress →
  stays in context forever        ~300-token summary stored
                                  original not in context

Synthesis: 20 files ×            Synthesis: 4 queries ×
  2,000 tokens = 40,000 ctx        ~300 tokens = 1,200 ctx
```

---

## Tools

| Tool | Purpose |
|------|---------|
| `tool_read_file_compress` | Read a file, compress it, store the summary — original stays out of context |
| `tool_semantic_store` | Store any text as a queryable memory |
| `tool_semantic_query` | Retrieve compressed context relevant to a natural language query |
| `tool_semantic_write` | Store a typed, tagged memory object |
| `tool_store_verbatim` | Store exact content (code, diffs) losslessly by key |
| `tool_retrieve` | Retrieve verbatim content by key |
| `tool_write_file_safe` | Write files to the sandboxed workspace |

---

## How Claude uses it

Once installed, Claude follows this policy automatically (injected via `CLAUDE.md`):

1. **Before reading anything** — query memory first (`tool_semantic_query`). If the answer is there, skip the read.
2. **For files not being edited** — use `tool_read_file_compress`. The file is compressed by a local LLM; only the ~300-token summary enters context.
3. **For files about to be edited** — use the built-in Read tool to get exact current content.
4. **For synthesis** — query memory instead of re-reading already-compressed files.
5. **For code / diffs / exact data** — use `tool_store_verbatim` (lossless), retrieve with `tool_retrieve`.

---

## When SMOS saves tokens

| Scenario | Benefit |
|----------|---------|
| Multi-session work on the same codebase | No re-reading on session 2+ |
| Corpus > 50K tokens | Fits what would overflow context |
| Iterative analysis from multiple angles | Query the same stored knowledge repeatedly |
| Long conversations | Previous responses stored out-of-context, not accumulating |

Single-session audits of small codebases (< 10 files) see no token savings — the overhead of tool calls outweighs the savings. SMOS pays off at scale and across sessions.

---

## Configuration

Environment variables (set in `~/.smos/.env` or your shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Local LLM for summarization |
| `SUMMARIZER_MAX_TOKENS` | `512` | Max tokens per summary |

### Model options

| Model | Size | RAM | Quality |
|-------|------|-----|---------|
| `qwen2.5:7b` | 4.7 GB | 16 GB | Best |
| `qwen2.5:3b` | 2.0 GB | 8 GB  | Good |
| `qwen2.5:1.5b` | 0.9 GB | 6 GB | Fast |
| none | — | — | Extractive fallback |

Without Ollama, SMOS falls back to extractive summarization (first sentences of the file). Semantic querying and verbatim storage still work normally.

---

## Data storage

All data lives in `data/` relative to where the server runs:

- `data/faiss.index` — vector index for semantic search
- `data/metadata.db` — SQLite with memory content, tags, tiers, and verbatim store

The database survives crashes: on restart, SMOS detects FAISS/SQLite divergence and rebuilds the index from SQLite automatically.

---

## Development

```bash
git clone https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS
cd Semantic_Memory_Operating_System_SMOS
pip install -e ".[dev]"
pytest tests/
```

Run the server directly:

```bash
python -m smos
# or
smos-server
```

---

## License

MIT
