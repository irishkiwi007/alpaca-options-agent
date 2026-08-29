"""
The only two constraints the autonomous agent cannot reason its way
around, because they're checked in code before any order reaches
Alpaca — not instructions in a prompt the agent could weigh against
other considerations.

1. DEFINED RISK ONLY: every options position must have a known,
   bounded maximum loss. No naked/undefined-risk legs. This is
   enforced by requiring every order to be a two-leg spread with
   opposite sides (one bought, one sold) on the same underlying.

2. PER-TRADE SIZING CAP: no single trade may risk more than
   MAX_TRADE_RISK_PCT of current account equity. This is the "don't
   bet the account on one read" rule — it bounds the worst case of
   any single decision without constraining strategy, timing, or
   how many trades happen.

Both checks return a clear rejection reason rather than silently
blocking, so the agent can see why a proposed trade was refused and
adjust — the backstop is a wall, not a black box.
"""
from dataclasses import dataclass
from typing import Optional

MAX_TRADE_RISK_PCT = 0.15  # 15% of account equity, per trade, hard cap


@dataclass
class BackstopResult:
    approved: bool
    reason: str
    max_loss_dollars: Optional[float] = None


def check_defined_risk(short_symbol: str, long_symbol: str, short_side: str, long_side: str) -> BackstopResult:
    """
    Requires a genuine two-leg spread: distinct option symbols, with
    opposite sides (one buy, one sell). This blocks naked single-leg
    orders and same-side "spreads" (which aren't risk-defining) alike.
    """
    if not short_symbol or not long_symbol:
        return BackstopResult(False, "Both legs of a spread must be specified; single-leg (naked) orders are not permitted.")

    if short_symbol == long_symbol:
        return BackstopResult(False, "Both legs resolved to the same option symbol; this is not a defined-risk spread.")

    sides = {short_side.lower(), long_side.lower()}
    if sides != {"buy", "sell"}:
        return BackstopResult(False, f"Legs must be opposite sides (one buy, one sell) to define risk; got {short_side}/{long_side}.")

    return BackstopResult(True, "Two-leg opposite-side spread confirmed; risk is defined.")


def check_position_sizing(
    max_loss_per_contract: float,
    contracts: int,
    account_equity: float,
) -> BackstopResult:
    """
    max_loss_per_contract should be the worst-case loss for ONE contract
    of the spread (e.g. spread width minus credit received, times 100
    for the options multiplier) — the caller is responsible for that
    calculation being correct; this function only enforces the cap
    against whatever figure it's given.
    """
    if account_equity <= 0:
        return BackstopResult(False, "Account equity is zero or unavailable; cannot size a trade safely.")

    total_max_loss = max_loss_per_contract * contracts
    risk_pct = total_max_loss / account_equity

    if risk_pct > MAX_TRADE_RISK_PCT:
        max_affordable_contracts = int((MAX_TRADE_RISK_PCT * account_equity) / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        return BackstopResult(
            False,
            f"Trade risks {risk_pct:.1%} of account equity (${total_max_loss:,.2f} of ${account_equity:,.2f}), "
            f"exceeding the {MAX_TRADE_RISK_PCT:.0%} per-trade cap. "
            f"At most {max_affordable_contracts} contract(s) would be within the cap.",
            max_loss_dollars=total_max_loss,
        )

    return BackstopResult(
        True,
        f"Trade risks {risk_pct:.1%} of account equity, within the {MAX_TRADE_RISK_PCT:.0%} cap.",
        max_loss_dollars=total_max_loss,
    )
