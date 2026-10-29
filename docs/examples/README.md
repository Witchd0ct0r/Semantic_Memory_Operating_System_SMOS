# Examples

`standards_pdf_parser.py` turns a text PDF of a standard into the SMOS ingestion contract and posts it.
It is a teaching example for parser authors (see `docs/guide/parser-author-guide.md`), not part of SMOS.

```bash
pip install -r docs/examples/requirements.txt
export SMOS_TOKEN=...   # a read/write token
python docs/examples/standards_pdf_parser.py --pdf std.pdf --system std-pdf --designation "ACME 1000" --name "ACME 1000 Widgets" --url http://localhost:8765
```

Add `--dry-run` to see the block and link counts without posting.
