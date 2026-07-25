from __future__ import annotations

from smos.ingest.contract import ContentBlock, ContractBlockAdapter, _NoopAdapter, blocks_to_full_text
from smos.ingest.segmenter import _flatten_leaves, build_span_tree


def test_blocks_to_full_text_concatenates_without_separator():
    blocks = [
        ContentBlock(block_type="heading", text="# Title", heading_level=1),
        ContentBlock(block_type="paragraph", text="\nBody text.\n"),
    ]
    full_text, spans = blocks_to_full_text(blocks)
    assert full_text == "# Title\nBody text.\n"
    assert spans == [(0, 7), (7, 19)]


def test_noop_adapter_reports_no_structure_and_no_references():
    adapter = _NoopAdapter()
    assert adapter.structural_boundaries("anything") == []
    assert adapter.extract_references("anything") == []
    assert adapter.llm_boundary_prompt() is None


def test_contract_block_adapter_returns_block_spans_only_for_full_text():
    blocks = [
        ContentBlock(block_type="heading", text="# Title", heading_level=1),
        ContentBlock(block_type="paragraph", text="Body sentence here."),
    ]
    full_text, spans = blocks_to_full_text(blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, spans)

    assert adapter.structural_boundaries(full_text) == spans
    # A sub-span (as the segmenter would pass during recursion) gets no
    # further structural splitting — the block is already the finest unit.
    assert adapter.structural_boundaries(full_text[0:7]) == []


def test_contract_block_adapter_drives_span_tree_into_one_leaf_per_block():
    # Both blocks are kept well over segmenter._MIN_SEGMENT_CHARS (20) so the
    # cascade's undersized-fragment merge doesn't collapse them into one leaf
    # (that merge is exercised deliberately in test_contract_ingest.py, not here).
    blocks = [
        ContentBlock(block_type="heading", text="# A Reasonably Long Title", heading_level=1),
        ContentBlock(
            block_type="paragraph",
            text="This paragraph body is long enough to avoid the undersized-merge threshold.",
        ),
    ]
    full_text, spans = blocks_to_full_text(blocks)
    adapter = ContractBlockAdapter(_NoopAdapter(), full_text, spans)

    root = build_span_tree(full_text, adapter)
    leaves = _flatten_leaves(root)

    assert len(leaves) == 2
    assert (leaves[0].start, leaves[0].end) == spans[0]
    assert (leaves[1].start, leaves[1].end) == spans[1]
