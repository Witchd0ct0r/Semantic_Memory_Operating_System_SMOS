from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from smos.ingest.adapters.base import DomainAdapter
from smos.memory.embeddings import embed

# Character-based proxy for all-MiniLM-L6-v2's 256-token truncation window.
# ~4 chars/token for English prose, with margin — avoids loading the
# tokenizer just to count tokens during segmentation.
_MAX_SEGMENT_CHARS = 800
_MIN_SEGMENT_CHARS = 20
_FIXED_WINDOW_CHARS = 700
_SEMANTIC_VALLEY_THRESHOLD = 0.35

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Span:
    start: int
    end: int
    children: list["Span"] = field(default_factory=list)
    memory_id: Optional[str] = None

    @property
    def is_leaf(self) -> bool:
        return not self.children


def _fits(text: str) -> bool:
    return len(text) <= _MAX_SEGMENT_CHARS


def _split_sentences(text: str) -> list[tuple[int, int]]:
    bounds = [m.end() for m in _SENTENCE_BOUNDARY_RE.finditer(text)]
    starts = [0] + bounds
    ends = bounds + [len(text)]
    return [(s, e) for s, e in zip(starts, ends) if text[s:e].strip()]


def _fixed_size_split(text: str) -> list[tuple[int, int]]:
    spans = []
    n = len(text)
    pos = 0
    while pos < n:
        end = min(pos + _FIXED_WINDOW_CHARS, n)
        spans.append((pos, end))
        pos = end
    return spans or [(0, n)]


def _semantic_boundaries(text: str) -> list[tuple[int, int]]:
    sentences = _split_sentences(text)
    if len(sentences) < 2:
        return [(0, len(text))] if sentences else []

    vectors = [np.array(embed(text[s:e])) for s, e in sentences]
    sims = [float(np.dot(vectors[i], vectors[i + 1])) for i in range(len(vectors) - 1)]

    boundaries = [0]
    for i, sim in enumerate(sims):
        is_local_min = (i == 0 or sim <= sims[i - 1]) and (i == len(sims) - 1 or sim <= sims[i + 1])
        if is_local_min and sim < _SEMANTIC_VALLEY_THRESHOLD:
            boundaries.append(i + 1)
    boundaries.append(len(sentences))

    chunks = []
    for i in range(len(boundaries) - 1):
        chunk_sentences = sentences[boundaries[i]:boundaries[i + 1]]
        if not chunk_sentences:
            continue
        chunks.append((chunk_sentences[0][0], chunk_sentences[-1][1]))
    return chunks


def _build_span_tree(full_text: str, start: int, end: int, adapter: DomainAdapter) -> Span:
    span_text = full_text[start:end]
    node = Span(start=start, end=end)

    structural = adapter.structural_boundaries(span_text)
    if len(structural) > 1:
        for s, e in structural:
            node.children.append(_build_span_tree(full_text, start + s, start + e, adapter))
        return node

    if _fits(span_text):
        return node

    semantic = _semantic_boundaries(span_text)
    if len(semantic) > 1:
        for s, e in semantic:
            child_start, child_end = start + s, start + e
            child_text = full_text[child_start:child_end]
            if _fits(child_text):
                node.children.append(Span(start=child_start, end=child_end))
            else:
                for fs, fe in _fixed_size_split(child_text):
                    node.children.append(Span(start=child_start + fs, end=child_start + fe))
        return node

    for s, e in _fixed_size_split(span_text):
        node.children.append(Span(start=start + s, end=start + e))
    return node


def _merge_undersized(full_text: str, node: Span) -> None:
    for child in node.children:
        _merge_undersized(full_text, child)

    if len(node.children) < 2:
        return

    merged: list[Span] = []
    for child in node.children:
        text_len = len(full_text[child.start:child.end].strip())
        if text_len < _MIN_SEGMENT_CHARS:
            if merged:
                # Merge into previous
                prev = merged[-1]
                prev.end = child.end
                prev.children.extend(child.children)
            else:
                # First child is undersized, add it for now
                merged.append(child)
        else:
            # Check if last merged item is undersized and merge it into this child
            if merged and len(full_text[merged[-1].start:merged[-1].end].strip()) < _MIN_SEGMENT_CHARS:
                prev = merged[-1]
                prev.end = child.end
                prev.children.extend(child.children)
            else:
                merged.append(child)
    node.children[:] = merged


def _flatten_leaves(node: Span) -> list[Span]:
    if node.is_leaf:
        return [node]
    leaves: list[Span] = []
    for child in node.children:
        leaves.extend(_flatten_leaves(child))
    return leaves
