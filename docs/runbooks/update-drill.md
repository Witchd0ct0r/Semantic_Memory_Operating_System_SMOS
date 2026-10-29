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

Prepared locally in `C:\Private\smos-update-drill` (a clone of `main` with no remote; commits `drill: harmless patch 0.3.2`,
tag `v0.3.2`, and the failing `v0.3.3` after it). Publish in this order, never both releases at once:

1. Add the remote and push `main` plus ONLY the two drill tags:
   `git remote add origin https://github.com/<owner>/smos-update-drill.git`,
   `git push origin main v0.3.1 v0.3.2 v0.3.3` (the old v0.1.x / v0.3.0 tags stay local).
2. On GitHub create a Release for tag `v0.3.2` only (notes: "drill: harmless patch"). Do not create one for `v0.3.3` yet.

On Debian: open `/admin/update`, press *Check now*. Expected: "Release 0.3.2 is available", **not** "needs a rebuilt image".
(If it says rebuild, the single-sourcing fix did not hold: stop and read the listed differing files.) Press *Update* and
confirm both version and SHA. Expected: maintenance page, restart, dashboard back at 0.3.2, `/api/v1/health` reports `0.3.2`,
an `update_log` row "success", data intact (counts unchanged).

## 2. Failing release: `v0.3.3` (must roll back)

`v0.3.3` adds admin-DB migration 5 (creates the table `drill_marker`, succeeds) and then makes `open_admin_db` raise, so the
new code migrates the data and then cannot start: the worst case for a restore. Create its GitHub Release (notes: "drill: must
be rolled back"), press *Check now*, *Update*. Expected: health never reports 0.3.3 (up to 120 s), the supervisor kills the
child, restores the pre-update snapshot of the data directory and starts 0.3.2 again; `/api/v1/health` reports `0.3.2`;
`update_log` shows a rolled-back result; counts unchanged; `docker compose logs smos` shows the reason. **Proof that the data was
restored, not just the code:** `docker compose exec smos python -c "import sqlite3; print(sqlite3.connect('/data/admin.db').execute(\"select name from sqlite_master where name='drill_marker'\").fetchall())"` prints `[]`
(and `select * from schema_version` on admin.db says 4).

If time allows, as separate runs: `docker restart smos` during the 120 s wait (restart-safe marker `rollback.json`), and
`docker stop smos` (SIGTERM through PID 1; the container should exit within the 60 s grace period).

## 3. Clean up

Remove `SMOS_UPDATE_REPO` from `.env` and `docker compose up -d`; delete the drill repository; keep `smos-backup-drill.tgz`
until satisfied. The installation then runs overlay code 0.3.2 on top of the 0.3.1 image; the next rebuild from the real `main`
returns it to the image's own code (a pending update from an older image build is discarded by design).
