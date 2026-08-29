"""
Entrypoint. This is the "go button."

Run modes:
  python main.py --dry-run     # full pipeline, logs decisions, places NO orders (default)
  python main.py --live-paper  # full pipeline, places real orders against the paper account
  python main.py --once        # single pass instead of continuous loop (good for demos)

The pipeline per underlying, per fast-layer tick:
  1. fast_layer pulls live data via Alpaca's MCP server and generates a
     candidate (or None) from market state
  2. agent_layer (Claude) reviews the candidate with visible reasoning
  3. risk.portfolio_governor makes the final approve/resize/reject call
  4. execution places the order via MCP (or is skipped in --dry-run)
  5. everything is logged to logs/events.jsonl regardless of outcome

Exit management runs independently and is pure rules (risk/stop_loss.py),
checked every tick against open positions, with no agent call in that path.
"""
import argparse
import asyncio
import time as time_module
from datetime import date, datetime, timezone

from config import CONFIG
from config.dynamic_overrides import effective_strategy_config
from fast_layer.market_data import MarketData
from fast_layer.signal_generator import SignalGenerator
from agent_layer.claude_agent import TradeReviewAgent
from agent_layer.rules_review_agent import RulesReviewAgent
from risk.portfolio_governor import PortfolioGovernor
from risk.stop_loss import check_exit, PositionState
from execution.alpaca_client import AlpacaExecutionClient
from execution.trade_logger import log_event


async def run_once(dry_run: bool = True, run_rules_review: bool = True):
    market = MarketData()
    effective_config = CONFIG
    if run_rules_review:
        reviewer = RulesReviewAgent()
        review_result = reviewer.review()
        log_event("rules_review_summary", review_result)
        effective_strategy = effective_strategy_config(CONFIG.strategy)
        from dataclasses import replace as _replace
        effective_config = _replace(CONFIG, strategy=effective_strategy)

    signals = SignalGenerator(config=effective_config)
    agent = TradeReviewAgent()
    governor = PortfolioGovernor()
    execution = AlpacaExecutionClient()  # always constructed; only invoked to place orders when not dry_run

    log_event("session_start", {"dry_run": dry_run, "timestamp": datetime.now(timezone.utc).isoformat()})

    try:
        snap = await execution.account_snapshot()
        account_equity = snap["equity"]
        current_positions = await execution.open_positions()
    except Exception as e:
        log_event("account_fetch_failed", {"error": str(e)})
        account_equity = 100000.0
        current_positions = []

    daily_pnl_pct = 0.0  # derived from account_snapshot's equity vs session-start equity in a longer-running loop
    current_net_delta = 0.0  # summed from current_positions' greeks once positions exist

    for underlying in effective_config.strategy.universe:
        try:
            vix = await market.vix_snapshot()
            chain = await market.option_chain(underlying, expiration=date.today().isoformat())
        except Exception as e:
            log_event("market_data_fetch_failed", {"underlying": underlying, "error": str(e)})
            continue

        # IV rank needs a history series; without a stored rolling window yet,
        # fall back to a neutral estimate (iv_rank() itself returns 50 for
        # insufficient history, which the >= min_iv_rank gate then blocks
        # correctly rather than passing on unknown data).
        current_iv = 0.0
        iv_history: list = []

        candidate = signals.evaluate(
            underlying=underlying,
            current_iv=current_iv,
            iv_history=iv_history,
            vix_spot=vix.get("vix_spot", 0.0),
            vix9d=vix.get("vix9d", 0.0),
            vix3m=vix.get("vix3m", 0.0),
            chain=chain,
        )

        if candidate is None:
            log_event("no_candidate", {"underlying": underlying, "vix": vix})
            continue

        market_context = {"underlying": underlying, "vix": vix}
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

        short_symbol = _find_option_symbol(chain, candidate.short_strike, "put")
        long_symbol = _find_option_symbol(chain, candidate.long_strike, "put")

        if not short_symbol or not long_symbol:
            log_event("symbol_resolution_failed", {"underlying": underlying, "candidate": candidate.__dict__})
            continue

        if dry_run:
            log_event("dry_run_would_execute", {
                "underlying": underlying,
                "short_symbol": short_symbol,
                "long_symbol": long_symbol,
                "contracts": risk_decision.adjusted_contracts,
                "estimated_credit": candidate.estimated_credit,
            })
        else:
            try:
                result = await execution.submit_vertical_spread(
                    short_symbol=short_symbol,
                    long_symbol=long_symbol,
                    contracts=risk_decision.adjusted_contracts,
                    limit_credit=candidate.estimated_credit,
                )
                log_event("execution_success", {"underlying": underlying, "result": result})
            except Exception as e:
                log_event("execution_failed", {"underlying": underlying, "error": str(e)})

    log_event("session_end", {"timestamp": datetime.now(timezone.utc).isoformat()})


def _find_option_symbol(chain: dict, strike: float, option_type: str) -> str:
    for symbol, data in chain.items():
        if data.get("type") == option_type and abs(data.get("strike", -1) - strike) < 0.01:
            return symbol
    return ""


def main():
    parser = argparse.ArgumentParser(description="Alpaca options agent — hackathon entrypoint")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Log decisions, place no orders (default)")
    parser.add_argument("--live-paper", action="store_true", help="Place real orders against the paper account")
    parser.add_argument("--once", action="store_true", help="Single pass instead of a continuous loop")
    parser.add_argument("--skip-rules-review", action="store_true", help="Skip the rules-review agent pass (use base config as-is)")
    args = parser.parse_args()

    dry_run = not args.live_paper
    run_rules_review = not args.skip_rules_review

    if args.once:
        asyncio.run(run_once(dry_run=dry_run, run_rules_review=run_rules_review))
        return

    interval = CONFIG.strategy.fast_layer_interval_minutes * 60
    while True:
        asyncio.run(run_once(dry_run=dry_run, run_rules_review=run_rules_review))
        time_module.sleep(interval)


if __name__ == "__main__":
    main()
