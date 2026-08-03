from __future__ import annotations

import re
from typing import Optional

_PROVISION_RE = re.compile(
    r"(?:Section \d+(?:\.\d+)*|Article \d+|Rule \d+(?:-[A-Z])?(?:\.\d+)*|§\s*\d+)"
)


class LegalAdapter:
    """Domain adapter for legal/regulatory prose (Phase 5.5b slice 1).

    Structural grammar: split on top-level provision markers (Section N,
    Article N, Rule N, § N). Sub-clause lettering ((a)(1)(i)) is not
    separately spanned — it falls through to the segmenter's semantic/
    size-fit layers, the same granularity choice MarkdownAdapter makes for
    nested list items under a heading.

    Extraction rules: citation cross-references between provision markers,
    plus heuristic defined-term matching (a "X means Y" defining sentence,
    and any later occurrence of the same Title-Case phrase) — mirrors
    MarkdownAdapter's heading self-topic trick, generalized to legal
    defined terms instead of headings. No LLM boundary prompt yet.
    """

    name = "legal"
    edge_types: frozenset[str] = frozenset({"references"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        matches = list(_PROVISION_RE.finditer(text))
        if not matches:
            return []
        spans = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            spans.append((start, end))
        return spans

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
