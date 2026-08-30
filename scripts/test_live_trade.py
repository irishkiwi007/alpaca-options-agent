"""
One-off script: places a single real defined-risk spread order through
the exact same code path the autonomous agent uses (agent_layer.tools
.ToolDispatcher._place_spread_order), including both hard backstops.
This is the one thing that hadn't been exercised end-to-end yet — every
prior test correctly declined to trade due to closed markets, so the
actual order-submission step was unverified.

Not part of the autonomous loop — run manually, once, for verification:

    python3 scripts/test_live_trade.py

Picks two adjacent call strikes near the current SPY price from
whatever expiration the live chain returns, buys the lower strike and
sells the higher strike (a bull call debit spread), sized at 1
contract — the smallest possible test, well under any risk limit.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
from execution.mcp_client import AlpacaMCPClient, unwrap_data
from agent_layer.tools import ToolDispatcher
from config import CONFIG


async def find_test_spread():
    async with AlpacaMCPClient(CONFIG) as mcp:
        quote_raw = await mcp.call_tool("get_stock_latest_quote", {"symbols": "SPY"})
    quote_data = unwrap_data(quote_raw)
    spy_price = quote_data.get("quotes", {}).get("SPY", {}).get("ap") or quote_data.get("quotes", {}).get("SPY", {}).get("bp")
    if not spy_price:
        raise RuntimeError("Could not get a current SPY price to base the test spread on.")
    print(f"Current SPY quote: ~${spy_price:.2f}")

    # Try a few upcoming dates to find one with an actual listed chain —
    # weekends/holidays or far-future dates may return empty snapshots.
    chain = {}
    expiration_used = None
    for days_ahead in range(0, 10):
        candidate_date = (date.today() + timedelta(days=days_ahead)).isoformat()
        async with AlpacaMCPClient(CONFIG) as mcp:
            chain_raw = await mcp.call_tool("get_option_chain", {
                "underlying_symbol": "SPY",
                "expiration_date": candidate_date,
            })
        data = unwrap_data(chain_raw)
        snapshots = data.get("snapshots", {}) if isinstance(data, dict) else {}
        if snapshots:
            chain = snapshots
            expiration_used = candidate_date
            break

    if not chain:
        raise RuntimeError("No option chain snapshots found in the next 10 days — cannot build a test spread.")

    print(f"Using expiration: {expiration_used} ({len(chain)} contracts in chain)")

    # Parse calls only, extract strike from OCC symbol, keep ones with real quotes.
    calls = []
    for symbol, snap in chain.items():
        if len(symbol) < 9 or symbol[-9] != "C":
            continue
        strike = int(symbol[-8:]) / 1000.0
        quote = snap.get("latestQuote", {})
        bid, ask = quote.get("bp"), quote.get("ap")
        if bid and ask and bid > 0 and ask > 0:
            calls.append({"symbol": symbol, "strike": strike, "bid": bid, "ask": ask})

    if len(calls) < 2:
        raise RuntimeError(f"Not enough liquid call quotes found near {spy_price} to build a spread.")

    calls.sort(key=lambda c: abs(c["strike"] - spy_price))
    buy_leg = calls[0]  # nearest to current price
    # find the next strike up from buy_leg for the short leg
    higher_strikes = sorted([c for c in calls if c["strike"] > buy_leg["strike"]], key=lambda c: c["strike"])
    if not higher_strikes:
        raise RuntimeError("No higher strike available to form a spread above the nearest-the-money call.")
    sell_leg = higher_strikes[0]

    net_debit = round(buy_leg["ask"] - sell_leg["bid"], 2)
    spread_width = sell_leg["strike"] - buy_leg["strike"]
    max_loss_per_contract = round(net_debit * 100, 2)

    print(f"Buy leg:  {buy_leg['symbol']}  strike ${buy_leg['strike']}  ask ${buy_leg['ask']}")
    print(f"Sell leg: {sell_leg['symbol']}  strike ${sell_leg['strike']}  bid ${sell_leg['bid']}")
    print(f"Net debit: ${net_debit}/contract  |  Spread width: ${spread_width}  |  Max loss: ${max_loss_per_contract}/contract")

    if net_debit <= 0:
        raise RuntimeError(f"Net debit is {net_debit} (non-positive) — chain pricing looks off, aborting rather than placing a bad order.")

    return {
        "underlying": "SPY",
        "buy_symbol": buy_leg["symbol"],
        "sell_symbol": sell_leg["symbol"],
        "contracts": 1,
        "limit_price": net_debit,
        "max_loss_per_contract": max_loss_per_contract,
        "rationale": "One-off manual verification trade: confirms the place_spread_order code path, both hard backstops, and Alpaca order submission all work end-to-end before relying on it unattended.",
    }


async def main():
    print("=" * 70)
    print("TEST TRADE: placing one real spread order through the actual pipeline")
    print("=" * 70)

    order_input = await find_test_spread()

    print("\nSubmitting through ToolDispatcher (same path the autonomous agent uses)...")
    dispatcher = ToolDispatcher(CONFIG)
    result = await dispatcher.dispatch("place_spread_order", order_input)
    print("\nResult:")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
