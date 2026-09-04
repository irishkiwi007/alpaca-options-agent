"""
Tests for three related self-assessed gaps:
1. "Real-time order fill quality" — I reason about mid-prices but often
   don't know actual fill prices until after the fact.
3. "Why orders didn't fill" — the META close attempt at -$3.75 that
   didn't fill; I had to guess the market was offering $3.29-3.49.
4. "Intraday sector/macro context" — I'm inferring broad market tone
   from QQQ price moves but don't have real-time access to VIX level.
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
        if name == "get_account_info":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {"equity": 100000}}
        if name == "place_option_order":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.order_response}
        if name == "get_option_snapshot":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.snapshots}
        if name == "get_order_by_id":
            order_id = arguments.get("order_id")
            order = FakeMCPClient.orders_by_id.get(order_id)
            if order is None:
                raise Exception(f"order not found: {order_id}")
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": order}
        if name == "get_index_latest_values":
            return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": FakeMCPClient.index_values}
        return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {}}

    calls = []
    order_response = {"id": "order123", "status": "accepted"}
    snapshots = {}
    orders_by_id = {}
    index_values = {}


@pytest.fixture(autouse=True)
def reset(monkeypatch, tmp_path):
    FakeMCPClient.calls = []
    FakeMCPClient.order_response = {"id": "order123", "status": "accepted"}
    FakeMCPClient.snapshots = {}
    FakeMCPClient.orders_by_id = {}
    FakeMCPClient.index_values = {}
    import execution.trade_logger as trade_logger_module
    monkeypatch.setattr(trade_logger_module, "LOG_PATH", str(tmp_path / "events.jsonl"))
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)
    yield


# ---------------------------------------------------------------------
# Gaps 1 & 3: NBBO snapshot at order submission
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nbbo_snapshot_included_in_response_on_open():
    FakeMCPClient.snapshots = {
        "QQQ260902C00716000": {"latestQuote": {"bp": 2.10, "ap": 2.30}},
        "QQQ260902C00720000": {"latestQuote": {"bp": 0.80, "ap": 0.95}},
    }
    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "open", "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00716000", "sell_symbol": "QQQ260902C00720000",
        "contracts": 10, "limit_price": 1.20, "max_loss_per_contract": 300.0,
        "rationale": "test", "setup_type": "momentum_breakout",
    })
    parsed = json.loads(result)
    assert parsed["rejected"] is False
    nbbo = parsed["nbbo_at_submission"]
    assert nbbo["QQQ260902C00716000"] == {"bid": 2.10, "ask": 2.30}
    assert nbbo["QQQ260902C00720000"] == {"bid": 0.80, "ask": 0.95}


@pytest.mark.asyncio
async def test_nbbo_snapshot_failure_does_not_block_order():
    """The snapshot call is best-effort — if it errors, the order still
    goes through rather than being blocked by a data-quality nicety."""
    class BrokenSnapshotClient(FakeMCPClient):
        async def call_tool(self, name, arguments):
            if name == "get_option_snapshot":
                raise Exception("snapshot service unavailable")
            return await super().call_tool(name, arguments)

    import agent_layer.tools as tools_module
    import pytest as _pytest
    from unittest.mock import patch
    with patch.object(tools_module, "AlpacaMCPClient", BrokenSnapshotClient):
        dispatcher = ToolDispatcher()
        result = await dispatcher._place_spread_order({
            "action": "open", "underlying": "QQQ",
            "buy_symbol": "QQQ260902C00716000", "sell_symbol": "QQQ260902C00720000",
            "contracts": 10, "limit_price": 1.20, "max_loss_per_contract": 300.0,
            "rationale": "test", "setup_type": "momentum_breakout",
        })
    parsed = json.loads(result)
    assert parsed["rejected"] is False
    assert "error" in parsed["nbbo_at_submission"]


@pytest.mark.asyncio
async def test_order_id_and_status_surfaced_in_note():
    FakeMCPClient.order_response = {"id": "order999", "status": "accepted"}
    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "open", "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00716000", "sell_symbol": "QQQ260902C00720000",
        "contracts": 10, "limit_price": 1.20, "max_loss_per_contract": 300.0,
        "rationale": "test", "setup_type": "momentum_breakout",
    })
    parsed = json.loads(result)
    assert "order999" in parsed["note"]
    assert "get_order_fill_status" in parsed["note"]


# ---------------------------------------------------------------------
# Gaps 1 & 3: get_order_fill_status
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_order_fill_status_returns_real_fill_data():
    FakeMCPClient.orders_by_id = {
        "order123": {
            "id": "order123", "status": "filled", "filled_qty": "10",
            "filled_avg_price": "1.15", "submitted_at": "2026-09-04T14:00:00Z",
            "filled_at": "2026-09-04T14:00:03Z",
            "legs": [
                {"symbol": "QQQ260902C00716000", "side": "buy", "position_intent": "buy_to_open", "status": "filled", "filled_qty": "10", "filled_avg_price": "2.20"},
                {"symbol": "QQQ260902C00720000", "side": "sell", "position_intent": "sell_to_open", "status": "filled", "filled_qty": "10", "filled_avg_price": "1.05"},
            ],
        }
    }
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_order_fill_status(["order123"])
    parsed = json.loads(result)
    order = parsed["orders"][0]
    assert order["status"] == "filled"
    assert order["filled_avg_price"] == "1.15"
    assert len(order["legs"]) == 2
    assert order["legs"][0]["filled_avg_price"] == "2.20"


@pytest.mark.asyncio
async def test_get_order_fill_status_handles_unfilled_order():
    FakeMCPClient.orders_by_id = {
        "order456": {"id": "order456", "status": "new", "filled_qty": "0", "filled_avg_price": None, "legs": []}
    }
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_order_fill_status(["order456"])
    parsed = json.loads(result)
    assert parsed["orders"][0]["status"] == "new"


@pytest.mark.asyncio
async def test_get_order_fill_status_handles_unknown_order_gracefully():
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_order_fill_status(["order_does_not_exist"])
    parsed = json.loads(result)
    assert "error" in parsed["orders"][0]


@pytest.mark.asyncio
async def test_get_order_fill_status_handles_multiple_ids():
    FakeMCPClient.orders_by_id = {
        "order1": {"id": "order1", "status": "filled", "filled_qty": "5", "filled_avg_price": "1.00", "legs": []},
        "order2": {"id": "order2", "status": "canceled", "filled_qty": "0", "filled_avg_price": None, "legs": []},
    }
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_order_fill_status(["order1", "order2"])
    parsed = json.loads(result)
    statuses = {o["order_id"]: o["status"] for o in parsed["orders"]}
    assert statuses == {"order1": "filled", "order2": "canceled"}


# ---------------------------------------------------------------------
# Gap 4: get_market_context
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_market_context_low_vix_regime():
    FakeMCPClient.index_values = {"VIX": {"value": 12.5}, "SPX": {"value": 5900.0}}
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_market_context()
    parsed = json.loads(result)
    assert parsed["vix"] == 12.5
    assert parsed["spx"] == 5900.0
    assert parsed["vix_regime"] == "low"


@pytest.mark.asyncio
async def test_market_context_elevated_vix_regime():
    FakeMCPClient.index_values = {"VIX": {"value": 24.0}, "SPX": {"value": 5700.0}}
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_market_context()
    parsed = json.loads(result)
    assert parsed["vix_regime"] == "elevated"


@pytest.mark.asyncio
async def test_market_context_high_vix_regime():
    FakeMCPClient.index_values = {"VIX": {"value": 35.0}, "SPX": {"value": 5200.0}}
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_market_context()
    parsed = json.loads(result)
    assert parsed["vix_regime"] == "high"


@pytest.mark.asyncio
async def test_market_context_missing_vix_returns_unknown_regime():
    FakeMCPClient.index_values = {}
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_market_context()
    parsed = json.loads(result)
    assert parsed["vix"] is None
    assert parsed["vix_regime"] == "unknown"


@pytest.mark.asyncio
async def test_market_context_handles_plain_numeric_values():
    """Some snapshot response shapes might return bare numbers rather
    than {"value": ...} dicts — handle both."""
    FakeMCPClient.index_values = {"VIX": 18.0, "SPX": 5850.0}
    dispatcher = ToolDispatcher()
    result = await dispatcher._get_market_context()
    parsed = json.loads(result)
    assert parsed["vix"] == 18.0
    assert parsed["vix_regime"] == "normal"
