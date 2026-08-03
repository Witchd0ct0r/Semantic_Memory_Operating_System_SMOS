from __future__ import annotations

import re
from typing import Optional

_PROVISION_RE = re.compile(
    r"(?:Section \d+(?:\.\d+)*|Article \d+|Rule \d+(?:-[A-Z])?(?:\.\d+)*|§\s*\d+)"
)

_DEFINING_PATTERN_RE = re.compile(
    r'"?([A-Z][A-Za-z]+(?:\s+[A-Z][a-z]+){1,3})"?\s+(?:means|shall mean)\b'
)

_TITLE_CASE_PHRASE_RE = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b"
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
        """(source_name, target_name, "references") triples. Two independent
        kinds, merged into one list — `smos/ingest/segmenter.py::_wire_references`
        wires every edge from the *current leaf* to `concept:{target_name}`
        and discards `source_name`, so only `target_name` drives graph
        connectivity; `source_name` exists for interface completeness and
        this adapter's own tests.

        a) Citation cross-references: reuses `_PROVISION_RE`. The *last*
        provision marker found in `text` is this window's own
        nearest/enclosing provision; every *other* marker match becomes a
        citation target, e.g. "as defined in Section 3.1" inside a window
        whose own nearest provision is "Section 5.2" emits
        ("Section 5.2", "Section 3.1", "references"). One-directional by
        design (matches the design spec's literal wording): a provision's
        own leaf does not get a self-topic triple from this rule alone, so
        citation cross-references alone do not guarantee bidirectional
        graph-rescue — that guarantee comes from (b) below.

        b) Defined-term matching (heuristic phrase matching, no
        corpus-wide term registry):
          - `_DEFINING_PATTERN_RE` matches a "X means"/"X shall mean"
            defining sentence and emits a self-topic triple (term, term) —
            mirrors MarkdownAdapter's heading self-topic trick.
          - `_TITLE_CASE_PHRASE_RE` matches any 2-4-word Title-Case phrase
            (accepted false-positive risk: also fires on proper nouns and
            sentence-initial capitals — additive graph noise, not a
            correctness bug, per the design spec) and emits
            (nearest_provision, phrase, "references") for each occurrence.
            A segment that merely *uses* a term (without defining it)
            still resolves to the same concept:{name} node the defining
            segment created, purely because both triples' target lowercases
            to the same string. When no provision marker is present in the
            window, `nearest_provision` falls back to the phrase itself —
            harmless since source_name is unused by the wiring step.

        Deduplicates by target only (case-insensitive) — mirrors
        MarkdownAdapter's dedup, since only the target drives a new
        concept-node edge.
        """
        triples: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        def _add(source: str, target: str) -> None:
            target = target.strip()
            key = target.lower()
            if not target or key in seen:
                return
            seen.add(key)
            triples.append((source, target, "references"))

        provision_matches = list(_PROVISION_RE.finditer(text))
        nearest_provision: Optional[str] = (
            provision_matches[-1].group(0) if provision_matches else None
        )
        for m in provision_matches[:-1]:
            _add(nearest_provision, m.group(0))

        for m in _DEFINING_PATTERN_RE.finditer(text):
            term = m.group(1).strip()
            _add(term, term)

        for m in _TITLE_CASE_PHRASE_RE.finditer(text):
            phrase = m.group(0).strip()
            _add(nearest_provision if nearest_provision else phrase, phrase)

        return triples

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
