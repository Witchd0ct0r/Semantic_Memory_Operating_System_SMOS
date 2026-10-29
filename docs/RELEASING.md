# Releasing SMOS

Installed servers update themselves from **GitHub Releases** (see `INSTALL.md`, "Updating from the dashboard").
A commit on `main` never reaches an installation; only a release does. Follow this list for every release.

1. **Version.** Set `X.Y.Z` in `smos/__init__.py` (`__version__`) and nowhere else: `pyproject.toml` reads it
   (`dynamic = ["version"]`) and `compose.yaml` uses the image tag `smos:${SMOS_IMAGE_TAG:-latest}`. Those files are part of
   the rebuild fingerprint (step 3), so a version number in any of them would make every release look like it needs a
   rebuilt image (the v0.3.0 defect); `tests/test_versioning.py` fails if one appears. The running server compares its
   `__version__` with the release tag, and an update is refused if the release's code says another version than its tag.
2. **Tests.** `python -m pytest tests/ -q` is green (use `rtk proxy python -m pytest tests/ -q` if it says
   "No tests collected").
3. **Does it need a rebuild?** If the release changes any of `pyproject.toml`, `Dockerfile`, `compose.yaml`,
   `models/` or `smos/launcher.py`, installations cannot take it from the dashboard: they show "needs a rebuilt
   image" and operators must `git pull` and rebuild. Say so at the top of the release notes. Never edit
   `smos/launcher.py` casually: the supervisor and its state files (`/data/updates/*.json`) are frozen between
   rebuilds on purpose.
4. **File types.** The updater only unpacks `.py .html .css .js .json .txt .md` under `smos/`
   (`ALLOWED_SUFFIXES` in `smos/update/unpack.py`). A release that adds another file type under `smos/`
   (an image, a font) is refused by running installations until they are rebuilt, so avoid it.
5. **Database changes.** Add a new numbered migration; never edit an existing one (migrations are forward-only).
   An update that fails its health check restores the data from before, so a bad migration is recoverable,
   but a migration that succeeds and is wrong is not.
6. **Publish.** On GitHub, create a Release for tag `vX.Y.Z` on that commit. It must not be a draft or a
   pre-release (those are never offered). Write the notes for operators: what changed, whether a rebuild is
   needed, anything to do by hand. The notes are shown on the update page as plain text.
7. **Never move or delete a published tag.** If a release is bad, publish a new, higher patch release.

SMOS is not published to PyPI; a tag does not trigger any automatic publishing.
