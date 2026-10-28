from __future__ import annotations

import threading


def test_concurrent_writes_and_queries_all_succeed(api) -> None:
    client, headers, app = api
    errors: list[str] = []
    ids: list[str] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            for i in range(6):
                r = client.post(
                    "/api/v1/memories",
                    json={"type": "doc", "content": f"Concurrent memory {n}-{i} about radiated emissions."},
                    headers=headers,
                )
                if r.status_code != 200:
                    errors.append(f"post {n}-{i}: {r.status_code} {r.text[:100]}")
                    continue
                with lock:
                    ids.append(r.json()["id"])
                q = client.post("/api/v1/query", json={"query": "radiated emissions memory", "k": 3}, headers=headers)
                if q.status_code != 200:
                    errors.append(f"query {n}-{i}: {q.status_code} {q.text[:100]}")
        except Exception as exc:  # noqa: BLE001 - surface any thread failure
            errors.append(f"thread {n}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(ids) == 48 and len(set(ids)) == 48
    assert app.state.store.count() == 48
