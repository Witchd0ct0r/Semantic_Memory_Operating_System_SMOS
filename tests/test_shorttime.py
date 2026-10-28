from __future__ import annotations

from smos.admin.pages import shorttime


def test_shorttime_formats_iso_timestamps() -> None:
    assert shorttime("2026-10-28T13:46:53.268237+00:00") == "2026-10-28 13:46:53"
    assert shorttime("2026-10-28T13:46:53+00:00") == "2026-10-28 13:46:53"


def test_shorttime_is_forgiving() -> None:
    assert shorttime(None) == ""
    assert shorttime("") == ""
    assert shorttime("not a date") == "not a date"
