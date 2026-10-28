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
curl -s http://localhost:8080/api/v1/health         # {"status":"ok","version":"0.2.0"} (use your SMOS_HOST_PORT)
docker compose logs --tail 15 smos
echo | openssl s_client -connect pypi.org:443 -servername pypi.org 2>/dev/null | openssl x509 -noout -issuer
```

## State of the installation

Installed and working from the code of 2026-10-28 (Projects 1 and 2: API, tokens, Docker). **Not updated** with the
dashboard (Project 3) or later work: the Governor decided not to touch the Debian installation until all planned
updates are finished. Do not tell them to `git pull` there before they say so.
