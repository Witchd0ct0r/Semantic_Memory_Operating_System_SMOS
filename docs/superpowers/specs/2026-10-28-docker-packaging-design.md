# SMOS Standalone — Project 2: Docker packaging (design)

Date: 2026-10-28. Status: draft for Governor approval. Follows Project 1
(`2026-10-28-standalone-core-design.md`).

## Intent

Let the Governor install and run SMOS in a container on a company Debian Linux machine for
trial runs, with one documented set of commands, persistent data, and no dependence on sites
the company blocks.

## Environment facts (from the Governor)

- Target: Debian Linux, x86_64, a non-privileged user in the `docker` group. The Governor starts
  it. The machine tested during design: Docker 29.3.0, Compose 5.1.1, 2 cores, 7 GB RAM, 93 GB disk.
- Reachable: GitHub, Docker Hub (`python:3.12-slim` pulled), PyPI. Blocked or unreachable:
  Hugging Face (company blocks most AI-related sites by wildcard), `download.pytorch.org`,
  the Aliyun PyTorch mirror. ModelScope is open but not needed.
- Access is plain HTTP on the internal network; no TLS inside SMOS.
- The Governor is often not connected to the machine and retypes commands, so every instruction
  must be short and runnable one command at a time.

## Goals

1. `docker compose build` and `docker compose up -d` give a working SMOS on port 8765.
2. The image works with Hugging Face blocked: the embedding model is bundled, loaded from a local
   folder, and never downloaded at build or run time.
3. All state lives in one named volume mounted at `/data` (memory DB, search index, admin DB,
   backups made by the migration runner); removing and recreating the container loses nothing.
4. The container runs as a non-root user, restarts on failure, and reports health through
   `GET /api/v1/health`.
5. A short `docs/INSTALL.md` for Debian: build, start, first login, check, logs, stop,
   backup and restore, manual upgrade (rebuild), common problems.

## Non-goals

HTTPS or a reverse proxy; the dashboard, metrics and self-update (Projects 3 and 4: the update
button cannot rebuild a container by itself, so Project 4 will decide how updates reach a
container); multi-architecture images; GPU support; Kubernetes; the full user guide (Project 5).

## Anti-goals (failure even if the build passes)

- The container needing internet at runtime, or the first query downloading anything.
- The admin password or any token baked into the image or committed to git.
- Data lost on `docker compose down` or on rebuilding the image.
- Running as root, or needing `--privileged` or the Docker socket.

## Decisions

- **Model bundling (Governor):** the model files are committed to the repo under
  `models/all-MiniLM-L6-v2/` (91.6 MB, weights 90.9 MB; under GitHub's 100 MB file limit),
  copied into the image, and loaded by path. The vectors stay identical to those used so far.
- **Build source (Governor):** build on the company machine from the repo; dependencies from
  PyPI only. The PyTorch download is the large CUDA build from PyPI (image about 4-5 GB) because
  the CPU-only sources are blocked; a build argument allows a CPU-only index later.
- **Access (Governor):** HTTP only on the internal network.

## Design (builder's calls; reject any)

- `smos/memory/embeddings.py` reads `SMOS_EMBEDDING_MODEL_DIR`; when set it loads from that
  directory, otherwise it keeps loading `all-MiniLM-L6-v2` by name (development). The image sets
  the variable and `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. A missing or incomplete model
  folder must fail startup with a clear message, not silently try the network.
- `Dockerfile`: `python:3.12-slim`; install the package with `pip install --no-cache-dir .`;
  a dedicated user (uid 10001); `/data` owned by it and declared a volume; defaults
  `SMOS_DATA_DIR=/data`, `SMOS_HOST=0.0.0.0`, `SMOS_PORT=8765`, `SMOS_USAGE_RETENTION_DAYS=90`;
  `HEALTHCHECK` using Python's `urllib` (no curl in the slim image); `CMD ["smos-server"]`.
- `compose.yaml`: one service `smos`, image `smos:0.2.0`, `ports: "8765:8765"`, named volume
  `smos-data:/data`, `env_file: .env`, `restart: unless-stopped`, `stop_grace_period` long enough
  for the index save at exit.
- `.env.example` (committed): `SMOS_ADMIN_USER`, `SMOS_ADMIN_PASSWORD` (required on first start
  only), optional `SMOS_PUBLIC_URL` (the base URL put into invitation links; set it to the
  address people really use, e.g. `http://host:8765`), `SMOS_TRUST_PROXY`, `SMOS_INVITE_TTL_HOURS`.
  `.env` is git-ignored.
- `.dockerignore` keeps tests, docs, `.git`, caches and `.superpowers/` out of the image.
- First start without admin variables must fail with the existing clear message.
- Verification (done by the builder, on this PC with Docker Desktop, and by the Governor on the
  Debian machine with the install guide): the image builds; `/api/v1/health` is healthy; the full
  invitation flow works against the container; data survives `docker compose down && up`; no
  network is used at run time (run once with the container network disabled); runs as non-root;
  the offline model path really is the one used.

## Left to the builder

File layout inside the image, exact base-image tag pinning, layer ordering for build cache, the
wording of the install guide, and how the model-folder completeness check is written.

## Risks

- Image size and build time with the CUDA PyTorch wheel on a 2-core machine (about 3 GB of
  downloads). Mitigation: build-arg for a CPU index; document the expected size and time.
- A 91 MB binary in git history is permanent; acceptable because the model will not change.
- `SMOS_PUBLIC_URL` unset makes invitation links use the request's Host header, which may not be
  the address users type; the install guide tells the admin to set it.
