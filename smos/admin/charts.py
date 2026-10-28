from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Optional

_MARGIN_LEFT, _MARGIN_RIGHT, _MARGIN_TOP, _MARGIN_BOTTOM = 62, 14, 14, 52
_GRID_LINES = 4


@dataclass
class Series:
    name: str
    points: list[tuple[float, float]]
    css_class: str = "s0"


def format_bytes(n: Optional[float]) -> str:
    if n is None:
        return "n/a"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover


def downsample(points: list[tuple[float, float]], max_points: int = 240) -> list[tuple[float, float]]:
    """Average consecutive points into at most `max_points` buckets, keeping time order."""
    if len(points) <= max_points:
        return list(points)
    size = math.ceil(len(points) / max_points)
    out: list[tuple[float, float]] = []
    for start in range(0, len(points), size):
        chunk = points[start:start + size]
        out.append((sum(t for t, _ in chunk) / len(chunk), sum(v for _, v in chunk) / len(chunk)))
    return out


def _nice_ceiling(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude  # pragma: no cover


def _fmt_value(value: float, unit: str) -> str:
    if unit == "bytes":
        return format_bytes(value)
    if unit == "%":
        return f"{value:.0f}%"
    text = f"{value:.0f}" if abs(value) >= 10 or value == int(value) else f"{value:.1f}"
    return f"{text} {unit}".strip()


def _fmt_time(epoch: float, span: float) -> str:
    moment = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return moment.strftime("%H:%M") if span <= 36 * 3600 else moment.strftime("%m-%d")


def _empty(title: str, width: int, height: int) -> str:
    safe = escape(title, quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" '
        f'role="img" aria-label="{safe}"><title>{safe}</title>'
        f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" class="axis-label">No data yet</text></svg>'
    )


def line_chart(
    series: list[Series],
    *,
    title: str,
    y_unit: str = "",
    y_max: Optional[float] = None,
    reference: Optional[tuple[str, float]] = None,
    width: int = 640,
    height: int = 220,
) -> str:
    """An inline SVG line chart. Needs no scripts, fonts or network; colours come from CSS classes."""
    drawable = [s for s in series if s.points]
    if not drawable:
        return _empty(title, width, height)

    all_points = [p for s in drawable for p in s.points]
    x_min = min(t for t, _ in all_points)
    x_max = max(t for t, _ in all_points)
    span = max(x_max - x_min, 1.0)
    data_max = max(v for _, v in all_points)
    if reference is not None:
        data_max = max(data_max, reference[1])
    top = y_max if y_max is not None else _nice_ceiling(data_max)
    top = top if top > 0 else 1.0

    plot_w = width - _MARGIN_LEFT - _MARGIN_RIGHT
    plot_h = height - _MARGIN_TOP - _MARGIN_BOTTOM

    def px(t: float) -> float:
        return _MARGIN_LEFT + ((t - x_min) / span) * plot_w if x_max > x_min else _MARGIN_LEFT + plot_w / 2

    def py(v: float) -> float:
        return _MARGIN_TOP + plot_h - (min(max(v, 0.0), top) / top) * plot_h

    safe_title = escape(title, quote=True)
    last_values = ", ".join(f"{escape(s.name)} {_fmt_value(s.points[-1][1], y_unit)}" for s in drawable)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" '
        f'role="img" aria-label="{safe_title}">',
        f"<title>{escape(title)}</title>",
        f"<desc>Latest values: {last_values}</desc>",
    ]

    for i in range(_GRID_LINES + 1):
        value = top * i / _GRID_LINES
        y = py(value)
        parts.append(f'<line class="grid" x1="{_MARGIN_LEFT}" x2="{width - _MARGIN_RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="axis-label" x="{_MARGIN_LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">'
            f"{escape(_fmt_value(value, y_unit))}</text>"
        )

    # a span of under two minutes would print identical, overlapping labels: show one in the middle
    labels = (((0.5, "middle"),) if (x_max - x_min) < 120
              else ((0.0, "start"), (0.5, "middle"), (1.0, "end")))
    for fraction, anchor in labels:
        t = x_min + span * fraction
        parts.append(
            f'<text class="axis-label" x="{px(t):.1f}" y="{_MARGIN_TOP + plot_h + 16}" text-anchor="{anchor}">'
            f"{_fmt_time(t, span)}</text>"
        )

    if reference is not None:
        label, value = reference
        y = py(value)
        parts.append(
            f'<line class="reference" stroke-dasharray="6 4" x1="{_MARGIN_LEFT}" x2="{width - _MARGIN_RIGHT}" '
            f'y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="axis-label" x="{width - _MARGIN_RIGHT - 4}" y="{max(y - 4, _MARGIN_TOP + 8):.1f}" '
            f'text-anchor="end">{escape(label)} {escape(_fmt_value(value, y_unit))}</text>'
        )

    for s in drawable:
        coords = " ".join(f"{px(t):.1f},{py(v):.1f}" for t, v in downsample(s.points))
        css = escape(s.css_class, quote=True)
        if len(s.points) == 1:  # a lone point has no line to draw
            t, v = s.points[0]
            parts.append(f'<circle class="dot {css}" cx="{px(t):.1f}" cy="{py(v):.1f}" r="3.5"/>')
        else:
            parts.append(f'<polyline class="line {css}" fill="none" points="{coords}"/>')

    legend_y = height - 8
    x = _MARGIN_LEFT
    for s in drawable:
        parts.append(f'<rect class="swatch {escape(s.css_class, quote=True)}" x="{x}" y="{legend_y - 9}" width="10" height="10"/>')
        parts.append(f'<text class="axis-label" x="{x + 14}" y="{legend_y}">{escape(s.name)}</text>')
        x += 24 + 7 * len(s.name)

    parts.append("</svg>")
    return "".join(parts)
