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


def _fmt_tick(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if abs(value) >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


def xy_chart(
    series: list[Series],
    *,
    title: str,
    x_label: str,
    y_unit: str = "",
    x_ticks: Optional[list[float]] = None,
    x_log: bool = False,
    reference: Optional[tuple[str, float]] = None,
    marker: Optional[tuple[str, float]] = None,
    width: int = 640,
    height: int = 220,
) -> str:
    """An inline SVG line chart over a numeric x axis (database size, client count). Same look as line_chart."""
    drawable = [s for s in series if s.points]
    if not drawable:
        return _empty(title, width, height)

    xs = sorted({x for s in drawable for x, _ in s.points})
    ticks = sorted(x_ticks) if x_ticks else xs
    x_values = xs + ticks + ([marker[1]] if marker else [])
    x_min, x_max = min(x_values), max(x_values)

    def scale(x: float) -> float:
        return math.log10(max(x, 1e-9)) if x_log else x

    lo, hi = scale(x_min), scale(x_max)
    data_max = max(v for s in drawable for _, v in s.points)
    if reference is not None:
        data_max = max(data_max, reference[1])
    top = _nice_ceiling(data_max)
    plot_w = width - _MARGIN_LEFT - _MARGIN_RIGHT
    plot_h = height - _MARGIN_TOP - _MARGIN_BOTTOM

    def px(x: float) -> float:
        return _MARGIN_LEFT + ((scale(x) - lo) / (hi - lo)) * plot_w if hi > lo else _MARGIN_LEFT + plot_w / 2

    def py(v: float) -> float:
        return _MARGIN_TOP + plot_h - (min(max(v, 0.0), top) / top) * plot_h

    safe_title = escape(title, quote=True)
    last_values = ", ".join(f"{escape(s.name)} {_fmt_value(s.points[-1][1], y_unit)}" for s in drawable)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" '
        f'role="img" aria-label="{safe_title}">',
        f"<title>{escape(title)}</title>",
        f"<desc>Last values: {last_values}</desc>",
    ]
    for i in range(_GRID_LINES + 1):
        value = top * i / _GRID_LINES
        y = py(value)
        parts.append(f'<line class="grid" x1="{_MARGIN_LEFT}" x2="{width - _MARGIN_RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis-label" x="{_MARGIN_LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">'
                     f"{escape(_fmt_value(value, y_unit))}</text>")
    for tick in ticks:
        parts.append(f'<text class="axis-label" x="{px(tick):.1f}" y="{_MARGIN_TOP + plot_h + 16}" '
                     f'text-anchor="middle">{_fmt_tick(tick)}</text>')
    parts.append(f'<text class="axis-label" x="{_MARGIN_LEFT + plot_w / 2:.1f}" y="{_MARGIN_TOP + plot_h + 30}" '
                 f'text-anchor="middle">{escape(x_label)}</text>')

    if reference is not None:
        label, value = reference
        y = py(value)
        parts.append(f'<line class="reference" stroke-dasharray="6 4" x1="{_MARGIN_LEFT}" x2="{width - _MARGIN_RIGHT}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis-label" x="{width - _MARGIN_RIGHT - 4}" y="{max(y - 4, _MARGIN_TOP + 8):.1f}" '
                     f'text-anchor="end">{escape(label)} {escape(_fmt_value(value, y_unit))}</text>')
    if marker is not None:
        label, x = marker
        parts.append(f'<line class="marker" stroke-dasharray="3 3" x1="{px(x):.1f}" x2="{px(x):.1f}" '
                     f'y1="{_MARGIN_TOP}" y2="{_MARGIN_TOP + plot_h}"/>')
        parts.append(f'<text class="axis-label" x="{px(x) + 4:.1f}" y="{_MARGIN_TOP + 10}">{escape(label)}</text>')

    for s in drawable:
        css = escape(s.css_class, quote=True)
        ordered = sorted(s.points)
        if len(ordered) == 1:
            x, v = ordered[0]
            parts.append(f'<circle class="dot {css}" cx="{px(x):.1f}" cy="{py(v):.1f}" r="3.5"/>')
        else:
            coords = " ".join(f"{px(x):.1f},{py(v):.1f}" for x, v in ordered)
            parts.append(f'<polyline class="line {css}" fill="none" points="{coords}"/>')
            for x, v in ordered:
                parts.append(f'<circle class="dot {css}" cx="{px(x):.1f}" cy="{py(v):.1f}" r="2.5"/>')

    legend_y = height - 8
    x = _MARGIN_LEFT
    for s in drawable:
        parts.append(f'<rect class="swatch {escape(s.css_class, quote=True)}" x="{x}" y="{legend_y - 9}" width="10" height="10"/>')
        parts.append(f'<text class="axis-label" x="{x + 14}" y="{legend_y}">{escape(s.name)}</text>')
        x += 24 + 7 * len(s.name)
    parts.append("</svg>")
    return "".join(parts)


_BAR_LABEL_GUTTER, _BAR_ROW_H, _BAR_THICKNESS = 70, 34, 20


def stacked_bar(
    rows: list[tuple[str, list[tuple[str, float]]]],
    *,
    title: str,
    unit: str = "ms",
    width: int = 640,
) -> str:
    """One horizontal bar per row, split into named parts; each part's share is in its tooltip."""
    rows = [(label, parts_) for label, parts_ in rows if sum(v for _, v in parts_) > 0]
    if not rows:
        return _empty(title, width, 80)
    names: list[str] = []
    for _, row_parts in rows:
        for name, _v in row_parts:
            if name not in names:
                names.append(name)
    plot_w = width - _BAR_LABEL_GUTTER - _MARGIN_RIGHT
    height = _MARGIN_TOP + _BAR_ROW_H * len(rows) + 30
    safe_title = escape(title, quote=True)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" '
        f'role="img" aria-label="{safe_title}">',
        f"<title>{escape(title)}</title>",
    ]
    for r, (label, row_parts) in enumerate(rows):
        total = sum(v for _, v in row_parts)
        y = _MARGIN_TOP + r * _BAR_ROW_H
        out.append(f'<text class="axis-label" x="{_BAR_LABEL_GUTTER - 6}" y="{y + 14}" text-anchor="end">{escape(label)}</text>')
        x = float(_BAR_LABEL_GUTTER)
        for name, value in row_parts:
            if value <= 0:
                continue
            w = value / total * plot_w
            share = value / total * 100
            out.append(
                f'<rect class="bar s{names.index(name) % 6}" x="{x:.1f}" y="{y}" width="{w:.1f}" height="{_BAR_THICKNESS}">'
                f"<title>{escape(name)}: {_fmt_value(value, unit)} ({share:.0f}%)</title></rect>"
            )
            x += w
    legend_y = height - 8
    x = float(_BAR_LABEL_GUTTER)
    for i, name in enumerate(names):
        out.append(f'<rect class="swatch s{i % 6}" x="{x:.0f}" y="{legend_y - 9}" width="10" height="10"/>')
        out.append(f'<text class="axis-label" x="{x + 14:.0f}" y="{legend_y}">{escape(name)}</text>')
        x += 24 + 7 * len(name)
    out.append("</svg>")
    return "".join(out)


def data_table(headers: list[str], rows: list[list[str]]) -> str:
    """The numbers behind a chart, as an escaped table."""
    head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="data"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def bar_chart(values: list[tuple[str, float]], *, title: str, unit: str = "", width: int = 640) -> str:
    """One horizontal bar per labelled value, all on the same scale, so lengths can be compared."""
    values = [(label, v) for label, v in values if v is not None]
    top = max((v for _, v in values), default=0.0)
    if not values or top <= 0:
        return _empty(title, width, 80)
    gutter, value_room = 90, 70
    plot_w = width - gutter - value_room
    height = _MARGIN_TOP + _BAR_ROW_H * len(values) + 6
    safe_title = escape(title, quote=True)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" class="chart" '
        f'role="img" aria-label="{safe_title}">',
        f"<title>{escape(title)}</title>",
    ]
    for i, (label, value) in enumerate(values):
        y = _MARGIN_TOP + i * _BAR_ROW_H
        w = max(value / top * plot_w, 1.0)
        out.append(f'<text class="axis-label" x="{gutter - 6}" y="{y + 14}" text-anchor="end">{escape(label)}</text>')
        out.append(f'<rect class="bar s{i % 6}" x="{gutter}" y="{y}" width="{w:.1f}" height="{_BAR_THICKNESS}">'
                   f"<title>{escape(label)}: {_fmt_value(value, unit)}</title></rect>")
        out.append(f'<text class="axis-label" x="{gutter + w + 6:.1f}" y="{y + 14}">{escape(_fmt_value(value, unit))}</text>')
    out.append("</svg>")
    return "".join(out)
