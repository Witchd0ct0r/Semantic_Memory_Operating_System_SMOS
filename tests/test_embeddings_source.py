from __future__ import annotations

from pathlib import Path

import pytest

from smos.memory import embeddings

_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED = _ROOT / "models" / "all-MiniLM-L6-v2"
_REQUIRED = ["modules.json", "config.json", "tokenizer.json", "model.safetensors"]


@pytest.fixture(autouse=True)
def _fresh_model_cache():
    embeddings._get_model.cache_clear()
    yield
    embeddings._get_model.cache_clear()


def _fake_model_dir(root: Path, skip: tuple[str, ...] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in _REQUIRED:
        if name not in skip:
            (root / name).write_bytes(b"x")
    return root


def test_default_source_is_the_model_name(monkeypatch) -> None:
    monkeypatch.delenv("SMOS_EMBEDDING_MODEL_DIR", raising=False)
    assert embeddings.model_source() == "all-MiniLM-L6-v2"


def test_complete_folder_is_returned_as_path(monkeypatch, tmp_path) -> None:
    folder = _fake_model_dir(tmp_path / "m")
    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(folder))
    assert embeddings.model_source() == str(folder)


def test_pytorch_bin_weights_are_accepted(monkeypatch, tmp_path) -> None:
    folder = _fake_model_dir(tmp_path / "m", skip=("model.safetensors",))
    (folder / "pytorch_model.bin").write_bytes(b"x")
    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(folder))
    assert embeddings.model_source() == str(folder)


def test_incomplete_folder_names_the_variable_and_missing_files(monkeypatch, tmp_path) -> None:
    folder = _fake_model_dir(tmp_path / "m", skip=("modules.json", "tokenizer.json"))
    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(folder))
    with pytest.raises(RuntimeError) as err:
        embeddings.model_source()
    message = str(err.value)
    assert "SMOS_EMBEDDING_MODEL_DIR" in message
    assert "modules.json" in message and "tokenizer.json" in message


def test_missing_weights_are_reported(monkeypatch, tmp_path) -> None:
    folder = _fake_model_dir(tmp_path / "m", skip=("model.safetensors",))
    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(folder))
    with pytest.raises(RuntimeError, match="model.safetensors"):
        embeddings.model_source()


def test_nonexistent_folder_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="SMOS_EMBEDDING_MODEL_DIR"):
        embeddings.model_source()


def test_create_app_fails_before_touching_the_data_dir(monkeypatch, tmp_path) -> None:
    from smos.api.app import create_app

    folder = _fake_model_dir(tmp_path / "m", skip=("config.json",))
    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(folder))
    data = tmp_path / "data"
    with pytest.raises(RuntimeError, match="SMOS_EMBEDDING_MODEL_DIR"):
        create_app(data, admin_user="admin", admin_password="pw-123456789")
    assert not data.exists()


@pytest.mark.skipif(not _BUNDLED.exists(), reason="bundled model folder not added yet")
def test_bundled_folder_gives_the_same_vectors_as_loading_by_name(monkeypatch) -> None:
    import numpy as np

    monkeypatch.delenv("SMOS_EMBEDDING_MODEL_DIR", raising=False)
    try:
        by_name = np.array(embeddings.embed("radiated emissions limit for the device"))
    except Exception as exc:  # no local Hugging Face cache and no network: nothing to compare against
        pytest.skip(f"model not available by name offline: {type(exc).__name__}")
    embeddings._get_model.cache_clear()

    monkeypatch.setenv("SMOS_EMBEDDING_MODEL_DIR", str(_BUNDLED))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from_folder = np.array(embeddings.embed("radiated emissions limit for the device"))

    assert from_folder.shape == (384,)
    cosine = float(by_name @ from_folder / (np.linalg.norm(by_name) * np.linalg.norm(from_folder)))
    assert cosine > 0.9999
