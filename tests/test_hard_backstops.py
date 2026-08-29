import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk.hard_backstops import check_defined_risk, check_position_sizing, MAX_TRADE_RISK_PCT


def test_defined_risk_rejects_missing_leg():
    result = check_defined_risk("", "SPY260829P00440000", "sell", "buy")
    assert not result.approved


def test_defined_risk_rejects_same_symbol():
    sym = "SPY260829P00440000"
    result = check_defined_risk(sym, sym, "sell", "buy")
    assert not result.approved
    assert "same option symbol" in result.reason


def test_defined_risk_rejects_same_side():
    result = check_defined_risk("SPY260829P00440000", "SPY260829P00435000", "sell", "sell")
    assert not result.approved
    assert "opposite sides" in result.reason


def test_defined_risk_approves_genuine_spread():
    result = check_defined_risk("SPY260829P00440000", "SPY260829P00435000", "sell", "buy")
    assert result.approved


def test_defined_risk_approves_regardless_of_side_order():
    result = check_defined_risk("SPY260829P00440000", "SPY260829P00435000", "buy", "sell")
    assert result.approved


def test_sizing_rejects_zero_equity():
    result = check_position_sizing(max_loss_per_contract=350, contracts=1, account_equity=0)
    assert not result.approved


def test_sizing_approves_within_cap():
    # $350 max loss * 1 contract = $350, on $100k equity = 0.35%, well within 15%
    result = check_position_sizing(max_loss_per_contract=350, contracts=1, account_equity=100000)
    assert result.approved
    assert result.max_loss_dollars == 350


def test_sizing_rejects_above_cap():
    # $350 max loss * 50 contracts = $17,500, on $100k equity = 17.5%, exceeds 15% cap
    result = check_position_sizing(max_loss_per_contract=350, contracts=50, account_equity=100000)
    assert not result.approved
    assert "exceeding the 15% per-trade cap" in result.reason


def test_sizing_boundary_exactly_at_cap():
    # exactly 15% of 100k = 15000
    result = check_position_sizing(max_loss_per_contract=15000, contracts=1, account_equity=100000)
    assert result.approved  # at, not over, the cap


def test_sizing_boundary_just_above_cap():
    result = check_position_sizing(max_loss_per_contract=15001, contracts=1, account_equity=100000)
    assert not result.approved


def test_sizing_suggests_max_affordable_contracts():
    result = check_position_sizing(max_loss_per_contract=1000, contracts=50, account_equity=100000)
    assert not result.approved
    assert "15 contract(s)" in result.reason  # 15% of 100k = 15000 / 1000 = 15


def test_max_trade_risk_pct_is_fifteen_percent():
    # Locks in the agreed-upon cap so an accidental future edit is caught by tests
    assert MAX_TRADE_RISK_PCT == 0.15
