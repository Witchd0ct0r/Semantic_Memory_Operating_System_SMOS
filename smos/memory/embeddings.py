from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional

from sentence_transformers import SentenceTransformer

_MODEL_NAME = "all-MiniLM-L6-v2"
_MODEL_DIR_ENV = "SMOS_EMBEDDING_MODEL_DIR"
_REQUIRED_FILES = ("modules.json", "config.json", "tokenizer.json")
_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")
_THREADS_ENV = "SMOS_TORCH_THREADS"


def model_source() -> str:
    """Where to load the embedding model from.

    With SMOS_EMBEDDING_MODEL_DIR set (the container sets it) the model is loaded from that
    folder only, so nothing is ever downloaded; an incomplete folder fails loudly. Without it
    (development) the model is loaded by name through the normal Hugging Face cache.
    """
    folder = os.environ.get(_MODEL_DIR_ENV)
    if not folder:
        return _MODEL_NAME
    path = Path(folder)
    if not path.is_dir():
        raise RuntimeError(f"{_MODEL_DIR_ENV}={folder} is not a directory")
    missing = [name for name in _REQUIRED_FILES if not (path / name).is_file()]
    if not any((path / name).is_file() for name in _WEIGHT_FILES):
        missing.append(" or ".join(_WEIGHT_FILES))
    if missing:
        raise RuntimeError(
            f"The embedding model folder {folder} (from {_MODEL_DIR_ENV}) is incomplete; "
            f"missing: {', '.join(missing)}"
        )
    return str(path)


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(model_source())


def warm_up() -> None:
    """Load the model now so a broken model folder or thread setting stops startup instead of the first query."""
    configured_threads()
    _get_model()


def configured_threads(environ: Mapping[str, str] = os.environ) -> Optional[int]:
    """Threads torch should use per embedding, from SMOS_TORCH_THREADS; None (unset or empty) keeps torch's default."""
    raw = (environ.get(_THREADS_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise RuntimeError(f"{_THREADS_ENV}={raw!r} must be a whole number of at least 1")
    return value


def apply_threads() -> None:
    """Set torch's thread count for the calling thread. On OpenMP builds the setting is per thread, and requests run
    in worker threads, so this runs before every encode rather than once at start-up."""
    threads = configured_threads()
    if threads is not None:
        import torch

        torch.set_num_threads(threads)


def embed(text: str) -> list[float]:
    apply_threads()
    model = _get_model()
    vector = model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
    return vector.tolist()  # type: ignore[return-value]


def embed_batch(texts: list[str]) -> list[list[float]]:
    apply_threads()
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return vectors.tolist()  # type: ignore[return-value]
