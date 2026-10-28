# Installing SMOS on a Debian server (Docker)

Written for the person who starts SMOS on a Linux machine. Every step is one short command.
You need: Docker with the Compose plugin, a user in the `docker` group, access to GitHub,
Docker Hub and PyPI **during the build only**. At run time SMOS needs no internet, and it never
contacts Hugging Face (the embedding model is bundled).

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

You should see `{"status":"ok","version":"0.2.0"}`.

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

## Upgrading

Until the in-app updater exists, upgrade by rebuilding:

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
| Forgot the admin password | Stop SMOS, remove the account with `docker compose run --rm smos python -c "import sqlite3; c=sqlite3.connect('/data/admin.db'); c.execute('DELETE FROM admins'); c.commit()"`, then set a new `SMOS_ADMIN_PASSWORD` in `.env` and start SMOS; the first-start bootstrap creates the admin again. |
| Invitation links point to the wrong address | Set `SMOS_PUBLIC_URL` in `.env`, then `docker compose up -d`. |
| Build stops with `CERTIFICATE_VERIFY_FAILED` | Do step 3 (give the build your network's CA bundle), then build again. |
| Build is very slow or runs out of disk | The first build downloads about 3 GB. Check free space with `df -h`. |

## What is inside the container

SMOS runs as a normal (non-root) user, listens on port 8765 over plain HTTP, and writes only to
`/data`. Put a reverse proxy in front of it if you later need HTTPS; then set
`SMOS_TRUST_PROXY=1` and `SMOS_COOKIE_SECURE=1` in `.env`, and make sure the proxy does not log
`/claim/*` URLs (they carry one-time invitation secrets).
