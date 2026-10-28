from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MemoryIn(BaseModel):
    type: str = Field(description="Memory type, e.g. doc, adr, log, issue, episodic, semantic.")
    content: str = Field(description="Exact text to store (at least 10 characters).")
    tags: list[str] = Field(default_factory=list, description="Optional scope tags used to filter queries.")
    summary: Optional[str] = Field(default=None, description="Caller-written summary, stored and returned as-is.")
    summary_source: Optional[str] = Field(default=None, description="Who/what wrote the summary.")


class DerivedMemoryIn(BaseModel):
    kind: Literal["compressed", "reflection"] = Field(
        description="'compressed' merges several memories into one; 'reflection' records reasoning about them."
    )
    text: str = Field(description="The text YOU wrote. SMOS stores it verbatim and links it to the sources.")
    source_ids: list[str] = Field(description="Memory ids this text was derived from; all must exist.")


class QueryIn(BaseModel):
    query: str = Field(description="Natural-language query (at least 10 characters).")
    k: int = Field(default=5, ge=1, le=100, description="How many results to return.")
    tags: list[str] = Field(default_factory=list, description="Restrict the search to items with any of these tags.")


class RecallIn(BaseModel):
    query: str = Field(default="", description="Natural-language query (unused for the chain strategy).")
    strategy: str = Field(default="hybrid", description="hybrid, temporal, chain, repo or exact.")
    k: int = Field(default=5, ge=1, le=100, description="Result count, or hop depth for chain.")
    since: str = Field(default="", description="ISO-8601 lower bound for temporal.")
    until: str = Field(default="", description="ISO-8601 upper bound for temporal.")
    seed_id: str = Field(default="", description="Starting memory id for chain.")
    component: str = Field(default="", description="Tag that scopes the repo strategy.")


class GraphQueryIn(BaseModel):
    term: str = Field(description="Entity name (resolved as entity:<term>) or an exact node id.")
    max_hops: int = Field(default=2, ge=1, le=6)


class LinkIn(BaseModel):
    id_a: str
    id_b: str
    edge_type: str = Field(description="One of the registered edge types, e.g. references, depends_on, related_to.")


class SummaryIn(BaseModel):
    summary: str = Field(min_length=1)
    source: str = Field(default="", description="Who/what wrote the summary.")


class RequirementContextIn(BaseModel):
    text: str = Field(default="", description="Draft requirement text (optional when the item is stored).")
    doc_id: str = Field(default="", description="Stored item's document id.")
    system: str = Field(default="", description="Source system; with native_id it identifies a stored item.")
    native_id: str = Field(default="", description="Item id in the source system.")
    level: str = Field(default="", description="Process level, e.g. SYS.2 (matches tag level:SYS.2).")
    domain_tags: list[str] = Field(default_factory=list, description="Peers must share at least one.")
    normative_tags: list[str] = Field(default_factory=list, description="Scope the normative section by tag.")
    declared_links: list[dict] = Field(
        default_factory=list,
        description="Links a draft intends to have: [{target_native_id, edge_type[, target_system, clause]}].",
    )
    token_budget: int = Field(default=4000, ge=100)
    k_per_section: int = Field(default=5, ge=1, le=50)


class VerbatimIn(BaseModel):
    content: str = Field(min_length=1, description="Exact content to keep byte-for-byte.")
    label: str = Field(default="", description="Short human-readable label.")
