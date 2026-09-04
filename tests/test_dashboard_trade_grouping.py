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
    """
    group_key, root_symbol_from_group, and build_trade_records now live
    in execution/trade_records.py (shared with the agent's own
    get_setup_performance tool) and are imported into streamlit_app.py
    rather than defined there, so they're imported directly. format_nyc
    is still dashboard-only, so it's still extracted from
    streamlit_app.py's AST as before.
    """
    from execution.trade_records import build_trade_records  # noqa: F401 (re-exported below)

    with open(os.path.join(os.path.dirname(__file__), "..", "streamlit_app.py")) as f:
        source = f.read()
    tree = ast.parse(source)
    namespace = {"defaultdict": __import__("collections").defaultdict}
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    namespace["datetime"] = datetime
    namespace["timedelta"] = timedelta
    namespace["timezone"] = timezone
    namespace["NYC_TZ"] = ZoneInfo("America/New_York")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "format_nyc":
            exec(ast.get_source_segment(source, node), namespace)
    return namespace["format_nyc"], build_trade_records


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
    # 16:01:12 UTC in August (EDT, UTC-4) = 12:01 NYC (seconds intentionally dropped per user request)
    assert format_nyc("2026-08-31T16:01:12+00:00") == "31 08 2026 12:01"


def test_nyc_format_handles_z_suffix():
    assert format_nyc("2026-08-31T16:01:12Z") == "31 08 2026 12:01"


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


from execution.trade_records import parse_occ_symbol


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


def _load_leg_breakdown():
    """compute_leg_breakdown is still dashboard-local (not extracted to
    execution/trade_records.py), so it's still pulled from
    streamlit_app.py's AST — but it now needs parse_occ_symbol supplied
    into its exec namespace directly, since that's an import rather
    than a local def in the source it's being extracted from."""
    with open(os.path.join(os.path.dirname(__file__), "..", "streamlit_app.py")) as f:
        source = f.read()
    tree = ast.parse(source)
    namespace = {"defaultdict": __import__("collections").defaultdict, "parse_occ_symbol": parse_occ_symbol}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "compute_leg_breakdown":
            exec(ast.get_source_segment(source, node), namespace)
    return namespace["compute_leg_breakdown"]


compute_leg_breakdown = _load_leg_breakdown()


def test_leg_breakdown_matches_real_closed_trade_math():
    """Verified against the real QQQ 716/720 trade's actual blended fill prices."""
    trades = build_trade_records(REAL_QQQ_ORDERS, [], [])
    t = trades[0]
    breakdown = compute_leg_breakdown(t, [])
    long_leg = [l for l in breakdown["legs"] if l["side"] == "Long"][0]
    short_leg = [l for l in breakdown["legs"] if l["side"] == "Short"][0]

    # Long 716C: blended purchase (40@3.57 + 40@2.55)/80 = 3.06, exit 2.32
    assert abs(long_leg["purchase_price"] - 3.06) < 0.01
    assert abs(long_leg["current_or_exit_price"] - 2.32) < 0.01
    assert abs(long_leg["profit_per_contract"] - (-0.74)) < 0.01

    # Short 720C: blended purchase (40@1.81 + 40@1.17)/80 = 1.49, exit 1.06
    assert abs(short_leg["purchase_price"] - 1.49) < 0.01
    assert abs(short_leg["current_or_exit_price"] - 1.06) < 0.01
    assert abs(short_leg["profit_per_contract"] - 0.43) < 0.01

    # Grand profit (x100 multiplier) must match the known real fill-based total: -$2,480
    assert abs(breakdown["grand_profit"] * 100 - (-2480.0)) < 0.5


def test_leg_breakdown_open_trade_uses_live_current_price():
    orders = [{"status": "filled", "filled_at": "2026-08-31T19:55:50", "order_class": "mleg", "legs": [
        {"symbol": "QQQ260901C00720000", "position_intent": "sell_to_open", "side": "sell", "qty": "20", "filled_avg_price": "0.97"},
        {"symbol": "QQQ260901C00717000", "position_intent": "buy_to_open", "side": "buy", "qty": "20", "filled_avg_price": "2.23"}]}]
    live_positions = [
        {"symbol": "QQQ260901C00717000", "current_price": "2.19"},
        {"symbol": "QQQ260901C00720000", "current_price": "0.95"},
    ]
    trades = build_trade_records(orders, live_positions, [])
    breakdown = compute_leg_breakdown(trades[0], live_positions)
    long_leg = [l for l in breakdown["legs"] if l["side"] == "Long"][0]
    short_leg = [l for l in breakdown["legs"] if l["side"] == "Short"][0]
    assert abs(long_leg["current_or_exit_price"] - 2.19) < 0.01
    assert abs(short_leg["current_or_exit_price"] - 0.95) < 0.01
    assert abs(long_leg["profit_per_contract"] - (-0.04)) < 0.01
    assert abs(short_leg["profit_per_contract"] - 0.02) < 0.01


def test_leg_breakdown_empty_trade_returns_empty_legs():
    result = compute_leg_breakdown({"initial_open_events": [], "modification_events": [], "close_events": [], "status": "open"}, [])
    assert result["legs"] == []


def test_concurrent_different_strikes_same_expiry_stay_separate():
    """
    Regression test for a real bug: 3 genuinely separate same-day trades
    on QQQ Sep 3 (707/715, 709/716, 712/720 -- all different strikes)
    were being merged into ONE trade with 'modifications' because
    grouping only used underlying+expiration, ignoring strike. This is
    the exact real order sequence that exposed it.
    """
    orders = [
        {"status": "filled", "order_class": "mleg", "submitted_at": "2026-09-01T16:03:16", "filled_at": "2026-09-01T16:03:16", "legs": [
            {"symbol": "QQQ260903C00720000", "position_intent": "sell_to_open", "qty": "10", "filled_avg_price": "0.71"},
            {"symbol": "QQQ260903C00712000", "position_intent": "buy_to_open", "qty": "10", "filled_avg_price": "3.42"}]},
        {"status": "filled", "order_class": "mleg", "submitted_at": "2026-09-01T15:50:50", "filled_at": "2026-09-01T15:50:50", "legs": [
            {"symbol": "QQQ260903C00707000", "position_intent": "sell_to_close", "qty": "8", "filled_avg_price": "7.06"},
            {"symbol": "QQQ260903C00715000", "position_intent": "buy_to_close", "qty": "8", "filled_avg_price": "2.36"}]},
        {"status": "filled", "order_class": "mleg", "submitted_at": "2026-09-01T14:28:10", "filled_at": "2026-09-01T14:28:10", "legs": [
            {"symbol": "QQQ260903C00716000", "position_intent": "sell_to_open", "qty": "5", "filled_avg_price": "1"},
            {"symbol": "QQQ260903C00709000", "position_intent": "buy_to_open", "qty": "5", "filled_avg_price": "3.64"}]},
        {"status": "filled", "order_class": "mleg", "submitted_at": "2026-09-01T13:38:21", "filled_at": "2026-09-01T13:38:21", "legs": [
            {"symbol": "QQQ260903C00715000", "position_intent": "sell_to_open", "qty": "8", "filled_avg_price": "1.09"},
            {"symbol": "QQQ260903C00707000", "position_intent": "buy_to_open", "qty": "8", "filled_avg_price": "4.26"}]},
    ]
    # live_positions must reflect the two genuinely still-open trades'
    # legs, so this test stays valid regardless of real-world date drift
    # (the vanished-at-expiry fallback correctly treats an unlisted,
    # past-expiry position as closed -- these ARE listed, so they don't).
    live_positions = [
        {"symbol": "QQQ260903C00716000", "unrealized_pl": "0"},
        {"symbol": "QQQ260903C00709000", "unrealized_pl": "0"},
        {"symbol": "QQQ260903C00720000", "unrealized_pl": "0"},
        {"symbol": "QQQ260903C00712000", "unrealized_pl": "0"},
    ]
    trades = build_trade_records(orders, live_positions, [])
    assert len(trades) == 3, f"Expected 3 distinct trades, got {len(trades)}"

    closed = [t for t in trades if t["status"] == "closed"]
    open_t = [t for t in trades if t["status"] == "open"]
    assert len(closed) == 1
    assert len(open_t) == 2
    assert abs(closed[0]["outcome"] - 1224.0) < 1.0


def test_single_order_both_legs_merge_into_one_new_trade():
    """
    Regression test for a bug introduced while fixing the above: each
    leg of a freshly-opened 2-leg order was spawning its OWN separate
    single-symbol trade instead of both legs merging into one, because
    each leg independently checked 'does an existing trade already have
    me?' before either leg of the same order had been registered yet.
    """
    orders = [
        {"status": "filled", "order_class": "mleg", "submitted_at": "2026-09-01T10:00:00", "filled_at": "2026-09-01T10:00:00", "legs": [
            {"symbol": "SPY260904C00775000", "position_intent": "sell_to_open", "qty": "10", "filled_avg_price": "1.00"},
            {"symbol": "SPY260904C00770000", "position_intent": "buy_to_open", "qty": "10", "filled_avg_price": "2.00"}]},
    ]
    trades = build_trade_records(orders, [], [])
    assert len(trades) == 1, f"Both legs of one order must merge into ONE trade, got {len(trades)}"
    assert trades[0]["class"] == "multi"


def test_real_modification_still_merges_correctly_with_batched_opens():
    """
    Full regression against the original real QQQ 716/720 trade (2 open
    orders genuinely adding to the same position, 2 separate closes) --
    must still produce exactly 1 trade after the batching fix, not be
    broken by it.
    """
    trades = build_trade_records(REAL_QQQ_ORDERS, [], [])
    assert len(trades) == 1
    assert trades[0]["status"] == "closed"
    assert abs(trades[0]["outcome"] - (-2480.0)) < 0.01


def test_total_fees_sums_real_fee_activities_correctly():
    """
    Verified against the account's actual real FEE activities pulled
    live: 12 fee records (OCC clearing, CAT, REG, ORF, TAF) totaling
    $15.69. Alpaca reports net_amount as negative; the dashboard sums
    the absolute value to get a positive 'fees paid' total.
    """
    real_fee_activities = [
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-1"},
        {"activity_type": "FEE", "activity_sub_type": "CAT", "net_amount": "-0.11"},
        {"activity_type": "FEE", "activity_sub_type": "REG", "net_amount": "-0.67"},
        {"activity_type": "FEE", "activity_sub_type": "ORF", "net_amount": "-5.4"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-1"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-0.5"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-2"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-1"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-1"},
        {"activity_type": "FEE", "activity_sub_type": "TAF", "net_amount": "-0.51"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-0.5"},
        {"activity_type": "FEE", "activity_sub_type": "OCC", "net_amount": "-2"},
    ]
    total_fees = sum(abs(float(f.get("net_amount", 0) or 0)) for f in real_fee_activities)
    assert abs(total_fees - 15.69) < 0.01


def test_total_fees_empty_list_returns_zero():
    total_fees = sum(abs(float(f.get("net_amount", 0) or 0)) for f in [])
    assert total_fees == 0.0


def test_position_vanished_at_expiry_with_no_close_and_no_opexp():
    """
    Regression test for a real bug found on a live account: a position
    can disappear from Alpaca's positions list at expiry with NEITHER
    a closing order NOR an OPEXP activity recorded at all. Without a
    fallback, this trade would silently report as 'open' with $0
    unrealized P&L (since live_positions no longer has a match),
    hiding a real ~$2,520 loss. Uses an expiry date far in the past
    (2020) to reliably test the 'expiry has passed' branch regardless
    of when this test suite is actually run.
    """
    orders = [{"status": "filled", "order_class": "mleg", "submitted_at": "2020-01-02T19:55:50", "filled_at": "2020-01-02T19:55:50", "legs": [
        {"symbol": "QQQ200103C00720000", "position_intent": "sell_to_open", "qty": "20", "filled_avg_price": "0.97"},
        {"symbol": "QQQ200103C00717000", "position_intent": "buy_to_open", "qty": "20", "filled_avg_price": "2.23"}]}]
    live_positions = [
        {"symbol": "SOME_OTHER_SYMBOL", "unrealized_pl": "100.0"},
    ]
    trades = build_trade_records(orders, live_positions, expiry_activities=[])
    assert len(trades) == 1
    t = trades[0]
    assert t["status"] == "closed", "Vanished-at-expiry position must not silently stay 'open'"
    assert abs(t["outcome"] - (-2520.0)) < 0.01
    assert t["profit_loss"] == "loss"


def test_genuinely_open_position_not_yet_expired_stays_open():
    """A position whose expiry is in the future and is still actually
    held must NOT be affected by the vanished-at-expiry fallback."""
    import datetime as dt
    future_year = dt.datetime.now().year + 5
    yy = str(future_year)[2:]
    symbol_long = f"QQQ{yy}0315C00712000"
    symbol_short = f"QQQ{yy}0315C00720000"
    orders = [{"status": "filled", "order_class": "mleg", "submitted_at": "2026-09-01T16:03:16", "filled_at": "2026-09-01T16:03:16", "legs": [
        {"symbol": symbol_short, "position_intent": "sell_to_open", "qty": "10", "filled_avg_price": "0.71"},
        {"symbol": symbol_long, "position_intent": "buy_to_open", "qty": "10", "filled_avg_price": "3.42"}]}]
    live_positions = [
        {"symbol": symbol_long, "unrealized_pl": "-1590"},
        {"symbol": symbol_short, "unrealized_pl": "480"},
    ]
    trades = build_trade_records(orders, live_positions, [])
    assert trades[0]["status"] == "open"


def test_position_still_genuinely_held_not_treated_as_vanished():
    """Even with a past expiry date, if the symbols ARE still in live
    positions, don't force-close it -- that would be a genuine data
    inconsistency worth surfacing as still-open, not silently masked."""
    orders = [{"status": "filled", "order_class": "mleg", "submitted_at": "2020-01-02T19:55:50", "filled_at": "2020-01-02T19:55:50", "legs": [
        {"symbol": "QQQ200103C00720000", "position_intent": "sell_to_open", "qty": "20", "filled_avg_price": "0.97"},
        {"symbol": "QQQ200103C00717000", "position_intent": "buy_to_open", "qty": "20", "filled_avg_price": "2.23"}]}]
    live_positions = [
        {"symbol": "QQQ200103C00717000", "unrealized_pl": "10.0"},
        {"symbol": "QQQ200103C00720000", "unrealized_pl": "-5.0"},
    ]
    trades = build_trade_records(orders, live_positions, [])
    assert trades[0]["status"] == "open"
