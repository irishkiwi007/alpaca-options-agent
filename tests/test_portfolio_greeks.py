"""
Tests for get_portfolio_greeks — item 2 of the agent's own self-assessed
limitations: "I don't have a consolidated view of my net delta, theta,
vega exposure across all open positions simultaneously... a same-day
concentration of multiple NVDA spreads wasn't quantified portfolio-wide."
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_layer.tools import ToolDispatcher


class FakeMCPClient:
    def __init__(self, config=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call_tool(self, name, arguments):
        FakeMCPClient.calls.append((name, arguments))
        if name == "get_positions":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.positions}
        if name == "get_option_snapshot":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.snapshots}
        return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {}}

    calls = []
    positions = []
    snapshots = {}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    FakeMCPClient.calls = []
    FakeMCPClient.positions = []
    FakeMCPClient.snapshots = {}
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)
    yield


def _position(symbol, qty, side):
    return {"symbol": symbol, "qty": str(qty), "side": side}


def _snapshot(delta, theta, vega, gamma=0.05):
    return {"greeks": {"delta": delta, "theta": theta, "vega": vega, "gamma": gamma}}


@pytest.mark.asyncio
async def test_no_option_positions_returns_zeroed_result():
    FakeMCPClient.positions = []
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_portfolio_greeks(0.4)
    parsed = json.loads(result)
    assert parsed["positions_included"] == 0
    assert parsed["portfolio_totals"] == {"delta": 0.0, "theta": 0.0, "vega": 0.0, "gamma": 0.0}
    assert parsed["by_underlying"] == []


@pytest.mark.asyncio
async def test_single_long_position_aggregates_correctly():
    FakeMCPClient.positions = [_position("QQQ260902C00716000", 10, "long")]
    FakeMCPClient.snapshots = {"QQQ260902C00716000": _snapshot(delta=0.45, theta=-0.10, vega=0.12)}

    dispatcher = ToolDispatcher()
    result = await dispatcher._get_portfolio_greeks(0.4)
    parsed = json.loads(result)

    # 10 contracts * 100 multiplier * 0.45 delta = 450
    assert parsed["portfolio_totals"]["delta"] == 450.0
    assert parsed["portfolio_totals"]["theta"] == -100.0
    assert parsed["portfolio_totals"]["vega"] == 120.0
    assert parsed["positions_included"] == 1


@pytest.mark.asyncio
async def test_short_position_sign_flips_exposure():
    FakeMCPClient.positions = [_position("QQQ260902C00720000", 10, "short")]
    FakeMCPClient.snapshots = {"QQQ260902C00720000": _snapshot(delta=0.30, theta=-0.08, vega=0.09)}

    dispatcher = ToolDispatcher()
    result = await dispatcher._get_portfolio_greeks(0.4)
    parsed = json.loads(result)

    # Short leg: signed_qty negative, so exposure flips sign from the raw greek
    assert parsed["portfolio_totals"]["delta"] == -300.0
    assert parsed["portfolio_totals"]["theta"] == 80.0


@pytest.mark.asyncio
async def test_concentration_warning_fires_for_dominant_underlying():
    """Reproduces the actual scenario the agent flagged: several NVDA
    spreads concentrating delta risk that isn't visible position-by-position."""
    FakeMCPClient.positions = [
        _position("NVDA260918C00180000", 20, "long"),
        _position("NVDA260918C00185000", 20, "short"),
        _position("NVDA260918C00190000", 15, "long"),
        _position("SPY260911C00570000", 5, "long"),
    ]
    FakeMCPClient.snapshots = {
        "NVDA260918C00180000": _snapshot(delta=0.60, theta=-0.05, vega=0.20),
        "NVDA260918C00185000": _snapshot(delta=0.40, theta=-0.04, vega=0.18),
        "NVDA260918C00190000": _snapshot(delta=0.35, theta=-0.03, vega=0.15),
        "SPY260911C00570000": _snapshot(delta=0.20, theta=-0.02, vega=0.10),
    }

    dispatcher = ToolDispatcher()
    result = await dispatcher._get_portfolio_greeks(0.4)
    parsed = json.loads(result)

    by_underlying = {row["underlying"]: row for row in parsed["by_underlying"]}
    assert "NVDA" in by_underlying
    assert by_underlying["NVDA"]["positions"] == 3

    warned = {w["underlying"] for w in parsed["concentration_warnings"]}
    assert "NVDA" in warned
    nvda_warning = next(w for w in parsed["concentration_warnings"] if w["underlying"] == "NVDA")
    assert nvda_warning["share_of_portfolio_abs_delta"] > 0.4


@pytest.mark.asyncio
async def test_missing_snapshot_greeks_excluded_not_estimated():
    FakeMCPClient.positions = [
        _position("QQQ260902C00716000", 10, "long"),
        _position("ILLIQUID260902C00500000", 5, "long"),
    ]
    # No snapshot entry at all for the illiquid symbol
    FakeMCPClient.snapshots = {"QQQ260902C00716000": _snapshot(delta=0.5, theta=-0.1, vega=0.1)}

    dispatcher = ToolDispatcher()
    result = await dispatcher._get_portfolio_greeks(0.4)
    parsed = json.loads(result)

    assert "ILLIQUID260902C00500000" in parsed["positions_missing_greeks"]
    assert parsed["positions_included"] == 1
    # Only the QQQ leg contributes: 10 * 100 * 0.5 = 500
    assert parsed["portfolio_totals"]["delta"] == 500.0


@pytest.mark.asyncio
async def test_stock_positions_without_occ_symbols_are_skipped():
    FakeMCPClient.positions = [
        _position("AAPL", 100, "long"),  # plain stock, no Greeks applicable
        _position("QQQ260902C00716000", 10, "long"),
    ]
    FakeMCPClient.snapshots = {"QQQ260902C00716000": _snapshot(delta=0.5, theta=-0.1, vega=0.1)}

    dispatcher = ToolDispatcher()
    result = await dispatcher._get_portfolio_greeks(0.4)
    parsed = json.loads(result)

    assert parsed["positions_included"] == 1
    assert "AAPL" not in [row["underlying"] for row in parsed["by_underlying"]]
