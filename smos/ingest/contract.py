from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

from smos.ingest.adapters.registry import all_edge_types
from smos.memory.vector_store import VectorStore


class ExternalHandle(BaseModel):
    system: str
    native_id: str
    deep_link: Optional[str] = None
    version: Optional[str] = None
    source_updated_at: Optional[datetime] = None


class ContentBlock(BaseModel):
    block_type: Literal["heading", "paragraph", "list", "table"]
    text: str
    heading_level: Optional[int] = None
    source_anchor: Optional[str] = None


class SourceLink(BaseModel):
    target_native_id: str
    edge_type: str
    metadata: dict = Field(default_factory=dict)


class IngestionPayload(BaseModel):
    schema_version: str = "1.0"
    external_handle: ExternalHandle
    doc_name: Optional[str] = None
    blocks: list[ContentBlock]
    doc_type: Optional[str] = None
    domain_tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    source_links: list[SourceLink] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    transformer_name: Optional[str] = None
    transformer_version: Optional[str] = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def derive_doc_id(system: str, native_id: str) -> str:
    """Deterministic id from system+native_id — re-ingesting the same source
    document always resolves to the same doc_id (idempotent upsert key)."""
    return hashlib.sha1(f"{system}:{native_id}".encode("utf-8")).hexdigest()[:16]


def content_hash(full_text: str) -> str:
    return hashlib.sha1(full_text.encode("utf-8")).hexdigest()


_MAX_PAYLOAD_CHARS = 5_000_000


def validate_payload(payload: IngestionPayload) -> list[str]:
    errors: list[str] = []

    if not payload.blocks:
        errors.append("blocks: must contain at least one block")

    for i, block in enumerate(payload.blocks):
        if not block.text.strip():
            errors.append(f"blocks[{i}]: text must not be empty")
        if block.block_type == "heading":
            if block.heading_level is None or not (1 <= block.heading_level <= 6):
                errors.append(f"blocks[{i}]: heading blocks require heading_level in 1..6")

    if not payload.external_handle.system.strip():
        errors.append("external_handle.system: must not be empty")
    if not payload.external_handle.native_id.strip():
        errors.append("external_handle.native_id: must not be empty")

    valid_edge_types = all_edge_types()
    for i, link in enumerate(payload.source_links):
        if link.edge_type not in valid_edge_types:
            errors.append(f"source_links[{i}]: unknown edge_type '{link.edge_type}'")

    total_chars = sum(len(b.text) for b in payload.blocks)
    if total_chars > _MAX_PAYLOAD_CHARS:
        errors.append(f"payload: total block text {total_chars} exceeds max {_MAX_PAYLOAD_CHARS}")

    return errors


def quarantine_payload(store: VectorStore, payload: IngestionPayload, errors: list[str]) -> str:
    key = str(uuid.uuid4())
    store.save_quarantine(
        key=key,
        system=payload.external_handle.system,
        native_id=payload.external_handle.native_id,
        raw_payload=payload.model_dump_json(),
        errors=json.dumps(errors),
    )
    return key
