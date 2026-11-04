"""Real-server check of the 0.5.0 upgrade path (docs/scoped-retrieval-verification.md).

1. Extract the previous release's `smos/` from git:   git archive <tag> smos | tar -x -C $SP/old
2. Seed a store with that code:   OLD=$SP/old DATA=$SP/data python docs/runbooks/scripts/e2e_scoped_retrieval_seed.py
3. Run this script:               SP=$SP python docs/runbooks/scripts/e2e_scoped_retrieval.py
It copies the store, migrates the copy, starts `python -m smos.api.main` on the seeded store with two tokens, runs the HTTP checks
and always stops the server. `SP` is a scratch folder outside the repo. Uses the bundled embedding model, no network.
"""
import json, os, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
SP = Path(os.environ["SP"])
DATA = SP / "data"
PORT = 18765
BASE = f"http://127.0.0.1:{PORT}/api/v1"
os.environ.update(SMOS_EMBEDDING_MODEL_DIR=str(REPO / "models" / "all-MiniLM-L6-v2"),
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
checks = []


def check(name, ok, detail=""):
    checks.append((name, bool(ok), str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail != "" else ""), flush=True)


# 1. migration time on a copy of the v3 store
copy = SP / "data-copy"
shutil.rmtree(copy, ignore_errors=True)
shutil.copytree(DATA, copy)
from smos.memory.vector_store import VectorStore

t0 = time.perf_counter()
s = VectorStore(persist_path=copy)
dt = time.perf_counter() - t0
ver = s._db.execute("SELECT version FROM schema_version").fetchone()[0]
s.close()
check("migration of the 0.4.0 store opens as schema 5", ver == 5, f"version={ver}, open+migrate {dt:.2f}s for 27 rows")
check("backup copy written before migrating", (copy / "metadata.db.bak-v3").exists())

# 2. admin + tokens (new code)
from smos.api.app import create_app
from smos.admin import tokens

app = create_app(DATA, admin_user="admin", admin_password="correct-horse-1", start_sampler=False)
_, rw = tokens.create_token(app.state.admin_db, "rw-token", scope="rw")
_, ro = tokens.create_token(app.state.admin_db, "ro-token", scope="read")
app.state.close()
H = {"rw": {"Authorization": f"Bearer {rw}"}, "ro": {"Authorization": f"Bearer {ro}"}}


def call(method, path, body=None, who="rw", params=None):
    url = BASE + path + (("?" + urllib.parse.urlencode(params, doseq=True)) if params else "")
    req = urllib.request.Request(url, method=method, headers={**H[who], "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


env = {**os.environ, "SMOS_DATA_DIR": str(DATA), "SMOS_ADMIN_USER": "admin", "SMOS_ADMIN_PASSWORD": "correct-horse-1",
       "SMOS_HOST": "127.0.0.1", "SMOS_PORT": str(PORT), "PYTHONPATH": str(REPO)}
log = open(SP / "server.log", "w")
srv = subprocess.Popen([sys.executable, "-m", "smos.api.main"], env=env, cwd=str(REPO), stdout=log, stderr=log)
try:
    health = None
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as r:
                health = json.loads(r.read())
                break
        except Exception:
            time.sleep(1)
    if health is None:
        raise SystemExit("server did not start")
    check("server starts on the migrated store, version 0.5.0", health.get("version") == "0.5.0", health)

    Q = "Prüfung der Bremsanlage Äußerst sorgfältig prüfen und dokumentieren"
    c, a = call("POST", "/query", {"query": Q, "k": 50, "tags": ["norm:A"]})
    tags_ok = all("norm:A" in x["metadata"]["tags"].split(",") for x in a["results"])
    check("scoped query returns only norm:A on the migrated store", c == 200 and a["results"] and tags_ok,
          f"{len(a['results'])} hits, total={a['total']}")
    check("total = items carrying norm:A (8 segments + 3 notes)", a["total"] == 11, a["total"])
    c, a2 = call("POST", "/query", {"query": Q, "k": 50, "tags": ["norm:A"], "tags_none": ["topic:brakes"]})
    check("tags_none excludes the brake documents (only the 3 notes remain)",
          len(a2["results"]) == 3 and a2["total"] == 3, a2["total"])
    c, a3 = call("POST", "/query", {"query": Q, "k": 5, "tags": ["norm:TYPO"]})
    check("unknown tag: empty result, no fallback, unknown_tags set",
          a3["results"] == [] and a3["unknown_tags"] == ["norm:TYPO"] and a3["total"] == 0)
    c, a4 = call("POST", "/query", {"query": Q, "k": 5, "tags_all": ["norm:A", "norm:B"]})
    check("tags_all with disjoint norms is empty", a4["results"] == [] and a4["total"] == 0)

    c, whole = call("POST", "/query", {"query": Q, "k": 12, "tags": ["topic:brakes"]})
    pages = []
    for off in (0, 4, 8):
        c, p = call("POST", "/query", {"query": Q, "k": 4, "offset": off, "tags": ["topic:brakes"]})
        pages += [x["id"] for x in p["results"]]
    check("pages of 4 equal one page of 12, no repeats",
          pages == [x["id"] for x in whole["results"]] and len(set(pages)) == 12, f"{len(pages)} ids")
    c, bad = call("POST", "/query", {"query": Q, "k": 20, "offset": 990})
    check("offset + k > 1000 is HTTP 400", c == 400, bad.get("detail"))
    c, big = call("POST", "/query", {"query": Q, "k": 100})
    check("k=100 returns numeric scores for every hit",
          c == 200 and all(isinstance(x["score"], float) for x in big["results"]), f"{len(big['results'])} hits")

    c, d = call("GET", "/documents", params={"limit": 500})
    check("GET /documents lists 12 documents", d["total"] == 12 and len(d["documents"]) == 12)
    c, d2 = call("GET", "/documents", params=[("system", "norms"), ("tag", "norm:B"), ("tag", "topic:brakes")])
    check("GET /documents filters by system and two tags", d2["total"] == 4, d2["total"])
    c, t = call("GET", "/tags", params={"prefix": "norm:"})
    check("GET /tags counts per norm",
          {x["tag"]: x["count"] for x in t["tags"]} == {"norm:A": 11, "norm:B": 8, "norm:C": 8}, t["tags"])

    doc = next(x for x in d["documents"] if x["external_handle"]["native_id"] == "A-1")
    QA = "Weitere Anforderungen A1 Ölverlust Verschleißgrenzen Beläge"
    c, hit = call("POST", "/query", {"query": QA, "k": 1, "tags": ["norm:A"]})
    m = hit["results"][0]["metadata"]
    check("old document hit: block fields null, document_hash present",
          m["block_ordinal"] is None and m["document_hash"] == doc["document_hash"] and bool(doc["document_hash"]))
    c, txt0 = call("GET", f"/nodes/{doc['id']}/text", params={"include": "blocks"})
    check("old document text: complete, length ok, no stored blocks",
          txt0["complete"] and txt0["blocks"] == [] and txt0["char_length"] == len(txt0["text"]))

    for path in ("/documents", "/tags"):
        c, _ = call("GET", path, who="ro")
        check(f"read-only token can GET {path}", c == 200)
    c, r = call("POST", "/query", {"query": Q, "k": 3, "tags": ["norm:C"]}, who="ro")
    check("read-only token can run a scoped query", c == 200 and r["total"] == 8)
    c, _ = call("POST", "/documents", {"external_handle": {"system": "x", "native_id": "y"},
                                       "blocks": [{"block_type": "paragraph", "text": "forbidden write attempt text\n"}]}, who="ro")
    check("read-only token still cannot write", c == 403)

    seed = json.loads((DATA / "seed-ids.json").read_text())

    def payload(norm, n, summary=True):
        blocks = [{"block_type": "heading", "text": f"{n}.1\n", "heading_level": 2},
                  {"block_type": "paragraph", "text": f"Die Prüfung der Bremsanlage nach Norm {norm}, Abschnitt {n}: Äußerst sorgfältig prüfen und dokumentieren.\n"},
                  {"block_type": "paragraph", "text": f"Weitere Anforderungen {norm}{n}: Ölverlust, Verschleißgrenzen und Größe der Beläge sind zu messen.\n"}]
        body = {"external_handle": {"system": "norms", "native_id": f"{norm}-{n}"}, "blocks": blocks,
                "domain_tags": [f"norm:{norm}", "topic:brakes"], "doc_type": "standard"}
        if summary:
            body.update(summary=f"Summary of {norm}-{n}", summary_source="seed")
        return body

    c, r = call("POST", "/documents", payload("A", 1))
    check("identical re-send keeps the segments (same ids)",
          r["segments_kept"] is True and r["segment_ids"] == seed["A-1"], r["segment_ids"][:1])
    c, txt = call("GET", f"/nodes/{doc['id']}/text", params={"include": "blocks"})
    blocks, text = txt["blocks"], txt["text"]
    check("blocks now stored and tile the text exactly",
          [b["ordinal"] for b in blocks] == [0, 1, 2] and blocks[0]["char_start"] == 0
          and blocks[-1]["char_end"] == len(text)
          and all(q["char_start"] == p["char_end"] for p, q in zip(blocks, blocks[1:]))
          and "".join(text[b["char_start"]:b["char_end"]] for b in blocks) == text)
    check("document hash unchanged by the re-send", txt["document_hash"] == doc["document_hash"])
    c, hit = call("POST", "/query", {"query": QA, "k": 1, "tags": ["norm:A"]})
    h = hit["results"][0]
    m = h["metadata"]
    blk = blocks[m["block_ordinal"]]
    check("hit maps into the text and onto its block (umlauts, code points)",
          text[m["char_start"]:m["char_end"]] == h["content"] and blk["char_start"] <= m["char_start"]
          and m["char_end"] <= blocks[m["block_ordinal_end"]]["char_end"] and m["block_char_start"] == blk["char_start"],
          f"block {m['block_ordinal']}..{m['block_ordinal_end']}")
    check("document summary still there after the re-send", h["document_summary"] == "Summary of A-1")
    c, r = call("POST", "/documents", payload("B", 2, summary=False))
    c, hit = call("POST", "/query", {"query": "Weitere Anforderungen B2 Ölverlust Verschleißgrenzen Beläge", "k": 1, "tags": ["norm:B"]})
    check("re-send without summary deletes the document summary (documented)", hit["results"][0]["document_summary"] is None)
    c, r = call("POST", "/documents", {**payload("C", 3), "domain_tags": ["norm:C2", "topic:brakes"]})
    check("changed tags replace the segments", r["segments_kept"] is False and set(r["segment_ids"]).isdisjoint(seed["C-3"]))
    c, t = call("GET", "/tags", params={"prefix": "norm:C"})
    check("tag index follows the change (norm:C 6 items, norm:C2 2)",
          {x["tag"]: x["count"] for x in t["tags"]} == {"norm:C": 6, "norm:C2": 2}, t["tags"])
finally:
    srv.terminate()
    try:
        srv.wait(timeout=20)
    except Exception:
        srv.kill()
    log.close()
fails = [c for c in checks if not c[1]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} checks passed")
sys.exit(1 if fails else 0)
