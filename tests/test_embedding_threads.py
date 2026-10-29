from __future__ import annotations

import threading

import pytest
import torch

from smos.memory import embeddings
from smos.bench import runner


def test_unset_or_empty_means_default() -> None:
    assert embeddings.configured_threads({}) is None
    assert embeddings.configured_threads({"SMOS_TORCH_THREADS": ""}) is None
    assert embeddings.configured_threads({"SMOS_TORCH_THREADS": "  "}) is None


def test_positive_integer_is_accepted() -> None:
    assert embeddings.configured_threads({"SMOS_TORCH_THREADS": "1"}) == 1
    assert embeddings.configured_threads({"SMOS_TORCH_THREADS": " 4 "}) == 4


@pytest.mark.parametrize("value", ["0", "-2", "two", "1.5"])
def test_zero_negative_and_text_are_rejected_naming_the_variable(value: str) -> None:
    with pytest.raises(RuntimeError, match="SMOS_TORCH_THREADS"):
        embeddings.configured_threads({"SMOS_TORCH_THREADS": value})


def test_apply_threads_takes_effect_in_a_fresh_thread(monkeypatch) -> None:
    monkeypatch.setenv("SMOS_TORCH_THREADS", "1")
    seen = []

    def work() -> None:
        embeddings.apply_threads()
        seen.append(torch.get_num_threads())

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(10)
    assert seen == [1]


def test_embed_applies_threads_before_encoding(monkeypatch) -> None:
    monkeypatch.setenv("SMOS_TORCH_THREADS", "1")
    seen = []

    class _Model:
        def encode(self, text, **_kwargs):
            import numpy as np

            seen.append(torch.get_num_threads())
            return np.zeros(3, dtype="float32") if isinstance(text, str) else np.zeros((len(text), 3), dtype="float32")

    monkeypatch.setattr(embeddings, "_get_model", lambda: _Model())
    thread = threading.Thread(target=lambda: (embeddings.embed("x"), embeddings.embed_batch(["x", "y"])))
    thread.start()
    thread.join(10)
    assert seen == [1, 1]


def test_hardware_info_records_torch_threads(monkeypatch) -> None:
    monkeypatch.setenv("SMOS_TORCH_THREADS", "2")
    assert runner.hardware_info()["torch_threads"] == 2
    monkeypatch.delenv("SMOS_TORCH_THREADS")
    assert runner.hardware_info()["torch_threads"] == torch.get_num_threads()


def test_warm_up_rejects_a_bad_thread_setting_at_startup(monkeypatch) -> None:
    monkeypatch.setattr(embeddings, "_get_model", lambda: object())
    monkeypatch.setenv("SMOS_TORCH_THREADS", "lots")
    with pytest.raises(RuntimeError, match="SMOS_TORCH_THREADS"):
        embeddings.warm_up()
    monkeypatch.setenv("SMOS_TORCH_THREADS", "2")
    embeddings.warm_up()  # a valid value does not raise
