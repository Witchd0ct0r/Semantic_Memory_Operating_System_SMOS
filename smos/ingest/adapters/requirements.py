from __future__ import annotations

from typing import Optional


class RequirementsAdapter:
    """Declares the traceability edge vocabulary for requirements-style
    corpora. Deliberately behaviourless on text: the third-party parser that
    produces the ingestion payload already knows the links and sends them as
    `source_links`, so there is nothing to extract from prose.

    Edge types (all item -> item, direction exactly as the parser sends it):
      parent_of   parent item -> child item (tree hierarchy)
      member_of   item -> epic / group it belongs to
      cites       item -> normative document (legal / standard); the cited
                  clause travels in the link's metadata (`clause`)
      verifies    verification item (e.g. SYS.2) -> the requirement it verifies (SYS.1).
                  Point it at requirements only; norm evidence goes through `cites`.
    A `cites` link may carry `anchors`: a list of {clause, quote, block_ordinal,
    char_start, char_end} locating the evidence inside the cited document. SMOS
    stamps `target_hash` on the edge so a later re-send of the target shows the
    anchors as stale.
    `depends_on` and `references` are already core vocabulary.
    """

    name = "requirements"
    edge_types: frozenset[str] = frozenset({"parent_of", "member_of", "cites", "verifies"})

    def structural_boundaries(self, text: str) -> list[tuple[int, int]]:
        return []

    def extract_references(self, text: str) -> list[tuple[str, str, str]]:
        return []

    def llm_boundary_prompt(self) -> Optional[str]:
        return None
