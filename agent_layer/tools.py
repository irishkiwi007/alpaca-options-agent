"""
Defines the tools Claude can call during an autonomous decision cycle,
and dispatches each tool call to the underlying Alpaca MCP client.

The critical design point: place_spread_order — the only tool that can
actually risk money — runs three hard backstops (risk/hard_backstops.py)
BEFORE calling Alpaca. If any fail, the tool returns a clear
rejection to Claude instead of executing, and nothing is sent to
Alpaca. Every other tool is read-only or account-management and
carries no backstop because it can't place risk.
"""
import json
from datetime import date

from execution.mcp_client import AlpacaMCPClient, unwrap_data
from risk.hard_backstops import check_defined_risk, check_position_sizing, check_spread_economics
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
            "Open OR close a two-leg options spread order — you must specify which via the "
            "required 'action' field. This is the ONLY way to place a multi-leg options order — "
            "naked single-leg orders are not available as a tool, by design. Both legs are required, "
            "on opposite sides (one buy, one sell), which structurally caps the position's maximum loss. "
            "IMPORTANT: to close an existing spread, use this same tool with action='close' and the "
            "correct buy/sell symbols for what you're doing in THIS order (e.g. if you originally "
            "bought the 716 call and sold the 720 call to open, closing means buy_symbol=the 720 call "
            "you're now buying back, sell_symbol=the 716 call you're now selling off). Do NOT call this "
            "with action='open' when your intent is to reduce or close a position — that will incorrectly "
            "open an ADDITIONAL position instead of closing the existing one. This exact mistake has "
            "happened before and doubled a position's size unintentionally. "
            "The order will be rejected with an explanation if it fails any hard backstop: "
            "not being a genuine defined-risk spread, the price paid/received not being economically "
            "sane relative to the spread's width, or risking more than 15% of account equity."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["open", "close"], "description": "Whether this order opens a new position or closes/reduces an existing one. Always required, never inferred."},
                "underlying": {"type": "string"},
                "buy_symbol": {"type": "string", "description": "OCC option symbol for the leg to buy in THIS order"},
                "sell_symbol": {"type": "string", "description": "OCC option symbol for the leg to sell in THIS order"},
                "contracts": {"type": "integer"},
                "limit_price": {"type": "number", "description": "Net limit price for the spread; negative for net credit, positive for net debit"},
                "max_loss_per_contract": {"type": "number", "description": "Your calculated worst-case loss for ONE contract of this spread, in dollars (e.g. spread width minus credit, times 100). For a close order, use the remaining max loss on the position being closed."},
                "rationale": {"type": "string", "description": "Your reasoning for this specific order, including why action is open vs close"},
            },
            "required": ["action", "underlying", "buy_symbol", "sell_symbol", "contracts", "limit_price", "max_loss_per_contract", "rationale"],
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
        "name": "get_most_active_stocks",
        "description": (
            "Screens the whole market for the most actively traded stocks right now, by volume or "
            "trade count. Use this to discover candidates beyond SPY/QQQ — this is the actual "
            "mechanism for looking at the broader liquid large-cap universe, not just a suggestion "
            "to do so. A stock showing up here is, by definition, currently liquid enough to trade well."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "by": {"type": "string", "enum": ["volume", "trades"], "description": "Ranking metric, default volume"},
                "top": {"type": "integer", "description": "How many to return, 1-100, default 10"},
            },
        },
    },
    {
        "name": "get_market_movers",
        "description": (
            "Returns today's top gainers and losers by percentage move, real-time. Use this alongside "
            "get_most_active_stocks to discover candidates with an actual catalyst or directional move "
            "happening right now, not just habitually checking the same one or two symbols every cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "description": "How many gainers and losers each to return, 1-50, default 10"},
            },
        },
    },
    {
        "name": "get_recent_activity_log",
        "description": "Read recent logged events from this agent's own history — past trades, reasoning, and outcomes — to inform self-assessment and strategy adjustment.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "Number of recent events to retrieve, default 100"}},
        },
    },
    {
        "name": "report_tooling_issue",
        "description": (
            "Report a suspected bug or unexpected behavior in one of YOUR OWN tools — not a market "
            "observation, a code/infrastructure problem. You cannot fix your own tools; you have no "
            "access to your own source code and no ability to modify it. What you CAN do is report the "
            "problem clearly and immediately, the moment you notice it, so a human can review and fix "
            "it quickly rather than only discovering it later by reading through trade history. Use this "
            "whenever a tool behaves in a way that doesn't match its description, produces a result you "
            "didn't expect given your input, or you find yourself having to work around something rather "
            "than use a tool as intended (e.g., using close_position leg-by-leg instead of a tool that "
            "should have handled it directly). Report as soon as you notice the issue, in the same cycle "
            "— don't wait."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["low", "medium", "high"], "description": "high = caused or risked an unintended trade/position change; medium = caused confusion or a workaround but no unintended action; low = cosmetic or informational"},
                "tool_name": {"type": "string", "description": "The tool you believe has the problem"},
                "what_you_tried": {"type": "string", "description": "What you called the tool to do, and with what inputs"},
                "what_happened": {"type": "string", "description": "What actually happened, including any resulting order/position changes"},
                "suspected_cause": {"type": "string", "description": "Your best guess at the root cause, if you have one — this is a hypothesis for the human to check, not a diagnosis you can verify yourself"},
            },
            "required": ["severity", "tool_name", "what_you_tried", "what_happened", "suspected_cause"],
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
            elif tool_name == "get_most_active_stocks":
                return await self._get_most_active_stocks(tool_input.get("by", "volume"), tool_input.get("top", 10))
            elif tool_name == "get_market_movers":
                return await self._get_market_movers(tool_input.get("top", 10))
            elif tool_name == "place_spread_order":
                return await self._place_spread_order(tool_input)
            elif tool_name == "close_position":
                return await self._close_position(tool_input["symbol"])
            elif tool_name == "close_all_positions":
                return await self._close_all_positions()
            elif tool_name == "get_recent_activity_log":
                return self._get_recent_activity_log(tool_input.get("limit", 100))
            elif tool_name == "report_tooling_issue":
                return self._report_tooling_issue(tool_input)
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

    def _looks_like_non_common_stock(self, symbol: str) -> bool:
        """
        Heuristic filter: on Nasdaq-style tickers, a 5th character of W/U/R
        typically denotes a warrant, unit, or rights offering — not the
        underlying common stock, and these essentially never have liquid
        (or any) listed options. Only applied to 5-character symbols
        specifically to avoid false-positives on legitimate short tickers
        that happen to end in those letters (e.g. 3-4 letter common tickers).
        Found necessary via live testing: get_market_movers surfaced
        "BRLSW" and "PASW" as top gainers, both warrants, not tradeable
        via options at all.
        """
        return len(symbol) == 5 and symbol[-1] in ("W", "U", "R")

    async def _get_most_active_stocks(self, by: str, top: int) -> str:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_most_active_stocks", {"by": by, "top": top})
        data = unwrap_data(result)
        actives = data.get("most_actives", []) if isinstance(data, dict) else []
        filtered = [a for a in actives if not self._looks_like_non_common_stock(a.get("symbol", ""))]
        removed = len(actives) - len(filtered)
        return json.dumps({
            "most_actives": filtered,
            "note": (
                f"Filtered out {removed} likely warrant/unit/rights symbols (not options-tradeable). "
                "Remaining results are still raw 'most active by share volume' data, which skews toward "
                "low-priced, high-turnover names — many won't have liquid options either. Cross-check "
                "against get_option_chain before treating any of these as a real candidate."
            ) if removed else "Note: raw 'most active by volume' data skews toward low-priced, high-turnover names — cross-check against get_option_chain before treating any as a real candidate.",
        })

    async def _get_market_movers(self, top: int) -> str:
        async with AlpacaMCPClient(self.config) as mcp:
            result = await mcp.call_tool("get_market_movers", {"market_type": "stocks", "top": top})
        data = unwrap_data(result)
        gainers = data.get("gainers", []) if isinstance(data, dict) else []
        losers = data.get("losers", []) if isinstance(data, dict) else []

        MIN_PRICE = 10.0  # excludes penny-stock noise; real optionable large-caps are essentially never this cheap

        def keep(item):
            symbol = item.get("symbol", "")
            price = float(item.get("price", 0) or 0)
            return price >= MIN_PRICE and not self._looks_like_non_common_stock(symbol)

        filtered_gainers = [g for g in gainers if keep(g)]
        filtered_losers = [l for l in losers if keep(l)]
        removed = (len(gainers) - len(filtered_gainers)) + (len(losers) - len(filtered_losers))

        return json.dumps({
            "gainers": filtered_gainers,
            "losers": filtered_losers,
            "note": (
                f"Filtered out {removed} symbols under ${MIN_PRICE:.0f} or that look like warrants/units/rights "
                "(speculative penny-stock moves that dominate raw % gainers/losers data and essentially never "
                "have liquid options markets)."
            ),
        })

    async def _place_spread_order(self, tool_input: dict) -> str:
        action = tool_input["action"]
        if action not in ("open", "close"):
            return json.dumps({"rejected": True, "backstop": "invalid_action", "reason": f"action must be 'open' or 'close', got '{action}'."})

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

        # --- Hard backstop 2: spread economics sanity ---
        economics_check = check_spread_economics(buy_symbol, sell_symbol, limit_price)
        if not economics_check.approved:
            log_event("backstop_rejected", {"backstop": "spread_economics", "reason": economics_check.reason, "input": tool_input})
            return json.dumps({"rejected": True, "backstop": "spread_economics", "reason": economics_check.reason})

        # --- Hard backstop 3: per-trade sizing cap ---
        async with AlpacaMCPClient(self.config) as mcp:
            account = await mcp.call_tool("get_account_info", {})
        equity = float(unwrap_data(account).get("equity", 0))

        sizing_check = check_position_sizing(max_loss_per_contract, contracts, equity)
        if not sizing_check.approved:
            log_event("backstop_rejected", {"backstop": "position_sizing", "reason": sizing_check.reason, "input": tool_input})
            return json.dumps({"rejected": True, "backstop": "position_sizing", "reason": sizing_check.reason})

        # Both backstops passed — place the order. Intent suffix is derived
        # directly and unambiguously from the required 'action' field — this
        # is the fix for a real incident where a hardcoded "_to_open" caused
        # an attempted position reduction to instead open an ADDITIONAL
        # position, doubling size unintentionally. There is no code path
        # left where intent can be inferred wrong: it's always exactly what
        # the agent explicitly declared.
        intent_suffix = "open" if action == "open" else "close"
        legs = [
            {"symbol": sell_symbol, "side": "sell", "ratio_qty": "1", "position_intent": f"sell_to_{intent_suffix}"},
            {"symbol": buy_symbol, "side": "buy", "ratio_qty": "1", "position_intent": f"buy_to_{intent_suffix}"},
        ]
        log_event("agent_order_submit", {
            "action": action,
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

    def _report_tooling_issue(self, tool_input: dict) -> str:
        """
        Writes to two places: the structured event log (so it's part of
        the same audit trail as everything else) AND a dedicated,
        human-readable TOOLING_ISSUES.md file (so a human doesn't have
        to dig through the full log to notice it). This is the agent's
        only channel for surfacing a suspected bug in its own tools —
        it cannot fix the tool itself, only report it clearly and
        immediately so a human can.
        """
        import os
        from datetime import datetime, timezone

        severity = tool_input.get("severity", "medium")
        tool_name = tool_input.get("tool_name", "unknown")
        what_tried = tool_input.get("what_you_tried", "")
        what_happened = tool_input.get("what_happened", "")
        suspected_cause = tool_input.get("suspected_cause", "")
        timestamp = datetime.now(timezone.utc).isoformat()

        report = {
            "timestamp": timestamp,
            "severity": severity,
            "tool_name": tool_name,
            "what_you_tried": what_tried,
            "what_happened": what_happened,
            "suspected_cause": suspected_cause,
        }
        log_event("tooling_issue_reported", report)

        issues_path = os.path.join(os.path.dirname(__file__), "..", "TOOLING_ISSUES.md")
        severity_marker = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(severity, "🟡")
        entry = (
            f"\n## {severity_marker} [{severity.upper()}] {tool_name} — {timestamp}\n\n"
            f"**What I tried:** {what_tried}\n\n"
            f"**What happened:** {what_happened}\n\n"
            f"**Suspected cause:** {suspected_cause}\n\n"
            f"---\n"
        )
        file_exists = os.path.exists(issues_path)
        with open(issues_path, "a") as f:
            if not file_exists:
                f.write("# Tooling Issues Reported by the Autonomous Agent\n\n"
                        "Each entry below was written by the agent itself the moment it noticed a "
                        "tool behaving unexpectedly. It cannot fix these — only report them for human "
                        "review. Check this file for anything unreviewed.\n")
            f.write(entry)

        return json.dumps({
            "acknowledged": True,
            "message": "Issue reported. A human will review TOOLING_ISSUES.md. You cannot fix this yourself — if possible, avoid the problematic tool/pattern for the rest of this session.",
        })
