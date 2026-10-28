"""Wires the updater into an app and describes its state for the dashboard."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import smos
from smos.launcher import UpdatePaths, load_build_info, read_json
from smos.update.apply import Updater
from smos.update.config import UpdateConfig
from smos.update.github import GitHubClient
from smos.update.status import UpdateChecker
from smos.update.versions import format_version

log = logging.getLogger("smos.update")


@dataclass
class UpdateService:
    config: UpdateConfig
    checker: Optional[UpdateChecker]
    updater: Optional[Updater]
    disabled_reason: Optional[str]


def attach(app, data_dir: Path, environ: Optional[Mapping[str, str]] = None,
           start_checker: bool = True) -> UpdateService:
    """Create the update service for `app` (disabled with a reason when it cannot work)."""
    config = UpdateConfig.from_env(os.environ if environ is None else environ)
    info = load_build_info()
    reason: Optional[str] = None
    if not config.repo:
        reason = "Updates are switched off (SMOS_UPDATE_REPO is empty)."
    elif config.problem:
        reason = f"Updates are disabled: {config.problem}."
    elif not info.get("build_id") or not isinstance(info.get("manifest"), dict):
        reason = "Updating is only available when SMOS runs from its container image."

    if reason is not None:
        service = UpdateService(config, None, None, reason)
    else:
        client = GitHubClient(config)
        checker = UpdateChecker(config, client, smos.__version__, info["manifest"])
        updater = Updater(app, config, client, checker, UpdatePaths(data_dir), running_version=smos.__version__)
        service = UpdateService(config, checker, updater, None)
        previous_close = app.state.close

        def close() -> None:
            checker.stop()
            previous_close()

        app.state.close = close
        if start_checker:
            checker.start()
    app.state.update = service
    return service


def describe(app) -> dict:
    """Everything the dashboard and the update page show about updates."""
    service: Optional[UpdateService] = getattr(app.state, "update", None)
    paths = UpdatePaths(app.state.data_dir)
    last = read_json(paths.last_result)
    out = {
        "enabled": False, "reason": None, "state": "disabled", "running": smos.__version__,
        "release_version": None, "tag": None, "notes": "", "url": "", "sha": None, "rebuild_files": [],
        "error": None, "can_apply": False, "retry_after": 0, "updater_state": "idle", "last_result": last,
    }
    if service is None or service.checker is None or service.updater is None:
        out["reason"] = service.disabled_reason if service else "Updates are not configured."
        return out
    status = service.checker.status()
    out.update(
        enabled=True, state=status.state, running=status.running, sha=status.sha,
        rebuild_files=list(status.rebuild_files), error=status.error,
        retry_after=service.checker.retry_after(), updater_state=service.updater.state(),
    )
    if status.release is not None:
        out.update(release_version=format_version(status.release.version), tag=status.release.tag,
                   notes=status.release.notes, url=status.release.url)
    out["can_apply"] = status.state == "available" and out["updater_state"] == "idle"
    return out
