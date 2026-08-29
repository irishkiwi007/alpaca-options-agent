"""
Turns market state (indicators + option chain) into candidate trade
proposals. This module NEVER places orders — it only proposes. The
agent layer reviews proposals, and execution places them. Keeping
proposal generation pure/deterministic makes it testable and keeps
the LLM's job narrow: judgment on a small number of well-formed
candidates, not free-form market interpretation.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from config import CONFIG
from fast_layer.indicators import iv_rank, vix_term_structure_state


@dataclass
class SpreadCandidate:
    underlying: str
    strategy_type: str          # "bull_put_spread" | "bear_call_spread" | "iron_condor"
    expiration: str             # ISO date, 0DTE = today
    short_strike: float
    long_strike: float
    short_delta: float
    estimated_credit: float
    max_loss: float
    rationale: str              # human-readable, fed to the agent for context


class SignalGenerator:
    def __init__(self, config=CONFIG):
        self.config = config

    def evaluate(
        self,
        underlying: str,
        current_iv: float,
        iv_history: List[float],
        vix_spot: float,
        vix9d: float,
        vix3m: float,
        chain: dict,
    ) -> Optional[SpreadCandidate]:
        """
        Core gate: only proposes a trade if regime and IV conditions are
        met. Returns None (no candidate) far more often than it returns
        something — the fast layer is a filter, not a trade generator
        that always has an opinion.
        """
        term_state = vix_term_structure_state(vix_spot, vix9d, vix3m)
        if self.config.strategy.vix_backwardation_block and term_state in (
            "backwardation",
            "mixed",
        ):
            return None

        if vix_spot < self.config.strategy.vix_entry_threshold:
            return None

        rank = iv_rank(current_iv, iv_history)
        if rank < self.config.strategy.min_iv_rank:
            return None

        candidate = self._select_strikes(underlying, chain, rank, vix_spot)
        return candidate

    def _select_strikes(
        self, underlying: str, chain: dict, rank: float, vix_spot: float
    ) -> Optional[SpreadCandidate]:
        """
        Pick short/long strikes targeting StrategyConfig.target_short_delta.
        `chain` is expected as {option_symbol: {"delta": ..., "strike": ...,
        "bid": ..., "ask": ..., "type": "put"|"call"}}. Real chain wiring
        happens in fast_layer/market_data.py; this function is intentionally
        pure so it can be unit tested against a mocked chain dict.
        """
        target = self.config.strategy.target_short_delta
        tolerance = self.config.strategy.delta_tolerance
        width = self.config.strategy.spread_width

        puts = [
            o for o in chain.values()
            if o.get("type") == "put" and abs(abs(o.get("delta", 0)) - target) <= tolerance
        ]
        if not puts:
            return None

        puts.sort(key=lambda o: abs(abs(o["delta"]) - target))
        short_put = puts[0]
        short_strike = short_put["strike"]
        long_strike = short_strike - width

        long_candidates = [
            o for o in chain.values()
            if o.get("type") == "put" and abs(o.get("strike", -1) - long_strike) < 0.5
        ]
        if not long_candidates:
            return None
        long_put = long_candidates[0]

        credit = (short_put.get("bid", 0) - long_put.get("ask", 0))
        if credit <= 0:
            return None

        max_loss = width - credit

        return SpreadCandidate(
            underlying=underlying,
            strategy_type="bull_put_spread",
            expiration=date.today().isoformat(),
            short_strike=short_strike,
            long_strike=long_strike,
            short_delta=short_put["delta"],
            estimated_credit=round(credit, 2),
            max_loss=round(max_loss, 2),
            rationale=(
                f"VIX {vix_spot:.1f} above entry threshold, IV rank {rank:.0f}, "
                f"contango term structure. Short {short_strike}p (delta "
                f"{short_put['delta']:.2f}) / long {long_strike}p, "
                f"credit {credit:.2f} vs max loss {max_loss:.2f}."
            ),
        )
