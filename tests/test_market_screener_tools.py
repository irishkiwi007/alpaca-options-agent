"""
Tests for get_most_active_stocks and get_market_movers — added because
telling the agent "you can consider other names" without giving it an
actual discovery mechanism resulted in it never doing so in practice
(confirmed: every real cycle only ever checked SPY/QQQ). These tools
give it a genuine way to find candidates, not just permission to.
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agent_layer.tools import ToolDispatcher


class FakeMCPClient:
    calls = []

    def __init__(self, config=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def call_tool(self, name, arguments):
        FakeMCPClient.calls.append((name, arguments))
        if name == "get_most_active_stocks":
            return {"_alpaca_mcp_security": {}, "data": {"most_actives": [{"symbol": "NVDA", "volume": 50000000}]}}
        if name == "get_market_movers":
            return {"_alpaca_mcp_security": {}, "data": {"gainers": [{"symbol": "TSLA"}], "losers": [{"symbol": "META"}]}}
        return {"_alpaca_mcp_security": {}, "data": {}}


@pytest.fixture(autouse=True)
def reset_calls():
    FakeMCPClient.calls = []
    yield


@pytest.mark.asyncio
async def test_most_active_stocks_passes_correct_params(monkeypatch):
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)

    dispatcher = ToolDispatcher()
    result = await dispatcher.dispatch("get_most_active_stocks", {"by": "trades", "top": 15})

    calls = [c for c in FakeMCPClient.calls if c[0] == "get_most_active_stocks"]
    assert len(calls) == 1
    assert calls[0][1] == {"by": "trades", "top": 15}
    parsed = json.loads(result)
    assert "NVDA" in json.dumps(parsed)


@pytest.mark.asyncio
async def test_most_active_stocks_defaults(monkeypatch):
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)

    dispatcher = ToolDispatcher()
    await dispatcher.dispatch("get_most_active_stocks", {})

    calls = [c for c in FakeMCPClient.calls if c[0] == "get_most_active_stocks"]
    assert calls[0][1] == {"by": "volume", "top": 10}


@pytest.mark.asyncio
async def test_market_movers_passes_market_type_stocks(monkeypatch):
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)

    dispatcher = ToolDispatcher()
    result = await dispatcher.dispatch("get_market_movers", {"top": 20})

    calls = [c for c in FakeMCPClient.calls if c[0] == "get_market_movers"]
    assert len(calls) == 1
    assert calls[0][1] == {"market_type": "stocks", "top": 20}
    parsed = json.loads(result)
    assert "TSLA" in json.dumps(parsed)


@pytest.mark.asyncio
async def test_market_movers_default_top(monkeypatch):
    import agent_layer.tools as tools_module
    monkeypatch.setattr(tools_module, "AlpacaMCPClient", FakeMCPClient)

    dispatcher = ToolDispatcher()
    await dispatcher.dispatch("get_market_movers", {})

    calls = [c for c in FakeMCPClient.calls if c[0] == "get_market_movers"]
    assert calls[0][1] == {"market_type": "stocks", "top": 10}


def test_both_tools_registered_in_schema():
    from agent_layer.tools import TOOL_SCHEMAS
    names = [t["name"] for t in TOOL_SCHEMAS]
    assert "get_most_active_stocks" in names
    assert "get_market_movers" in names
