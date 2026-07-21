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
