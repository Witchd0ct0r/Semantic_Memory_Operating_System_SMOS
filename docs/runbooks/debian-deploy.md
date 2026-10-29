# Debian trial deployment: facts and recipes

Durable facts about the Governor's target environment and how SMOS is installed there. The step-by-step
guide for the person starting SMOS is `docs/INSTALL.md`; the verification record is
`docs/docker-verification.md`. **No company names, host names or addresses belong in this repo** (see the
project memory note); keep them in chat only.

## The machine

- Debian Linux, x86_64, 2 cores, about 7 GB RAM, 93 GB disk. Docker 29.3, Compose 5.1.
- The Governor is a non-privileged user in the `docker` group (no sudo without a password). No host firewall is
  active (`systemctl is-active ufw nftables firewalld` shows inactive).
- The Governor usually is **not** connected to the machine from the assistant's session and retypes commands by
  hand: give one short command at a time and ask for the output.
- Install path: `git clone` from GitHub, `cp .env.example .env`, edit, `docker compose build`, `docker compose up -d`.

## The network (what it blocks and rewrites)

- Reachable from the machine: GitHub, Docker Hub, PyPI.
- **HTTPS is inspected**: the network re-signs certificates with its own CA. The host trusts it, build containers do
  not, so `pip install` fails with `CERTIFICATE_VERIFY_FAILED`. Fix: `cp /etc/ssl/certs/ca-certificates.crt
  certs/ca-bundle.crt` before building (git-ignored; applied to every `pip` step through `PIP_CERT`).
  Anything that later needs outbound HTTPS from inside the container (the Project 4 updater talking to GitHub) will
  need the same CA bundle at run time.
- Hugging Face and the PyTorch CPU index are blocked (AI-related sites are filtered). So the embedding model is
  bundled in `models/` and loaded by path, and the PyTorch GPU wheel from PyPI is used (large image).
- Port 8765 timed out from client PCs while 8080 worked: a network rule, not the host. Use `SMOS_HOST_PORT=8080`
  in `.env` (and set `SMOS_PUBLIC_URL` to the address people type, with that port).
- Some browsers upgrade `http://` to `https://` and fail with `ERR_SSL_PROTOCOL_ERROR`; the container log then shows
  `Invalid HTTP request received`. A private window or another browser worked; `curl` from a client PC proves the
  network path. SMOS serves plain HTTP only (Governor's decision).
- `SMOS_PUBLIC_URL` must use the DNS name the client PCs use, which can differ from the server's own `hostname -f`.

## Useful checks (one at a time)

```bash
docker compose ps                                   # STATUS should say (healthy)
curl -s http://localhost:8080/api/v1/health         # {"status":"ok","version":"0.3.0"} (use your SMOS_HOST_PORT)
docker compose logs --tail 15 smos
echo | openssl s_client -connect pypi.org:443 -servername pypi.org 2>/dev/null | openssl x509 -noout -issuer
```

## Rebuild / update recipe (used 2026-10-28 for 0.3.0; about 15 minutes in all)

Needed whenever a release is shown as "needs a rebuilt image" (or for the first image with the updater). One command at a time:

```bash
docker compose stop                                   # data stays; never `down -v`
docker volume ls                                      # the data volume is <folder>_smos-data (here smos_smos-data); the `certs` volume is not SMOS's
docker run --rm -v smos_smos-data:/data -v "$PWD":/backup debian:stable-slim tar czf /backup/smos-backup.tgz -C /data .
tar tzf smos-backup.tgz                               # expect ./admin.db ./faiss.index ./metadata.db
git pull
df -h .                                               # a build needs about 12 GB free
docker compose build                                  # 384 s on 2026-10-28 (dependency layer 132 s, image export 244 s)
docker compose run --rm --no-deps smos cat /opt/smos/build-info.json   # compare with `git rev-parse HEAD:<path>` for pyproject.toml Dockerfile compose.yaml models smos/launcher.py
docker compose up -d                                  # migrates the databases (writes *.bak-vN first)
docker compose ps                                     # (healthy) after 30-60 s
curl -s http://localhost:8080/api/v1/health           # version
```

`.env` and `certs/ca-bundle.crt` are git-ignored and survive `git pull`. `smos-backup.tgz` is written into the install folder
(untracked, root-owned); delete it when no longer needed.

## State of the installation

- **2026-10-28 evening: updated to 0.3.0** (dashboard, self-update, Codex-reviewed). Image `smos:0.3.0`, container healthy on
  host port 8080, health reports 0.3.0, startup log clean. The in-image `/opt/smos/build-info.json` manifest was compared with
  git and **matched on all five entries** (so releases will not falsely say "needs a rebuilt image").
- The dashboard "Check now" was reported to look good by the Governor (exact banner text not recorded; expected "SMOS is up to
  date", which also proves outbound HTTPS to GitHub with the CA bundle works inside the container).
- **Not yet exercised on this machine:** an actual in-place update (no release newer than 0.3.0 exists), rollback in the
  container, and the real SIGTERM path. Do that with a harmless patch release (see the newest handoff).
- **2026-10-29: rebuilt to 0.3.1** (`smos:latest`, in-image manifest equals git on all five entries, *Check now* said "up to
  date"). Old images `smos:0.2.0` removed, `smos:0.3.0` and the build cache cleaned.
- **2026-10-29: update drill passed** (`update-drill.md`, throwaway repo, since deleted): in-place update 0.3.1 -> 0.3.2 applied from the
  dashboard (no rebuild offered, history row "applied", data and counts unchanged, container not recreated); failing 0.3.3
  (migration 5 succeeds, then startup raises) was **rolled back** automatically ("the new code exited with code 1 before becoming
  healthy"), health back to 0.3.2, counts unchanged, and admin.db had no `drill_marker` table and schema version 4, so the DATA
  was restored. `docker compose stop` took 1.0 s (SIGTERM through PID 1 is clean).
- **Caution:** the installation now runs the drill repository's 0.3.2 code as an overlay on the 0.3.1 image (identical to the real
  0.3.1 except a docstring). The real repository must NOT publish a different `v0.3.2`: this install would say "up to date".
  Next real release: 0.3.3 or higher, or rebuild the image first (a rebuilt image discards the older overlay).
- Older images (`smos:0.2.0`, base images) are still on disk; `docker image prune` / `docker rmi smos:0.2.0` when happy.
