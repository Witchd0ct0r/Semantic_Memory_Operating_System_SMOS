# Installing SMOS on a Debian server (Docker)

Written for the person who starts SMOS on a Linux machine. Every step is one short command.
You need: Docker with the Compose plugin, a user in the `docker` group, access to GitHub,
Docker Hub and PyPI **during the build only**. At run time SMOS needs no internet (the optional update
check contacts GitHub, see *Updating from the dashboard*), and it never contacts Hugging Face (the
embedding model is bundled).

Size and time to expect: the image is about 6 GB unpacked on disk (3.4 GB compressed). The first
build downloads about 3 GB; it took about 8 minutes on a fast connection. Keep at least 12 GB of
free disk space for the build. The running service used about 0.5 GB of RAM when idle. (Docker
Desktop on Windows reports a larger size because it counts layers differently; on Debian check
with `docker images smos`.)

Why so large: PyTorch from PyPI is the GPU build, and its CUDA libraries (3 GB) cannot be removed
because PyTorch refuses to start without them. If a CPU-only PyTorch index becomes reachable from
your network, set `TORCH_INDEX_URL` in `.env` (for example `https://download.pytorch.org/whl/cpu`)
and the image shrinks by about 4 GB.

## 1. Get the code

```bash
git clone https://github.com/Witchd0ct0r/Semantic_Memory_Operating_System_SMOS.git smos
cd smos
```

## 2. Create your settings

```bash
cp .env.example .env
nano .env
```

Set:

- `SMOS_ADMIN_USER` and `SMOS_ADMIN_PASSWORD`: the first admin account. Choose a real password.
  They are only read the first time the data volume is empty.
- `SMOS_PUBLIC_URL`: the address people will actually type, for example `http://smos-host:8765`.
  Invitation links are built from it. If you leave it unset, links use whatever host name the
  admin's browser used, which may not work for other people.

`.env` is private to your machine and is never committed to git.

## 3. If your network inspects HTTPS (do this before building)

Many corporate networks re-sign HTTPS traffic with their own certificate authority (CA). The server
trusts it, but the build container does not, and the build stops with
`CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`. To find out, run:

```bash
echo | openssl s_client -connect pypi.org:443 -servername pypi.org 2>/dev/null | openssl x509 -noout -issuer
```

If the issuer is your organisation's or a firewall vendor's name instead of a public one such as
DigiCert, Sectigo or Let's Encrypt, give the build your network's CA bundle (the server already
has it installed):

```bash
cp /etc/ssl/certs/ca-certificates.crt certs/ca-bundle.crt
```

The file stays on your machine (git ignores it). Skip this step if the check shows a public issuer.

## 4. Build and start

```bash
docker compose build
```

```bash
docker compose up -d
```

## 5. Check that it works

```bash
docker compose ps
```

The `STATUS` column shows `healthy` after about a minute (the model loads on start). Then:

```bash
curl -s http://localhost:8765/api/v1/health
```

You should see `{"status":"ok","version":"0.3.0"}`.

## 6. First login and giving people access

1. Open `http://<server>:8765/admin/login` and log in with the admin from `.env`.
2. Under **Access**, type the user's name, choose **Read-only** (for people and agents that only
   look things up) or **Read and write** (for parsers that load documents), and press
   **Create invitation link**.
3. Send that link to the user. It works once and expires in 72 hours.
4. The user opens it, presses **Create token**, and saves the token shown. You never see it.

Your parser or agent then sends `Authorization: Bearer <token>` on every call. The API is
described at `http://<server>:8765/api/v1/docs` (log in as admin first, or use a token) and in
`docs/API.md`.

## The dashboard

After logging in you land on the dashboard (`/admin/dashboard`). It shows how many documents and stored
items SMOS holds, the database size, API calls, error rate and latency over time, CPU, memory and disk
use against what the container is allocated, the most-used API calls, activity per token, and recent
events (writes, errors, rejected tokens). Pick 1h, 24h, 7d or 30d at the top; the page refreshes itself
every minute. History is kept in the data volume (30 days of hardware samples, 90 days of API calls by
default), so it survives restarts.

Hardware figures are the container's own: if you limit it (for example `mem_limit` or `cpus` in
`compose.yaml`), the charts show usage against that limit.

## Everyday commands

| Task | Command |
|---|---|
| See logs | `docker compose logs --tail 100 smos` |
| Follow logs | `docker compose logs -f smos` |
| Stop (data stays) | `docker compose stop` |
| Start again | `docker compose start` |
| Restart | `docker compose restart smos` |
| Remove the container (data stays) | `docker compose down` |

**Never run `docker compose down -v`**: the `-v` deletes the data volume.

## Backup and restore

All data is in the Docker volume `smos-data`. Stop SMOS first so the files are consistent:

```bash
docker compose stop
```

```bash
docker run --rm -v smos_smos-data:/data -v "$PWD":/backup debian:stable-slim tar czf /backup/smos-backup.tgz -C /data .
```

```bash
docker compose start
```

To restore into an empty volume (after `docker compose down -v` or on a new machine), stop SMOS and run:

```bash
docker run --rm -v smos_smos-data:/data -v "$PWD":/backup debian:stable-slim sh -c "cd /data && tar xzf /backup/smos-backup.tgz && chown -R 10001:10001 /data"
```

The volume is named `<folder>_smos-data`; if your folder is not called `smos`, run
`docker volume ls` to see the exact name.

## Updating from the dashboard

The dashboard checks GitHub for a newer **release** (not every commit: only a release the maintainer
publishes, tagged `vX.Y.Z`) every 6 hours, and when you press *Check now*. When one is found, the banner
says so; open it, read the release notes and press *Update*. What happens:

1. SMOS downloads that exact release, checks it (only the `smos/` code is accepted, of known file types and
   limited size) and test-imports it.
2. It starts refusing writes (HTTP 503; reads and the dashboard keep working) and restarts.
3. A copy of the whole data folder is taken, the new code is switched on and has up to 2 minutes to answer
   its health check **with the new version number**.
4. If it does not, SMOS goes back to the previous code **and** the copy of the data, and the dashboard shows
   why. Anything written between the copy and the failed start is lost, which is why writes are refused for
   those minutes.

Good to know:

- Needs free disk: the data folder size plus 20 %, and room for the download (about 100 MB at most). If space
  is short the update is refused before anything changes.
- A release that changes the files baked into the image (`pyproject.toml`, `Dockerfile`, `compose.yaml`,
  the bundled model, `smos/launcher.py`) is shown as **needs a rebuilt image** and is not applied; use
  *Rebuilding* below.
- Updating needs outbound HTTPS to `api.github.com` and `github.com` (and the hosts GitHub redirects
  downloads to). The CA bundle from step 3 is used for this at run time as well; for a proxy set
  `HTTPS_PROXY` (and `NO_PROXY`) in `.env`.
- Every attempt and its outcome is recorded (who, from/to, result) in the admin database.
- **Trust:** an update runs code downloaded from the configured GitHub repository. Whoever controls that
  repository's owner account controls what your installation runs after the next update. SMOS does not
  verify signatures. To switch updating off, set `SMOS_UPDATE_REPO=` (empty) in `.env`.
- Updating is only available from the container image (it needs the build information baked in).

## Rebuilding

Use this when the dashboard says a release needs a rebuilt image, or whenever you prefer not to update
from the dashboard:

```bash
git pull
```

```bash
docker compose build
```

```bash
docker compose up -d
```

Your data is kept. SMOS upgrades its own database format on start and keeps a backup file
(`*.bak-v<N>`) next to the database before it does.

## Problems

| Symptom | Fix |
|---|---|
| `env file .env not found` | Do step 2. |
| Container exits at once; log says "No admin account exists" | `SMOS_ADMIN_USER` / `SMOS_ADMIN_PASSWORD` are missing in `.env` and the volume is new. |
| Log says "embedding model folder ... is incomplete" | The `models/` folder was not copied completely; run `git status` and `git checkout -- models`, then rebuild. |
| `port is already allocated` | Something else uses 8765. Set `SMOS_HOST_PORT=9000` (any free port) in `.env`, set `SMOS_PUBLIC_URL` to match, then `docker compose up -d`. |
| Browser times out, but `curl http://<server-address>:8765/api/v1/health` works on the server | Something between your PC and the server blocks the port (a network rule; check with `systemctl is-active ufw nftables firewalld` that the server has no host firewall). Ask your network administrator to allow the port, or pick a port your network already allows with `SMOS_HOST_PORT` in `.env` and run `docker compose up -d`. |
| Browser says `ERR_SSL_PROTOCOL_ERROR` and the container log shows `Invalid HTTP request received` | The browser is upgrading the address to `https://`, but SMOS speaks plain HTTP. Type the full `http://...` address, use a private window, turn off the browser's "Always use secure connections" setting, or use another browser. `curl http://<server>:<port>/api/v1/health` from the client PC proves the network path works. If policy forces HTTPS everywhere, put a TLS reverse proxy in front of SMOS. |
| Forgot the admin password | Stop SMOS, remove the account with `docker compose run --rm smos python -c "import sqlite3; c=sqlite3.connect('/data/admin.db'); c.execute('DELETE FROM admins'); c.commit()"`, then set a new `SMOS_ADMIN_PASSWORD` in `.env` and start SMOS; the first-start bootstrap creates the admin again. |
| Invitation links point to the wrong address | Set `SMOS_PUBLIC_URL` in `.env`, then `docker compose up -d`. |
| Build stops with `CERTIFICATE_VERIFY_FAILED` | Do step 3 (give the build your network's CA bundle), then build again. |
| Build is very slow or runs out of disk | The first build downloads about 3 GB. Check free space with `df -h`. |

## What is inside the container

SMOS runs as a normal (non-root) user, listens on port 8765 over plain HTTP, and writes only to
`/data`. Put a reverse proxy in front of it if you later need HTTPS; then set
`SMOS_TRUST_PROXY=1` and `SMOS_COOKIE_SECURE=1` in `.env`, and make sure the proxy does not log
`/claim/*` URLs (they carry one-time invitation secrets).
