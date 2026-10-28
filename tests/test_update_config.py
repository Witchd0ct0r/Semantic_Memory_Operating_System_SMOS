from __future__ import annotations

import pytest

from smos.update.config import DEFAULT_REPO, UpdateConfig
from smos.update.versions import format_version, is_newer, parse_tag, parse_version


@pytest.mark.parametrize("tag", ["v0.3.0", "v10.20.30", "v0.0.1"])
def test_parse_tag_accepts_vx_y_z(tag) -> None:
    assert parse_tag(tag) is not None


@pytest.mark.parametrize("tag", ["v0.3", "0.3.0", "v1.0.0-rc1", "v1.0.0 ", " v1.0.0", "vX", "", "v1.0.0.0", "V1.0.0",
                                 "v01.0.0x", "v1.0.0\n"])
def test_parse_tag_rejects_everything_else(tag) -> None:
    assert parse_tag(tag) is None


def test_parse_tag_returns_comparable_tuple() -> None:
    assert parse_tag("v10.2.3") == (10, 2, 3)
    assert parse_version("0.2.0") == (0, 2, 0) and parse_version("0.0.0-dev") is None
    assert format_version((1, 2, 3)) == "1.2.3"


def test_is_newer_is_strict() -> None:
    assert is_newer((0, 3, 0), (0, 2, 0))
    assert is_newer((0, 10, 0), (0, 9, 9))  # numeric, not text, comparison
    assert not is_newer((0, 2, 0), (0, 2, 0))
    assert not is_newer((0, 1, 9), (0, 2, 0))
    assert is_newer((0, 0, 1), None)  # an unparseable running version counts as 0.0.0


def test_from_env_defaults() -> None:
    cfg = UpdateConfig.from_env({})
    assert cfg.repo == DEFAULT_REPO and cfg.enabled and cfg.problem is None
    assert cfg.check_minutes == 360
    assert cfg.api_base == "https://api.github.com" and cfg.archive_base == "https://github.com"


def test_empty_repo_disables() -> None:
    cfg = UpdateConfig.from_env({"SMOS_UPDATE_REPO": ""})
    assert not cfg.enabled and cfg.problem is None


@pytest.mark.parametrize("repo", ["nonsense", "a/b/c", "a b/c", "../x/y", "https://evil/x/y", "a/"])
def test_bad_repo_sets_problem_and_disables(repo) -> None:
    cfg = UpdateConfig.from_env({"SMOS_UPDATE_REPO": repo})
    assert not cfg.enabled and cfg.problem


def test_good_repo_is_accepted() -> None:
    assert UpdateConfig.from_env({"SMOS_UPDATE_REPO": "some-owner/Some_Repo.x"}).enabled


@pytest.mark.parametrize("value,expected", [("30", 30), ("5", 5), ("10080", 10080),
                                            ("4", 360), ("10081", 360), ("abc", 360), ("", 360)])
def test_check_minutes_bounds(value, expected) -> None:
    assert UpdateConfig.from_env({"SMOS_UPDATE_CHECK_MINUTES": value}).check_minutes == expected


@pytest.mark.parametrize("base,ok", [
    ("https://example.org", True), ("http://127.0.0.1:9999", True), ("http://localhost:1", True),
    ("http://host.docker.internal:8000", True), ("http://example.org", False),
    ("ftp://example.org", False), ("file:///etc", False), ("example.org", False),
])
def test_base_overrides_require_https_or_loopback(base, ok) -> None:
    for var in ("SMOS_UPDATE_API_BASE", "SMOS_UPDATE_ARCHIVE_BASE"):
        cfg = UpdateConfig.from_env({var: base})
        assert (cfg.problem is None) is ok, (var, base)
        assert cfg.enabled is ok
