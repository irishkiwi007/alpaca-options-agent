"""
Exit management stays rule-based, not LLM-driven — deliberately, so
closing a losing position never waits on an API round-trip. This
mirrors the bot-managed state-file pattern from the existing fleet's
stop-loss handling, compressed to same-day (0DTE) timeframes.
"""
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

from config import CONFIG


class ExitReason(Enum):
    NONE = "none"
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    TIME_STOP = "time_stop"  # forced close near expiry/close


@dataclass
class PositionState:
    underlying: str
    strategy_type: str
    entry_credit: float
    contracts: int
    expiration: str  # ISO date


def check_exit(
    position: PositionState,
    current_mark: float,
    now: datetime,
    market_close: time = time(16, 0),
    config=CONFIG,
) -> ExitReason:
    """
    current_mark: current cost to close the spread (what you'd pay to buy it back).
    A positive entry_credit shrinking toward zero is profit; growing
    past entry_credit is loss, scaled by stop_loss_multiple.
    """
    sc = config.strategy

    profit_captured_pct = (position.entry_credit - current_mark) / position.entry_credit if position.entry_credit else 0
    if profit_captured_pct >= sc.profit_take_pct:
        return ExitReason.PROFIT_TARGET

    loss = current_mark - position.entry_credit
    if loss >= position.entry_credit * sc.stop_loss_multiple:
        return ExitReason.STOP_LOSS

    if position.expiration == now.date().isoformat():
        minutes_to_close = (
            datetime.combine(now.date(), market_close) - now
        ).total_seconds() / 60
        if minutes_to_close <= sc.hard_close_minutes_before_expiry:
            return ExitReason.TIME_STOP

    return ExitReason.NONE
