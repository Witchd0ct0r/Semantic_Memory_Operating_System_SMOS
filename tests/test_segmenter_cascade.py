from __future__ import annotations

from smos.ingest.segmenter import (
    Span,
    _MAX_SEGMENT_CHARS,
    _MIN_SEGMENT_CHARS,
    _fits,
    _fixed_size_split,
    _split_sentences,
)


def test_fits_true_under_threshold():
    assert _fits("short text") is True


def test_fits_false_over_threshold():
    assert _fits("x" * (_MAX_SEGMENT_CHARS + 1)) is False


def test_split_sentences_returns_offsets_covering_text():
    text = "First sentence. Second sentence! Third sentence?"
    spans = _split_sentences(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].strip() == "First sentence."
    assert text[spans[1][0]:spans[1][1]].strip() == "Second sentence!"
    assert text[spans[2][0]:spans[2][1]].strip() == "Third sentence?"


def test_split_sentences_single_sentence():
    assert len(_split_sentences("Only one sentence here.")) == 1


def test_fixed_size_split_covers_text_without_gaps():
    text = "x" * 1500
    spans = _fixed_size_split(text)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for i in range(len(spans) - 1):
        assert spans[i][1] == spans[i + 1][0]


def test_fixed_size_split_short_text_returns_single_span():
    text = "short"
    assert _fixed_size_split(text) == [(0, len(text))]


# Tests for semantic boundaries and tree building
from smos.ingest.segmenter import (
    _build_span_tree,
    _flatten_leaves,
    _merge_undersized,
    _semantic_boundaries,
)


class _NoStructureAdapter:
    name = "no-structure"
    edge_types: frozenset[str] = frozenset()

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self):
        return None


def test_semantic_boundaries_splits_unrelated_sentences():
    text = "The cat sat quietly on the warm mat. Quantum entanglement violates local realism."
    chunks = _semantic_boundaries(text)
    assert len(chunks) == 2
    assert text[chunks[0][0]:chunks[0][1]] == text[: chunks[0][1]]
    assert chunks[-1][1] == len(text)


def test_semantic_boundaries_keeps_related_sentences_together():
    text = "The cat sat on the mat. The cat then sat on the rug."
    chunks = _semantic_boundaries(text)
    assert len(chunks) == 1


def test_semantic_boundaries_single_sentence_returns_one_chunk():
    chunks = _semantic_boundaries("Just one sentence, nothing to split.")
    assert len(chunks) == 1


def test_build_span_tree_uses_structural_boundaries_when_available():
    from smos.ingest.adapters.markdown import MarkdownAdapter

    text = "# Title\nIntro.\n## Section A\nBody A.\n## Section B\nBody B.\n"
    root = _build_span_tree(text, 0, len(text), MarkdownAdapter())
    assert len(root.children) == 3
    assert not root.is_leaf


def test_build_span_tree_leaf_when_no_structure_and_fits():
    text = "A short paragraph with no headings at all."
    root = _build_span_tree(text, 0, len(text), _NoStructureAdapter())
    assert root.is_leaf
    assert (root.start, root.end) == (0, len(text))


def test_build_span_tree_falls_back_to_size_fit_when_oversized_and_no_structure():
    text = "x" * (_MAX_SEGMENT_CHARS * 3)
    root = _build_span_tree(text, 0, len(text), _NoStructureAdapter())
    assert not root.is_leaf
    leaves = _flatten_leaves(root)
    assert leaves[0].start == 0
    assert leaves[-1].end == len(text)
    for i in range(len(leaves) - 1):
        assert leaves[i].end == leaves[i + 1].start


def test_merge_undersized_absorbs_tiny_fragment_into_neighbor():
    root = Span(start=0, end=100, children=[
        Span(start=0, end=5),
        Span(start=5, end=100),
    ])
    _merge_undersized("x" * 100, root)
    assert len(root.children) == 1
    assert root.children[0].start == 0
    assert root.children[0].end == 100


def test_flatten_leaves_returns_leaves_in_document_order():
    root = Span(start=0, end=10, children=[
        Span(start=0, end=4),
        Span(start=4, end=10, children=[
            Span(start=4, end=7),
            Span(start=7, end=10),
        ]),
    ])
    leaves = _flatten_leaves(root)
    assert [(s.start, s.end) for s in leaves] == [(0, 4), (4, 7), (7, 10)]


def test_build_span_tree_matches_ingest_document_text_leaves():
    from smos.ingest.adapters.markdown import MarkdownAdapter
    from smos.ingest.segmenter import build_span_tree

    text = "# Title\nIntro.\n## Section A\n" + ("Sentence about A. " * 60)
    root = build_span_tree(text, MarkdownAdapter())
    leaves = _flatten_leaves(root)

    assert len(leaves) >= 2
    reconstructed = "".join(text[leaf.start:leaf.end] for leaf in leaves)
    assert reconstructed == text
