import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_layer.signal_generator import SignalGenerator


def mock_chain():
    return {
        "SPY260829P00440000": {"type": "put", "strike": 440, "delta": -0.16, "bid": 1.60, "ask": 1.70},
        "SPY260829P00435000": {"type": "put", "strike": 435, "delta": -0.08, "bid": 0.40, "ask": 0.50},
        "SPY260829C00460000": {"type": "call", "strike": 460, "delta": 0.16, "bid": 1.50, "ask": 1.60},
    }


def test_no_candidate_when_vix_below_threshold():
    sg = SignalGenerator()
    result = sg.evaluate(
        underlying="SPY", current_iv=0.30, iv_history=[0.20, 0.25, 0.30, 0.35],
        vix_spot=10, vix9d=9, vix3m=11, chain=mock_chain(),
    )
    assert result is None


def test_no_candidate_in_backwardation():
    sg = SignalGenerator()
    result = sg.evaluate(
        underlying="SPY", current_iv=0.35, iv_history=[0.20, 0.25, 0.30, 0.35],
        vix_spot=25, vix9d=30, vix3m=22,  # backwardation
        chain=mock_chain(),
    )
    assert result is None


def test_no_candidate_when_iv_rank_low():
    sg = SignalGenerator()
    result = sg.evaluate(
        underlying="SPY", current_iv=0.21, iv_history=[0.20, 0.25, 0.30, 0.35, 0.40],
        vix_spot=25, vix9d=20, vix3m=28,  # contango, VIX above threshold
        chain=mock_chain(),
    )
    assert result is None  # IV rank ~2.5, below min_iv_rank 40


def test_candidate_generated_when_all_gates_pass():
    sg = SignalGenerator()
    result = sg.evaluate(
        underlying="SPY", current_iv=0.38, iv_history=[0.20, 0.25, 0.30, 0.35, 0.40],
        vix_spot=25, vix9d=20, vix3m=28,  # contango, VIX above threshold, IV rank ~90
        chain=mock_chain(),
    )
    assert result is not None
    assert result.strategy_type == "bull_put_spread"
    assert result.short_strike == 440
    assert result.long_strike == 435
    assert result.estimated_credit > 0
