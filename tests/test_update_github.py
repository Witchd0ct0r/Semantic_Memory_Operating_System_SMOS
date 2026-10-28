from __future__ import annotations

import ssl
from unittest import mock

import pytest

from smos.update import github
from smos.update.config import UpdateConfig
from smos.update.github import GitHubClient, GitHubError
from tests.fake_github import FakeGitHub, json_response

REPO = "o/r"
SHA = "a" * 40


def _client(fake: FakeGitHub, **overrides) -> GitHubClient:
    cfg = UpdateConfig(repo=REPO, api_base=fake.url, archive_base=fake.url, ca_bundle=None, **overrides)
    return GitHubClient(cfg, proxies={})


def _release(tag="v0.3.0", body="notes", **extra) -> dict:
    return {"tag_name": tag, "body": body, "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
            "draft": False, "prerelease": False, **extra}


def test_latest_release_parses_tag_notes_url() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(_release(body="Fixes things"))
        release = _client(fake).latest_release()
    assert release.version == (0, 3, 0) and release.tag == "v0.3.0"
    assert release.notes == "Fixes things" and release.url.endswith("/releases/tag/v0.3.0")


def test_no_release_returns_none() -> None:
    with FakeGitHub() as fake:  # unknown route -> 404, exactly what a repo without releases answers
        assert _client(fake).latest_release() is None


@pytest.mark.parametrize("tag", ["0.3.0", "v0.3", "v1.0.0-rc1", "nightly"])
def test_non_semver_tag_is_an_error(tag) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(_release(tag=tag))
        with pytest.raises(GitHubError, match="not a vX.Y.Z"):
            _client(fake).latest_release()


def test_draft_or_prerelease_is_never_offered() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(_release(prerelease=True))
        assert _client(fake).latest_release() is None


def test_rate_limit_message() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(
            {"message": "rate limit"}, status=403, headers={"X-RateLimit-Remaining": "0"})
        with pytest.raises(GitHubError, match="rate limit"):
            _client(fake).latest_release()


def test_other_http_errors_name_the_status() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = (500, {}, b"oops")
        with pytest.raises(GitHubError, match="HTTP 500"):
            _client(fake).latest_release()


@pytest.mark.parametrize("body", [b"<html>Bad gateway</html>", b"", b"[1, 2]", b"\xff\xfe"])
def test_invalid_json_and_html_error_page_are_errors(body) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = (200, {}, body)
        with pytest.raises(GitHubError, match="unexpected response"):
            _client(fake).latest_release()


def test_oversized_json_is_refused() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = (200, {}, b"{" + b" " * (2 * 1024 * 1024) + b"}")
        with pytest.raises(GitHubError, match="unexpected response"):
            _client(fake).latest_release()


def test_connection_refused_is_an_error_not_an_exception_leak() -> None:
    with FakeGitHub() as fake:
        client = _client(fake)
    with pytest.raises(GitHubError, match="cannot reach GitHub"):
        client.latest_release()


def test_notes_are_truncated() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(_release(body="x" * 100_000))
        assert len(_client(fake).latest_release().notes) <= 20_000


def test_notes_may_be_null() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(_release(body=None))
        assert _client(fake).latest_release().notes == ""


def test_resolve_tag_returns_the_commit_sha() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/commits/v0.3.0"] = json_response({"sha": SHA})
        assert _client(fake).resolve_tag("v0.3.0") == SHA


@pytest.mark.parametrize("sha", ["abc123", "A" * 40, "g" * 40, "a" * 41, ""])
def test_resolve_tag_requires_40_lowercase_hex(sha) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/commits/v0.3.0"] = json_response({"sha": sha})
        with pytest.raises(GitHubError):
            _client(fake).resolve_tag("v0.3.0")


def test_target_manifest_reads_root_and_smos_subtree() -> None:
    root = {"tree": [
        {"path": "pyproject.toml", "type": "blob", "sha": "1" * 40},
        {"path": "Dockerfile", "type": "blob", "sha": "2" * 40},
        {"path": "compose.yaml", "type": "blob", "sha": "3" * 40},
        {"path": "models", "type": "tree", "sha": "4" * 40},
        {"path": "smos", "type": "tree", "sha": "5" * 40},
        {"path": "README.md", "type": "blob", "sha": "9" * 40},
    ]}
    smos = {"tree": [{"path": "launcher.py", "type": "blob", "sha": "6" * 40},
                     {"path": "__init__.py", "type": "blob", "sha": "7" * 40}]}
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/git/trees/{SHA}"] = json_response(root)
        fake.routes[f"/repos/{REPO}/git/trees/{'5' * 40}"] = json_response(smos)
        manifest = _client(fake).target_manifest(SHA)
    assert manifest == {"pyproject.toml": "1" * 40, "Dockerfile": "2" * 40, "compose.yaml": "3" * 40,
                        "models": "4" * 40, "smos/launcher.py": "6" * 40}


def test_target_manifest_omits_missing_entries() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/git/trees/{SHA}"] = json_response(
            {"tree": [{"path": "Dockerfile", "type": "blob", "sha": "2" * 40}]})
        assert _client(fake).target_manifest(SHA) == {"Dockerfile": "2" * 40}


def test_download_writes_the_archive(tmp_path) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = (200, {}, b"archive-bytes")
        dest = tmp_path / "a.tar.gz"
        _client(fake).download_archive(SHA, dest)
    assert dest.read_bytes() == b"archive-bytes"


def test_download_enforces_size_cap_and_cleans_partial_file(tmp_path) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = (200, {}, b"x" * 5000)
        dest = tmp_path / "a.tar.gz"
        with pytest.raises(GitHubError, match="larger than"):
            _client(fake, max_archive_bytes=1000).download_archive(SHA, dest)
    assert not dest.exists()


def test_download_refuses_redirect_to_plain_http_elsewhere(tmp_path) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = (302, {"Location": "http://example.invalid/x.tgz"}, b"")
        dest = tmp_path / "a.tar.gz"
        with pytest.raises(GitHubError):
            _client(fake).download_archive(SHA, dest)
    assert not dest.exists()


def test_download_follows_same_origin_redirect_on_loopback(tmp_path) -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/{REPO}/archive/{SHA}.tar.gz"] = (302, {"Location": f"{fake.url}/codeload/x"}, b"")
        fake.routes["/codeload/x"] = (200, {}, b"payload")
        dest = tmp_path / "a.tar.gz"
        _client(fake).download_archive(SHA, dest)
    assert dest.read_bytes() == b"payload"


def test_download_rejects_a_non_sha_argument(tmp_path) -> None:
    with FakeGitHub() as fake:
        with pytest.raises(GitHubError):
            _client(fake).download_archive("main", tmp_path / "a.tar.gz")
        assert fake.requests == []


def test_request_urls_use_only_the_configured_repo() -> None:
    with FakeGitHub() as fake:
        fake.routes[f"/repos/{REPO}/releases/latest"] = json_response(_release())
        fake.routes[f"/repos/{REPO}/commits/v0.3.0"] = json_response({"sha": SHA})
        client = _client(fake)
        client.latest_release()
        client.resolve_tag("v0.3.0")
        assert fake.requests and all(p.startswith(f"/repos/{REPO}/") for p in fake.requests)


def test_ssl_context_verifies_and_adds_bundle_only_when_present(tmp_path) -> None:
    bundle = tmp_path / "ca.crt"
    bundle.write_text("x")
    spy = mock.MagicMock(spec=ssl.SSLContext)
    with mock.patch.object(github.ssl, "create_default_context", return_value=spy):
        github.build_ssl_context(UpdateConfig(repo=REPO, ca_bundle=bundle))
        spy.load_verify_locations.assert_called_once_with(cafile=str(bundle))
        spy.reset_mock()
        github.build_ssl_context(UpdateConfig(repo=REPO, ca_bundle=tmp_path / "missing.crt"))
        github.build_ssl_context(UpdateConfig(repo=REPO, ca_bundle=None))
        spy.load_verify_locations.assert_not_called()


def test_ssl_context_defaults_are_strict() -> None:
    ctx = github.build_ssl_context(UpdateConfig(repo=REPO, ca_bundle=None))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
