from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from smos.llm.summarizer import compress_memories, summarize_text


def _make_mock_client(response_text: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value.choices[0].message.content = response_text
    return client


def test_summarize_text_returns_string() -> None:
    mock_client = _make_mock_client("Redis is a fast in-memory key-value store.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        result = summarize_text("Redis is an open-source, in-memory data structure store.")
    assert isinstance(result, str)
    assert len(result) > 0


def test_summarize_text_with_context_hint() -> None:
    mock_client = _make_mock_client("Summary with hint.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        result = summarize_text("Some text.", context_hint="File: config.py")
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    user_msg = call_kwargs["messages"][1]["content"]
    assert "config.py" in user_msg
    assert result == "Summary with hint."


def test_compress_memories_returns_summary_and_confidence() -> None:
    memories = [
        {"id": "abc12345", "content": "PostgreSQL is used for relational data.", "distance": 0.15},
        {"id": "def67890", "content": "Redis is used for caching.", "distance": 0.25},
    ]
    mock_client = _make_mock_client("PostgreSQL for relational data; Redis for caching.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        summary, confidence = compress_memories(memories, "database architecture")

    assert isinstance(summary, str)
    assert len(summary) > 0
    assert 0.0 <= confidence <= 1.0


def test_compress_memories_confidence_near_one_for_close_match() -> None:
    memories = [{"id": "aaa00000", "content": "Very relevant content.", "distance": 0.01}]
    mock_client = _make_mock_client("Compressed result.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        _, confidence = compress_memories(memories, "query")
    assert confidence > 0.9


def test_compress_memories_empty_returns_zero() -> None:
    summary, confidence = compress_memories([], "anything")
    assert summary == ""
    assert confidence == 0.0


def test_compress_memories_includes_ids_in_prompt() -> None:
    memories = [{"id": "abcd1234xtra", "content": "Some content.", "distance": 0.2}]
    mock_client = _make_mock_client("Compressed.")
    with patch("smos.llm.summarizer.get_llm_client", return_value=mock_client):
        compress_memories(memories, "test query")
    call_kwargs = mock_client.chat.completions.create.call_args[1]
    user_msg = call_kwargs["messages"][1]["content"]
    assert "abcd1234" in user_msg
    assert "test query" in user_msg
