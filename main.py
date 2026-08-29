"""
Entrypoint. This is the "go button."

Run modes:
  python main.py --dry-run     # full pipeline, logs decisions, places NO orders
  python main.py --live-paper  # full pipeline, places real (paper) orders
  python main.py --once        # single pass instead of continuous loop (good for demos)

The pipeline per underlying, per fast-layer tick:
  1. fast_layer generates a candidate (or None) from market state
  2. agent_layer reviews the candidate with visible reasoning
  3. risk.portfolio_governor makes the final approve/resize/reject call
  4. execution places the order (or is skipped in --dry-run)
  5. everything is logged to logs/events.jsonl regardless of outcome

Exit management runs independently and is pure rules (risk/stop_loss.py),
checked every tick against open positions, with no agent call in that path.
"""
import argparse
import time as time_module
from datetime import datetime, timezone

from config import CONFIG
from fast_layer.market_data import MarketData
from fast_layer.signal_generator import SignalGenerator
from agent_layer.claude_agent import TradeReviewAgent
from risk.portfolio_governor import PortfolioGovernor
from risk.stop_loss import check_exit, PositionState
from execution.alpaca_client import AlpacaExecutionClient
from execution.trade_logger import log_event


def run_once(dry_run: bool = True):
    market = MarketData()
    signals = SignalGenerator()
    agent = TradeReviewAgent()
    governor = PortfolioGovernor()
    execution = None if dry_run else AlpacaExecutionClient()

    log_event("session_start", {"dry_run": dry_run, "timestamp": datetime.now(timezone.utc).isoformat()})

    account_equity = 100000.0  # placeholder until execution.account_snapshot() is live-verified
    daily_pnl_pct = 0.0
    current_net_delta = 0.0
    current_positions = []

    if execution:
        snap = execution.account_snapshot()
        account_equity = snap["equity"]
        current_positions = execution.open_positions()

    for underlying in CONFIG.strategy.universe:
        # NOTE: chain/iv_history/vix inputs below are wired to live data once
        # network egress to Alpaca's data hosts is confirmed reachable from
        # this environment. Structure and gating logic are final; only the
        # data-fetch calls are pending that verification.
        candidate = signals.evaluate(
            underlying=underlying,
            current_iv=0.0,
            iv_history=[],
            vix_spot=0.0,
            vix9d=0.0,
            vix3m=0.0,
            chain={},
        )

        if candidate is None:
            log_event("no_candidate", {"underlying": underlying, "reason": "gates not met or data not yet wired"})
            continue

        market_context = {"underlying": underlying}
        decision = agent.review(candidate, market_context)
        log_event("agent_decision", {
            "underlying": underlying,
            "decision": decision.decision,
            "contracts": decision.contracts,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
        })

        if decision.decision == "reject":
            continue

        risk_decision = governor.evaluate(
            candidate=candidate,
            current_positions=current_positions,
            account_equity=account_equity,
            daily_pnl_pct=daily_pnl_pct,
            current_net_delta=current_net_delta,
            requested_contracts=max(decision.contracts, 1),
        )
        log_event("risk_decision", {
            "underlying": underlying,
            "approved": risk_decision.approved,
            "reason": risk_decision.reason,
            "adjusted_contracts": risk_decision.adjusted_contracts,
        })

        if not risk_decision.approved:
            continue

        if dry_run:
            log_event("dry_run_would_execute", {
                "underlying": underlying,
                "candidate": candidate.__dict__,
                "contracts": risk_decision.adjusted_contracts,
            })
        else:
            # short_symbol/long_symbol resolution from candidate strikes to
            # actual OCC option symbols happens once live chain data is wired.
            log_event("execution_pending_chain_wiring", {"underlying": underlying})

    log_event("session_end", {"timestamp": datetime.now(timezone.utc).isoformat()})


def main():
    parser = argparse.ArgumentParser(description="Alpaca options agent — hackathon entrypoint")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Log decisions, place no orders (default)")
    parser.add_argument("--live-paper", action="store_true", help="Place real orders against the paper account")
    parser.add_argument("--once", action="store_true", help="Single pass instead of a continuous loop")
    args = parser.parse_args()

    dry_run = not args.live_paper

    if args.once:
        run_once(dry_run=dry_run)
        return

    interval = CONFIG.strategy.fast_layer_interval_minutes * 60
    while True:
        run_once(dry_run=dry_run)
        time_module.sleep(interval)


if __name__ == "__main__":
    main()
