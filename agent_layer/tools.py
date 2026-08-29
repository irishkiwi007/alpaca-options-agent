"""
Defines the tools Claude can call during an autonomous decision cycle,
and dispatches each tool call to the underlying Alpaca MCP client.

The critical design point: place_spread_order — the only tool that can
actually risk money — runs both hard backstops (risk/hard_backstops.py)
BEFORE calling Alpaca. If either fails, the tool returns a clear
rejection to Claude instead of executing, and nothing is sent to
Alpaca. Every other tool is read-only or account-management and
carries no backstop because it can't place risk.
"""
import json
from datetime import date

from execution.mcp_client import AlpacaMCPClient
from risk.hard_backstops import check_defined_risk, check_position_sizing
from execution.trade_logger import log_event
from config import CONFIG

TOOL_SCHEMAS = [
    {
        "name": "get_account_info",
        "description": "Get current account equity, buying power, and options buying power.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_positions",
        "description": "Get all currently open positions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_stock_quote",
        "description": "Get the latest quote for a stock/ETF symbol.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_stock_bars",
        "description": "Get recent intraday bars for a symbol, for trend/momentum context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "minutes": {"type": "integer", "description": "How many minutes of history to fetch"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_option_chain",
        "description": "Get the option chain (strikes, deltas, bid/ask) for an underlying, optionally filtered to a specific expiration date (YYYY-MM-DD). Use today's date for 0DTE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "underlying": {"type": "string"},
                "expiration": {"type": "string", "description": "ISO date, e.g. 2026-08-29. Omit for all expirations."},
            },
            "required": ["underlying"],
        },
    },
    {
        "name": "place_spread_order",
        "description": (
            "Place a two-leg options spread order. This is the ONLY way to open a new position — "
            "naked single-leg orders are not available as a tool, by design. Both legs are required, "
            "on opposite sides (one buy, one sell), which structurally caps the position's maximum loss. "
            "The order will be rejected with an explanation if it fails either hard backstop: "
            "not being a genuine defined-risk spread, or risking more than 15% of account equity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "underlying": {"type": "string"},
                "buy_symbol": {"type": "string", "description": "OCC option symbol for the leg to buy"},
                "sell_symbol": {"type": "string", "description": "OCC option symbol for the leg to sell"},
                "contracts": {"type": "integer"},
                "limit_price": {"type": "number", "description": "Net limit price for the spread; negative for net credit, positive for net debit"},
                "max_loss_per_contract": {"type": "number", "description": "Your calculated worst-case loss for ONE contract of this spread, in dollars (e.g. spread width minus credit, times 100)"},
                "rationale": {"type": "string", "description": "Your reasoning for this specific trade"},
            },
            "required": ["underlying", "buy_symbol", "sell_symbol", "contracts", "limit_price", "max_loss_per_contract", "rationale"],
        },
    },
    {
        "name": "close_position",
        "description": "Close a specific open position by its symbol.",
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "close_all_positions",
        "description": "Close ALL open positions immediately. Use for emergency de-risking, not routine exits.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_activity_log",
        "description": "Read recent logged events from this agent's own history — past trades, reasoning, and outcomes — to inform self-assessment and strategy adjustment.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Number of recent events to retrieve, default 100"}},
        },
    },
]


class ToolDispatcher:
    def __init__(self, config=CONFIG):
        self.config = config

    async def dispatch(self, tool_name: str, tool_input: dict) -> str:
        """Returns a JSON string result to feed back to Claude as tool_result content."""
        try:
            if tool_name == "get_account_info":
                return await self._get_account_info()
            elif tool_name == "get_positions":
                return await self._get_positions()
            elif tool_name == "get_stock_quote":
                return await self._get_stock_quote(tool_input["symbol"])
            elif tool_name == "get_stock_bars":
                return await self._get_stock_bars(tool_input["symbol"], tool_input.get("minutes", 60))
            elif tool_name == "get_option_chain":
                return await self._get_option_chain(tool_input["underlying"], tool_input.get("expiration"))
            elif tool_name == "place_spread_order":
                return await self._place_spread_order(tool_input)
            elif tool_name == "close_position":
                return await self._close_position(tool_input["symbol"])
            elif tool_name == "close_all_positions":
                return await self._close_all_positions()
            elif tool_name == "get_recent_activity_log":
                return self._get_recent_activity_log(tool_input.get("limit", 100))
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            log_event("tool_dispatch_error", {"tool": tool_name, "input": tool_input, "error": str(e)})
            return json.dumps({"error": f"Tool '{tool_name}' failed: {str(e)}"})

    async def _get_account_info(self) -> str:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_account_info", {})
        return json.dumps(result)

    async def _get_positions(self) -> str:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_all_positions", {})
        return json.dumps(result)

    async def _get_stock_quote(self, symbol: str) -> str:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_stock_latest_quote", {"symbols": symbol})
        return json.dumps(result)

    async def _get_stock_bars(self, symbol: str, minutes: int) -> str:
        from datetime import datetime, timedelta, timezone
        start = (datetime.now(timezone.utc) - timedelta(minutes=minutes * 2)).isoformat()
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_stock_bars", {"symbols": symbol, "timeframe": "1Min", "start": start})
        return json.dumps(result)

    async def _get_option_chain(self, underlying: str, expiration: str = None) -> str:
        params = {"underlying_symbol": underlying}
        if expiration:
            params["expiration_date"] = expiration
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_option_chain", params)
        return json.dumps(result)

    async def _place_spread_order(self, tool_input: dict) -> str:
        underlying = tool_input["underlying"]
        buy_symbol = tool_input["buy_symbol"]
        sell_symbol = tool_input["sell_symbol"]
        contracts = int(tool_input["contracts"])
        limit_price = float(tool_input["limit_price"])
        max_loss_per_contract = float(tool_input["max_loss_per_contract"])
        rationale = tool_input["rationale"]

        # --- Hard backstop 1: defined risk only ---
        risk_check = check_defined_risk(sell_symbol, buy_symbol, short_side="sell", long_side="buy")
        if not risk_check.approved:
            log_event("backstop_rejected", {"backstop": "defined_risk", "reason": risk_check.reason, "input": tool_input})
            return json.dumps({"rejected": True, "backstop": "defined_risk", "reason": risk_check.reason})

        # --- Hard backstop 2: per-trade sizing cap ---
        async with AlpacaMCPClient(self.config) as mcp:
            account = await mcp.call_tool("get_account_info", {})
        equity = float(account.get("equity", 0))

        sizing_check = check_position_sizing(max_loss_per_contract, contracts, equity)
        if not sizing_check.approved:
            log_event("backstop_rejected", {"backstop": "position_sizing", "reason": sizing_check.reason, "input": tool_input})
            return json.dumps({"rejected": True, "backstop": "position_sizing", "reason": sizing_check.reason})

        # Both backstops passed — place the order.
        legs = [
            {"symbol": sell_symbol, "side": "sell", "ratio_qty": "1", "position_intent": "sell_to_open"},
            {"symbol": buy_symbol, "side": "buy", "ratio_qty": "1", "position_intent": "buy_to_open"},
        ]
        log_event("agent_order_submit", {
            "underlying": underlying,
            "sell_symbol": sell_symbol,
            "buy_symbol": buy_symbol,
            "contracts": contracts,
            "limit_price": limit_price,
            "max_loss_per_contract": max_loss_per_contract,
            "rationale": rationale,
        })
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("place_option_order", {
                "qty": str(contracts),
                "type": "limit",
                "time_in_force": "day",
                "order_class": "mleg",
                "legs": legs,
                "limit_price": str(limit_price),
            })
        log_event("agent_order_response", {"result": result})
        return json.dumps({"rejected": False, "order_result": result})

    async def _close_position(self, symbol: str) -> str:
        log_event("agent_close_position", {"symbol": symbol})
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("close_position", {"symbol_or_asset_id": symbol})
        return json.dumps(result)

    async def _close_all_positions(self) -> str:
        log_event("agent_close_all_positions", {})
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("close_all_positions", {"cancel_orders": True})
        return json.dumps(result)

    def _get_recent_activity_log(self, limit: int) -> str:
        from execution.trade_logger import read_events
        events = read_events(limit=limit)
        return json.dumps(events)
