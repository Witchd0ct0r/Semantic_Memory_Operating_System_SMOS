# Guide verification record (2026-10-29)

The guide in `docs/guide/` quotes real responses. This file is where they come from, plus what was found while producing them.

## 1. How the run was set up

- SMOS 0.3.1 from the working tree, started with `python -m smos.api.main` on `127.0.0.1:8799`, an empty temp data directory,
  `SMOS_ADMIN_USER`/`SMOS_ADMIN_PASSWORD` set, the bundled embedding model, offline.
- Tokens were created the way the guide says: admin login, one invitation per scope on `/admin/invitations`, `POST /claim/<secret>`
  (tokens start with `smos_`, shown once). `RW` = read/write token, `READ` = read-only token.
- Documents came from `docs/examples/standards_pdf_parser.py` run on small made-up PDFs ("ACME 1000", "ISO 9001" examples).
- The server was stopped afterwards (port free). The pytest suite (`tests/test_example_standards_pdf.py`) pins the behaviors listed
  in section 3 against an in-process app.
- Long responses are trimmed by the probe (lists cut to 3 entries, strings to 160 characters); the repeated null summary fields,
  timestamps, tiers and empty `source_ids` lines are removed from the pasted blocks below. Nothing else was edited.

## 2. Real requests and responses

#### Health (no token)

`GET /api/v1/health`

Response **200**:
```json
{
  "status": "ok",
  "version": "0.3.1"
}
```

#### Token flow (real)

Admin login, an invitation per scope on `/admin/invitations`, and `POST /claim/<secret>` returned tokens starting with `smos_` (shown once). Used below as `RW` and `READ`.

#### No token

`POST /api/v1/query`

Request body:
```json
{
  "query": "radiated emissions limits",
  "k": 3
}
```

Response **401**:
```json
{
  "detail": "valid bearer token required"
}
```

#### Bad token

`POST /api/v1/query`

Request body:
```json
{
  "query": "radiated emissions limits",
  "k": 3
}
```

Response **401**:
```json
{
  "detail": "valid bearer token required"
}
```

#### Ingest ACME 1000 with the example parser

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "std-pdf",
    "native_id": "ACME 1000"
  },
  "doc_name": "ACME 1000 (example)",
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n"
    },
    {
      "block_type": "heading",
      "heading_level": 1,
      "text": "2 Limits\n"
    },
    {
      "block_type": "paragraph",
      "text": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n"
    },
    "..."
  ],
  "doc_type": "standard",
  "domain_tags": [
    "widgets"
  ],
  "source_links": [
    {
      "target_native_id": "ISO 9001",
      "edge_type": "references",
      "metadata": {
        "target_system": "std-pdf"
      }
    }
  ],
}
```

Response **200**:
```json
{
  "status": "success",
  "doc_id": "67e53410c17a5649",
  "segment_count": 3,
  "segment_ids": [
    "dcf8fb45-28db-4db4-bb74-b7ad218fb2dd",
    "8abeb1fa-2c1b-445e-9556-23b1e08ddee3",
    "f7353d80-f2ae-45d6-9f80-67884bc6108c"
  ],
  "section_count": 0,
  "reference_count": 0,
  "link_count": 1,
  "resumed": false
}
```

#### Ingest ISO 9001 with the example parser

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "std-pdf",
    "native_id": "ISO 9001"
  },
  "doc_name": "ISO 9001 (example)",
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "1 Quality\nAn organisation shall control documented information.\n"
    }
  ],
  "doc_type": "standard",
  "domain_tags": [
    "widgets"
  ],
  "source_links": [],
}
```

Response **200**:
```json
{
  "status": "success",
  "doc_id": "d0d490309ffeb4fc",
  "segment_count": 1,
  "segment_ids": [
    "eabf7fa8-47ea-4bbd-9652-6930a8d89661"
  ],
  "section_count": 0,
  "reference_count": 0,
  "link_count": 0,
  "resumed": false
}
```

#### Re-ingest unchanged (idempotent)

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "std-pdf",
    "native_id": "ACME 1000"
  },
  "doc_name": "ACME 1000 (example)",
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n"
    },
    {
      "block_type": "heading",
      "heading_level": 1,
      "text": "2 Limits\n"
    },
    {
      "block_type": "paragraph",
      "text": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n"
    },
    "..."
  ],
  "doc_type": "standard",
  "domain_tags": [
    "widgets"
  ],
  "source_links": [
    {
      "target_native_id": "ISO 9001",
      "edge_type": "references",
      "metadata": {
        "target_system": "std-pdf"
      }
    }
  ]
}
```

Response **200**:
```json
{
  "status": "success",
  "doc_id": "67e53410c17a5649",
  "segment_count": 3,
  "segment_ids": [
    "95c39aa2-391f-442c-a43c-390dcaf46f7e",
    "4ddeded0-763a-49dc-812e-42ab4be180c9",
    "96754d5b-db84-496a-b88f-4c415c58c585"
  ],
  "section_count": 0,
  "reference_count": 0,
  "link_count": 1,
  "resumed": false
}
```

#### Quarantine: empty block

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q1"
  },
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "  "
    }
  ]
}
```

Response **200**:
```json
{
  "status": "quarantined",
  "quarantine_key": "464a1af5-991b-4fb9-84c4-6d0b943158a3",
  "errors": [
    "blocks[0]: text must not be empty"
  ]
}
```

#### Quarantine: heading without level

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q2"
  },
  "blocks": [
    {
      "block_type": "heading",
      "text": "Title\n"
    }
  ]
}
```

Response **200**:
```json
{
  "status": "quarantined",
  "quarantine_key": "7bbacd86-634d-475f-ac5b-13c99553ae7c",
  "errors": [
    "blocks[0]: heading blocks require heading_level in 1..6"
  ]
}
```

#### Quarantine: unknown edge type

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q3"
  },
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "Hello there.\n"
    }
  ],
  "source_links": [
    {
      "target_native_id": "x",
      "edge_type": "bogus"
    }
  ]
}
```

Response **200**:
```json
{
  "status": "quarantined",
  "quarantine_key": "a277d471-5497-49b3-8a76-842d0ef6a7e5",
  "errors": [
    "source_links[0]: unknown edge_type 'bogus'"
  ]
}
```

#### Quarantine: link to itself

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q4"
  },
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "Hello there.\n"
    }
  ],
  "source_links": [
    {
      "target_native_id": "q4",
      "edge_type": "references"
    }
  ]
}
```

Response **200**:
```json
{
  "status": "quarantined",
  "quarantine_key": "1cec24e9-bd3c-4718-9c05-c2a1a6cecabd",
  "errors": [
    "source_links[0]: link targets its own item"
  ]
}
```

#### Quarantine: tag with a comma

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q5"
  },
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "Hello there.\n"
    }
  ],
  "domain_tags": [
    "a,b"
  ]
}
```

Response **200**:
```json
{
  "status": "quarantined",
  "quarantine_key": "41fa57d0-e03d-4172-8043-9bbadb713a1c",
  "errors": [
    "domain_tags[0]: must not contain ','"
  ]
}
```

#### Schema violation: no blocks field

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q6"
  }
}
```

Response **422**:
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": [
        "body",
        "blocks"
      ],
      "msg": "Field required",
      "input": {
        "external_handle": {
          "system": "t",
          "native_id": "q6"
        }
      }
    }
  ]
}
```

#### Read-only token cannot ingest

`POST /api/v1/documents`

Request body:
```json
{
  "external_handle": {
    "system": "t",
    "native_id": "q7"
  },
  "blocks": [
    {
      "block_type": "paragraph",
      "text": "Hello there.\n"
    }
  ]
}
```

Response **403**:
```json
{
  "detail": "this token is read-only"
}
```

#### Query

`POST /api/v1/query`

Request body:
```json
{
  "query": "maximum permitted widget deflection",
  "k": 3
}
```

Response **200**:
```json
{
  "results": [
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "content": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.7521909475326538,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 65,
        "char_end": 154
      }
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n2 Limits\n",
      "score": 0.6077289581298828,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 65
      }
    },
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.4906913638114929,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 2,
        "char_start": 154,
        "char_end": 218
      }
    }
  ],
  "sources": [
    "4ddeded0-763a-49dc-812e-42ab4be180c9",
    "95c39aa2-391f-442c-a43c-390dcaf46f7e",
    "96754d5b-db84-496a-b88f-4c415c58c585"
  ],
  "confidence": 0.8084352115790049,
  "mode": "extractive"
}
```

#### Query with a tag

`POST /api/v1/query`

Request body:
```json
{
  "query": "maximum permitted widget deflection",
  "k": 3,
  "tags": [
    "widgets"
  ]
}
```

Response **200**:
```json
{
  "results": [
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "content": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.7521909475326538,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 65,
        "char_end": 154
      }
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n2 Limits\n",
      "score": 0.6077289581298828,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 65
      }
    },
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.4906913638114929,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 2,
        "char_start": 154,
        "char_end": 218
      }
    }
  ],
  "sources": [
    "4ddeded0-763a-49dc-812e-42ab4be180c9",
    "95c39aa2-391f-442c-a43c-390dcaf46f7e",
    "96754d5b-db84-496a-b88f-4c415c58c585"
  ],
  "confidence": 0.8084352115790049,
  "mode": "extractive"
}
```

#### Query too short

`POST /api/v1/query`

Request body:
```json
{
  "query": "short",
  "k": 3
}
```

Response **400**:
```json
{
  "detail": "query must be at least 10 characters"
}
```

#### Query k out of range

`POST /api/v1/query`

Request body:
```json
{
  "query": "maximum permitted widget deflection",
  "k": 101
}
```

Response **422**:
```json
{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": [
        "body",
        "k"
      ],
      "msg": "Input should be less than or equal to 100",
      "input": 101,
      "ctx": {
        "le": 100
      }
    }
  ]
}
```

#### Nothing matches

`POST /api/v1/query`

Request body:
```json
{
  "query": "zzzz qqqq xxxx wwww vvvv",
  "k": 3,
  "tags": [
    "no-such-tag"
  ]
}
```

Response **200**:
```json
{
  "results": [
    {
      "id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "content": "1 Quality\nAn organisation shall control documented information.\n",
      "document_summary": "Example standard for the guide.",
      "score": 0.17755083739757538,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "d0d490309ffeb4fc",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 64
      }
    },
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.02785409428179264,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 2,
        "char_start": 154,
        "char_end": 218
      }
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n2 Limits\n",
      "score": 0.022969665005803108,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 65
      }
    }
  ],
  "sources": [
    "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
    "96754d5b-db84-496a-b88f-4c415c58c585",
    "95c39aa2-391f-442c-a43c-390dcaf46f7e"
  ],
  "confidence": 0.5380624327808619,
  "mode": "extractive"
}
```

#### Recall exact

`POST /api/v1/recall`

Request body:
```json
{
  "query": "serial marking",
  "strategy": "exact",
  "k": 3
}
```

Response **200**:
```json
{
  "segments": [
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 2,
        "char_start": 154,
        "char_end": 218
      },
      "distance": 0.41402769088745117,
      "score": 0.5859723091125488
    },
    {
      "id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "content": "1 Quality\nAn organisation shall control documented information.\n",
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "d0d490309ffeb4fc",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 64
      },
      "distance": 0.7928163409233093,
      "score": 0.20718365907669067
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n2 Limits\n",
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 65
      },
      "distance": 0.8600390553474426,
      "score": 0.13996094465255737
    }
  ],
  "context": [
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "content": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 65,
        "char_end": 154
      }
    }
  ],
  "sources": [
    "96754d5b-db84-496a-b88f-4c415c58c585",
    "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
    "95c39aa2-391f-442c-a43c-390dcaf46f7e"
  ],
  "mode": "extractive"
}
```

#### Recall hybrid

`POST /api/v1/recall`

Request body:
```json
{
  "query": "serial marking",
  "strategy": "hybrid",
  "k": 3
}
```

Response **200**:
```json
{
  "results": [
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.5859723091125488,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 2,
        "char_start": 154,
        "char_end": 218
      }
    },
    {
      "id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "content": "1 Quality\nAn organisation shall control documented information.\n",
      "document_summary": "Example standard for the guide.",
      "score": 0.20718365907669067,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "d0d490309ffeb4fc",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 64
      }
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n2 Limits\n",
      "score": 0.13996094465255737,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 65
      }
    }
  ],
  "sources": [
    "96754d5b-db84-496a-b88f-4c415c58c585",
    "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
    "95c39aa2-391f-442c-a43c-390dcaf46f7e"
  ],
  "confidence": 0.6555194854736328,
  "mode": "extractive"
}
```

#### Recall temporal

`POST /api/v1/recall`

Request body:
```json
{
  "strategy": "temporal",
  "since": "2020-01-01T00:00:00",
  "until": "2100-01-01T00:00:00"
}
```

Response **200**:
```json
{
  "results": [
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.05754334479570389,
      "metadata": {}
    },
    {
      "id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "content": "1 Quality\nAn organisation shall control documented information.\n",
      "score": 0.04188030958175659,
      "metadata": {}
    },
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "content": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.026286033913493156,
      "metadata": {}
    },
    "..."
  ],
  "sources": [
    "96754d5b-db84-496a-b88f-4c415c58c585",
    "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
    "4ddeded0-763a-49dc-812e-42ab4be180c9",
    "..."
  ],
  "confidence": 0.5144011974334717,
  "mode": "extractive"
}
```

#### Recall repo

`POST /api/v1/recall`

Request body:
```json
{
  "strategy": "repo",
  "component": "widgets"
}
```

Response **200**:
```json
{
  "results": [
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.05754334479570389,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 2,
        "char_start": 154,
        "char_end": 218
      }
    },
    {
      "id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "content": "1 Quality\nAn organisation shall control documented information.\n",
      "document_summary": "Example standard for the guide.",
      "score": 0.04188030958175659,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "d0d490309ffeb4fc",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 64
      }
    },
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "content": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.026286093518137932,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 65,
        "char_end": 154
      }
    },
    "..."
  ],
  "sources": [
    "96754d5b-db84-496a-b88f-4c415c58c585",
    "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
    "4ddeded0-763a-49dc-812e-42ab4be180c9",
    "..."
  ],
  "confidence": 0.5144012146629393,
  "mode": "extractive"
}
```

#### Recall unknown strategy

`POST /api/v1/recall`

Request body:
```json
{
  "query": "serial marking",
  "strategy": "magic"
}
```

Response **400**:
```json
{
  "detail": "unknown strategy: magic"
}
```

#### Whole document text

`GET /api/v1/nodes/67e53410c17a5649/text`

Response **200**:
```json
{
  "node_id": "67e53410c17a5649",
  "text": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n2.2...",
  "segment_count": 3,
  "complete": true,
  "error": null
}
```

#### Graph query from the document

`POST /api/v1/graph/query`

Request body:
```json
{
  "term": "67e53410c17a5649",
  "max_hops": 2
}
```

Response **200**:
```json
{
  "term": "67e53410c17a5649",
  "start_id": "67e53410c17a5649",
  "nodes": [
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "type": "memory",
      "name": "4ddeded0",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "type": "memory",
      "name": "95c39aa2",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "type": "memory",
      "name": "96754d5b",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "d0d490309ffeb4fc",
      "type": "document",
      "name": "ISO 9001 (example)",
      "metadata": {
        "domain_tags": [
          "widgets"
        ],
        "doc_type": "standard",
        "system": "std-pdf",
        "native_id": "ISO 9001",
        "metadata": {}
      },
      "hop": 1
    },
    {
      "id": "67e53410c17a5649",
      "type": "document",
      "name": "ACME 1000 (example)",
      "metadata": {
        "domain_tags": [
          "widgets"
        ],
        "doc_type": "standard",
        "system": "std-pdf",
        "native_id": "ACME 1000",
        "metadata": {}
      },
      "hop": 2
    },
    {
      "id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "type": "memory",
      "name": "eabf7fa8",
      "metadata": {},
      "hop": 2
    }
  ],
  "count": 6
}
```

#### Graph query, one hop

`POST /api/v1/graph/query`

Request body:
```json
{
  "term": "67e53410c17a5649",
  "max_hops": 1
}
```

Response **200**:
```json
{
  "term": "67e53410c17a5649",
  "start_id": "67e53410c17a5649",
  "nodes": [
    {
      "id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "type": "memory",
      "name": "4ddeded0",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "95c39aa2-391f-442c-a43c-390dcaf46f7e",
      "type": "memory",
      "name": "95c39aa2",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "type": "memory",
      "name": "96754d5b",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "d0d490309ffeb4fc",
      "type": "document",
      "name": "ISO 9001 (example)",
      "metadata": {
        "domain_tags": [
          "widgets"
        ],
        "doc_type": "standard",
        "system": "std-pdf",
        "native_id": "ISO 9001",
        "metadata": {}
      },
      "hop": 1
    }
  ],
  "count": 4
}
```

#### Graph query from an entity name

`POST /api/v1/graph/query`

Request body:
```json
{
  "term": "ISO 9001",
  "max_hops": 2
}
```

Response **200**:
```json
{
  "term": "ISO 9001",
  "start_id": "entity:ISO 9001",
  "nodes": [],
  "count": 0
}
```

#### Attach a summary to a document

`PUT /api/v1/nodes/67e53410c17a5649/summary`

Request body:
```json
{
  "source": "probe 1"
}
```

Response **200**:
```json
{
  "ok": true
}
```

#### Store a memory

`POST /api/v1/memories`

Request body:
```json
{
  "type": "doc",
  "content": "Decision: widget marking uses laser etching.",
  "tags": [
    "widgets"
  ]
}
```

Response **200**:
```json
{
  "id": "7ebe1081-278c-49f0-8436-b50345d3f547"
}
```

#### Derived memory

`POST /api/v1/memories/derived`

Request body:
```json
{
  "kind": "reflection",
  "text": "Laser etching satisfies the marking clause.",
  "source_ids": [
    "7ebe1081-278c-49f0-8436-b50345d3f547"
  ]
}
```

Response **200**:
```json
{
  "id": "c333a9bf-c860-45ec-9efb-351efc178291"
}
```

#### Recall chain

`POST /api/v1/recall`

Request body:
```json
{
  "strategy": "chain",
  "seed_id": "7ebe1081-278c-49f0-8436-b50345d3f547",
  "k": 2
}
```

Response **200**:
```json
{
  "sources": [],
  "chain": [],
  "mode": "extractive"
}
```

#### Link two nodes

`POST /api/v1/links`

Request body:
```json
{
  "id_a": "7ebe1081-278c-49f0-8436-b50345d3f547",
  "id_b": "67e53410c17a5649",
  "edge_type": "references"
}
```

Response **200**:
```json
{
  "success": true,
  "error": null
}
```

#### Link with an unknown edge type

`POST /api/v1/links`

Request body:
```json
{
  "id_a": "7ebe1081-278c-49f0-8436-b50345d3f547",
  "id_b": "67e53410c17a5649",
  "edge_type": "bogus"
}
```

Response **400**:
```json
{
  "detail": "invalid edge_type: bogus"
}
```

#### Delete a memory

`DELETE /api/v1/memories/7ebe1081-278c-49f0-8436-b50345d3f547`

Response **200**:
```json
{
  "deleted": "7ebe1081-278c-49f0-8436-b50345d3f547"
}
```

#### Delete again

`DELETE /api/v1/memories/7ebe1081-278c-49f0-8436-b50345d3f547`

Response **404**:
```json
{
  "detail": "memory not found"
}
```

#### Verbatim store

`POST /api/v1/verbatim`

Request body:
```json
{
  "content": "exact bytes",
  "label": "note"
}
```

Response **200**:
```json
{
  "key": "1f2f94df-f211-4c34-ad50-398e7f28a8e6"
}
```

#### Requirement context

`POST /api/v1/requirement-context`

Request body:
```json
{
  "text": "The widget shall not deflect more than 3 mm.",
  "normative_tags": [
    "doctype:standard"
  ]
}
```

Response **200**:
```json
{
  "query": {
    "doc_id": null,
    "text": "The widget shall not deflect more than 3 mm.",
    "level": "",
    "domain_tags": []
  },
  "linked": [],
  "peers": [],
  "normative": [
    {
      "id": "67e53410c17a5649",
      "doc_id": "67e53410c17a5649",
      "segment_id": "4ddeded0-763a-49dc-812e-42ab4be180c9",
      "doc_name": "ACME 1000 (example)",
      "text": "2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.7893006205558777,
      "reason": "standard match",
      "clause": null,
      "source_kind": "standard",
      "hops": null,
      "path": [],
      "clipped": false
    },
    {
      "id": "67e53410c17a5649",
      "doc_id": "67e53410c17a5649",
      "segment_id": "96754d5b-db84-496a-b88f-4c415c58c585",
      "doc_name": "ACME 1000 (example)",
      "text": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.48249390721321106,
      "reason": "standard match",
      "clause": null,
      "source_kind": "standard",
      "hops": null,
      "path": [],
      "clipped": false
    },
    {
      "id": "d0d490309ffeb4fc",
      "doc_id": "d0d490309ffeb4fc",
      "segment_id": "eabf7fa8-47ea-4bbd-9652-6930a8d89661",
      "doc_name": "ISO 9001 (example)",
      "text": "1 Quality\nAn organisation shall control documented information.\n",
      "score": 0.10441797971725464,
      "reason": "standard match",
      "clause": null,
      "source_kind": "standard",
      "hops": null,
      "path": [],
      "clipped": false
    },
    "..."
  ],
  "coverage": {
    "has_upstream": false,
    "has_downstream": false,
    "near_duplicates": 0,
    "normative_by_source": {
      "legal": 0,
      "standard": 4
    },
    "unresolved_links": []
  },
  "truncated": {
    "linked": false,
    "peers": false,
    "normative": false
  }
}
```

#### OpenAPI document is protected

`GET /api/v1/openapi.json`

Response **401**:
```json
{
  "detail": "bearer token or admin session required"
}
```


## 3. What the run found (behaviors the guide must state)

Each item is pinned by a test in `tests/test_example_standards_pdf.py` (name in brackets) so the guide fails loudly if SMOS changes.

1. **Segments follow block boundaries and size, not meaning.** SMOS cuts at block boundaries; a block over 800 characters is split
   by sentences/size; a block under 20 characters is merged into the PREVIOUS segment. `block_type` and `heading_level` are
   validated (a heading needs a level 1 to 6) but not otherwise used. A bare heading block "2.2 Marking" therefore ends up at
   the END of the previous clause's segment (`test_short_heading_block_is_glued_to_the_previous_segment`). The example parser
   emits one block per clause, heading line plus first paragraph, so a segment starts at its clause
   (`test_clause_block_keeps_heading_with_its_body`).
2. **Sections come only from split blocks.** Headings never create sections: `section_count` is 0 unless a single block was long
   enough to be split (`test_headings_do_not_create_sections_only_split_blocks_do`).
3. **`tags` on `/query` is a preference, not a filter.** When the tag-filtered vector search finds nothing, `/query` falls back to
   an unfiltered search, and the keyword (BM25) part is never filtered; `docs/API.md` and the request model say "restrict".
   Recorded as technical debt (`test_query_tags_are_a_preference_not_a_filter`).
4. **A query always returns its nearest items.** Even a nonsense query returned 3 results with `mode: "extractive"` and
   `confidence` about 0.54; `mode: "uncertain"` appears only when the store is empty or nothing was retrieved at all. Callers must
   judge `score` themselves.
5. **Links to documents not yet ingested work.** A declared link creates an edge to a placeholder document node with the target's
   deterministic id (`sha1("system:native_id")[:16]`; the target's system is `metadata.target_system`, default the source's own);
   ingesting the target later fills that node (`test_link_to_a_document_ingested_later_uses_a_placeholder_node`). Edges owned by a
   document are replaced wholesale when it is re-ingested.
6. **Blocks are joined with no separator**; end each block text with its own newline
   (`test_blocks_without_trailing_newline_run_together`).
7. **Re-ingest** of the same `external_handle` is an upsert: same `doc_id`, new segment ids, old text gone
   (`test_reingest_is_idempotent_and_changed_pdf_replaces`).
8. **Quarantine returns HTTP 200** with `status: "quarantined"`, a `quarantine_key` and an `errors` list; there is no endpoint to read
   quarantined payloads back, so keep your own copy and resend after fixing.
9. **`graph/query`** from a document id returns its segments (hop 1), documents it links to or that link to it (hop 1) and their
   segments (hop 2). A term that is not a stored node id is resolved as `entity:<term>` and returned nothing for a standard's
   designation: references declared in `source_links` are document-to-document edges, not entities.
10. **`recall` strategies** `temporal` and `repo` return `metadata: {}` for temporal hits; `chain` needs a seed with links and
    returned an empty chain for a memory without any; `exact` adds a `context` list of neighbouring segments.
11. The OpenAPI document needs a token or admin session (401 otherwise).

## 4. Steps for the Governor on Debian (deferred from the previous session)

Run on the Debian host in the install folder, one command at a time.

1. **Persistence across `down`/`up`.** Note the dashboard document and segment counts. Then
   `docker compose down` (never add `-v`), `docker compose up -d`, wait for `(healthy)` in `docker compose ps`, and compare the
   counts and `curl -s http://localhost:8080/api/v1/health`. Expected: counts unchanged, version reported.
2. **A read-only token is refused on upload.** Create a read-only invitation under Access, claim it, then
   `curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8080/api/v1/documents -H "Authorization: Bearer $READ" -H "Content-Type: application/json" -d '{"external_handle":{"system":"t","native_id":"x"},"blocks":[{"block_type":"paragraph","text":"Hello there.\n"}]}'`
   Expected: `403`. With a read/write token the same call returns `200` and should be followed by deleting nothing (the
   test document is harmless; it can be left or ignored).
   The same refusal is also asserted automatically (`test_read_only_token_cannot_ingest`).

## 5. Readability pass

(Filled in at the end of the project.)
