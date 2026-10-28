from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from smos.api.app import create_app


def build_app():
    return create_app(
        Path(os.environ.get("SMOS_DATA_DIR", "/data")),
        admin_user=os.environ.get("SMOS_ADMIN_USER"),
        admin_password=os.environ.get("SMOS_ADMIN_PASSWORD"),
    )


def run() -> None:
    uvicorn.run(
        build_app(),
        host=os.environ.get("SMOS_HOST", "0.0.0.0"),
        port=int(os.environ.get("SMOS_PORT", "8765")),
    )


if __name__ == "__main__":
    run()
