from smos.ingest.adapters.markdown import MarkdownAdapter
from smos.ingest.adapters.registry import register_adapter
from smos.ingest.adapters.requirements import RequirementsAdapter

register_adapter(MarkdownAdapter())
register_adapter(RequirementsAdapter())
