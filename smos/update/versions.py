from __future__ import annotations

import re
from typing import Optional

Version = tuple[int, int, int]

_TAG = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)")
_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")


def _parse(pattern: re.Pattern, text: str) -> Optional[Version]:
    match = pattern.fullmatch(text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def parse_tag(tag: str) -> Optional[Version]:
    """Only an exact vMAJOR.MINOR.PATCH tag is a release tag."""
    return _parse(_TAG, tag)


def parse_version(text: str) -> Optional[Version]:
    return _parse(_VERSION, text)


def is_newer(candidate: Version, running: Optional[Version]) -> bool:
    return candidate > (running or (0, 0, 0))


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)
