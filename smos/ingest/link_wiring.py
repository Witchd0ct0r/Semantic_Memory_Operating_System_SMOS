from __future__ import annotations

from typing import Callable, Optional

from smos.ingest.contract import IngestionPayload, derive_doc_id
from smos.memory.graph_store import GraphStore

LINK_ORIGIN = "source_link"


def wire_source_links(
    graph: GraphStore,
    payload: IngestionPayload,
    doc_id: str,
    hash_lookup: Optional[Callable[[list[str]], dict[str, str]]] = None,
) -> int:
    """Turn `payload.source_links` into `document --edge_type--> document`
    edges, direction exactly as sent.

    Edges this document owns (origin == "source_link") are replaced wholesale,
    so a re-sync with a changed link set drops stale links but leaves every
    other document's links to this one intact. A target that has not been
    ingested yet gets a placeholder `document` node (metadata.placeholder =
    True) that the real ingest later overwrites via `upsert_node`.

    `link.metadata["target_system"]` selects the target's namespace; without
    it the target lives in the payload's own system. All other link metadata
    (e.g. a cited `clause`, or a list of `anchors`) is stored on the edge.

    A `cites` edge whose target is already ingested also gets `target_hash`,
    the target's content hash at link time (unless the sender supplied one).
    Anchors are only checkable against it; when the target arrives later the
    edge has no hash and its anchors read as unverified."""
    graph.remove_owned_edges(doc_id, LINK_ORIGIN)
    count = 0
    for link in payload.source_links:
        edge_metadata = dict(link.metadata)
        target_system = edge_metadata.pop("target_system", None) or payload.external_handle.system
        target_id = derive_doc_id(target_system, link.target_native_id)
        target_node = graph.get_node(target_id)
        if (
            link.edge_type == "cites"
            and hash_lookup is not None
            and target_node is not None
            and not target_node["metadata"].get("placeholder")
            and "target_hash" not in edge_metadata
        ):
            target_hash = hash_lookup([target_id]).get(target_id)
            if target_hash:
                edge_metadata["target_hash"] = target_hash
        if target_node is None:
            graph.add_node(
                target_id,
                "document",
                link.target_native_id,
                metadata={
                    "placeholder": True,
                    "system": target_system,
                    "native_id": link.target_native_id,
                },
            )
        graph.add_edge(
            doc_id, target_id, link.edge_type,
            metadata={**edge_metadata, "origin": LINK_ORIGIN},
        )
        count += 1
    return count
