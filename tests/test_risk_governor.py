import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk.portfolio_governor import PortfolioGovernor
from fast_layer.signal_generator import SpreadCandidate


def make_candidate(max_loss=3.5):
    return SpreadCandidate(
        underlying="SPY",
        strategy_type="bull_put_spread",
        expiration="2026-08-29",
        short_strike=440,
        long_strike=435,
        short_delta=0.16,
        estimated_credit=1.5,
        max_loss=max_loss,
        rationale="test candidate",
    )


def test_approves_within_limits():
    gov = PortfolioGovernor()
    decision = gov.evaluate(
        candidate=make_candidate(),
        current_positions=[],
        account_equity=100000,
        daily_pnl_pct=0.0,
        current_net_delta=0.0,
        requested_contracts=1,
    )
    assert decision.approved
    assert decision.adjusted_contracts == 1


def test_rejects_after_daily_loss_limit():
    gov = PortfolioGovernor()
    decision = gov.evaluate(
        candidate=make_candidate(),
        current_positions=[],
        account_equity=100000,
        daily_pnl_pct=-0.05,  # exceeds 3% default limit
        current_net_delta=0.0,
        requested_contracts=1,
    )
    assert not decision.approved
    assert "loss" in decision.reason.lower()


def test_rejects_at_max_positions():
    gov = PortfolioGovernor()
    decision = gov.evaluate(
        candidate=make_candidate(),
        current_positions=[{}] * 4,  # default max is 4
        account_equity=100000,
        daily_pnl_pct=0.0,
        current_net_delta=0.0,
        requested_contracts=1,
    )
    assert not decision.approved


def test_scales_down_for_notional_cap():
    gov = PortfolioGovernor()
    # Large max_loss forces scaling: 10% of 100k = 10000 cap, max_loss=350/contract*100=35000 for 1 contract
    decision = gov.evaluate(
        candidate=make_candidate(max_loss=350),
        current_positions=[],
        account_equity=100000,
        daily_pnl_pct=0.0,
        current_net_delta=0.0,
        requested_contracts=5,
    )
    assert not decision.approved  # even 1 contract exceeds cap at this max_loss


def test_delta_cap_rejection():
    gov = PortfolioGovernor()
    decision = gov.evaluate(
        candidate=make_candidate(),
        current_positions=[],
        account_equity=100000,
        daily_pnl_pct=0.0,
        current_net_delta=40,  # near default cap of 50
        requested_contracts=1,
    )
    # short_delta 0.16 * 1 contract * 100 = 16, projected = 56 > 50 cap
    assert not decision.approved
