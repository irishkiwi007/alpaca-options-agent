"""
Portfolio governor: the final checkpoint before any order reaches
execution. This runs regardless of what the fast layer proposed or
what the agent layer approved — it has veto power over both, because
neither of those layers has full-account visibility by design.
"""
from dataclasses import dataclass
from typing import List

from config import CONFIG
from fast_layer.signal_generator import SpreadCandidate


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    adjusted_contracts: int = 0


class PortfolioGovernor:
    def __init__(self, config=CONFIG):
        self.config = config

    def evaluate(
        self,
        candidate: SpreadCandidate,
        current_positions: List[dict],
        account_equity: float,
        daily_pnl_pct: float,
        current_net_delta: float,
        requested_contracts: int = 1,
    ) -> RiskDecision:
        risk = self.config.risk

        # Kill switches first — these override everything else.
        if daily_pnl_pct <= -risk.max_daily_loss_pct:
            return RiskDecision(False, f"Daily loss limit hit ({daily_pnl_pct:.1%}); new entries halted for session.")

        if daily_pnl_pct <= -risk.catastrophic_drawdown_pct:
            return RiskDecision(False, "Catastrophic drawdown threshold hit; flatten and halt.")

        if len(current_positions) >= risk.max_concurrent_positions:
            return RiskDecision(False, f"At max concurrent positions ({risk.max_concurrent_positions}).")

        notional = candidate.max_loss * requested_contracts * 100  # options contract multiplier
        if account_equity <= 0:
            return RiskDecision(False, "Account equity unavailable or zero.")
        if notional / account_equity > risk.max_notional_pct_of_equity:
            # Try scaling down contracts before rejecting outright.
            max_affordable = int(
                (risk.max_notional_pct_of_equity * account_equity) / (candidate.max_loss * 100)
            )
            if max_affordable < 1:
                return RiskDecision(False, "Even 1 contract exceeds per-trade notional cap.")
            return RiskDecision(
                True,
                f"Scaled from {requested_contracts} to {max_affordable} contracts to respect notional cap.",
                adjusted_contracts=max_affordable,
            )

        projected_delta = current_net_delta + (candidate.short_delta * requested_contracts * 100)
        if abs(projected_delta) > risk.max_portfolio_delta:
            return RiskDecision(False, f"Would breach portfolio delta cap ({projected_delta:.0f} vs {risk.max_portfolio_delta}).")

        contracts = min(requested_contracts, risk.max_contracts_per_trade)
        return RiskDecision(True, "Within all risk limits.", adjusted_contracts=contracts)
