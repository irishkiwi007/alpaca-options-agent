"""
Market data access for the fast layer, routed through Alpaca's MCP
server (execution/mcp_client.py) for consistency with the execution
layer — both trading and data now go through the same MCP tool
surface, which is what the hackathon's "use the MCP server" requirement
is asking for.

Kept in fast_layer/ rather than execution/ to preserve the read-only /
write separation: nothing in this module can place an order, even
though it shares the same underlying MCP client class.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from execution.mcp_client import AlpacaMCPClient, unwrap_data
from config import CONFIG


class MarketData:
    def __init__(self, config=CONFIG):
        self.config = config

    async def recent_bars(self, symbol: str, minutes: int = 60, timeframe: str = "1Min") -> list:
        start = (datetime.now(timezone.utc) - timedelta(minutes=minutes * 2)).isoformat()
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_stock_bars", {
                "symbols": symbol,
                "timeframe": timeframe,
                "start": start,
            })
        data = unwrap_data(result)
        bars = data.get("bars", {}).get(symbol, []) if isinstance(data, dict) else []
        return bars

    async def latest_quote(self, symbol: str) -> dict:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_stock_latest_quote", {"symbols": symbol})
        data = unwrap_data(result)
        return data.get("quotes", {}).get(symbol, {}) if isinstance(data, dict) else {}

    async def option_chain(self, underlying: str, expiration: Optional[str] = None) -> dict:
        """
        Return the current option chain for an underlying, formatted as
        {option_symbol: {"type": ..., "strike": ..., "delta": ..., "bid": ..., "ask": ...}}
        to match what fast_layer.signal_generator expects. If expiration
        is None, callers filtering for 0DTE should filter to today's date.
        """
        params = {"underlying_symbol": underlying}
        if expiration:
            params["expiration_date"] = expiration
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_option_chain", params)

        chain = {}
        data = unwrap_data(result)
        raw_snapshots = data.get("snapshots", {}) if isinstance(data, dict) else {}
        for symbol, snap in raw_snapshots.items():
            greeks = snap.get("greeksTrade", {}) or snap.get("greeks", {}) or {}
            quote = snap.get("latestQuote", {}) or {}
            # OCC symbols encode type as 'C' or 'P' at a fixed offset; the
            # MCP snapshot response also typically includes it explicitly.
            option_type = "call" if symbol[-9] == "C" else "put"
            strike = int(symbol[-8:]) / 1000.0
            chain[symbol] = {
                "type": option_type,
                "strike": strike,
                "delta": greeks.get("delta", 0.0),
                "bid": quote.get("bidPrice", 0.0),
                "ask": quote.get("askPrice", 0.0),
            }
        return chain

    async def option_quote(self, option_symbol: str) -> dict:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_option_latest_quote", {"symbols": option_symbol})
        data = unwrap_data(result)
        return data.get("quotes", {}).get(option_symbol, {}) if isinstance(data, dict) else {}

    async def vix_snapshot(self) -> dict:
        """
        VIX spot/9D/3M aren't standard Alpaca-tradable symbols on every
        plan; this pulls whichever VIX-family tickers are available and
        callers should treat missing values defensively — see
        fast_layer.signal_generator's handling of zero/None inputs.
        """
        symbols = "VIX,VIX9D,VIX3M"
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_stock_latest_quote", {"symbols": symbols})
        data = unwrap_data(result)
        quotes = data.get("quotes", {}) if isinstance(data, dict) else {}
        return {
            "vix_spot": quotes.get("VIX", {}).get("bidPrice", 0.0),
            "vix9d": quotes.get("VIX9D", {}).get("bidPrice", 0.0),
            "vix3m": quotes.get("VIX3M", {}).get("bidPrice", 0.0),
        }
