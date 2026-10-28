# Project 2 (Docker packaging) — verification record

Date: 2026-10-28. Spec: `docs/superpowers/specs/2026-10-28-docker-packaging-design.md`.
Plan: `docs/superpowers/plans/2026-10-28-docker-packaging.md`.
Tested on Docker Desktop 29.6.1 (Windows, Linux engine) with `python:3.12-slim`, then installed on the
Governor's Debian machine (Docker 29.3.0, Compose 5.1.1, 2 cores, 7 GB RAM) by following the install guide.

## Build

`docker compose build`: 499 s on a fast connection, exit 0. Image: 3.35 GB compressed
(`docker image inspect`); unpacked site-packages measured at about 5.5 GB (CUDA libraries 3.2 GB,
PyTorch 1.2 GB, Triton 0.9 GB). Docker Desktop reports 9.8 GB because of its containerd store.
Removing the NVIDIA/Triton packages was tried: PyTorch then fails to import
(`libcublasLt not found`), so they stay. `TORCH_INDEX_URL` allows a CPU-only index if one is reachable.

## Checks against the spec

| Check | Result |
|---|---|
| Container becomes healthy | healthy 12 s after start; `GET /api/v1/health` returns `{"status":"ok","version":"0.2.0"}` |
| Runs as non-root | `uid=10001(smos)` |
| Invitation flow and scopes against the container | read-only token 403 on ingest/delete, 200 on query/OpenAPI; rw token ingests; admin list shows scope; 403s in usage |
| Invitation secret in container log | 0 occurrences |
| Data survives `docker compose down` then `up` | same memories and tokens; admin login still works without re-supplying credentials |
| Fresh volume without admin variables | exits 1: "No admin account exists. Set SMOS_ADMIN_USER and SMOS_ADMIN_PASSWORD ..." |
| Incomplete model folder | exits with "The embedding model folder ... is incomplete; missing: ..." (no download attempt) |
| No network at run time | `--network none`: starts, embeds a new document, query finds it; `1.1.1.1` unreachable |
| Model loaded from the bundled folder | `SMOS_EMBEDDING_MODEL_DIR=/opt/smos/models/all-MiniLM-L6-v2`, `HF_HUB_OFFLINE=1`; vectors match loading by name (cosine > 0.9999, test in the suite) |
| Backup and restore from the install guide | tar backup, restore into a new volume, same counts, admin login works |
| Forgot-password recipe | old password 401, new password 303, tokens kept |
| Idle memory | about 0.5 GB |

Tests: 438 + static Docker file checks (`tests/test_docker_files.py`) all pass.

## Debian install (Governor's machine, 2026-10-28)

Result: working. Findings that changed the repo:

| Finding | Change |
|---|---|
| The build failed with `CERTIFICATE_VERIFY_FAILED`: the network re-signs HTTPS with its own CA, trusted by the host but not by the build container | optional `certs/ca-bundle.crt` (git-ignored) used by every `pip` step through `PIP_CERT`; install guide step 3 |
| The first fix covered only one `pip` step; `pip install .` downloads build tools too | the bundle is applied to both steps; a test checks every `pip install` step |
| Port 8765 timed out from client PCs; 8080 worked (a network rule; no host firewall) | `SMOS_HOST_PORT` in `.env`; troubleshooting row |
| Some browsers upgrade `http://` to `https://` (log: `Invalid HTTP request received`) | troubleshooting row: private window, another browser or setting; `curl` proves the path |
| `SMOS_PUBLIC_URL` filled from the server's own host name did not match the DNS name clients use | guide already says to use the address people type; the invitation link follows `SMOS_PUBLIC_URL` |

Verified on the Debian machine: image built, container healthy, health endpoint on the server and from a
client PC (`curl`), admin login in a browser, invitation link created and claimed, token used from a
client PC to ingest a document (`success`) and query it back (exact text, score, document summary).

## Not verified here

Building and running on the Debian machine (the Governor's step), `TORCH_INDEX_URL` with a real CPU
index (unreachable from the target), HTTPS or a reverse proxy (out of scope).
