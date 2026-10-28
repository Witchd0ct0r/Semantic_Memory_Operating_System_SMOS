# SMOS Docker Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (the Governor chose inline execution; no subagents).

**Goal:** A container image and compose file that run SMOS on a Debian host with the embedding model bundled, persistent data, and no internet at run time.

**Architecture:** `smos/memory/embeddings.py` loads the model from `SMOS_EMBEDDING_MODEL_DIR` when set (fail-fast on an incomplete folder); the model files live in the repo under `models/all-MiniLM-L6-v2/`; a single-stage Dockerfile installs the package from PyPI and copies the model; compose adds a named volume, a `.env` file and a healthcheck.

**Tech Stack:** Docker 29 / Compose 5, `python:3.12-slim`, existing Python package.

**Spec:** `docs/superpowers/specs/2026-10-28-docker-packaging-design.md`

## Global Constraints

- Hugging Face, `download.pytorch.org` and the Aliyun mirror are unreachable from the target: nothing may download a model at build or run time. PyPI and Docker Hub are reachable.
- Plain HTTP on port 8765; run as a non-root user (uid 10001); all state under `/data` (named volume `smos-data`).
- No secrets in the image or in git (`.env` ignored, `.env.example` committed).
- Use `python`/`pytest`, never `python3`; if bare `pytest` reports "No tests collected" use `rtk proxy pytest`.
- Baseline before Task 1: 430 passing tests.

## Review Focus

- Model folder missing, empty or half-copied: startup fails with a message naming `SMOS_EMBEDDING_MODEL_DIR` and the missing files (no network attempt, no silent fallback).
- Container started without `SMOS_ADMIN_USER` / `SMOS_ADMIN_PASSWORD` on a fresh volume: exits non-zero with the existing clear message; with an existing volume it starts without them.
- `docker compose down` (without `-v`) then `up`: tokens, documents and the admin account survive.
- Container with its network disabled: starts, answers a query, embeds a new document.
- A non-root user must be able to write `/data` on a fresh named volume and on an existing one.
- `SMOS_PUBLIC_URL` unset: invitation links still work (built from the request host).

---

### Task 1: Load the embedding model from a local folder

**Files:** Modify `smos/memory/embeddings.py`, `smos/api/app.py`, `smos/api/main.py`; Test `tests/test_embeddings_source.py`.

**Interfaces:** Produces `model_source() -> str` (the folder path when `SMOS_EMBEDDING_MODEL_DIR` is set and complete, else `"all-MiniLM-L6-v2"`; raises `RuntimeError` naming the variable and the missing files otherwise), `warm_up() -> None` (loads the model). `create_app` calls `model_source()` before building anything; `main.build_app` calls `warm_up()`.

- [ ] Write failing tests: default source is the model name; a complete fake folder is returned as a string path; an incomplete folder raises `RuntimeError` mentioning `SMOS_EMBEDDING_MODEL_DIR` and each missing file; a nonexistent folder raises; `create_app` with an incomplete folder raises before creating the data directory contents; with the bundled `models/all-MiniLM-L6-v2` folder (added in Task 2) an embedding has 384 dimensions and cosine similarity > 0.9999 with the by-name embedding.
- [ ] Run them; expect FAIL. Implement. Run; expect PASS (the bundled-folder test is skipped until Task 2 adds the files, then must pass).
- [ ] Commit `feat: load the embedding model from a local folder`.

### Task 2: Bundle the model files

**Files:** Create `models/all-MiniLM-L6-v2/` (copied from the local Hugging Face cache snapshot: `config.json`, `config_sentence_transformers.json`, `model.safetensors`, `modules.json`, `sentence_bert_config.json`, `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json`, `vocab.txt`, `1_Pooling/config.json`), plus `models/all-MiniLM-L6-v2/NOTICE.md` (source model id, Apache-2.0 licence note, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`).

- [ ] Copy the files, run the bundled-folder test from Task 1 (must now pass, not skip), run the full suite.
- [ ] Commit `feat: bundle the all-MiniLM-L6-v2 embedding model`.

### Task 3: Image, compose, environment template

**Files:** Create `Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`; Modify `.gitignore` (`.env`); Test `tests/test_docker_files.py` (static checks: files exist, no secrets, non-root user, healthcheck, volume, `.env` ignored, model folder copied).

- [ ] Write the static tests, watch them fail, write the files, watch them pass.
- [ ] Build: `docker compose build` (record time and image size).
- [ ] Run the checks from the spec: healthy status; invitation flow with curl against the container; data survives `docker compose down && up`; start without admin variables on a fresh volume fails clearly; run as non-root (`id`); a run with `--network none` answers a query and embeds a new document; the model really loads from the bundled folder (`SMOS_EMBEDDING_MODEL_DIR` set, offline flags set).
- [ ] Commit `feat: Dockerfile and compose for the Debian trial install`.

### Task 4: Install guide and records

**Files:** Create `docs/INSTALL.md`; Modify `README.md` (link), `docs/HANDOFF.md`, `docs/standalone-core-verification.md` → add a Docker section (or `docs/docker-verification.md`).

- [ ] Write the Debian install guide (short commands, one per step; backup/restore with a throwaway container; manual upgrade; troubleshooting: port in use, permission on volume, forgot password, model folder error, `SMOS_PUBLIC_URL`).
- [ ] Record the verification results and image size/build time; update the handoff.
- [ ] Commit `docs: install guide and Docker verification`.
