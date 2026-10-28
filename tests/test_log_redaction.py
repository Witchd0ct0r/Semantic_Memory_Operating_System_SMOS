from __future__ import annotations

import logging

from smos.api.logging_config import RedactClaimSecret, build_log_config


def _record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "POST", path, "1.1", 200), None,
    )


def test_claim_secret_is_redacted_from_access_log() -> None:
    record = _record("/claim/SeCrEt_abc-123")
    assert RedactClaimSecret().filter(record) is True
    assert "SeCrEt_abc-123" not in record.getMessage()
    assert "/claim/<redacted>" in record.getMessage()


def test_claim_secret_with_query_string_redacted() -> None:
    record = _record("/claim/SeCrEt_abc-123?x=1")
    RedactClaimSecret().filter(record)
    assert "SeCrEt_abc-123" not in record.getMessage()


def test_other_paths_untouched() -> None:
    record = _record("/api/v1/query")
    RedactClaimSecret().filter(record)
    assert "/api/v1/query" in record.getMessage()


def test_log_config_attaches_filter_to_access_handler() -> None:
    config = build_log_config()
    assert "redact_claim" in config["handlers"]["access"]["filters"]
    assert config["filters"]["redact_claim"]["()"] == "smos.api.logging_config.RedactClaimSecret"
    # the config must be loadable as-is
    logging.config.dictConfig(config)
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, RedactClaimSecret) for h in access.handlers for f in h.filters)
