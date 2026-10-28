from __future__ import annotations

import copy
import logging
import logging.config
import re

import uvicorn.config

_CLAIM_PATH = re.compile(r"(/claim/)[^\s?#/]+")


class RedactClaimSecret(logging.Filter):
    """Keeps invitation secrets out of the access log: `/claim/<secret>` is logged as
    `/claim/<redacted>`, so reading the logs can never yield a usable claim link."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                _CLAIM_PATH.sub(r"\1<redacted>", a) if isinstance(a, str) else a for a in args
            )
        return True


def build_log_config() -> dict:
    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config.setdefault("filters", {})["redact_claim"] = {"()": "smos.api.logging_config.RedactClaimSecret"}
    config["handlers"]["access"]["filters"] = ["redact_claim"]
    return config
