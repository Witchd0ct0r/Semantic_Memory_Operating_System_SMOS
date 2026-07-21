from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.adapters.registry import register_adapter

register_adapter(MarkdownAdapter())
