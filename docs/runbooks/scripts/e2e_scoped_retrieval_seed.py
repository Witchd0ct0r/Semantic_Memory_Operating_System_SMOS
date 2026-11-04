"""Seed a store with the 0.4.0 code: 3 norms x 4 documents, tagged memories. Run with PYTHONPATH=<old>."""
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["OLD"])
import smos
assert smos.__version__ == "0.4.0", smos.__version__
from smos.ingest.contract import ContentBlock, ExternalHandle, IngestionPayload
from smos.memory.schemas import MemoryObject
from smos.memory.vector_store import VectorStore
from smos.tools.ingest_tools import do_ingest_document_schema

store = VectorStore(persist_path=Path(os.environ["DATA"]))
ids = {}
for norm in ("A", "B", "C"):
    for n in range(4):
        blocks = [
            ContentBlock(block_type="heading", text=f"{n}.1\n", heading_level=2),
            ContentBlock(block_type="paragraph", text=f"Die Prüfung der Bremsanlage nach Norm {norm}, Abschnitt {n}: Äußerst sorgfältig prüfen und dokumentieren.\n"),
            ContentBlock(block_type="paragraph", text=f"Weitere Anforderungen {norm}{n}: Ölverlust, Verschleißgrenzen und Größe der Beläge sind zu messen.\n"),
        ]
        r = do_ingest_document_schema(IngestionPayload(
            external_handle=ExternalHandle(system="norms", native_id=f"{norm}-{n}"),
            blocks=blocks, domain_tags=[f"norm:{norm}", "topic:brakes"], doc_type="standard",
            summary=f"Summary of {norm}-{n}", summary_source="seed"), store)
        ids[f"{norm}-{n}"] = r["segment_ids"]
for i in range(3):
    store.store(MemoryObject(type="doc", content=f"Plain note {i} about brake pad inspection intervals", tags=["norm:A"]))
store._save_index()
import json
Path(os.environ["DATA"], "seed-ids.json").write_text(json.dumps(ids))
print("seeded", store.stats())
store.close()
