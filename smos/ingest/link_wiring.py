from __future__ import annotations

from smos.ingest.contract import IngestionPayload, derive_doc_id
from smos.memory.graph_store import GraphStore

LINK_ORIGIN = "source_link"


def wire_source_links(graph: GraphStore, payload: IngestionPayload, doc_id: str) -> int:
    """Turn `payload.source_links` into `document --edge_type--> document`
    edges, direction exactly as sent.

    Edges this document owns (origin == "source_link") are replaced wholesale,
    so a re-sync with a changed link set drops stale links but leaves every
    other document's links to this one intact. A target that has not been
    ingested yet gets a placeholder `document` node (metadata.placeholder =
    True) that the real ingest later overwrites via `upsert_node`.

    `link.metadata["target_system"]` selects the target's namespace; without
    it the target lives in the payload's own system. All other link metadata
    (e.g. a cited `clause`) is stored on the edge."""
    graph.remove_owned_edges(doc_id, LINK_ORIGIN)
    count = 0
    for link in payload.source_links:
        edge_metadata = dict(link.metadata)
        target_system = edge_metadata.pop("target_system", None) or payload.external_handle.system
        target_id = derive_doc_id(target_system, link.target_native_id)
        if graph.get_node(target_id) is None:
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
