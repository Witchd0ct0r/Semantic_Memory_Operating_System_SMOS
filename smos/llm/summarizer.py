from __future__ import annotations

import os

from smos.llm.client import get_llm_client, OLLAMA_MODEL

_MAX_TOKENS = int(os.getenv("SUMMARIZER_MAX_TOKENS", "512"))

_SUMMARIZE_SYSTEM = (
    "You are a precise technical summarizer. "
    "Compress the provided text into a dense, information-rich summary. "
    "Preserve key facts, decisions, and technical details. "
    "Output only the summary — no preamble, no metadata."
)

_COMPRESS_SYSTEM = (
    "You are a context compression engine. "
    "You receive multiple retrieved memory fragments and a query. "
    "Synthesize them into a single dense, accurate context summary "
    "that directly addresses the query. "
    "Preserve technical specifics. Remove redundancy. "
    "Output only the summary."
)

_COMPRESS_SYSTEM_STRICT = _COMPRESS_SYSTEM + " Be concise. One paragraph maximum."


def _is_valid_output(text: str) -> bool:
    stripped = text.strip()
    return 5 < len(stripped) < 4000


def _extractive_fallback_from_memories(memories: list[dict]) -> str:
    parts: list[str] = []
    for m in memories[:3]:
        content = m["content"].strip()
        for delim in (".", "!", "?", "\n"):
            idx = content.find(delim)
            if idx > 10:
                parts.append(content[: idx + 1].strip())
                break
        else:
            parts.append(content[:150])
    return " ".join(parts) if parts else memories[0]["content"][:300]


def _extractive_fallback_from_text(text: str) -> str:
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    return ". ".join(sentences[:3])[:500]


def summarize_text(text: str, context_hint: str = "") -> str:
    if not text.strip():
        return ""
    client = get_llm_client()
    user_content = f"Context: {context_hint}\n\nText:\n{text}" if context_hint else text

    def _call() -> str:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()

    try:
        result = _call()
        if not _is_valid_output(result):
            result = _call()
        if not _is_valid_output(result):
            return _extractive_fallback_from_text(text)
        return result
    except Exception:
        return _extractive_fallback_from_text(text)


def compress_memories_full(
    memories: list[dict], query: str
) -> tuple[str, float, str]:
    """Return (summary, confidence, mode) where mode is abstractive/extractive/uncertain."""
    if not memories:
        return "", 0.0, "uncertain"

    sections = [f"[{m['id'][:8]}] {m['content']}" for m in memories]
    combined = "\n\n".join(sections)
    user_content = f"Query: {query}\n\nMemory fragments:\n{combined}"

    client = get_llm_client()

    def _call(system: str) -> str:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            max_tokens=_MAX_TOKENS,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()

    try:
        summary = _call(_COMPRESS_SYSTEM)
        if not _is_valid_output(summary):
            summary = _call(_COMPRESS_SYSTEM_STRICT)
        if not _is_valid_output(summary):
            summary = _extractive_fallback_from_memories(memories)
            mode: str = "extractive"
        else:
            mode = "abstractive"
    except Exception:
        summary = _extractive_fallback_from_memories(memories)
        mode = "uncertain"

    avg_distance = sum(m["distance"] for m in memories) / len(memories)
    confidence = max(0.0, min(1.0, 1.0 - (avg_distance / 2.0)))

    return summary, confidence, mode


def compress_memories(memories: list[dict], query: str) -> tuple[str, float]:
    """Backward-compatible wrapper — returns (summary, confidence)."""
    summary, confidence, _ = compress_memories_full(memories, query)
    return summary, confidence
