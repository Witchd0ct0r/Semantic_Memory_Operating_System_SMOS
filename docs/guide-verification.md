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
      "block_type": "paragraph",
      "text": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n"
    },
    {
      "block_type": "paragraph",
      "text": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n"
    }
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
    "25753d3c-a1e7-40f6-aef3-8ed2850fdd1a",
    "fc0af62f-e227-419c-a3a6-7dfbb19684df",
    "601bbfe1-75fa-43b0-9cc1-ce7d9c72aebc"
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
  "source_links": []
}
```

Response **200**:
```json
{
  "status": "success",
  "doc_id": "d0d490309ffeb4fc",
  "segment_count": 1,
  "segment_ids": [
    "cb775ce7-fcea-4fe2-afed-2a83c4bba794"
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
      "block_type": "paragraph",
      "text": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n"
    },
    {
      "block_type": "paragraph",
      "text": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n"
    }
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
    "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
    "6e263a94-5eed-4462-bfaa-d46ccb3da296",
    "0a14090d-5245-4798-89d0-ca2a54e757ef"
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
  "quarantine_key": "5a72a398-5b9a-4784-9da0-6b4cdb6b5d30",
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
  "quarantine_key": "d75f8366-78da-48f5-91b9-622db70c1f0c",
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
  "quarantine_key": "60b749ca-f5ed-493c-8fb0-c35037f27e62",
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
  "quarantine_key": "b5a90bce-3c40-41f0-8e73-13a781ed81b9",
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
  "quarantine_key": "c13cef32-20b8-49b8-946d-600c641e26a0",
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
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.8095625638961792,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 56,
        "char_end": 154
      }
    },
    {
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
    },
    {
      "id": "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n",
      "score": 0.48766398429870605,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 56
      }
    }
  ],
  "sources": [
    "6e263a94-5eed-4462-bfaa-d46ccb3da296",
    "0a14090d-5245-4798-89d0-ca2a54e757ef",
    "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411"
  ],
  "confidence": 0.7979863186677297,
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
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.8095625638961792,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 56,
        "char_end": 154
      }
    },
    {
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
    },
    {
      "id": "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n",
      "score": 0.48766398429870605,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 56
      }
    }
  ],
  "sources": [
    "6e263a94-5eed-4462-bfaa-d46ccb3da296",
    "0a14090d-5245-4798-89d0-ca2a54e757ef",
    "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411"
  ],
  "confidence": 0.7979863186677297,
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
      "id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
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
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.030170125886797905,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 56,
        "char_end": 154
      }
    },
    {
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
    }
  ],
  "sources": [
    "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
    "6e263a94-5eed-4462-bfaa-d46ccb3da296",
    "0a14090d-5245-4798-89d0-ca2a54e757ef"
  ],
  "confidence": 0.5392625095943611,
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
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
      "id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
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
      "id": "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n",
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 56
      },
      "distance": 0.8324563801288605,
      "score": 0.16754361987113953
    }
  ],
  "context": [
    {
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 56,
        "char_end": 154
      }
    }
  ],
  "sources": [
    "0a14090d-5245-4798-89d0-ca2a54e757ef",
    "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
    "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411"
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
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
      "id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
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
      "id": "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
      "content": "1 Scope\nThis standard applies to widgets. See ISO 9001.\n",
      "score": 0.16754361987113953,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 0,
        "char_start": 0,
        "char_end": 56
      }
    }
  ],
  "sources": [
    "0a14090d-5245-4798-89d0-ca2a54e757ef",
    "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
    "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411"
  ],
  "confidence": 0.6601165980100632,
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
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
      "content": "2.2 Marking\nEach widget shall carry a permanent serial marking.\n",
      "score": 0.05754334479570389,
      "metadata": {}
    },
    {
      "id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
      "content": "1 Quality\nAn organisation shall control documented information.\n",
      "score": 0.04188030958175659,
      "metadata": {}
    },
    {
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.024116432294249535,
      "metadata": {}
    },
    "..."
  ],
  "sources": [
    "0a14090d-5245-4798-89d0-ca2a54e757ef",
    "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
    "6e263a94-5eed-4462-bfaa-d46ccb3da296",
    "..."
  ],
  "confidence": 0.5151076689362526,
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
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
      "id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
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
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "content": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.024116486310958862,
      "metadata": {
        "type": "segment",
        "tags": "widgets,doctype:standard",
        "doc_id": "67e53410c17a5649",
        "ordinal": 1,
        "char_start": 56,
        "char_end": 154
      }
    },
    "..."
  ],
  "sources": [
    "0a14090d-5245-4798-89d0-ca2a54e757ef",
    "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
    "6e263a94-5eed-4462-bfaa-d46ccb3da296",
    "..."
  ],
  "confidence": 0.5151076761540025,
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
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
      "type": "memory",
      "name": "0a14090d",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "type": "memory",
      "name": "6e263a94",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
      "type": "memory",
      "name": "cefc286c",
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
      "id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
      "type": "memory",
      "name": "cb775ce7",
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
      "id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
      "type": "memory",
      "name": "0a14090d",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "type": "memory",
      "name": "6e263a94",
      "metadata": {},
      "hop": 1
    },
    {
      "id": "cefc286c-9d1b-4bd8-a7fa-9e3fa5230411",
      "type": "memory",
      "name": "cefc286c",
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
  "id": "0de32bd7-82b5-4866-bbe3-3755af0a5b7a"
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
    "0de32bd7-82b5-4866-bbe3-3755af0a5b7a"
  ]
}
```

Response **200**:
```json
{
  "id": "27092c3f-ab7c-4d85-9844-39fe7c00d327"
}
```

#### Recall chain

`POST /api/v1/recall`

Request body:
```json
{
  "strategy": "chain",
  "seed_id": "0de32bd7-82b5-4866-bbe3-3755af0a5b7a",
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
  "id_a": "0de32bd7-82b5-4866-bbe3-3755af0a5b7a",
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
  "id_a": "0de32bd7-82b5-4866-bbe3-3755af0a5b7a",
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

`DELETE /api/v1/memories/0de32bd7-82b5-4866-bbe3-3755af0a5b7a`

Response **200**:
```json
{
  "deleted": "0de32bd7-82b5-4866-bbe3-3755af0a5b7a"
}
```

#### Delete again

`DELETE /api/v1/memories/0de32bd7-82b5-4866-bbe3-3755af0a5b7a`

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
  "key": "2d052d68-7b42-4070-98ba-a18848b1984a"
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
      "segment_id": "6e263a94-5eed-4462-bfaa-d46ccb3da296",
      "doc_name": "ACME 1000 (example)",
      "text": "2 Limits\n2.1 Deflection\nThe maximum permitted widget deflection shall not exceed two millimetres.\n",
      "score": 0.7893373966217041,
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
      "segment_id": "0a14090d-5245-4798-89d0-ca2a54e757ef",
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
      "segment_id": "cb775ce7-fcea-4fe2-afed-2a83c4bba794",
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
