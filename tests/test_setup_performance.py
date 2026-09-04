"""
Tests for setup_type tagging on place_spread_order (item 5 of the
agent's own self-assessed limitations: "I don't have a structured
record of which of my setups actually perform well vs. which ones I
just feel confident about in the moment") and the get_setup_performance
tool that reads that tagging back out, aggregated against real
reconstructed trades.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_layer.tools import ToolDispatcher


class FakeMCPClient:
    """Real async context manager, deterministic queued responses per
    tool name — same pattern as test_place_spread_order_action.py."""
    def __init__(self, config=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call_tool(self, name, arguments):
        FakeMCPClient.calls.append((name, arguments))
        if name == "get_account_info":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {"equity": 100000}}
        if name == "place_option_order":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {"id": "order123", "status": "accepted"}}
        if name == "get_orders":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.orders}
        if name == "get_positions":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.positions}
        if name == "get_account_activities":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.activities}
        return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {}}

    calls = []
    orders = []
    positions = []
    activities = []


@pytest.fixture(autouse=True)
def reset_calls(monkeypatch, tmp_path):
    FakeMCPClient.calls = []
    FakeMCPClient.orders = []
    FakeMCPClient.positions = []
    FakeMCPClient.activities = []
    # Point the event log at a throwaway file per test so tests don't
    # read/pollute the real repo's logs/events.jsonl or leak state
    # between tests.
    import execution.trade_logger as trade_logger_module
    fake_log = tmp_path / "events.jsonl"
    monkeypatch.setattr(trade_logger_module, "LOG_PATH", str(fake_log))
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)
    yield


def _open_order(long_symbol, long_price, short_symbol, short_price, contracts, ts):
    """Simulates a filled opening multi-leg order as Alpaca's get_orders
    would actually return it: one buy_to_open leg (long_symbol) and one
    sell_to_open leg (short_symbol), with real per-leg fill prices."""
    return {
        "status": "filled",
        "order_class": "mleg",
        "filled_at": ts,
        "legs": [
            {"symbol": short_symbol, "side": "sell", "qty": str(contracts), "position_intent": "sell_to_open", "filled_avg_price": str(short_price)},
            {"symbol": long_symbol, "side": "buy", "qty": str(contracts), "position_intent": "buy_to_open", "filled_avg_price": str(long_price)},
        ],
    }


def _close_order(long_symbol, long_close_price, short_symbol, short_close_price, contracts, ts):
    """Closes the SAME two symbols opened by _open_order: the
    originally-long symbol is sold to close, the originally-short
    symbol is bought to close — the correct round-trip pairing."""
    return {
        "status": "filled",
        "order_class": "mleg",
        "filled_at": ts,
        "legs": [
            {"symbol": long_symbol, "side": "sell", "qty": str(contracts), "position_intent": "sell_to_close", "filled_avg_price": str(long_close_price)},
            {"symbol": short_symbol, "side": "buy", "qty": str(contracts), "position_intent": "buy_to_close", "filled_avg_price": str(short_close_price)},
        ],
    }


@pytest.mark.asyncio
async def test_open_without_setup_type_is_rejected():
    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "open",
        "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00716000",
        "sell_symbol": "QQQ260902C00720000",
        "contracts": 10,
        "limit_price": 1.0,
        "max_loss_per_contract": 100.0,
        "rationale": "test open, no setup_type",
    })
    parsed = json.loads(result)
    assert parsed["rejected"] is True
    assert parsed["backstop"] == "missing_setup_type"

    place_calls = [c for c in FakeMCPClient.calls if c[0] == "place_option_order"]
    assert len(place_calls) == 0, "Order must not reach Alpaca when setup_type is missing on an open"


@pytest.mark.asyncio
async def test_open_with_setup_type_is_accepted_and_logged():
    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "open",
        "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00716000",
        "sell_symbol": "QQQ260902C00720000",
        "contracts": 10,
        "limit_price": 1.0,
        "max_loss_per_contract": 100.0,
        "rationale": "test open, tagged",
        "setup_type": "momentum_breakout",
    })
    parsed = json.loads(result)
    assert parsed["rejected"] is False

    setup_map = dispatcher._setup_type_map()
    key = frozenset({"QQQ260902C00716000", "QQQ260902C00720000"})
    assert setup_map[key] == "momentum_breakout"


@pytest.mark.asyncio
async def test_close_does_not_require_setup_type():
    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "close",
        "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00720000",
        "sell_symbol": "QQQ260902C00716000",
        "contracts": 10,
        "limit_price": 0.5,
        "max_loss_per_contract": 0,
        "rationale": "test close, no setup_type needed",
    })
    parsed = json.loads(result)
    assert parsed["rejected"] is False


@pytest.mark.asyncio
async def test_get_setup_performance_aggregates_by_tag():
    dispatcher = ToolDispatcher()

    # Winning momentum_breakout trade: short leg decays a lot more than
    # the long leg costs — net profit.
    await dispatcher._place_spread_order({
        "action": "open", "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00720000", "sell_symbol": "QQQ260902C00716000",
        "contracts": 10, "limit_price": -0.80, "max_loss_per_contract": 300.0,
        "rationale": "momentum entry", "setup_type": "momentum_breakout",
    })
    # Losing mean_reversion trade: short leg spikes against us — net loss.
    await dispatcher._place_spread_order({
        "action": "open", "underlying": "SPY",
        "buy_symbol": "SPY260904C00445000", "sell_symbol": "SPY260904C00440000",
        "contracts": 5, "limit_price": -0.50, "max_loss_per_contract": 200.0,
        "rationale": "reversion entry", "setup_type": "mean_reversion",
    })

    FakeMCPClient.orders = [
        _open_order("QQQ260902C00720000", 0.20, "QQQ260902C00716000", 1.00, 10, "2026-09-01T14:00:00Z"),
        _close_order("QQQ260902C00720000", 0.05, "QQQ260902C00716000", 0.10, 10, "2026-09-01T15:30:00Z"),
        _open_order("SPY260904C00445000", 0.50, "SPY260904C00440000", 1.00, 5, "2026-09-02T14:00:00Z"),
        _close_order("SPY260904C00445000", 1.00, "SPY260904C00440000", 3.00, 5, "2026-09-02T15:00:00Z"),
    ]
    FakeMCPClient.positions = []
    FakeMCPClient.activities = []

    result = await dispatcher._get_setup_performance(min_trades=1)
    parsed = json.loads(result)

    by_setup = {row["setup_type"]: row for row in parsed["by_setup_type"]}
    assert "momentum_breakout" in by_setup
    assert "mean_reversion" in by_setup

    momentum = by_setup["momentum_breakout"]
    assert momentum["closed_trades"] == 1
    assert momentum["wins"] == 1
    assert momentum["losses"] == 0
    assert momentum["win_rate"] == 1.0
    assert momentum["total_pnl"] > 0

    reversion = by_setup["mean_reversion"]
    assert reversion["closed_trades"] == 1
    assert reversion["wins"] == 0
    assert reversion["losses"] == 1
    assert reversion["win_rate"] == 0.0
    assert reversion["total_pnl"] < 0


@pytest.mark.asyncio
async def test_untagged_trades_bucketed_separately_not_dropped():
    dispatcher = ToolDispatcher()
    # No place_spread_order call at all for this symbol pair — simulates
    # a trade placed before setup_type tagging existed, or a gap in the log.
    FakeMCPClient.orders = [
        _open_order("IWM260905C00220000", 0.10, "IWM260905C00218000", 0.60, 3, "2026-08-20T14:00:00Z"),
        _close_order("IWM260905C00220000", 0.02, "IWM260905C00218000", 0.10, 3, "2026-08-20T15:00:00Z"),
    ]
    FakeMCPClient.positions = []
    FakeMCPClient.activities = []

    result = await dispatcher._get_setup_performance(min_trades=1)
    parsed = json.loads(result)
    by_setup = {row["setup_type"]: row for row in parsed["by_setup_type"]}
    assert "untagged" in by_setup
    assert by_setup["untagged"]["closed_trades"] == 1


@pytest.mark.asyncio
async def test_min_trades_filter_omits_thin_setups():
    dispatcher = ToolDispatcher()
    await dispatcher._place_spread_order({
        "action": "open", "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00720000", "sell_symbol": "QQQ260902C00716000",
        "contracts": 10, "limit_price": -1.00, "max_loss_per_contract": 300.0,
        "rationale": "one-off setup", "setup_type": "rare_setup",
    })
    FakeMCPClient.orders = [
        _open_order("QQQ260902C00720000", 0.20, "QQQ260902C00716000", 1.00, 10, "2026-09-01T14:00:00Z"),
        _close_order("QQQ260902C00720000", 0.05, "QQQ260902C00716000", 0.10, 10, "2026-09-01T15:30:00Z"),
    ]
    FakeMCPClient.positions = []
    FakeMCPClient.activities = []

    result = await dispatcher._get_setup_performance(min_trades=5)
    parsed = json.loads(result)
    assert parsed["by_setup_type"] == []
    omitted = {row["setup_type"]: row["closed_trades"] for row in parsed["omitted_below_min_trades"]}
    assert omitted.get("rare_setup") == 1
