from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MemoryObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # adr / log / doc / issue / episodic / semantic / compressed / reflection / segment
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list)
    tier: str = Field(default="hot")  # hot / warm / cold
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    doc_id: Optional[str] = None
    ordinal: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None


class CompressedContext(BaseModel):
    summary: str
    sources: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    mode: Literal["abstractive", "extractive", "uncertain"] = "abstractive"


class FileReadResult(BaseModel):
    summary: Optional[str]
    id: Optional[str]
    source: Optional[str]
    error: Optional[str] = None


class FileWriteResult(BaseModel):
    success: bool


class GraphNode(BaseModel):
    id: str
    type: Literal["entity", "concept", "memory", "repo_component", "snapshot", "agent", "document", "section"]
    name: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GraphEdge(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    target_id: str
    edge_type: str
    weight: float = 1.0
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
