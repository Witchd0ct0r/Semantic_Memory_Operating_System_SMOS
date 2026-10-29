from __future__ import annotations

import re

import pytest

from smos.admin.charts import Series, data_table, stacked_bar, xy_chart


def _tick_x(svg: str, label: str) -> float:
    match = re.search(rf'<text class="axis-label" x="([\d.]+)" y="[\d.]+" text-anchor="middle">{label}</text>', svg)
    assert match, f"tick {label} not found in {svg[:200]}"
    return float(match.group(1))


def test_xy_chart_is_svg_with_title_and_all_series() -> None:
    svg = xy_chart(
        [Series("p50", [(1000, 10), (5000, 20)], "s0"), Series("p95", [(1000, 15), (5000, 40)], "s1")],
        title="Latency vs size", x_label="segments", y_unit="ms",
    )
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<title>Latency vs size</title>" in svg
    assert svg.count("<polyline") == 2 and "p50" in svg and "p95" in svg
    assert ">1k<" in svg and ">5k<" in svg and "segments" in svg


def test_xy_chart_log_x_places_ticks_in_order() -> None:
    svg = xy_chart([Series("a", [(1000, 1), (10000, 2), (100000, 3)])], title="t", x_label="n", x_log=True,
                   x_ticks=[1000, 10000, 100000])
    a, b, c = _tick_x(svg, "1k"), _tick_x(svg, "10k"), _tick_x(svg, "100k")
    assert a < b < c
    assert (b - a) == pytest.approx(c - b, abs=0.2)  # equal spacing per decade


def test_xy_chart_marker_and_reference_drawn() -> None:
    svg = xy_chart([Series("rps", [(1, 10), (8, 40), (16, 41)])], title="t", x_label="clients",
                   reference=("budget", 30), marker=("knee 8", 8))
    assert 'class="reference"' in svg and "budget" in svg
    assert 'class="marker"' in svg and "knee 8" in svg


def test_xy_chart_empty_says_no_data() -> None:
    assert "No data yet" in xy_chart([Series("a", [])], title="t", x_label="n")


def test_xy_chart_single_point_draws_a_dot() -> None:
    assert "<circle" in xy_chart([Series("a", [(5, 5)])], title="t", x_label="n")


def test_xy_chart_escapes_series_names() -> None:
    svg = xy_chart([Series("<script>alert(1)</script>", [(1, 1), (2, 2)])], title="<b>t</b>", x_label="<i>x</i>")
    assert "<script>" not in svg and "<b>t</b>" not in svg and "<i>x</i>" not in svg


def test_stacked_bar_segments_sum_to_full_width() -> None:
    svg = stacked_bar([("25k", [("embedding", 30.0), ("vector search", 2.0), ("BM25", 8.0)])],
                      title="Query time", unit="ms", width=640)
    widths = [float(w) for w in re.findall(r'<rect class="bar s\d" x="[\d.]+" y="[\d.]+" width="([\d.]+)"', svg)]
    assert len(widths) == 3
    assert sum(widths) == pytest.approx(640 - 70 - 14, abs=0.5)  # plot width: left label gutter 70, right margin 14
    assert "75%" in svg  # embedding is 30 of 40


def test_stacked_bar_zero_total_is_empty_not_error() -> None:
    assert "No data yet" in stacked_bar([("a", [("x", 0.0)])], title="t")
    assert "No data yet" in stacked_bar([], title="t")


def test_data_table_escapes_cells() -> None:
    html = data_table(["a<b", "c"], [["<img src=x>", "2"]])
    assert "<img" not in html and "&lt;img" in html and "a&lt;b" in html
    assert html.count("<tr") == 2


def test_bar_chart_bars_are_proportional_and_labelled() -> None:
    from smos.admin.charts import bar_chart

    svg = bar_chart([("alone", 120.0), ("with writer", 180.0)], title="Read p95", unit="ms", width=640)
    widths = [float(w) for w in re.findall(r'<rect class="bar s\d" x="[\d.]+" y="[\d.]+" width="([\d.]+)"', svg)]
    assert len(widths) == 2 and widths[1] / widths[0] == pytest.approx(1.5, rel=0.01)
    assert "alone" in svg and "with writer" in svg and "180" in svg


def test_bar_chart_empty_and_escaped() -> None:
    from smos.admin.charts import bar_chart

    assert "No data yet" in bar_chart([], title="t")
    assert "<b>" not in bar_chart([("<b>x</b>", 1.0)], title="<i>t</i>")
