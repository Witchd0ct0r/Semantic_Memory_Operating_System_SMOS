from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from smos.api.deps import require_write
from smos.api.models import (
    DerivedMemoryIn,
    GraphQueryIn,
    LinkIn,
    MemoryIn,
    QueryIn,
    RecallIn,
    RequirementContextIn,
    SummaryIn,
    VerbatimIn,
)
from smos.ingest.contract import IngestionPayload
from smos.memory.exact_retrieval import reconstruct
from smos.memory.recall_engine import recall
from smos.memory.requirement_context import requirement_context
from smos.memory.schemas import MemoryObject, RetrievedContext
from smos.tools.agent_primitives import compress, forget, link, reflect
from smos.tools.ingest_tools import do_ingest_document_schema
from smos.tools.semantic_tools import _MIN_INPUT_CHARS, graph_query, semantic_query

# Two routers make the privilege split explicit. `read` serves any valid token; `write` also
# requires a read/write token. tests/test_api_scopes.py pins the exact list of write operations.
read = APIRouter(prefix="/api/v1")
write = APIRouter(prefix="/api/v1", dependencies=[Depends(require_write)])

_RW_NOTE = " Requires a read/write token."


def _store(request: Request):
    return request.app.state.store


def _too_short(text: str) -> bool:
    return not text or len(text.strip()) < _MIN_INPUT_CHARS


@write.post(
    "/memories",
    summary="Store a memory",
    description=(
        "Store exact text as a memory and get its id. Optionally attach a summary you wrote "
        "(SMOS never generates summaries). Use type 'semantic' for durable facts and 'episodic' "
        "for events; tags scope later queries."
    ),
)
def post_memory(body: MemoryIn, request: Request) -> dict:
    if _too_short(body.content):
        raise HTTPException(400, f"content must be at least {_MIN_INPUT_CHARS} characters")
    store = _store(request)
    memory_id = store.store(MemoryObject(
        type=body.type, content=body.content, timestamp=datetime.utcnow(), tags=body.tags,
    ))
    if body.summary:
        store.set_summary(memory_id, body.summary, body.summary_source or "")
    return {"id": memory_id}


@write.delete(
    "/memories/{id}",
    summary="Delete a memory",
    description="Delete a memory, its graph edges and its stored summary. 404 when the id does not exist.",
)
def delete_memory(id: str, request: Request) -> dict:
    if not forget(id, _store(request)):
        raise HTTPException(404, "memory not found")
    return {"deleted": id}


@write.post(
    "/memories/derived",
    summary="Store text you derived from existing memories",
    description=(
        "Record a merged summary ('compressed') or a reasoning note ('reflection') that YOU wrote "
        "from existing memories. SMOS stores your text verbatim and links it to every source id "
        "(generated_from edges). All source ids must exist, otherwise 400."
    ),
)
def post_derived(body: DerivedMemoryIn, request: Request) -> dict:
    fn = compress if body.kind == "compressed" else reflect
    new_id = fn(body.text, body.source_ids, _store(request))
    if not new_id:
        raise HTTPException(400, "text too short, no sources, or a source id does not exist")
    return {"id": new_id}


@read.post(
    "/query",
    response_model=RetrievedContext,
    summary="Semantic search over stored memories",
    description=(
        "Hybrid (vector + keyword + graph) search. Returns ranked exact text with any stored "
        "summary beside it ('summary' is null when none was supplied) and an overall confidence. "
        "Nothing is synthesized. Pass tags to restrict the search to a domain."
    ),
)
def post_query(body: QueryIn, request: Request) -> dict:
    if _too_short(body.query):
        raise HTTPException(400, f"query must be at least {_MIN_INPUT_CHARS} characters")
    return semantic_query(body.query, body.k, _store(request), tags=body.tags or None)


@read.post(
    "/recall",
    summary="Retrieve with an explicit strategy",
    description=(
        "strategy 'hybrid' (default) and 'repo' behave like /query; 'exact' returns verbatim "
        "segments plus their neighbours; 'temporal' filters by since/until then ranks; 'chain' "
        "follows evolves_into/related_to edges from seed_id (k is the hop depth)."
    ),
)
def post_recall(body: RecallIn, request: Request) -> dict:
    result = recall(body.query, body.strategy, body.k, body.since, body.until,
                    body.seed_id, body.component, _store(request))
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@read.post(
    "/graph/query",
    summary="Traverse the memory graph",
    description=(
        "Walk the graph from an entity name or an exact node id up to max_hops. "
        "Returns the connected nodes with their hop distance."
    ),
)
def post_graph_query(body: GraphQueryIn, request: Request) -> dict:
    result = graph_query(body.term, _store(request), max_hops=body.max_hops)
    if result.get("status") == "skipped":
        raise HTTPException(400, "term must not be empty")
    return result


@write.post(
    "/links",
    summary="Create a graph edge between two nodes",
    description="Create an explicit typed edge between two existing node ids. 400 on an unknown edge type or id.",
)
def post_link(body: LinkIn, request: Request) -> dict:
    result = link(body.id_a, body.id_b, body.edge_type, _store(request))
    if not result["success"]:
        raise HTTPException(400, result["error"])
    return result


@read.get(
    "/nodes/{node_id}/text",
    summary="Reconstruct the exact text of a document or section",
    description=(
        "Rebuild the byte-exact original text under a document or section node id "
        "(the doc_id returned by /documents, or '<doc_id>:section:<n>'). "
        "'complete' is false if segments have a gap."
    ),
)
def get_node_text(node_id: str, request: Request) -> dict:
    result = reconstruct(_store(request), node_id)
    if result.get("error"):
        raise HTTPException(404, result["error"])
    return result


@write.put(
    "/nodes/{node_id}/summary",
    summary="Attach or replace a summary on any node",
    description=(
        "Attach a summary you wrote to a memory, document or section id. "
        "Replaces an earlier one. 404 when the id is unknown."
    ),
)
def put_node_summary(node_id: str, body: SummaryIn, request: Request) -> dict:
    store = _store(request)
    if store.graph.get_node(node_id) is None and store.get_by_uuid(node_id) is None:
        raise HTTPException(404, "node not found")
    store.set_summary(node_id, body.summary, body.source)
    return {"ok": True}


@write.post(
    "/documents",
    summary="Ingest a structured document",
    description=(
        "Ingest a document that a parser converted to the ingestion contract (blocks, "
        "external_handle, optional source_links, domain_tags, doc_type and summary). "
        "Re-sending the same external_handle is an idempotent upsert. A payload that parses "
        "but breaks content rules is quarantined: status 'quarantined' with the error list, "
        "HTTP 200, so the parser can inspect and resend."
    ),
)
def post_document(body: IngestionPayload, request: Request) -> dict:
    return do_ingest_document_schema(body, _store(request))


@read.post(
    "/requirement-context",
    summary="Gather evidence for judging a requirement",
    description=(
        "Give a draft 'text', a stored item (doc_id, or system + native_id), or both. Returns "
        "linked items (<= 2 hops), similar items, relevant legal/standard clauses and coverage "
        "facts, all as exact stored text. SMOS gathers evidence only; it gives no verdict."
    ),
)
def post_requirement_context(body: RequirementContextIn, request: Request) -> dict:
    result = requirement_context(
        _store(request), text=body.text, doc_id=body.doc_id, system=body.system,
        native_id=body.native_id, level=body.level, domain_tags=body.domain_tags,
        normative_tags=body.normative_tags, declared_links=body.declared_links or None,
        token_budget=body.token_budget, k_per_section=body.k_per_section,
    )
    if "error" in result:
        status = 404 if result["error"].startswith("no ingested item") else 400
        raise HTTPException(status, result["error"])
    return result


@write.post(
    "/verbatim",
    summary="Store content losslessly under a key",
    description="Keep exact bytes (code, diffs, structured data) and get a retrieval key. Not searchable.",
)
def post_verbatim(body: VerbatimIn, request: Request) -> dict:
    return {"key": _store(request).store_verbatim(body.content, label=body.label)}


@read.get(
    "/verbatim/{key}",
    summary="Retrieve verbatim content by key",
    description="Return content stored with POST /verbatim. 404 when the key is unknown.",
)
def get_verbatim(key: str, request: Request) -> dict:
    result = _store(request).retrieve_verbatim(key)
    if result is None:
        raise HTTPException(404, "key not found")
    return result


for _route in write.routes:
    _route.description = (_route.description or "") + _RW_NOTE
