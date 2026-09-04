"""
Shared trade-record reconstruction logic.

This was originally implemented only in streamlit_app.py for the demo
dashboard. It's extracted here, unchanged, so that agent_layer/tools.py
can build the exact same round-trip trade records for the
get_setup_performance tool — the agent's own win-rate-by-setup query —
without a second, possibly-drifting copy of this logic. streamlit_app.py
now imports from here instead of defining these functions locally.

No streamlit dependency in this module — pure functions over
orders/positions/activities data, usable from either the dashboard or
the live agent process.
"""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from itertools import groupby


def parse_underlying(option_symbol: str) -> str:
    """OCC symbol: root + YYMMDD(6) + C/P(1) + strike*1000(8) = 15 trailing chars."""
    if not option_symbol or len(option_symbol) < 16:
        return option_symbol or "—"
    return option_symbol[:-15]


def group_key(symbol: str) -> str:
    """OCC symbol minus type+strike (9 trailing chars) = underlying+expiration, used to
    group both legs of one spread together regardless of how many separate orders
    opened or closed it."""
    return symbol[:-9] if len(symbol) >= 9 else symbol


def root_symbol_from_group(gkey: str) -> str:
    """group_key is root+YYMMDD (6 trailing digits) — strip those to get the plain
    underlying ticker for quote/bar lookups, e.g. 'QQQ260902' -> 'QQQ'."""
    return gkey[:-6] if len(gkey) > 6 else gkey


def parse_occ_symbol(symbol: str) -> dict:
    """Parses an OCC option symbol into its real components: underlying,
    expiry, put/call, strike."""
    if not symbol or len(symbol) < 16:
        return {"underlying": symbol or "—", "expiry": "—", "type": "—", "strike": "—"}
    underlying = symbol[:-15]
    date_code = symbol[-15:-9]  # YYMMDD
    opt_type = symbol[-9]
    strike_raw = symbol[-8:]
    try:
        yy, mm, dd = date_code[0:2], date_code[2:4], date_code[4:6]
        expiry = f"{dd} {mm} 20{yy}"
    except Exception:
        expiry = "—"
    try:
        strike = f"${int(strike_raw) / 1000:.2f}"
    except Exception:
        strike = "—"
    return {
        "underlying": underlying,
        "expiry": expiry,
        "type": "Call" if opt_type == "C" else ("Put" if opt_type == "P" else "—"),
        "strike": strike,
    }


def build_trade_records(orders, live_positions, expiry_activities=None):
    """
    Builds ONE record per actual round-trip TRADE, not one per order and
    not one per closing leg. Tracks trades by their ACTUAL set of
    symbols (specific strikes), not just underlying+expiration — two
    different strike combinations sharing the same underlying and
    expiry are genuinely different trades and must never be merged.

    Multiple trades can be active concurrently within the same
    underlying+expiration group — each is tracked independently by
    which specific symbols currently belong to it, only merging
    genuine repeat activity on the SAME symbols (true modifications),
    never activity on a different strike combination.

    Currently-open trades (never returned to flat) are included with
    status='open' and outcome = live unrealized P&L from the position.

    expiry_activities: Alpaca's OPEXP non-trade activities. An option
    expiring OTM never generates a closing ORDER at all — Alpaca just
    flattens the position silently — so without this, an expired
    position would incorrectly stay classified as 'open' forever, with
    no outcome. Each OPEXP event is converted into a synthetic
    zero-price close, correctly determined from which side (long/short)
    the symbol is actually sitting in at that point in the timeline.
    """
    leg_events = []
    for o in orders:
        if o.get("status") != "filled":
            continue
        legs = o.get("legs") or [o]
        ts = o.get("filled_at") or o.get("submitted_at") or ""
        order_class = o.get("order_class", "simple")
        for leg in legs:
            symbol = leg.get("symbol")
            qty = float(leg.get("qty", 0) or 0)
            if not symbol or qty == 0:
                continue
            leg_events.append({
                "ts": ts,
                "symbol": symbol,
                "intent": leg.get("position_intent") or "",
                "side": leg.get("side"),
                "qty": qty,
                "price": float(leg.get("filled_avg_price", 0) or 0),
                "order_class": order_class,
                "source": "order",
            })

    for act in (expiry_activities or []):
        symbol = act.get("symbol")
        raw_qty = act.get("qty")
        if not symbol or raw_qty is None:
            continue
        qty = abs(float(raw_qty))
        if qty == 0:
            continue
        date_str = act.get("date") or ""
        ts = f"{date_str}T20:00:00Z" if date_str and "T" not in date_str else (date_str or "")
        leg_events.append({
            "ts": ts,
            "symbol": symbol,
            "intent": "expire",  # resolved to sell_to_close/buy_to_close during processing
            "side": None,
            "qty": qty,
            "price": 0.0,
            "order_class": "expiration",
            "source": "expiration",
        })

    leg_events.sort(key=lambda e: e["ts"])

    groups = defaultdict(list)
    for e in leg_events:
        groups[group_key(e["symbol"])].append(e)

    trades = []
    for gkey, events in groups.items():
        active_trades = []

        def new_active_trade():
            return {
                "symbols": set(),
                "long_lots": defaultdict(list),
                "short_lots": defaultdict(list),
                "long_remaining": 0.0,
                "short_remaining": 0.0,
                "events": [],
                "pending_pnl": 0.0,
            }

        def find_active_trade_for(symbol):
            for at in active_trades:
                if symbol in at["symbols"]:
                    return at
            return None

        def flush(at, is_open):
            current_events = at["events"]
            if not current_events:
                return
            open_events = [e for e in current_events if e["intent"] in ("buy_to_open", "sell_to_open")]
            close_events = [e for e in current_events if e["intent"] in ("sell_to_close", "buy_to_close")]
            symbols_involved = sorted(set(e["symbol"] for e in current_events))
            trade_class = "multi" if len(symbols_involved) > 1 else "single"
            time_opened = open_events[0]["ts"] if open_events else current_events[0]["ts"]
            time_closed = close_events[-1]["ts"] if close_events else None

            initial_ts = open_events[0]["ts"] if open_events else None
            initial_open_events = [e for e in open_events if e["ts"] == initial_ts]
            modification_events = [e for e in open_events if e["ts"] != initial_ts]

            qty_per_symbol = defaultdict(float)
            for e in open_events:
                qty_per_symbol[e["symbol"]] += e["qty"]
            total_qty = max(qty_per_symbol.values()) if qty_per_symbol else 0

            if is_open:
                outcome = sum(
                    float(p.get("unrealized_pl", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                current_value_total = sum(
                    float(p.get("market_value", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                current_value_per_contract = (current_value_total / (total_qty * 100)) if total_qty else None
                entry_value_total = sum(
                    float(p.get("cost_basis", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                purchase_price_per_contract = (entry_value_total / (total_qty * 100)) if total_qty else None
                pl_word = None
                status = "open"
            else:
                outcome = at["pending_pnl"]
                current_value_per_contract = None
                purchase_price_per_contract = None
                pl_word = "win" if outcome > 0 else ("loss" if outcome < 0 else "flat")
                status = "closed"

            trades.append({
                "underlying": root_symbol_from_group(gkey),
                "group_key": gkey,
                "time_opened": time_opened,
                "time_closed": time_closed,
                "class": trade_class,
                "status": status,
                "qty": total_qty,
                "current_value_per_contract": current_value_per_contract,
                "purchase_price_per_contract": purchase_price_per_contract,
                "outcome": outcome,
                "profit_loss": pl_word,
                "initial_open_events": initial_open_events,
                "modification_events": modification_events,
                "close_events": close_events,
            })

        for ts, batch_iter in groupby(events, key=lambda e: e["ts"]):
            batch = list(batch_iter)
            open_events_in_batch = [e for e in batch if e["intent"] in ("buy_to_open", "sell_to_open")]
            other_events_in_batch = [e for e in batch if e["intent"] not in ("buy_to_open", "sell_to_open")]

            if open_events_in_batch:
                target = None
                for e in open_events_in_batch:
                    found = find_active_trade_for(e["symbol"])
                    if found is not None:
                        target = found
                        break
                if target is None:
                    target = new_active_trade()
                    active_trades.append(target)
                for e in open_events_in_batch:
                    symbol, intent, qty, price = e["symbol"], e["intent"], e["qty"], e["price"]
                    target["symbols"].add(symbol)
                    target["events"].append(e)
                    if intent == "buy_to_open":
                        target["long_lots"][symbol].append([qty, price])
                        target["long_remaining"] += qty
                    else:
                        target["short_lots"][symbol].append([qty, price])
                        target["short_remaining"] += qty

            for e in other_events_in_batch:
                symbol, intent, qty, price = e["symbol"], e["intent"], e["qty"], e["price"]
                at = find_active_trade_for(symbol)

                if intent == "expire":
                    if at is None:
                        continue
                    if at["long_lots"][symbol]:
                        intent = "sell_to_close"
                    elif at["short_lots"][symbol]:
                        intent = "buy_to_close"
                    else:
                        continue
                    e["intent"] = intent

                if intent not in ("sell_to_close", "buy_to_close"):
                    continue
                if at is None:
                    continue
                at["events"].append(e)
                if intent == "sell_to_close":
                    remaining, leg_pnl = qty, 0.0
                    while remaining > 0 and at["long_lots"][symbol]:
                        lot_qty, lot_price = at["long_lots"][symbol][0]
                        matched = min(lot_qty, remaining)
                        leg_pnl += (price - lot_price) * matched * 100
                        lot_qty -= matched
                        remaining -= matched
                        if lot_qty <= 0:
                            at["long_lots"][symbol].pop(0)
                        else:
                            at["long_lots"][symbol][0][0] = lot_qty
                    at["pending_pnl"] += leg_pnl
                    at["long_remaining"] -= qty
                else:  # buy_to_close
                    remaining, leg_pnl = qty, 0.0
                    while remaining > 0 and at["short_lots"][symbol]:
                        lot_qty, lot_price = at["short_lots"][symbol][0]
                        matched = min(lot_qty, remaining)
                        leg_pnl += (lot_price - price) * matched * 100
                        lot_qty -= matched
                        remaining -= matched
                        if lot_qty <= 0:
                            at["short_lots"][symbol].pop(0)
                        else:
                            at["short_lots"][symbol][0][0] = lot_qty
                    at["pending_pnl"] += leg_pnl
                    at["short_remaining"] -= qty

                if at["long_remaining"] <= 0.0001 and at["short_remaining"] <= 0.0001:
                    flush(at, is_open=False)
                    active_trades.remove(at)

        for at in active_trades:
            if not at["events"]:
                continue

            live_symbols = {p.get("symbol") for p in live_positions}
            still_held = bool(at["symbols"] & live_symbols)
            expiry_passed = False
            any_symbol = next(iter(at["symbols"]), None)
            expiry_ts = None
            if any_symbol and len(any_symbol) >= 15:
                date_code = any_symbol[-15:-9]
                try:
                    yy, mm, dd = int(date_code[0:2]), int(date_code[2:4]), int(date_code[4:6])
                    expiry_dt = datetime(2000 + yy, mm, dd, tzinfo=timezone.utc) + timedelta(hours=21)
                    expiry_ts = expiry_dt.isoformat()
                    expiry_passed = datetime.now(timezone.utc) > expiry_dt
                except Exception:
                    pass

            if not still_held and expiry_passed:
                for symbol, lots in list(at["long_lots"].items()):
                    remaining_qty = sum(l[0] for l in lots)
                    if remaining_qty > 0:
                        leg_pnl = sum((0 - lot_price) * lot_qty * 100 for lot_qty, lot_price in lots)
                        at["pending_pnl"] += leg_pnl
                        at["events"].append({
                            "ts": expiry_ts, "symbol": symbol, "intent": "sell_to_close", "side": None,
                            "qty": remaining_qty, "price": 0.0, "order_class": "expiration", "source": "expiration",
                        })
                for symbol, lots in list(at["short_lots"].items()):
                    remaining_qty = sum(l[0] for l in lots)
                    if remaining_qty > 0:
                        leg_pnl = sum((lot_price - 0) * lot_qty * 100 for lot_qty, lot_price in lots)
                        at["pending_pnl"] += leg_pnl
                        at["events"].append({
                            "ts": expiry_ts, "symbol": symbol, "intent": "buy_to_close", "side": None,
                            "qty": remaining_qty, "price": 0.0, "order_class": "expiration", "source": "expiration",
                        })
                flush(at, is_open=False)
            else:
                flush(at, is_open=True)

    trades.sort(key=lambda t: t["time_opened"], reverse=True)
    return trades
