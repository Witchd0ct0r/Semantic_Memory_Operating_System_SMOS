from __future__ import annotations

from smos.ingest.segmenter import (
    Span,
    _MAX_SEGMENT_CHARS,
    _MIN_SEGMENT_CHARS,
    _fits,
    _fixed_size_split,
    _split_sentences,
)


def test_fits_true_under_threshold():
    assert _fits("short text") is True


def test_fits_false_over_threshold():
    assert _fits("x" * (_MAX_SEGMENT_CHARS + 1)) is False


def test_split_sentences_returns_offsets_covering_text():
    text = "First sentence. Second sentence! Third sentence?"
    spans = _split_sentences(text)
    assert len(spans) == 3
    assert text[spans[0][0]:spans[0][1]].strip() == "First sentence."
    assert text[spans[1][0]:spans[1][1]].strip() == "Second sentence!"
    assert text[spans[2][0]:spans[2][1]].strip() == "Third sentence?"


def test_split_sentences_single_sentence():
    assert len(_split_sentences("Only one sentence here.")) == 1


def test_fixed_size_split_covers_text_without_gaps():
    text = "x" * 1500
    spans = _fixed_size_split(text)
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for i in range(len(spans) - 1):
        assert spans[i][1] == spans[i + 1][0]


def test_fixed_size_split_short_text_returns_single_span():
    text = "short"
    assert _fixed_size_split(text) == [(0, len(text))]
