import sys, os, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from dataclasses import dataclass

import config.dynamic_overrides as dyn


@pytest.fixture(autouse=True)
def isolated_overrides_file(monkeypatch, tmp_path):
    """Redirect OVERRIDES_PATH to a temp file for every test so tests never
    touch the real dynamic_overrides.json or leak state between tests."""
    temp_file = tmp_path / "test_overrides.json"
    monkeypatch.setattr(dyn, "OVERRIDES_PATH", str(temp_file))
    yield temp_file


def test_rejects_non_whitelisted_field():
    with pytest.raises(ValueError, match="not in the adjustable whitelist"):
        dyn.apply_override("max_daily_loss_pct", 0.10, "trying to loosen risk limit")


def test_rejects_non_whitelisted_field_even_with_plausible_name():
    # stop_loss_multiple sounds like it could be adjustable but is deliberately excluded
    with pytest.raises(ValueError):
        dyn.apply_override("stop_loss_multiple", 3.5, "widen stop loss")


def test_clamps_value_above_max():
    result = dyn.apply_override("vix_entry_threshold", 999.0, "test")
    assert result["value"] == 30.0  # upper bound
    assert result["was_clamped"] is True
    assert result["requested_value"] == 999.0


def test_clamps_value_below_min():
    result = dyn.apply_override("min_iv_rank", -50.0, "test")
    assert result["value"] == 10.0  # lower bound
    assert result["was_clamped"] is True


def test_accepts_value_within_bounds():
    result = dyn.apply_override("target_short_delta", 0.20, "test reasoning")
    assert result["value"] == 0.20
    assert result["was_clamped"] is False
    assert result["reasoning"] == "test reasoning"


def test_persists_and_reloads():
    dyn.apply_override("min_iv_rank", 25.0, "lowered due to sustained low-vol regime")
    reloaded = dyn.load_overrides()
    assert reloaded["min_iv_rank"]["value"] == 25.0


def test_effective_config_applies_overrides():
    @dataclass(frozen=True)
    class FakeStrategyConfig:
        vix_entry_threshold: float = 20.0
        min_iv_rank: float = 40.0
        target_short_delta: float = 0.16
        profit_take_pct: float = 0.50
        universe: tuple = ("SPY",)

    base = FakeStrategyConfig()
    dyn.apply_override("min_iv_rank", 22.0, "test")

    effective = dyn.effective_strategy_config(base)
    assert effective.min_iv_rank == 22.0
    assert effective.vix_entry_threshold == 20.0  # untouched field stays at base value


def test_effective_config_with_no_overrides_returns_base():
    @dataclass(frozen=True)
    class FakeStrategyConfig:
        vix_entry_threshold: float = 20.0

    base = FakeStrategyConfig()
    effective = dyn.effective_strategy_config(base)
    assert effective.vix_entry_threshold == 20.0


def test_previous_value_tracked_across_changes():
    dyn.apply_override("profit_take_pct", 0.50, "first change")
    result2 = dyn.apply_override("profit_take_pct", 0.60, "second change")
    assert result2["previous_value"] == 0.50
    assert result2["value"] == 0.60
