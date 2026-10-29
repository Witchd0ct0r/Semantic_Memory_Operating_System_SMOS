# Runbook: the Benchmark page

## Run it on an installed device (Debian)

1. Be on a release that contains the benchmark (0.3.3 or higher; update from the dashboard, no rebuilt image needed).
2. Log in to the admin pages, open **Benchmark**. Pick **Quick** (a few minutes), keep the 200 ms budget, press *Start*. The API
   answers 503 until it ends (health stays 200). *Cancel* ends it within seconds.
3. Open the report. Check: verdict box, 11 charts, "Cleanup: verified" under Run details. *Download JSON* keeps the raw numbers.
4. Afterwards confirm nothing is left: `ls <data volume>/bench-tmp` is empty and `docker top smos` shows no `smos.bench` process.
5. Run **Standard** twice (about 20 minutes each) and keep both JSON files; the second report shows the comparison table.

If a run fails, the report says why (`error`). If SMOS was restarted during a run the row is `interrupted` and the leftovers are removed
at the next start.

## Drive it for real on the dev PC (throwaway server)

The assistant's recipe (scripts lived in a scratch folder, not in the repository):

1. Make a temp data directory: create the admin DB with `smos.admin.db.open_admin_db`, `accounts.bootstrap_admin`, and an rw token
   with `tokens.create_token`; ingest a few documents with `smos.bench.phases.open_store` + `ingest_to` (the "real" data to protect).
2. Start `python -m smos.api.main` with `SMOS_DATA_DIR`, `SMOS_HOST=127.0.0.1`, `SMOS_PORT`, `SMOS_EMBEDDING_MODEL_DIR=models/all-MiniLM-L6-v2`,
   `HF_HUB_OFFLINE=1` in the background.
3. Log in over HTTP (`POST /admin/login`), read the CSRF value from `GET /admin/benchmark`, `POST /admin/benchmark/start` with
   `preset` and `budget_ms`, poll `GET /admin/benchmark/status`. During the run `POST /api/v1/query` must give 503 and
   `/api/v1/health` 200. Compare the SHA-1 of `faiss.index` and the `memories` count before and after.
4. Stop the server afterwards.

## Gotchas

- `python -m smos.bench` refuses to run by hand (exit 2): by design, runs start only from the admin page.
- Never kill or match processes by "a command line that mentions bench-tmp": a shell or editor does too.
- The browser tool cannot open `file://` pages; log in to the running app instead. PDF check: `chrome --headless=new
  --print-to-pdf=out.pdf <url or file>` then read it with `pypdf` (no `pdftoppm` here).
- Windows needs no special flags; on Windows the runner and server are separate process groups and are killed through `psutil`.
