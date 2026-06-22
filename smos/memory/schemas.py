from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MemoryObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str  # adr / log / doc / issue
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: list[str] = Field(default_factory=list)
    tier: str = Field(default="hot")  # hot / warm / cold


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
