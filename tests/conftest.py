from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


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

    app = create_app(tmp_path / "data", admin_user="admin", admin_password="correct-horse-1")
    _token_id, plaintext = tokens.create_token(app.state.admin_db, "test-token")
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {plaintext}"}
    yield client, headers, app
    client.close()
    app.state.close()
