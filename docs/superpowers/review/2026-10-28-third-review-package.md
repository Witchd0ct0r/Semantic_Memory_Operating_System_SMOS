# Review package 3: dashboard, Docker packaging, and the fixes for your second review

This is the third review of the same project. Your first review covered `e903ff7..82c1a5c`, your second
`82c1a5c..77165cc` (`docs/superpowers/review/*-review.md`). This one covers everything after:
`git diff 77165cc..75099fe`. Find real defects (security, data loss, wrong results, gaps against the design).
Style nits are not wanted. Report only; do not edit code.

## 1. Scope

- Repo: this repository, branch `main` (local; the last push predates the Docker work).
- Range: `77165cc..75099fe`, 21 commits. Ignore the bundled model files under `models/` (a vendored binary,
  86 MB, unmodified copy of a public model) and the `docs/` records except where named below.
- Reviewable code: 18 files, about +1,035 / -21 outside tests. Tests are in `tests/`.
- Commits by part:
  - **Part A** `8e66924` fixes for your second review (three Important findings).
  - **Part B** `a2bb7eb`, `44d063d`, `68bc690`, `ca28f03`, `7954211`, `b776646`: Docker packaging.
  - **Part C** `e0af3ee`, `cc5a477`, `2903a47`, `3e2436b`, `7832b2c`: the admin dashboard (the main subject).
- Read first: `docs/superpowers/specs/2026-10-28-dashboard-design.md` (binding for Part C),
  `docs/superpowers/specs/2026-10-28-docker-packaging-design.md` (binding for Part B),
  `docs/dashboard-verification.md`, `docs/docker-verification.md`.

## 2. Run it

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q        # expect 511 passed, about 55 s
# if a bare `pytest` says "No tests collected" (local hook quirk): rtk proxy pytest tests/ -q
SMOS_DATA_DIR=./tmp SMOS_ADMIN_USER=admin SMOS_ADMIN_PASSWORD=choose-one SMOS_PORT=18765 \
  SMOS_SAMPLE_INTERVAL_S=5 smos-server          # then log in at /admin/login; it lands on /admin/dashboard
```

Use `python`, not `python3`. Docker (optional): `cp .env.example .env`, edit it, `docker compose build`
(about 8 min, 3 GB download), `docker compose up -d`. The first run downloads nothing at run time.

## 3. Part C, the dashboard: what to attack, hardest first

Design in one paragraph: a background `Sampler` (`smos/admin/metrics.py`) writes one row a minute to a new
`samples` table in `admin.db` (migration 3). `smos/admin/dashboard_data.py` aggregates `usage_log` and
`samples` with single SQL statements, each under the shared `app.state.admin_lock`. `smos/admin/charts.py`
draws inline SVG. `smos/admin/dashboard_view.py` builds the page data; `GET /admin/dashboard` (admin session
only) renders `templates/dashboard.html`. The usage middleware (`smos/api/app.py`) now stores the matched
route template instead of the concrete path.

1. **Injection and escaping.** The SQL uses bound parameters; the only interpolated values are integer
   bucket sizes (`dashboard_data.py`, `latency_series`). Can `?range=` or anything else reach SQL or the
   template unescaped? Charts are inserted with `|safe`: is every string that enters `charts.py` escaped
   (series names, titles, CSS classes, reference labels, axis text)? Table cells (paths, token names, event
   kinds) rely on Jinja autoescape; check each.
2. **Lock discipline and blocking.** The admin lock is shared with token authentication on every API call.
   Is it ever held across more than one statement, across slow work, or re-entered so that it can deadlock?
   `Sampler.sample_once` also calls `VectorStore.stats()` and `file_sizes()`, which take `VectorStore._lock`
   (held during embedding). Can a sample or a dashboard page view stall API requests, or the reverse?
3. **Correctness of the numbers.** Bucketing (`CAST(strftime('%s', ts) AS INTEGER) / ? * ?` on ISO strings
   with microseconds and `+00:00`), gap filling, the p95 window-function query (`rn >= 0.95 * cnt`), error
   classification (`status >= 400`), the events query (errors plus writes by `routes.write.routes` paths),
   token activity joins, range boundaries (`ts >= since` as a string comparison). Look for off-by-one, wrong
   timezone handling, rows counted twice or missed, and results that disagree with `usage_log`.
4. **Unbounded growth and abuse.** The middleware logs every `/api/v1` request including unauthenticated
   and unmatched ones; unmatched paths are stored raw (no route template). Can an unauthenticated caller
   bloat `usage_log` or pollute "most-used calls" with many distinct paths? Pruning is batched and
   background (`_prune_loop` in `app.py`); does it keep up, and does `last_prune` logic misbehave?
5. **Hardware reader** (`metrics.read_hardware`). cgroup v2 then v1 then `psutil`. Malformed or unexpected
   file contents (`cpu.max` formats, `memory.max` = `max`, negative or huge values, v1 limits near 2^63),
   a CPU counter that goes backwards, a zero or negative elapsed time. An exception in a sample is caught
   and logged; check that a bad value cannot poison later samples or the page.
6. **Chart math** (`charts.py`): empty, single-point, flat, all-zero, negative, enormous or non-finite
   values; downsampling; label positions; anything that can produce `nan`/`inf` in the SVG or an
   exception that turns the dashboard into a 500.
7. **Access control.** The dashboard needs the admin session (`admin_required`). Confirm no data is served
   to a bearer-token holder or an anonymous user, that nothing sensitive (secrets, token hashes) appears,
   and that CSRF/session behaviour of the other pages is unchanged.
8. **Migration 3** (`smos/admin/db.py::_add_samples`) and its interaction with the existing migrations
   and their tests.

## 4. Part B, Docker packaging: what to attack

Files: `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`, `.gitignore`, `certs/README.md`,
`smos/memory/embeddings.py`, `smos/api/main.py`, `smos/api/app.py` (model check), `docs/INSTALL.md`.

1. **Secrets and the image.** `.env` is git-ignored and excluded from the build context; the admin password
   comes from `env_file` at run time. Is anything secret baked into a layer or visible through
   `docker inspect`/logs? `certs/ca-bundle.crt` (optional, git-ignored) is copied into the image
   (`COPY certs`): it is the host's CA list including the network's inspection CA. Public data, but is
   baking it into the image acceptable, and is it excluded from git as intended (`.gitignore`
   `certs/*.crt`)?
2. **Privileges and runtime.** Non-root user (uid 10001), writable `/data` only, no privileged mode or
   Docker socket. Check file ownership on a fresh and on an existing named volume, the `HEALTHCHECK`, and
   what happens if the data volume is owned by another uid.
3. **Offline model.** `SMOS_EMBEDDING_MODEL_DIR` makes SMOS load the bundled model by path with
   `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`. `model_source()` fails fast on an incomplete folder, and
   `create_app` checks it before touching the data directory. Can anything still reach the network?
4. **Supply chain and reproducibility.** Dependencies are unpinned ranges from PyPI; the base image tag
   is floating (`python:3.12-slim`). The build can take PyTorch from an optional index (`TORCH_INDEX_URL`).
   Flag anything that makes builds unsafe or non-repeatable that you judge important.
5. **Compose and ports.** `SMOS_HOST_PORT`, plain HTTP, `SMOS_PUBLIC_URL` used to build invitation links
   (taken from the environment, else the request Host header): any injection or open-redirect-style risk
   from the Host header?

## 5. Part A: verify the fixes for your second review

| Your finding | Fix | Tests |
|---|---|---|
| per-document locks accumulate | `VectorStore.document_lock` is now a reference-counted context manager (`vector_store.py`) | `tests/test_document_lock.py` |
| usage pruning could stall authentication | batched deletes, background thread (`usage.py`, `app.py`) | `tests/test_usage_retention.py` |
| route-classification pin could miss hidden routes | router-level guard in `tests/test_api_scopes.py` | same file |

Are they sound, and did they open new gaps (reference counting races, a leaked or double-released lock, the
background prune thread and the new sampler thread sharing the admin connection)?

## 6. Decisions already made (flag only if harmful)

- Plain HTTP on the internal network; no TLS inside SMOS.
- The PyTorch GPU wheel from PyPI is used (CPU-only sources are unreachable on the target network);
  the image is large.
- One shared SQLite connection per database, serialized by `admin_lock` and `VectorStore._lock`.
- Dashboard hardware figures are the container's cgroup view; disk is the data volume.
- Charts are drawn server-side as inline SVG (no JavaScript, works offline); the page auto-refreshes with
  `<meta http-equiv="refresh">`.
- Reverse-proxy logs of `/claim/<secret>` are outside SMOS and documented, not solved.
- Everything listed as a decision in the first two packages still stands.

## 7. Evidence already collected

- 511 tests pass, repeatedly. Each new feature test was seen failing first; one race (sampler thread vs. test
  code using the shared connection without the lock) was found by the suite and fixed in the test fixtures.
- Real runs: local service with curl traffic (3 ingests, 4 queries, 1 call without a token) gave exactly
  3 documents, 8 calls, 12.5% error rate on the dashboard; in a container started with `--memory 1536m
  --cpus 1.5` the hardware panel showed 1.5 cores and 1.5 GB, equal to the container's cgroup files;
  the page contains 0 external URLs and no `<script>`; the page was inspected in a browser (three polish
  fixes). Docker: non-root, healthy in 12 s, data survives `down`/`up`, no-network run embeds and queries,
  install guide commands (backup, restore, forgot password) verified. Installed and used from a client PC on
  the Governor's Debian server.

## 8. How to report

For each finding: file and line, one-sentence defect, a concrete failing request, input or sequence, and
severity (Critical = auth bypass, secret exposure, data loss; Important = wrong numbers or behaviour a user
will hit, a stall or unbounded growth; Minor = polish). State which items in sections 3, 4 and 5 you checked
and found sound.
