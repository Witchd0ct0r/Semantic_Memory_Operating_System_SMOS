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
