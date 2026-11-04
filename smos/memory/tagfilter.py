from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np


def _clean(tags: Optional[Iterable[str]]) -> tuple[str, ...]:
    """Drop blank names and duplicates, keep order. Matching stays exact and case-sensitive."""
    seen: dict[str, None] = {}
    for tag in tags or ():
        if tag is not None and tag.strip():
            seen.setdefault(tag, None)
    return tuple(seen)


@dataclass(frozen=True)
class TagFilter:
    """A tag scope: `any` = at least one of, `all` = every one of, `none` = none of. AND across the three."""

    any: tuple[str, ...] = ()
    all: tuple[str, ...] = ()
    none: tuple[str, ...] = ()

    @classmethod
    def make(
        cls,
        any: Optional[Iterable[str]] = None,
        all: Optional[Iterable[str]] = None,
        none: Optional[Iterable[str]] = None,
    ) -> "TagFilter":
        return cls(any=_clean(any), all=_clean(all), none=_clean(none))

    @property
    def is_empty(self) -> bool:
        return not (self.any or self.all or self.none)


@dataclass(frozen=True, eq=False)
class Eligible:
    """The memories a TagFilter lets through: FAISS row ids and the same set as uuids."""

    row_ids: np.ndarray = field(repr=False)
    uuids: frozenset[str]

    def __len__(self) -> int:
        return len(self.uuids)
