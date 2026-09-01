"""
Tests for the rebuilt dashboard's trade grouping (build_trade_records)
and NYC time formatting — replaces the earlier per-order/per-leg
approach after user feedback that a spread should show as one trade
row, and that times should be in NYC format (dd mm yyyy HH:MM:SS),
not raw UTC.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ast


def _load_functions():
    with open(os.path.join(os.path.dirname(__file__), "..", "streamlit_app.py")) as f:
        source = f.read()
    tree = ast.parse(source)
    namespace = {"defaultdict": __import__("collections").defaultdict}
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    namespace["datetime"] = datetime
    namespace["timezone"] = timezone
    namespace["NYC_TZ"] = ZoneInfo("America/New_York")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "group_key", "root_symbol_from_group", "format_nyc", "build_trade_records"
        ):
            exec(ast.get_source_segment(source, node), namespace)
    return namespace["format_nyc"], namespace["build_trade_records"]


format_nyc, build_trade_records = _load_functions()


def test_expired_worthless_leg_closes_trade_with_realistic_alpaca_shape():
    """
    Alpaca never generates an order for an option expiring OTM — only a
    non-trade OPEXP activity, with net_amount "0". Without handling
    this, a spread with an expired leg would incorrectly stay 'open'
    forever. Mixed scenario: long leg actively closed for a gain, short
    leg expires worthless (keeping its full credit).
    """
    orders = [
        {"status": "filled", "filled_at": "2026-09-05T14:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260911C00770000", "position_intent": "buy_to_open", "side": "buy", "qty": "10", "filled_avg_price": "2.00"},
            {"symbol": "SPY260911C00775000", "position_intent": "sell_to_open", "side": "sell", "qty": "10", "filled_avg_price": "0.80"},
        ]},
        {"status": "filled", "filled_at": "2026-09-11T15:00:00", "order_class": "simple", "legs": [
            {"symbol": "SPY260911C00770000", "position_intent": "sell_to_close", "side": "sell", "qty": "10", "filled_avg_price": "3.50"},
        ]},
    ]
    expiry_activities = [
        {"activity_type": "OPEXP", "id": "test123", "date": "2026-09-11", "net_amount": "0",
         "description": "Option Expiry", "symbol": "SPY260911C00775000", "qty": "-10", "status": "executed"},
    ]
    trades = build_trade_records(orders, [], expiry_activities)
    assert len(trades) == 1
    t = trades[0]
    assert t["status"] == "closed"
    # long gain (3.50-2.00)*10*100=$1500 + short kept full credit 0.80*10*100=$800 = $2300
    assert abs(t["outcome"] - 2300.0) < 0.01
    sources = sorted(e["source"] for e in t["close_events"])
    assert sources == ["expiration", "order"]


def test_fully_worthless_expiry_with_zero_manual_closes():
    """Whole spread expires OTM, no closing order ever placed at all."""
    orders = [
        {"status": "filled", "filled_at": "2026-09-05T14:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260911C00770000", "position_intent": "buy_to_open", "side": "buy", "qty": "10", "filled_avg_price": "2.00"},
            {"symbol": "SPY260911C00775000", "position_intent": "sell_to_open", "side": "sell", "qty": "10", "filled_avg_price": "0.80"},
        ]},
    ]
    expiry_activities = [
        {"activity_type": "OPEXP", "date": "2026-09-11", "symbol": "SPY260911C00770000", "qty": "-10"},
        {"activity_type": "OPEXP", "date": "2026-09-11", "symbol": "SPY260911C00775000", "qty": "-10"},
    ]
    trades = build_trade_records(orders, [], expiry_activities)
    assert len(trades) == 1
    t = trades[0]
    assert t["status"] == "closed"
    assert abs(t["outcome"] - (-1200.0)) < 0.01  # net debit paid at open, lost in full
    assert t["profit_loss"] == "loss"


def test_no_expiry_activities_does_not_break_anything():
    """Backward compatibility: omitting expiry_activities entirely must still work."""
    trades = build_trade_records([], [])
    assert trades == []


def test_open_trade_not_shown_as_closed_without_matching_expiry():
    """A position with no close order and no matching expiry activity must stay 'open', not silently vanish or misclassify."""
    orders = [
        {"status": "filled", "filled_at": "2026-09-05T14:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260911C00770000", "position_intent": "buy_to_open", "side": "buy", "qty": "10", "filled_avg_price": "2.00"},
            {"symbol": "SPY260911C00775000", "position_intent": "sell_to_open", "side": "sell", "qty": "10", "filled_avg_price": "0.80"},
        ]},
    ]
    live_positions = [
        {"symbol": "SPY260911C00770000", "unrealized_pl": "50.0"},
        {"symbol": "SPY260911C00775000", "unrealized_pl": "-20.0"},
    ]
    trades = build_trade_records(orders, live_positions, expiry_activities=[])
    assert len(trades) == 1
    assert trades[0]["status"] == "open"


def test_open_trade_qty_and_current_value_match_real_agent_math():
    """
    Verified against the real QQQ 717/720 position's own hand-computed
    math from the agent's live reasoning log: 20 contracts, current net
    value ~$1.24/contract (long $2.19 - short $0.95).
    """
    orders = [{"status": "filled", "filled_at": "2026-08-31T19:55:50", "order_class": "mleg", "legs": [
        {"symbol": "QQQ260901C00720000", "position_intent": "sell_to_open", "side": "sell", "qty": "20", "filled_avg_price": "0.97"},
        {"symbol": "QQQ260901C00717000", "position_intent": "buy_to_open", "side": "buy", "qty": "20", "filled_avg_price": "2.23"}]}]
    live_positions = [
        {"symbol": "QQQ260901C00717000", "unrealized_pl": "-8.0", "market_value": "4380.0"},
        {"symbol": "QQQ260901C00720000", "unrealized_pl": "40.0", "market_value": "-1900.0"},
    ]
    trades = build_trade_records(orders, live_positions, [])
    t = trades[0]
    assert t["qty"] == 20
    assert abs(t["current_value_per_contract"] - 1.24) < 0.01


def test_closed_trade_has_no_current_value():
    """A closed trade is done — 'current value' isn't a meaningful concept for it, should be None not a stale number."""
    trades = build_trade_records(REAL_QQQ_ORDERS, [], [])
    closed = [t for t in trades if t["status"] == "closed"]
    assert len(closed) == 1
    assert closed[0]["current_value_per_contract"] is None


def test_qty_correct_across_multiple_open_orders():
    """The real QQQ 716/720 trade opened in two 40-contract tranches -- qty must be 80, not 40."""
    trades = build_trade_records(REAL_QQQ_ORDERS, [], [])
    closed = [t for t in trades if t["status"] == "closed"]
    assert closed[0]["qty"] == 80

REAL_QQQ_ORDERS = [
    {"status": "filled", "filled_at": "2026-08-31T16:01:12", "order_class": "simple", "legs": None,
     "symbol": "QQQ260902C00716000", "position_intent": "sell_to_close", "side": "sell", "qty": "80", "filled_avg_price": "2.32"},
    {"status": "filled", "filled_at": "2026-08-31T16:00:40", "order_class": "simple", "legs": None,
     "symbol": "QQQ260902C00720000", "position_intent": "buy_to_close", "side": "buy", "qty": "80", "filled_avg_price": "1.06"},
    {"status": "filled", "filled_at": "2026-08-31T14:16:40", "order_class": "mleg", "legs": [
        {"symbol": "QQQ260902C00720000", "position_intent": "sell_to_open", "side": "sell", "qty": "40", "filled_avg_price": "1.17"},
        {"symbol": "QQQ260902C00716000", "position_intent": "buy_to_open", "side": "buy", "qty": "40", "filled_avg_price": "2.55"}]},
    {"status": "filled", "filled_at": "2026-08-31T13:31:50", "order_class": "mleg", "legs": [
        {"symbol": "QQQ260902C00720000", "position_intent": "sell_to_open", "side": "sell", "qty": "40", "filled_avg_price": "1.81"},
        {"symbol": "QQQ260902C00716000", "position_intent": "buy_to_open", "side": "buy", "qty": "40", "filled_avg_price": "3.57"}]},
]


def test_nyc_format_converts_correctly():
    # 16:01:12 UTC in August (EDT, UTC-4) = 12:01:12 NYC
    assert format_nyc("2026-08-31T16:01:12+00:00") == "31 08 2026 12:01:12"


def test_nyc_format_handles_z_suffix():
    assert format_nyc("2026-08-31T16:01:12Z") == "31 08 2026 12:01:12"


def test_nyc_format_empty_string():
    assert format_nyc("") == "—"


def test_multi_order_spread_becomes_one_trade_row():
    trades = build_trade_records(REAL_QQQ_ORDERS, [])
    assert len(trades) == 1
    t = trades[0]
    assert t["underlying"] == "QQQ"
    assert t["status"] == "closed"
    assert abs(t["outcome"] - (-2480.0)) < 0.01
    assert t["profit_loss"] == "loss"
    assert t["class"] == "multi"


def test_initial_open_vs_modification_split_correctly():
    trades = build_trade_records(REAL_QQQ_ORDERS, [])
    t = trades[0]
    assert len(t["initial_open_events"]) == 2  # first mleg order's 2 legs
    assert len(t["modification_events"]) == 2  # second mleg order's 2 legs
    assert len(t["close_events"]) == 2


def test_open_position_shows_live_unrealized_not_realized():
    orders = [
        {"status": "filled", "filled_at": "2026-08-31T19:55:50", "order_class": "mleg", "legs": [
            {"symbol": "QQQ260901C00720000", "position_intent": "sell_to_open", "side": "sell", "qty": "20", "filled_avg_price": "0.97"},
            {"symbol": "QQQ260901C00717000", "position_intent": "buy_to_open", "side": "buy", "qty": "20", "filled_avg_price": "2.23"}]},
    ]
    live_positions = [
        {"symbol": "QQQ260901C00717000", "unrealized_pl": "40.0"},
        {"symbol": "QQQ260901C00720000", "unrealized_pl": "-60.0"},
    ]
    trades = build_trade_records(orders, live_positions)
    assert len(trades) == 1
    t = trades[0]
    assert t["status"] == "open"
    assert t["profit_loss"] is None
    assert t["time_closed"] is None
    assert abs(t["outcome"] - (-20.0)) < 0.01


def test_different_expirations_stay_separate_trades():
    combined = REAL_QQQ_ORDERS + [
        {"status": "filled", "filled_at": "2026-08-31T19:55:50", "order_class": "mleg", "legs": [
            {"symbol": "QQQ260901C00720000", "position_intent": "sell_to_open", "side": "sell", "qty": "20", "filled_avg_price": "0.97"},
            {"symbol": "QQQ260901C00717000", "position_intent": "buy_to_open", "side": "buy", "qty": "20", "filled_avg_price": "2.23"}]},
    ]
    trades = build_trade_records(combined, [
        {"symbol": "QQQ260901C00717000", "unrealized_pl": "40.0"},
        {"symbol": "QQQ260901C00720000", "unrealized_pl": "-60.0"},
    ])
    assert len(trades) == 2
    statuses = sorted(t["status"] for t in trades)
    assert statuses == ["closed", "open"]


def test_two_sequential_trades_same_underlying_stay_separate():
    """Fully closed, then opened again later — must be 2 trades, not merged."""
    orders = [
        {"status": "filled", "filled_at": "2026-09-01T10:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260904C00770000", "position_intent": "buy_to_open", "side": "buy", "qty": "10", "filled_avg_price": "2.00"},
            {"symbol": "SPY260904C00775000", "position_intent": "sell_to_open", "side": "sell", "qty": "10", "filled_avg_price": "1.00"}]},
        {"status": "filled", "filled_at": "2026-09-01T11:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260904C00770000", "position_intent": "sell_to_close", "side": "sell", "qty": "10", "filled_avg_price": "2.50"},
            {"symbol": "SPY260904C00775000", "position_intent": "buy_to_close", "side": "buy", "qty": "10", "filled_avg_price": "1.20"}]},
        {"status": "filled", "filled_at": "2026-09-01T13:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260904C00770000", "position_intent": "buy_to_open", "side": "buy", "qty": "5", "filled_avg_price": "1.50"},
            {"symbol": "SPY260904C00775000", "position_intent": "sell_to_open", "side": "sell", "qty": "5", "filled_avg_price": "0.80"}]},
        {"status": "filled", "filled_at": "2026-09-01T14:00:00", "order_class": "mleg", "legs": [
            {"symbol": "SPY260904C00770000", "position_intent": "sell_to_close", "side": "sell", "qty": "5", "filled_avg_price": "1.00"},
            {"symbol": "SPY260904C00775000", "position_intent": "buy_to_close", "side": "buy", "qty": "5", "filled_avg_price": "0.50"}]},
    ]
    trades = build_trade_records(orders, [])
    assert len(trades) == 2


def test_no_orders_returns_empty():
    assert build_trade_records([], []) == []


def _load_parse_occ():
    with open(os.path.join(os.path.dirname(__file__), "..", "streamlit_app.py")) as f:
        source = f.read()
    tree = ast.parse(source)
    namespace = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "parse_occ_symbol":
            exec(ast.get_source_segment(source, node), namespace)
    return namespace["parse_occ_symbol"]


parse_occ_symbol = _load_parse_occ()


def test_parse_occ_symbol_real_call():
    result = parse_occ_symbol("QQQ260902C00716000")
    assert result["underlying"] == "QQQ"
    assert result["type"] == "Call"
    assert result["strike"] == "$716.00"
    assert result["expiry"] == "02 09 2026"


def test_parse_occ_symbol_real_put():
    result = parse_occ_symbol("SPY260904P00440000")
    assert result["type"] == "Put"
    assert result["strike"] == "$440.00"


def test_parse_occ_symbol_handles_garbage_input():
    result = parse_occ_symbol("not_a_real_symbol")
    assert result["type"] == "—"
