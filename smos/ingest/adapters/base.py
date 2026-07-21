from __future__ import annotations

from typing import Optional, Protocol


class DomainAdapter(Protocol):
    """Pluggable domain-specific knowledge for ingestion and the memory graph.

    The segmenter, extractor, storage, and retrieval layers stay
    domain-agnostic; an adapter supplies only what's specific to one
    domain's documents. A generic adapter (no structure detected, no
    references extracted) is a valid, if unhelpful, implementation — every
    method degrades gracefully to "nothing domain-specific here".
    """

    name: str
    edge_types: frozenset[str]

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        """(start_char, end_char) spans for this domain's native structural
        units (headings, articles, clauses...). Empty list means no
        domain-specific structure was detected; the caller falls through to
        the segmenter's semantic/size-fit layers."""
        ...

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        """(source_name, target_name, edge_type) triples for this domain's
        cross-references. Every edge_type returned must be a member of
        self.edge_types."""
        ...

    def llm_boundary_prompt(self) -> Optional[str]:
        """Prompt guiding LLM-proposed segment boundaries (segmenter's
        opt-in Layer 4), or None if this adapter doesn't need it."""
        ...
