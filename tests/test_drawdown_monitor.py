import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import json
from datetime import date

import risk.drawdown_monitor as dd


@pytest.fixture(autouse=True)
def isolated_baseline_file(monkeypatch, tmp_path):
    temp_file = tmp_path / "test_baseline.json"
    monkeypatch.setattr(dd, "BASELINE_PATH", str(temp_file))
    yield temp_file


def test_first_check_of_day_sets_baseline_no_trigger():
    result = dd.check_drawdown(100000.0)
    assert not result.triggered
    assert result.baseline_equity == 100000.0


def test_second_check_same_day_uses_existing_baseline():
    dd.check_drawdown(100000.0)  # sets baseline
    result = dd.check_drawdown(98000.0)  # 2% down, within limit
    assert not result.triggered
    assert result.baseline_equity == 100000.0
    assert abs(result.drawdown_pct - 0.02) < 1e-6


def test_triggers_at_exactly_fifteen_percent_drawdown():
    dd.check_drawdown(100000.0)  # baseline
    result = dd.check_drawdown(85000.0)  # exactly 15% down
    assert result.triggered


def test_does_not_trigger_just_under_threshold():
    dd.check_drawdown(100000.0)
    result = dd.check_drawdown(85001.0)  # 14.999% down
    assert not result.triggered


def test_triggers_beyond_threshold():
    dd.check_drawdown(100000.0)
    result = dd.check_drawdown(70000.0)  # 30% down
    assert result.triggered
    assert "30.0%" in result.reason


def test_gains_do_not_trigger():
    dd.check_drawdown(100000.0)
    result = dd.check_drawdown(120000.0)  # up 20%
    assert not result.triggered
    assert result.drawdown_pct < 0  # negative drawdown = gain


def test_new_day_resets_baseline(isolated_baseline_file):
    dd.check_drawdown(100000.0)  # today's baseline
    # Manually simulate a new day by rewriting the file with yesterday's date
    with open(isolated_baseline_file, "r") as f:
        record = json.load(f)
    record["date"] = "2020-01-01"  # force stale
    with open(isolated_baseline_file, "w") as f:
        json.dump(record, f)

    # Now a check with a much lower equity should reset baseline rather than trigger,
    # since it's treated as a new day
    result = dd.check_drawdown(50000.0)
    assert not result.triggered
    assert result.baseline_equity == 50000.0
