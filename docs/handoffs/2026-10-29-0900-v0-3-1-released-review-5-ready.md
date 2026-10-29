# Handoff — v0.3.1 released, rebuild defect fixed, review package 5 ready — 2026-10-29 0900

## State

The "every release needs a rebuild" defect from the previous handoff is fixed and released as **`v0.3.1`** (annotated tag,
public GitHub Release with operator notes, `main` pushed). The version now lives only in `smos/__init__.py`; `pyproject.toml`
reads it (`dynamic`), and `compose.yaml` uses `image: smos:${SMOS_IMAGE_TAG:-latest}`. Two cheap technical-debt items were done
(per-hit SELECT batching with `VectorStore.get_many`; graph tables moved under store migration 3). 750 tests pass.
**Debian still runs 0.3.0** and has not been touched. No server, container or watcher of the assistant's is running.

## Completed this session

- Fix + regression tests (`5ed9e7a`): `tests/test_versioning.py` (no version literal in rebuild files; two releases differing
  only in `__init__.py` need no rebuild), `docs/RELEASING.md` step 1 rewritten.
- `8b619f1`, `4b0532e`: `_query_internal` one SELECT per chunk; `get_many` used by `build_context`, exact retrieval,
  reconstruct, `requirement_context`. `tests/test_query_batching.py` counts SELECTs.
- `da56880`: `GraphStore` no longer owns DDL; migration 3 `_graph_tables` (legacy CHECK rebuilds moved in); `tests/helpers.py`.
- Release `v0.3.1` (tag, GitHub Release, notes say "needs a rebuilt image" from 0.3.0).
- Dev-PC housekeeping: `claude mcp remove smos --scope user` done; `pip uninstall smos-mcp` done (0.1.4 removed).
- `docs/hnsw-assessment.md` (recommendation: not yet; flat search is 13 ms at 300k segments here; HNSW has no `remove_ids`);
  `docs/runbooks/update-drill.md` (good release v0.3.2, deliberately failing v0.3.3, throwaway repository);
  `docs/superpowers/review/2026-10-29-5-review-package.md` (range `c042d4c..HEAD`).

## Next steps (in order)

1. **Governor, Debian:** rebuild to 0.3.1 with the recipe in `docs/runbooks/debian-deploy.md` (stop, backup, `git pull`,
   `docker compose build`, compare `/opt/smos/build-info.json`, `up -d`; image is now `smos:latest`). Check the dashboard:
   *Check now* should say "up to date". Housekeeping there: delete `smos-backup.tgz`, `docker rmi smos:0.2.0 smos:0.3.0`.
2. **Governor, Debian:** run the update drill (`docs/runbooks/update-drill.md`): good release, then the failing one to see the
   rollback in the real container; also `docker stop` timing and `docker restart` during the health wait.
3. **Governor:** give Codex `docs/superpowers/review/2026-10-29-5-review-package.md`; apply its findings (new review file
   `2026-10-29-5-review.md`, then a resolution table in the verification doc as before).
4. Project 5: documentation (user guide, parser-author guide, agent-oriented API guide).
5. Governor unpublishes the old PyPI project. Optional: third Codex look at the rollback marker in `smos/launcher.py`.

## Open items / blockers

- The in-place update, rollback and SIGTERM/PID-1 path are still unproven in the production image (the drill covers them).
- Deliberately not done: `embed()` outside `_lock`, incremental BM25, tier-driven eviction (recorded in `HANDOFF.md` debt list and
  `docs/hnsw-assessment.md`). `do_ingest_document_schema` stays non-atomic by design (checkpointed and resumable); the
  debt line was reworded accordingly.
- Governor-side checks pending on Debian: `docker compose down && up` persistence; a read-only token refused on upload.
- `ARCHITECTURE.md` section 4 latency figures look pessimistic against the new measurement; re-measure on Debian before use.

## Key decisions & context

- The release was created at the Governor's explicit request in this session (push, tag, GitHub Release). Nothing else was
  published. The drill must use a throwaway repository so no broken release appears on the real releases page.
- Test command: `rtk proxy python -m pytest tests -q` (bare `pytest` reports "No tests collected" here).
- The Write tool is safer than long Bash heredocs on this machine (one heredoc with a table failed to parse).
- Execution style unchanged: inline, no reviewer subagents; Codex review by package.

## Files that matter

`docs/HANDOFF.md`, `docs/runbooks/debian-deploy.md`, `docs/runbooks/update-drill.md`, `docs/RELEASING.md`,
`docs/hnsw-assessment.md`, `docs/superpowers/review/2026-10-29-5-review-package.md`, `smos/storage/migrations.py`,
`smos/memory/vector_store.py` (`get_many`, `_rows_by_row_id`), `tests/test_versioning.py`.
