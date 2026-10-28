from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

DEFAULT_REPO = "Witchd0ct0r/Semantic_Memory_Operating_System_SMOS"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal"}


@dataclass(frozen=True)
class UpdateConfig:
    repo: str
    check_minutes: int = 360
    api_base: str = "https://api.github.com"
    archive_base: str = "https://github.com"
    ca_bundle: Optional[Path] = Path("/opt/smos/certs/ca-bundle.crt")
    max_archive_bytes: int = 100 * 1024 * 1024
    max_total_bytes: int = 300 * 1024 * 1024
    max_files: int = 5000
    max_file_bytes: int = 20 * 1024 * 1024
    problem: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.repo) and self.problem is None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "UpdateConfig":
        config = cls(repo=environ.get("SMOS_UPDATE_REPO", DEFAULT_REPO))
        if config.repo and not _REPO.fullmatch(config.repo):
            config = replace(config, problem="SMOS_UPDATE_REPO must look like owner/name")

        raw = environ.get("SMOS_UPDATE_CHECK_MINUTES", "")
        if raw.isascii() and raw.isdigit() and 5 <= int(raw) <= 10080:
            config = replace(config, check_minutes=int(raw))

        for var, field in (("SMOS_UPDATE_API_BASE", "api_base"), ("SMOS_UPDATE_ARCHIVE_BASE", "archive_base")):
            base = environ.get(var)
            if not base:
                continue
            if _acceptable_base(base):
                config = replace(config, **{field: base.rstrip("/")})
            else:
                config = replace(config, problem=f"{var} must be https, or http to a loopback host")
        return config


def _acceptable_base(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS
