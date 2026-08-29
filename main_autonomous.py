"""
The persistent process. This is what runs continuously on the VM,
deployed as a systemd service (see deploy/alpaca-agent.service).

Each loop iteration:
  1. Check for the graceful-stop file (STOP_AND_FLATTEN) — if present,
     flatten all positions and exit cleanly.
  2. Check the catastrophic drawdown monitor — if triggered, flatten
     all positions and exit automatically, no human needed to notice.
  3. Run one autonomous decision cycle (agent_layer/autonomous_agent.py).
  4. Sleep for however many minutes Claude decided, then repeat.

Stopping this process entirely (without flattening) is a separate,
immediate mechanism: `systemctl stop alpaca-agent` kills it outright.
STOP_AND_FLATTEN is for "wind down safely first."
"""
import asyncio
import os
import time

from config import CONFIG
from execution.mcp_client import AlpacaMCPClient
from execution.trade_logger import log_event
from risk.drawdown_monitor import check_drawdown
from agent_layer.autonomous_agent import AutonomousTradingAgent

STOP_FLAG_PATH = os.path.join(os.path.dirname(__file__), "STOP_AND_FLATTEN")


async def _flatten_all(config, reason: str):
    log_event("auto_flatten_triggered", {"reason": reason})
    try:
        async with AlpacaMCPClient(config) as mcp:
            result = await mcp.call_tool("close_all_positions", {"cancel_orders": True})
        log_event("auto_flatten_complete", {"result": result})
    except Exception as e:
        log_event("auto_flatten_failed", {"error": str(e)})


async def _get_current_equity(config) -> float:
    async with AlpacaMCPClient(config) as mcp:
        account = await mcp.call_tool("get_account_info", {})
    return float(account.get("equity", 0))


async def main_loop():
    config = CONFIG
    agent = AutonomousTradingAgent(config)

    log_event("autonomous_runner_start", {})

    while True:
        if os.path.exists(STOP_FLAG_PATH):
            log_event("graceful_stop_requested", {})
            await _flatten_all(config, "Manual STOP_AND_FLATTEN file detected")
            os.remove(STOP_FLAG_PATH)
            log_event("autonomous_runner_stopped", {"reason": "graceful stop"})
            break

        try:
            equity = await _get_current_equity(config)
            drawdown = check_drawdown(equity)
            log_event("drawdown_check", {
                "current_equity": drawdown.current_equity,
                "baseline_equity": drawdown.baseline_equity,
                "drawdown_pct": drawdown.drawdown_pct,
                "triggered": drawdown.triggered,
            })
            if drawdown.triggered:
                await _flatten_all(config, drawdown.reason)
                log_event("autonomous_runner_stopped", {"reason": "catastrophic drawdown"})
                break
        except Exception as e:
            log_event("drawdown_check_failed", {"error": str(e)})
            # If we can't even check equity, don't trade blind this cycle —
            # skip trading, but don't crash the process either; retry next loop.
            time.sleep(60)
            continue

        try:
            next_check_minutes = await agent.run_cycle()
        except Exception as e:
            log_event("autonomous_cycle_failed", {"error": str(e)})
            next_check_minutes = 15  # conservative fallback if a cycle errors out

        log_event("sleeping_until_next_cycle", {"minutes": next_check_minutes})
        time.sleep(next_check_minutes * 60)


if __name__ == "__main__":
    asyncio.run(main_loop())
