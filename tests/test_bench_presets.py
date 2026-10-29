from __future__ import annotations

from smos.bench.presets import PRESETS, SMOKE


def test_presets_match_the_design() -> None:
    assert PRESETS["quick"].sizes == (500, 1000, 2500, 5000) and PRESETS["quick"].step_seconds == 8
    assert PRESETS["quick"].clients == (1, 2, 4, 8)
    assert PRESETS["standard"].sizes == (1000, 5000, 10000, 25000) and PRESETS["standard"].step_seconds == 15
    assert PRESETS["standard"].clients == (1, 2, 4, 8, 16, 32, 64)
    assert PRESETS["full"].sizes[-1] == 100000 and PRESETS["full"].step_seconds == 20
    assert set(PRESETS) == {"quick", "standard", "full"} and SMOKE.name not in PRESETS


def test_every_preset_grows_strictly() -> None:
    for preset in [*PRESETS.values(), SMOKE]:
        assert list(preset.sizes) == sorted(set(preset.sizes))
