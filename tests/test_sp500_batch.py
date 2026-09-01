"""
Tests for get_sp500_batch — gives the agent systematic coverage of real
S&P 500 constituents, since raw movers/most-active screener data skews
heavily toward penny stocks and rarely surfaces genuine large-caps.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_layer.tools import ToolDispatcher, TOOL_SCHEMAS


@pytest.fixture(autouse=True)
def isolated_rotation_state(monkeypatch, tmp_path):
    import config.sp500_rotation as rotation_module
    monkeypatch.setattr(rotation_module, "STATE_PATH", str(tmp_path / "test_rotation_state.json"))
    yield


def test_tool_registered_in_schema():
    names = [t["name"] for t in TOOL_SCHEMAS]
    assert "get_sp500_batch" in names


def test_ticker_list_has_no_duplicates():
    from config.sp500_tickers import SP500_TICKERS
    assert len(SP500_TICKERS) == len(set(SP500_TICKERS))
    assert len(SP500_TICKERS) > 300  # sanity check it's a real, substantial list


def test_default_batch_size():
    d = ToolDispatcher()
    result = json.loads(d._get_sp500_batch(15))
    assert len(result["tickers"]) == 15


def test_successive_batches_differ():
    d = ToolDispatcher()
    batch1 = json.loads(d._get_sp500_batch(10))["tickers"]
    batch2 = json.loads(d._get_sp500_batch(10))["tickers"]
    assert batch1 != batch2


def test_rotation_persists_across_dispatcher_instances():
    """State must persist across separate agent cycles, not reset each time."""
    d1 = ToolDispatcher()
    batch1 = json.loads(d1._get_sp500_batch(10))["tickers"]

    d2 = ToolDispatcher()  # simulates a fresh cycle's new dispatcher instance
    batch2 = json.loads(d2._get_sp500_batch(10))["tickers"]

    assert batch1 != batch2  # proves state persisted, not reset per-instance


def test_rotation_wraps_around_full_list():
    from config.sp500_tickers import SP500_TICKERS
    d = ToolDispatcher()
    total = len(SP500_TICKERS)
    seen = set()
    # Pull enough batches to guarantee wraparound
    for _ in range((total // 20) + 3):
        batch = json.loads(d._get_sp500_batch(20))["tickers"]
        seen.update(batch)
    assert seen == set(SP500_TICKERS)  # full coverage achieved after wrapping
