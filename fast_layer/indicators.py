"""
Indicator calculations reused from the existing bot fleet's trend-detection
logic (swing high/low, intraday range, breakout, Bollinger bands), plus
IV rank and VIX term-structure helpers specific to the options overlay.
"""
from dataclasses import dataclass
from statistics import mean, stdev
from typing import List, Sequence


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: object


def swing_high_low(bars: Sequence[Bar], lookback: int = 20) -> tuple:
    """Return (swing_high, swing_low) over the lookback window."""
    window = bars[-lookback:] if len(bars) >= lookback else bars
    if not window:
        return None, None
    return max(b.high for b in window), min(b.low for b in window)


def intraday_range_pct(bars: Sequence[Bar]) -> float:
    """Today's high-low range as a percentage of the open."""
    if not bars:
        return 0.0
    day_open = bars[0].open
    day_high = max(b.high for b in bars)
    day_low = min(b.low for b in bars)
    if day_open == 0:
        return 0.0
    return (day_high - day_low) / day_open * 100


def bollinger_bands(closes: Sequence[float], period: int = 20, num_std: float = 2.0):
    """Return (upper, middle, lower) Bollinger Bands for the given close series."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = mean(window)
    sd = stdev(window) if len(window) > 1 else 0.0
    return mid + num_std * sd, mid, mid - num_std * sd


def is_breakout(bars: Sequence[Bar], lookback: int = 20) -> str:
    """Return 'up', 'down', or 'none' if the latest close breaks the recent range."""
    if len(bars) < lookback + 1:
        return "none"
    swing_high, swing_low = swing_high_low(bars[:-1], lookback)
    latest_close = bars[-1].close
    if swing_high and latest_close > swing_high:
        return "up"
    if swing_low and latest_close < swing_low:
        return "down"
    return "none"


def iv_rank(current_iv: float, iv_history: Sequence[float]) -> float:
    """
    IV rank: where current IV sits within its historical range, 0-100.
    Falls back to 50 (neutral) if insufficient history — callers should
    treat that as 'unknown' and apply extra caution, not as a green light.
    """
    if not iv_history or len(iv_history) < 5:
        return 50.0
    lo, hi = min(iv_history), max(iv_history)
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (current_iv - lo) / (hi - lo) * 100))


def vix_term_structure_state(vix_spot: float, vix9d: float, vix3m: float) -> str:
    """
    Classify VIX term structure as 'contango' (normal, front-month cheaper)
    or 'backwardation' (stress signal, front-month richer). Backwardation
    is treated as a block on new premium-selling entries per StrategyConfig.
    """
    if vix9d > vix_spot > vix3m:
        return "backwardation"
    if vix9d < vix_spot < vix3m:
        return "contango"
    # Mixed / ambiguous ordering — treat conservatively as backwardation-like
    return "mixed"
