# Update drill: prove the in-place update and the rollback on Debian

Purpose: exercise the real updater and rollback inside the production image, which no test has done. Do it on the Debian
host after it runs the rebuilt `v0.3.1` image. Nothing here touches the real repository's releases: the drill uses a
throwaway GitHub repository, so a deliberately broken release never appears on the real releases page (published tags must
never move or be deleted, `docs/RELEASING.md`).

## 0. One-time setup

1. (Governor, GitHub) Create an empty public repository, e.g. `<owner>/smos-update-drill` (public so the updater needs no
   token).
2. From the dev PC: `git push https://github.com/<owner>/smos-update-drill.git main` (a copy of the real `main` at the `v0.3.1`
   commit; the repository holds no company names).
3. On Debian, in `.env`: `SMOS_UPDATE_REPO=<owner>/smos-update-drill`, then `docker compose up -d` (no rebuild needed).
4. Back up first: stop, `tar czf ~/smos-backup-drill.tgz` the data volume, start (recipe in `debian-deploy.md`).

## 1. Good release: `v0.3.2` (only `smos/` and the version line change)

On the dev PC, in a clone whose `origin` is the drill repository:

1. Edit `smos/__init__.py` to `0.3.2` and make one harmless change under `smos/` (a docstring is enough).
2. `rtk proxy python -m pytest tests -q`, commit, `git tag v0.3.2`, push `main` and the tag.
3. On the drill repository create a GitHub Release for `v0.3.2` with notes ("drill: harmless patch").

On Debian: open `/admin/update`, press *Check now*. Expected: "Release 0.3.2 is available", **not** "needs a rebuilt image".
(If it says rebuild, the single-sourcing fix did not hold: stop and read the listed differing files.) Press *Update* and
confirm both version and SHA. Expected: maintenance page, restart, dashboard back at 0.3.2, `/api/v1/health` reports `0.3.2`,
an `update_log` row "success", data intact (counts unchanged).

## 2. Failing release: `v0.3.3` (must roll back)

Make the new code unable to start healthy, the way a real bad release would: append an admin-DB migration that raises
(`smos/admin/db.py`: add `(5, "drill", _boom)` to `ADMIN_MIGRATIONS`, where `_boom` raises `RuntimeError("drill")`). Bump to
`0.3.3`, tag, push, create the release (notes: "drill: must be rolled back").

The staged package still imports, so it passes the smoke test and the failure appears at startup (the migration), which is the
case the rollback exists for. On Debian press *Update*. Expected: the supervisor starts the new code, health never reports
0.3.3 (up to 120 s), the supervisor kills the child, restores the pre-update snapshot of the data directory and starts 0.3.2
again; `/api/v1/health` reports `0.3.2`; `update_log` shows a rolled-back result; counts unchanged; `docker logs smos` shows
the reason.

If time allows, as separate runs: `docker restart smos` during the 120 s wait (restart-safe marker `rollback.json`), and
`docker stop smos` (SIGTERM through PID 1; the container should exit within the 60 s grace period).

## 3. Clean up

Remove `SMOS_UPDATE_REPO` from `.env` and `docker compose up -d`; delete the drill repository; keep `smos-backup-drill.tgz`
until satisfied. The installation then runs overlay code 0.3.2 on top of the 0.3.1 image; the next rebuild from the real `main`
returns it to the image's own code (a pending update from an older image build is discarded by design).
