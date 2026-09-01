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
