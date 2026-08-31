"""
Regression tests for a real incident: place_spread_order previously
hardcoded position_intent to "_to_open" always, with no way to close.
An agent attempting to reduce/close a position had its order silently
reinterpreted as opening an ADDITIONAL position, doubling size
unintentionally (confirmed against real fill data from the live
account on 2026-08-31). Fixed by requiring an explicit 'action' field
and deriving intent directly from it — no inference, no ambiguity.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_layer.tools import ToolDispatcher


class FakeMCPClient:
    """Real async context manager (not a MagicMock dunder-method guess)
    so call_tool's return sequence is deterministic and reliable."""
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
        return {"_alpaca_mcp_security": {"trust": "untrusted_tool_output"}, "data": {}}

    calls = []


@pytest.fixture(autouse=True)
def reset_calls():
    FakeMCPClient.calls = []
    yield


@pytest.mark.asyncio
async def test_action_open_produces_to_open_intents(monkeypatch):
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)

    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "open",
        "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00716000",
        "sell_symbol": "QQQ260902C00720000",
        "contracts": 40,
        "limit_price": 1.76,
        "max_loss_per_contract": 176.0,
        "rationale": "test open",
    })

    place_calls = [c for c in FakeMCPClient.calls if c[0] == "place_option_order"]
    assert len(place_calls) == 1, f"Expected exactly one place_option_order call, got {len(place_calls)}"
    legs = place_calls[0][1]["legs"]
    intents = {leg["symbol"]: leg["position_intent"] for leg in legs}
    assert intents["QQQ260902C00720000"] == "sell_to_open"
    assert intents["QQQ260902C00716000"] == "buy_to_open"

    parsed = json.loads(result)
    assert parsed["rejected"] is False


@pytest.mark.asyncio
async def test_action_close_produces_to_close_intents_not_to_open(monkeypatch):
    """The exact bug: closing must never produce '_to_open' intents."""
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)

    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "close",
        "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00720000",   # buying back the short leg
        "sell_symbol": "QQQ260902C00716000",  # selling off the long leg
        "contracts": 80,
        "limit_price": 1.51,
        "max_loss_per_contract": 0,
        "rationale": "test close",
    })

    place_calls = [c for c in FakeMCPClient.calls if c[0] == "place_option_order"]
    assert len(place_calls) == 1, f"Expected exactly one place_option_order call, got {len(place_calls)}"
    legs = place_calls[0][1]["legs"]
    intents = {leg["symbol"]: leg["position_intent"] for leg in legs}
    assert intents["QQQ260902C00720000"] == "buy_to_close"
    assert intents["QQQ260902C00716000"] == "sell_to_close"
    # Explicitly assert neither leg says "_to_open" — the exact failure mode
    assert "open" not in intents["QQQ260902C00720000"]
    assert "open" not in intents["QQQ260902C00716000"]

    parsed = json.loads(result)
    assert parsed["rejected"] is False


@pytest.mark.asyncio
async def test_missing_action_is_rejected():
    dispatcher = ToolDispatcher()
    result = await dispatcher._place_spread_order({
        "action": "not_a_real_action",
        "underlying": "QQQ",
        "buy_symbol": "QQQ260902C00720000",
        "sell_symbol": "QQQ260902C00716000",
        "contracts": 1,
        "limit_price": 1.0,
        "max_loss_per_contract": 100.0,
        "rationale": "test",
    })
    parsed = json.loads(result)
    assert parsed["rejected"] is True
    assert parsed["backstop"] == "invalid_action"
