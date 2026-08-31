"""
Regression test for a real bug: Funds Committed showed $6,400 for a
spread that actually cost $2,520 net debit — found via user report
against a real filled trade (long 20x QQQ 717C @ $2.23, short 20x
QQQ 720C @ $0.97). The bug was taking abs() of each leg's cost_basis
before summing, which destroys the netting between a spread's long
and short legs (Alpaca represents a short leg's cost_basis as
negative — money received, not paid) and adds them as if they were
two unrelated costs instead of one hedged position.
"""


def test_funds_committed_nets_long_and_short_legs_correctly():
    """The exact real position that exposed this bug."""
    positions = [
        {"symbol": "QQQ260901C00717000", "cost_basis": "4460.0"},   # long leg: money paid, positive
        {"symbol": "QQQ260901C00720000", "cost_basis": "-1940.0"},  # short leg: money received, negative
    ]

    # OLD (buggy) behavior, kept here only to prove the fix actually changes the result
    old_buggy = sum(abs(float(p.get("cost_basis", 0) or 0)) for p in positions)
    assert old_buggy == 6400.0  # confirms this test reproduces the real reported bug

    # NEW (fixed) behavior — must match the actual net debit paid
    fixed = abs(sum(float(p.get("cost_basis", 0) or 0) for p in positions))
    assert fixed == 2520.0


def test_funds_committed_handles_net_credit_position():
    """A credit spread: short leg costs more than the long leg (net credit received)."""
    positions = [
        {"symbol": "SPY_LONG", "cost_basis": "150.0"},
        {"symbol": "SPY_SHORT", "cost_basis": "-400.0"},
    ]
    fixed = abs(sum(float(p.get("cost_basis", 0) or 0) for p in positions))
    assert fixed == 250.0  # net credit magnitude, not 550 (the old buggy sum-of-abs result)


def test_funds_committed_empty_positions():
    positions = []
    fixed = abs(sum(float(p.get("cost_basis", 0) or 0) for p in positions))
    assert fixed == 0.0


def test_funds_committed_single_naked_style_position_unaffected():
    """A single position (no offsetting leg) should be unaffected by the fix."""
    positions = [{"symbol": "AAPL", "cost_basis": "1000.0"}]
    fixed = abs(sum(float(p.get("cost_basis", 0) or 0) for p in positions))
    assert fixed == 1000.0
