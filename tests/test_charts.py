from __future__ import annotations

import re

import pytest

from smos.admin.charts import Series, downsample, format_bytes, line_chart

T0 = 1_700_000_000.0


def _points(values, step=60.0):
    return [(T0 + i * step, float(v)) for i, v in enumerate(values)]


def test_downsample_keeps_at_most_max_points_and_the_mean() -> None:
    pts = _points(range(1000))
    out = downsample(pts, max_points=100)
    assert len(out) <= 100
    assert sum(v for _t, v in out) / len(out) == pytest.approx(sum(v for _t, v in pts) / len(pts), rel=0.02)
    assert [t for t, _v in out] == sorted(t for t, _v in out)


def test_downsample_short_series_is_unchanged() -> None:
    pts = _points([1, 2, 3])
    assert downsample(pts, max_points=240) == pts
    assert downsample([], max_points=10) == []


def test_chart_has_polylines_title_and_legend() -> None:
    svg = line_chart(
        [Series("ok", _points([1, 3, 2, 5]), "s0"), Series("failed", _points([0, 1, 0, 0]), "s1")],
        title="API calls per minute",
    )
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert 'role="img"' in svg and 'aria-label="API calls per minute"' in svg
    assert "<title>API calls per minute</title>" in svg
    assert svg.count("<polyline") == 2
    assert ">ok<" in svg and ">failed<" in svg


def test_empty_series_renders_a_no_data_chart() -> None:
    assert "No data" in line_chart([], title="Empty")
    assert "No data" in line_chart([Series("x", [], "s0")], title="Empty")


def test_single_point_and_flat_values_render() -> None:
    one = line_chart([Series("x", _points([5]), "s0")], title="One")
    flat = line_chart([Series("x", _points([2, 2, 2, 2]), "s0")], title="Flat")
    zero = line_chart([Series("x", _points([0, 0, 0]), "s0")], title="Zero")
    for svg in (one, flat, zero):
        assert "<svg" in svg and "nan" not in svg.lower() and "inf" not in svg.lower()


def test_reference_line_is_drawn_with_its_label() -> None:
    svg = line_chart([Series("used", _points([10, 20, 30]), "s0")], title="Memory",
                     y_unit="bytes", reference=("limit", 1_073_741_824))
    assert 'stroke-dasharray' in svg
    assert "limit" in svg
    assert "1.0 GB" in svg or "1.00 GB" in svg


def test_percent_axis_labels() -> None:
    svg = line_chart([Series("cpu", _points([10, 40, 20]), "s0")], title="CPU", y_unit="%", y_max=100)
    assert "100%" in svg


def test_text_is_escaped() -> None:
    svg = line_chart([Series("<script>alert(1)</script>", _points([1, 2]), "s0")],
                     title='Evil "title" <b>')
    assert "<script>" not in svg and "<b>" not in svg
    assert "&lt;script&gt;" in svg


def test_no_external_references() -> None:
    svg = line_chart([Series("x", _points([1, 2, 3]), "s0")], title="T")
    urls = re.findall(r"https?://[^\s\"']+", svg)
    assert set(urls) <= {"http://www.w3.org/2000/svg"}


def test_x_labels_use_hours_for_short_spans_and_dates_for_long_ones() -> None:
    short = line_chart([Series("x", _points([1, 2, 3, 4], step=600), "s0")], title="T")
    long = line_chart([Series("x", _points([1, 2, 3, 4], step=3 * 86400), "s0")], title="T")
    assert re.search(r">\d\d:\d\d<", short)
    assert re.search(r">\d\d-\d\d<", long)


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (5 * 1024 ** 2, "5.0 MB"),
    (3 * 1024 ** 3, "3.0 GB"), (None, "n/a"),
])
def test_format_bytes(n, expected) -> None:
    assert format_bytes(n) == expected


def test_single_point_series_draws_a_dot() -> None:
    svg = line_chart([Series("x", _points([5]), "s0")], title="One")
    assert "<circle" in svg and 'class="dot s0"' in svg


def test_a_very_short_span_has_one_x_label_not_overlapping_ones() -> None:
    svg = line_chart([Series("x", _points([1, 2], step=1.0), "s0")], title="Short")
    assert len(re.findall(r">\d\d:\d\d<", svg)) == 1
