from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from smos.ingest.adapters.base import DomainAdapter
from smos.memory.embeddings import embed
from smos.memory.graph_store import GraphStore
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore

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


def _absorb_span(target: Span, extra: Span, prepend: bool) -> None:
    """Fold `extra`'s range into `target` in place. If both are still plain
    leaves, just widen target's start/end — the union range is correct on
    its own. If either side already has children (was itself split further),
    naively stretching start/end and extending .children would silently drop
    whichever side's range isn't itemized as an actual leaf underneath the
    merged node, since _flatten_leaves only ever walks .children. Instead,
    represent each side's own range explicitly as a leaf before splicing, so
    every absorbed character stays reachable."""
    if target.is_leaf and extra.is_leaf:
        target.start = min(target.start, extra.start)
        target.end = max(target.end, extra.end)
        return

    target_pieces = target.children if not target.is_leaf else [Span(start=target.start, end=target.end)]
    extra_pieces = extra.children if not extra.is_leaf else [Span(start=extra.start, end=extra.end)]
    target.children = (extra_pieces + target_pieces) if prepend else (target_pieces + extra_pieces)
    target.start = min(target.start, extra.start)
    target.end = max(target.end, extra.end)


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
                _absorb_span(merged[-1], child, prepend=False)
            else:
                # First child is undersized, add it for now
                merged.append(child)
        else:
            # Check if last merged item is undersized and merge it into this child
            if merged and len(full_text[merged[-1].start:merged[-1].end].strip()) < _MIN_SEGMENT_CHARS:
                pending = merged.pop()
                _absorb_span(child, pending, prepend=True)
                merged.append(child)
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


def _wire_containment(graph: GraphStore, doc_id: str, root: Span) -> int:
    """Wire `contains` edges for the containment tree, capped at exactly
    three levels (document -> section -> segment) per the Phase 5 spec's
    resolved containment-depth decision — regardless of how deeply the
    cascade nested structurally. A section node is only created for a
    top-level child that was itself split further; a top-level child that is
    already a leaf links directly under the document (skipping a redundant
    single-child section). Returns the number of section nodes created.

    GraphStore.traverse() treats edges as undirected, so a `contains`
    traversal from a section node returns both its parent document AND its
    leaf children in one hop — callers wanting only the children must filter
    by node type (a section's non-memory neighbor is always its parent, by
    construction of this three-level cap)."""
    if root.is_leaf:
        graph.add_edge(doc_id, root.memory_id, "contains")
        return 0

    section_count = 0
    for i, child in enumerate(root.children):
        if child.is_leaf:
            graph.add_edge(doc_id, child.memory_id, "contains")
            continue

        section_id = f"{doc_id}:section:{i}"
        graph.add_node(section_id, "section", section_id)
        graph.add_edge(doc_id, section_id, "contains")
        section_count += 1
        for leaf in _flatten_leaves(child):
            graph.add_edge(section_id, leaf.memory_id, "contains")

    return section_count


_REFERENCE_LOOKBACK_CHARS = 300


def _wire_references(
    graph: GraphStore, adapter: DomainAdapter, full_text: str, leaves: list[Span]
) -> int:
    """Ask the adapter for cross-references near each leaf and wire them as
    `segment --edge_type--> concept:{target_name}` edges — the same shape
    `VectorStore._update_graph` already uses for code entities
    (`memory --references--> entity:{name}`). Two segments whose extracted
    triples share a target_name (normalized to lowercase) become connected
    through the shared concept node; `context_builder.py`'s existing 2-hop
    `edge_types=["references"]` graph-expansion step already walks exactly
    this shape, so no retrieval-side change is needed for it to take effect.

    Calls the adapter with a lookback window into `full_text` ending at each
    leaf, not just the leaf's own text — a client-supplied ingestion payload
    (Pillar G) commonly delivers a heading and its body as separate
    ContentBlocks, which become separate leaves with no heading text of
    their own; the lookback window recovers the nearest preceding heading
    regardless of which leaf boundary split it from its body.

    `source_name` is part of the DomainAdapter contract but unused here;
    edge_type must be one the adapter actually declares (`adapter.edge_types`)."""
    valid_edge_types = adapter.edge_types
    count = 0
    for leaf in leaves:
        if leaf.memory_id is None:
            continue
        window = full_text[max(0, leaf.start - _REFERENCE_LOOKBACK_CHARS):leaf.end]
        for _source_name, target_name, edge_type in adapter.extract_references(window):
            if edge_type not in valid_edge_types:
                continue
            concept_id = f"concept:{target_name.strip().lower()}"
            graph.add_node(concept_id, "concept", target_name.strip())
            graph.add_edge(leaf.memory_id, concept_id, edge_type)
            count += 1
    return count


def build_span_tree(text: str, adapter: DomainAdapter) -> Span:
    """Build the structural -> semantic -> size-fit span tree for `text` and
    merge any undersized fragments. Pure, no I/O — shared by `ingest_document_text`
    and any caller that needs to store leaves itself (e.g. checkpointed ingestion)."""
    root = _build_span_tree(text, 0, len(text), adapter)
    _merge_undersized(text, root)
    return root


def ingest_document_text(
    doc_id: str,
    text: str,
    store: VectorStore,
    adapter: DomainAdapter,
    doc_name: Optional[str] = None,
) -> dict:
    """Segment `text` via the structural -> semantic -> size-fit cascade,
    store each leaf as a searchable `segment` memory, and wire the
    containment tree (document -> section -> segment) into the memory graph
    via `contains` edges."""
    root = build_span_tree(text, adapter)
    leaves = _flatten_leaves(root)

    store.graph.add_node(doc_id, "document", doc_name or doc_id)

    memories = [
        MemoryObject(
            type="segment",
            content=text[leaf.start:leaf.end],
            doc_id=doc_id,
            ordinal=i,
            char_start=leaf.start,
            char_end=leaf.end,
        )
        for i, leaf in enumerate(leaves)
    ]
    segment_ids = store.store_batch(memories)
    for leaf, segment_id in zip(leaves, segment_ids):
        leaf.memory_id = segment_id

    section_count = _wire_containment(store.graph, doc_id, root)
    reference_count = _wire_references(store.graph, adapter, text, leaves)

    return {
        "doc_id": doc_id,
        "segment_count": len(segment_ids),
        "segment_ids": segment_ids,
        "section_count": section_count,
        "reference_count": reference_count,
    }
