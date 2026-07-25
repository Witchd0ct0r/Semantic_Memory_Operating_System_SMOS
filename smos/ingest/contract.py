from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


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
