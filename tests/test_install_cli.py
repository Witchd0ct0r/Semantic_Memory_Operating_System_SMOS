from __future__ import annotations

from pathlib import Path

import pytest

import smos.install.cli as cli_module
from smos.install.cli import _check_smos_package, _is_local_checkout


def test_is_local_checkout_true_for_smos_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "smos-mcp"\n', encoding="utf-8")
    assert _is_local_checkout(tmp_path) is True


def test_is_local_checkout_false_without_git(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "smos-mcp"\n', encoding="utf-8")
    assert _is_local_checkout(tmp_path) is False


def test_is_local_checkout_false_for_different_package(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "some-other-pkg"\n', encoding="utf-8"
    )
    assert _is_local_checkout(tmp_path) is False


def test_is_local_checkout_false_without_pyproject(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert _is_local_checkout(tmp_path) is False


def test_check_smos_package_fails_when_version_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "_current_version", lambda: "unknown")
    with pytest.raises(SystemExit):
        _check_smos_package()


def test_check_smos_package_ok_when_version_known(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli_module, "_current_version", lambda: "0.1.4")
    _check_smos_package()
    captured = capsys.readouterr()
    assert "0.1.4" in captured.out


def test_update_dry_run_uses_editable_install_for_local_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "smos-mcp"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "_current_version", lambda: "0.1.4")

    cli_module._update(dry_run=True)

    captured = capsys.readouterr()
    assert "-e" in captured.out
    assert "git+" not in captured.out


def test_update_dry_run_uses_git_remote_when_not_local_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "_current_version", lambda: "0.1.4")

    cli_module._update(dry_run=True)

    captured = capsys.readouterr()
    assert "git+" in captured.out


def test_write_claude_md_creates_file_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    cli_module._write_claude_md(dry_run=False)

    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    assert claude_md.exists()
    assert claude_md.read_text(encoding="utf-8").count(cli_module._SNIPPET_START) == 1


def test_write_claude_md_skips_without_prompting_when_already_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text(cli_module._CLAUDE_MD_SNIPPET, encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def _fail_if_called(_prompt: str = "") -> str:
        raise AssertionError("input() should not be called when the policy is already present")

    monkeypatch.setattr("builtins.input", _fail_if_called)

    cli_module._write_claude_md(dry_run=False)

    assert claude_md.read_text(encoding="utf-8").count(cli_module._SNIPPET_START) == 1


def test_write_claude_md_does_not_duplicate_on_repeated_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    cli_module._write_claude_md(dry_run=False)
    cli_module._write_claude_md(dry_run=False)

    claude_md = tmp_path / ".claude" / "CLAUDE.md"
    assert claude_md.read_text(encoding="utf-8").count(cli_module._SNIPPET_START) == 1


def _stub_setup_dependencies(monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    monkeypatch.setattr(cli_module, "_check_python", lambda: None)
    monkeypatch.setattr(cli_module, "_check_smos_package", lambda: None)
    monkeypatch.setattr(cli_module, "_check_claude_cli", lambda: None)
    monkeypatch.setattr(cli_module, "_check_ollama", lambda: False)
    monkeypatch.setattr(cli_module, "_install_python_deps", lambda: None)
    monkeypatch.setattr(cli_module, "_choose_model", lambda: "none")
    monkeypatch.setattr(cli_module, "_pull_model", lambda model, ollama_ok: None)
    monkeypatch.setattr(cli_module, "_register_mcp", lambda dry_run: None)
    monkeypatch.setattr(cli_module, "_write_claude_md", lambda dry_run: calls.append(dry_run))


def test_setup_skips_claude_md_write_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _stub_setup_dependencies(monkeypatch, calls)

    cli_module._setup(dry_run=True, skip_claude_md=True)

    assert calls == []


def test_setup_writes_claude_md_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _stub_setup_dependencies(monkeypatch, calls)

    cli_module._setup(dry_run=True, skip_claude_md=False)

    assert calls == [True]
