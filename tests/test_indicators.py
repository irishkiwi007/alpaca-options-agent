import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_layer.indicators import (
    Bar, swing_high_low, intraday_range_pct, bollinger_bands,
    is_breakout, iv_rank, vix_term_structure_state
)


def make_bars(closes):
    return [Bar(open=c, high=c + 1, low=c - 1, close=c, volume=1000, timestamp=i) for i, c in enumerate(closes)]


def test_swing_high_low():
    bars = make_bars([100, 102, 98, 105, 95])
    hi, lo = swing_high_low(bars, lookback=5)
    assert hi == 106  # 105 + 1
    assert lo == 94   # 95 - 1


def test_intraday_range_pct():
    bars = make_bars([100, 105, 95, 102])
    pct = intraday_range_pct(bars)
    assert pct > 0


def test_bollinger_bands_insufficient_data():
    upper, mid, lower = bollinger_bands([100, 101, 102], period=20)
    assert upper is None and mid is None and lower is None


def test_bollinger_bands_normal():
    closes = [100 + (i % 3) for i in range(25)]
    upper, mid, lower = bollinger_bands(closes, period=20)
    assert upper > mid > lower


def test_is_breakout_up():
    closes = [100] * 20 + [110]
    bars = make_bars(closes)
    assert is_breakout(bars, lookback=20) == "up"


def test_is_breakout_none():
    closes = [100] * 21
    bars = make_bars(closes)
    assert is_breakout(bars, lookback=20) == "none"


def test_iv_rank_bounds():
    assert abs(iv_rank(0.30, [0.20, 0.25, 0.30, 0.35, 0.40]) - 50.0) < 1e-6
    assert iv_rank(0.20, [0.20, 0.25, 0.30, 0.35, 0.40]) == 0.0
    assert iv_rank(0.40, [0.20, 0.25, 0.30, 0.35, 0.40]) == 100.0


def test_iv_rank_insufficient_history():
    assert iv_rank(0.30, [0.20, 0.25]) == 50.0


def test_vix_term_structure_contango():
    assert vix_term_structure_state(vix_spot=20, vix9d=18, vix3m=22) == "contango"


def test_vix_term_structure_backwardation():
    assert vix_term_structure_state(vix_spot=25, vix9d=30, vix3m=22) == "backwardation"
