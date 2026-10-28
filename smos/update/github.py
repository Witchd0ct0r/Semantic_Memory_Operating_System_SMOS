"""The only code that talks to GitHub: the latest release, its commit, a root tree and the archive.

Every URL is built from the configured repo and values GitHub itself returned (a 40-hex commit
SHA, a vX.Y.Z tag); nothing from a web request ever reaches this module.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

from smos.update.config import LOOPBACK_HOSTS, UpdateConfig
from smos.update.manifest import REBUILD_PATHS
from smos.update.versions import Version, parse_tag

_MAX_JSON_BYTES = 1024 * 1024
_MAX_NOTES_CHARS = 20_000
_API_TIMEOUT = 15
_DOWNLOAD_TIMEOUT = 60
_SHA = re.compile(r"[0-9a-f]{40}")


class GitHubError(Exception):
    """A failure whose message is safe to show an admin."""


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    notes: str
    url: str


def build_ssl_context(config: UpdateConfig) -> ssl.SSLContext:
    """Always verifies; an extra CA bundle (networks that re-sign HTTPS) only adds trust anchors."""
    context = ssl.create_default_context()
    if config.ca_bundle is not None and Path(config.ca_bundle).is_file():
        context.load_verify_locations(cafile=str(config.ca_bundle))
    return context


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects to https only (GitHub sends archives to another host); plain http is
    followed only inside the same loopback origin that tests use."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        same_loopback = (old.scheme == "http" and new.scheme == "http" and new.netloc == old.netloc
                         and old.hostname in LOOPBACK_HOSTS)
        if new.scheme != "https" and not same_loopback:
            raise urllib.error.HTTPError(req.full_url, code, "redirect to a non-https address refused",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubClient:
    def __init__(self, config: UpdateConfig, proxies: Optional[dict] = None) -> None:
        """`proxies=None` honours HTTPS_PROXY / NO_PROXY from the environment; tests pass {}."""
        self._config = config
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies),
            urllib.request.HTTPSHandler(context=build_ssl_context(config)),
            _SafeRedirect(),
        )

    # -- plumbing
    def _open(self, url: str, timeout: float):
        request = urllib.request.Request(url, headers={
            "User-Agent": "smos-updater", "Accept": "application/vnd.github+json"})
        try:
            return self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as err:
            if err.code in (403, 429) and err.headers.get("X-RateLimit-Remaining") == "0":
                raise GitHubError("GitHub rate limit reached, try again later") from None
            raise GitHubError(f"GitHub answered HTTP {err.code}") from None
        except (urllib.error.URLError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise GitHubError(f"cannot reach GitHub: {reason}") from None

    def _json(self, path: str):
        """Parsed JSON object for an API path, or None when GitHub answers 404."""
        try:
            with self._open(f"{self._config.api_base}{path}", _API_TIMEOUT) as response:
                raw = response.read(_MAX_JSON_BYTES + 1)
        except GitHubError as err:
            if str(err) == "GitHub answered HTTP 404":
                return None
            raise
        try:
            if len(raw) > _MAX_JSON_BYTES:
                raise ValueError("too large")
            value = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise GitHubError("unexpected response from GitHub") from None
        if not isinstance(value, dict):
            raise GitHubError("unexpected response from GitHub")
        return value

    def _repo_path(self, rest: str) -> str:
        return f"/repos/{self._config.repo}/{rest}"

    # -- API
    def latest_release(self) -> Optional[Release]:
        data = self._json(self._repo_path("releases/latest"))
        if data is None or data.get("draft") or data.get("prerelease"):
            return None
        tag = data.get("tag_name")
        version = parse_tag(tag) if isinstance(tag, str) else None
        if version is None:
            raise GitHubError(f"the latest release tag {str(tag)[:40]!r} is not a vX.Y.Z version")
        notes = data.get("body") if isinstance(data.get("body"), str) else ""
        url = data.get("html_url") if isinstance(data.get("html_url"), str) else ""
        return Release(version=version, tag=tag, notes=notes[:_MAX_NOTES_CHARS],
                       url=url if url.startswith("https://") else "")

    def resolve_tag(self, tag: str) -> str:
        data = self._json(self._repo_path(f"commits/{quote(tag, safe='')}"))
        sha = data.get("sha") if data else None
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise GitHubError("GitHub did not return a commit for the release tag")
        return sha

    def _tree(self, sha: str) -> dict[str, dict]:
        data = self._json(self._repo_path(f"git/trees/{sha}"))
        entries = data.get("tree") if data else None
        if not isinstance(entries, list):
            raise GitHubError("unexpected response from GitHub")
        return {e["path"]: e for e in entries if isinstance(e, dict) and isinstance(e.get("path"), str)}

    def target_manifest(self, sha: str) -> dict[str, str]:
        """Git object ids of the rebuild-relevant paths at a commit; absent paths are left out."""
        root = self._tree(sha)
        found: dict[str, str] = {}
        for path in REBUILD_PATHS:
            if "/" not in path and path in root:
                found[path] = str(root[path].get("sha", ""))
        smos = root.get("smos")
        if smos and smos.get("type") == "tree":
            sub = self._tree(str(smos["sha"]))
            for path in REBUILD_PATHS:
                head, _, tail = path.partition("/")
                if head == "smos" and tail in sub:
                    found[path] = str(sub[tail].get("sha", ""))
        return found

    def download_archive(self, sha: str, dest: Path) -> None:
        if not _SHA.fullmatch(sha):
            raise GitHubError("refusing to download anything but a full commit id")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cap = self._config.max_archive_bytes
        url = f"{self._config.archive_base}/{self._config.repo}/archive/{sha}.tar.gz"
        try:
            with self._open(url, _DOWNLOAD_TIMEOUT) as response:
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > cap:
                    raise GitHubError(f"the release archive is larger than the {cap // (1024 * 1024)} MB limit")
                written = 0
                with open(dest, "wb") as out:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > cap:
                            raise GitHubError(
                                f"the release archive is larger than the {cap // (1024 * 1024)} MB limit")
                        out.write(chunk)
        except GitHubError:
            dest.unlink(missing_ok=True)
            raise
        except OSError as err:
            dest.unlink(missing_ok=True)
            raise GitHubError(f"download failed: {err}") from None
