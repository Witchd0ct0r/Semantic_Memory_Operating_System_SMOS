from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Hermetic tests: load the embedding model from the bundled folder and never touch the network.
# (Without this, a machine with no internet and no Hugging Face cache stalls on the first embedding.)
os.environ.setdefault(
    "SMOS_EMBEDDING_MODEL_DIR",
    str(Path(__file__).parent.parent / "models" / "all-MiniLM-L6-v2"),
)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture
def tmp_path() -> Path:  # type: ignore[override]
    d = Path(tempfile.mkdtemp(prefix="smostest_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def api(tmp_path):
    """(client, headers, app): a real app on a temp data dir with one valid API token."""
    from fastapi.testclient import TestClient

    from smos.admin import tokens
    from smos.api.app import create_app

    app = create_app(tmp_path / "data", admin_user="admin", admin_password="correct-horse-1",
                     start_sampler=False)
    _token_id, plaintext = tokens.create_token(app.state.admin_db, "test-token")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {plaintext}"}
    yield client, headers, app
    client.close()
    app.state.close()
