"""
Streamlit demo dashboard for the hackathon's "Demo Application Platform"
requirement. Queries Alpaca's REST API directly — doesn't depend on the
VM running main_autonomous.py being up, so it works as a standalone,
always-available demo of the live paper account's real state.

Reads credentials from Streamlit secrets (st.secrets), never hardcoded.

Note: this app's dependencies live in the root requirements.txt
(lightweight — streamlit + requests + plotly) so Streamlit Cloud's
default auto-detection picks them up with zero configuration. The
trading agent's own, much heavier dependencies live in
requirements-agent.txt instead — install that one on the VM, not this one.

Honest limitation: this dashboard only sees what Alpaca's API exposes
(orders, positions, fills, bars). It does NOT have access to the
agent's own written reasoning/rationale for a trade — that lives only
in logs/events.jsonl on the VM, which this hosted app has no connection
to. The trade detail page shows real market context (a price chart
around the decision) but does not yet show the agent's own trigger
reasoning or lessons-learned — that requires syncing the VM's log
somewhere this app can read, which is a separate, deferred piece of work.
"""
import streamlit as st
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict
import plotly.graph_objects as go

st.set_page_config(page_title="Circuit Breaker — Live Demo", page_icon="⚡", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0A0E17; }
    h1, h2, h3, p, span, div, label { color: #F9FAFB; }
    .stDataFrame { background-color: #1F2937; }

    /* Streamlit's default metric font is oversized for mobile — was
    causing excessive scrolling on phones. */
    [data-testid="stMetricValue"] { font-size: 1.3rem; }
    [data-testid="stMetricLabel"] { font-size: 0.8rem; }
    [data-testid="stMetricDelta"] { font-size: 0.75rem; }

    h1 { font-size: 1.6rem !important; }
    h2 { font-size: 1.3rem !important; }
    h3 { font-size: 1.1rem !important; }

    .stDataFrame { font-size: 0.85rem; }

    @media (max-width: 600px) {
        [data-testid="stMetricValue"] { font-size: 1.05rem; }
        [data-testid="stMetricLabel"] { font-size: 0.7rem; }
        h1 { font-size: 1.35rem !important; }
        h2 { font-size: 1.1rem !important; }
        h3 { font-size: 0.95rem !important; }
        .stDataFrame { font-size: 0.75rem; }
        p, span, div, label { font-size: 0.85rem; }
    }
</style>
""", unsafe_allow_html=True)

API_KEY = st.secrets.get("ALPACA_API_KEY", "")
SECRET_KEY = st.secrets.get("ALPACA_SECRET_KEY", "")
BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL = st.secrets.get("ALPACA_DATA_URL", "https://data.alpaca.markets")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY,
}

NYC_TZ = ZoneInfo("America/New_York")


def fetch(base: str, path: str, params: dict = None):
    try:
        resp = requests.get(f"{base}{path}", headers=HEADERS, params=params or {}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


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
    expiry, put/call, strike — for display on the leg detail tables,
    rather than just showing the raw unreadable symbol string."""
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


def compute_leg_breakdown(trade: dict, live_positions: list) -> dict:
    """
    Per-leg purchase/current-or-exit/profit breakdown, per contract and
    per-leg totals, plus a grand total row across all legs of the trade.

    Purchase price per leg = qty-weighted average of that leg's opening
    fill prices (handles a leg built across multiple opening orders).
    Current/exit price per leg = live current_price (open trades, from
    the live position) or qty-weighted average of closing fill prices
    (closed trades, including any synthesized $0 expiration events).
    Profit per contract respects long vs short: a long leg profits when
    price rises, a short leg profits when price falls (bought back
    cheaper than the credit received).

    Per-leg totals are qty × price (no ×100 multiplier) to match a
    plain "cost of N contracts at $X each" reading. The grand total row
    additionally nets long cost against short credit (the same signed
    convention already used for entry/current spread value elsewhere in
    the app), since summing both legs' raw totals unsigned would double
    count rather than net them.
    """
    all_events = trade["initial_open_events"] + trade["modification_events"] + trade["close_events"]
    symbols = sorted(set(e["symbol"] for e in all_events))

    legs = []
    for symbol in symbols:
        opens = [e for e in (trade["initial_open_events"] + trade["modification_events"]) if e["symbol"] == symbol]
        closes = [e for e in trade["close_events"] if e["symbol"] == symbol]
        if not opens:
            continue

        is_long = opens[0]["intent"] == "buy_to_open"
        qty = sum(e["qty"] for e in opens)
        purchase_price = sum(e["qty"] * e["price"] for e in opens) / qty if qty else 0.0

        current_or_exit_price = None
        if closes:
            close_qty = sum(e["qty"] for e in closes)
            current_or_exit_price = sum(e["qty"] * e["price"] for e in closes) / close_qty if close_qty else None
        elif trade["status"] == "open":
            live = next((p for p in live_positions if p.get("symbol") == symbol), None)
            if live is not None:
                current_or_exit_price = float(live.get("current_price", 0) or 0)

        if current_or_exit_price is None:
            profit_per_contract = None
        else:
            profit_per_contract = (current_or_exit_price - purchase_price) if is_long else (purchase_price - current_or_exit_price)

        occ = parse_occ_symbol(symbol)
        legs.append({
            "symbol": symbol,
            "type": occ["type"],
            "strike": occ["strike"],
            "expiry": occ["expiry"],
            "side": "Long" if is_long else "Short",
            "qty": qty,
            "purchase_price": purchase_price,
            "current_or_exit_price": current_or_exit_price,
            "profit_per_contract": profit_per_contract,
            "leg_total_purchase": qty * purchase_price,
            "leg_total_current": (qty * current_or_exit_price) if current_or_exit_price is not None else None,
            "leg_total_profit": (qty * profit_per_contract) if profit_per_contract is not None else None,
        })

    # Grand total: net long cost against short credit, matching the
    # signed convention already used for spread entry/current value
    # elsewhere in the app — NOT a naive unsigned sum of both legs.
    grand_purchase = sum(
        (l["leg_total_purchase"] if l["side"] == "Long" else -l["leg_total_purchase"]) for l in legs
    )
    have_all_current = all(l["leg_total_current"] is not None for l in legs) and legs
    grand_current = sum(
        (l["leg_total_current"] if l["side"] == "Long" else -l["leg_total_current"]) for l in legs
    ) if have_all_current else None
    grand_profit = sum(l["leg_total_profit"] for l in legs if l["leg_total_profit"] is not None) if legs else 0.0

    return {
        "legs": legs,
        "grand_purchase": grand_purchase,
        "grand_current": grand_current,
        "grand_profit": grand_profit,
    }


def format_nyc(iso_ts: str) -> str:
    """Converts any ISO timestamp (assumed UTC if no offset given) to
    'dd mm yyyy HH:MM:SS' in America/New_York time."""
    if not iso_ts:
        return "—"
    try:
        ts = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        nyc = dt.astimezone(NYC_TZ)
        return nyc.strftime("%d %m %Y %H:%M:%S")
    except Exception:
        return iso_ts


def build_trade_records(orders, live_positions, expiry_activities=None):
    """
    Builds ONE record per actual round-trip TRADE, not one per order and
    not one per closing leg. Groups fills by (underlying, expiration),
    walking chronologically and tracking when the group's long AND short
    side both return to fully flat — splitting into a new trade whenever
    that happens, so two genuinely separate trades on the same underlying
    aren't merged, but one trade opened/closed across multiple orders is
    correctly treated as one.

    Currently-open trades (never returned to flat) are included with
    status='open' and outcome = live unrealized P&L from the position.

    expiry_activities: Alpaca's OPEXP non-trade activities. An option
    expiring OTM never generates a closing ORDER at all — Alpaca just
    flattens the position silently — so without this, an expired
    position would incorrectly stay classified as 'open' forever, with
    no outcome. Each OPEXP event is converted into a synthetic
    zero-price close (sell_to_close for a long leg that expired
    worthless, buy_to_close for a short leg that expired worthless and
    kept its full credit), correctly determined from which side
    (long_lots/short_lots) the symbol is actually sitting in at that
    point in the timeline — not assumed.
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
            "intent": "expire",  # resolved to sell_to_close/buy_to_close during processing, based on actual position side
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
        long_lots = defaultdict(list)
        short_lots = defaultdict(list)
        long_remaining = 0.0
        short_remaining = 0.0
        current_events = []
        pending_pnl = 0.0

        def flush(is_open):
            if not current_events:
                return
            open_events = [e for e in current_events if e["intent"] in ("buy_to_open", "sell_to_open")]
            close_events = [e for e in current_events if e["intent"] in ("sell_to_close", "buy_to_close")]
            symbols_involved = sorted(set(e["symbol"] for e in current_events))
            trade_class = "multi" if len(symbols_involved) > 1 else "single"
            time_opened = open_events[0]["ts"] if open_events else current_events[0]["ts"]
            time_closed = close_events[-1]["ts"] if close_events else None

            # Split opens into "initial" (the very first opening order's timestamp)
            # vs "modifications" (any later opening order added to the same trade).
            initial_ts = open_events[0]["ts"] if open_events else None
            initial_open_events = [e for e in open_events if e["ts"] == initial_ts]
            modification_events = [e for e in open_events if e["ts"] != initial_ts]

            # Contracts currently open: sum opening qty per symbol, take the
            # max across legs (a correctly-built spread has equal qty on
            # both legs, so this is robust even if legs were entered in
            # separate orders at different sizes over time).
            qty_per_symbol = defaultdict(float)
            for e in open_events:
                qty_per_symbol[e["symbol"]] += e["qty"]
            total_qty = max(qty_per_symbol.values()) if qty_per_symbol else 0

            if is_open:
                outcome = sum(
                    float(p.get("unrealized_pl", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                # Current value = live market value of the whole spread right
                # now (long leg's value minus short leg's liability — Alpaca
                # already signs market_value this way), divided into a
                # per-contract figure comparable to the price it was entered at.
                current_value_total = sum(
                    float(p.get("market_value", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                current_value_per_contract = (current_value_total / (total_qty * 100)) if total_qty else None
                # Purchase price = what was actually paid at entry, using the
                # exact same signed-sum pattern but with cost_basis instead
                # of market_value. Verified against the agent's own logged
                # math ($1.26/contract net debit) — exact match.
                entry_value_total = sum(
                    float(p.get("cost_basis", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                purchase_price_per_contract = (entry_value_total / (total_qty * 100)) if total_qty else None
                pl_word = None
                status = "open"
            else:
                outcome = pending_pnl
                current_value_per_contract = None  # closed trades don't have a "current" value — they're done
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

        for e in events:
            current_events.append(e)
            symbol, intent, qty, price = e["symbol"], e["intent"], e["qty"], e["price"]

            if intent == "expire":
                # Resolve based on which side this symbol is actually sitting
                # on right now — not assumed. A long leg expiring worthless
                # needs sell_to_close (it's worth $0); a short leg expiring
                # worthless needs buy_to_close (costs $0 to "buy back" — the
                # full credit is kept). If it's in neither, there's nothing
                # to close (already flat, or an activity we can't attribute);
                # skip rather than guess.
                if long_lots[symbol]:
                    intent = "sell_to_close"
                elif short_lots[symbol]:
                    intent = "buy_to_close"
                else:
                    continue
                e["intent"] = intent  # so flush() correctly counts this as a close event

            if intent == "buy_to_open":
                long_lots[symbol].append([qty, price])
                long_remaining += qty
            elif intent == "sell_to_open":
                short_lots[symbol].append([qty, price])
                short_remaining += qty
            elif intent == "sell_to_close":
                remaining, leg_pnl = qty, 0.0
                while remaining > 0 and long_lots[symbol]:
                    lot_qty, lot_price = long_lots[symbol][0]
                    matched = min(lot_qty, remaining)
                    leg_pnl += (price - lot_price) * matched * 100
                    lot_qty -= matched
                    remaining -= matched
                    if lot_qty <= 0:
                        long_lots[symbol].pop(0)
                    else:
                        long_lots[symbol][0][0] = lot_qty
                pending_pnl += leg_pnl
                long_remaining -= qty
            elif intent == "buy_to_close":
                remaining, leg_pnl = qty, 0.0
                while remaining > 0 and short_lots[symbol]:
                    lot_qty, lot_price = short_lots[symbol][0]
                    matched = min(lot_qty, remaining)
                    leg_pnl += (lot_price - price) * matched * 100
                    lot_qty -= matched
                    remaining -= matched
                    if lot_qty <= 0:
                        short_lots[symbol].pop(0)
                    else:
                        short_lots[symbol][0][0] = lot_qty
                pending_pnl += leg_pnl
                short_remaining -= qty

            if intent in ("sell_to_close", "buy_to_close"):
                if long_remaining <= 0.0001 and short_remaining <= 0.0001:
                    flush(is_open=False)
                    current_events = []
                    pending_pnl = 0.0
                    long_remaining = 0.0
                    short_remaining = 0.0

        if current_events:
            flush(is_open=True)

    trades.sort(key=lambda t: t["time_opened"], reverse=True)
    return trades


if not API_KEY or not SECRET_KEY:
    st.error(
        "Alpaca API credentials not configured. Add ALPACA_API_KEY and ALPACA_SECRET_KEY "
        "in this app's Streamlit Cloud secrets to connect to the live account."
    )
    st.stop()

# ---------------------------------------------------------------
# Session state / simple router: dashboard vs. trade detail
# ---------------------------------------------------------------
if "view" not in st.session_state:
    st.session_state.view = "dashboard"
if "selected_trade_idx" not in st.session_state:
    st.session_state.selected_trade_idx = None


def go_to_detail(idx: int):
    st.session_state.view = "detail"
    st.session_state.selected_trade_idx = idx
    st.rerun()


def go_to_dashboard():
    st.session_state.view = "dashboard"
    st.session_state.selected_trade_idx = None
    st.rerun()


def render_leg_table(events, empty_msg):
    if not events:
        st.caption(empty_msg)
        return
    rows = []
    for e in events:
        occ = parse_occ_symbol(e["symbol"])
        rows.append({
            "Time (NYC)": format_nyc(e["ts"]),
            "Type": occ["type"],
            "Strike": occ["strike"],
            "Expiry": occ["expiry"],
            "Side": e.get("side") or "—",
            "Intent": e["intent"],
            "Qty": e["qty"],
            "Fill Price": f"${e['price']:.2f}",
            "Source": "Expired worthless" if e.get("source") == "expiration" else "Order fill",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


# =================================================================
# Fetch everything up front — both views need it
# =================================================================
account = fetch(BASE_URL, "/v2/account")
if "error" in account:
    st.warning(f"Could not reach Alpaca right now: {account['error']}")
    st.stop()

positions = fetch(BASE_URL, "/v2/positions")
if not isinstance(positions, list):
    positions = []

all_orders = fetch(BASE_URL, "/v2/orders", {"status": "all", "limit": 200, "direction": "desc"})
if not isinstance(all_orders, list):
    all_orders = []

# OPEXP = Alpaca's non-trade activity for an option expiring — never
# shows up as an order at all, so without this, an expired position
# would silently stay stuck as "open" forever with no outcome.
expiry_activities = fetch(BASE_URL, "/v2/account/activities/OPEXP")
if not isinstance(expiry_activities, list):
    expiry_activities = []

trades = build_trade_records(all_orders, positions, expiry_activities)

# =================================================================
# DETAIL VIEW
# =================================================================
if st.session_state.view == "detail" and st.session_state.selected_trade_idx is not None:
    idx = st.session_state.selected_trade_idx
    if idx >= len(trades):
        st.error("That trade is no longer available.")
        if st.button("← Back to dashboard"):
            go_to_dashboard()
        st.stop()

    trade = trades[idx]

    if st.button("← Back to dashboard"):
        go_to_dashboard()

    st.title(f"Trade Detail — {trade['underlying']}")
    st.caption(f"Group: {trade['group_key']} · Status: {trade['status']}")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Class", trade["class"])
    with c2:
        st.metric("Status", trade["status"])
    with c3:
        st.metric("Qty", trade["qty"])
    with c4:
        st.metric("Outcome", f"${trade['outcome']:,.2f}")
    with c5:
        result_label = trade["profit_loss"].upper() if trade["profit_loss"] else ("OPEN" if trade["status"] == "open" else "—")
        st.metric("Result", result_label)

    if trade["current_value_per_contract"] is not None:
        value_line = f"Current value: ${trade['current_value_per_contract']:.2f}/contract"
        if trade.get("purchase_price_per_contract") is not None:
            value_line = f"Entry: ${trade['purchase_price_per_contract']:.2f}/contract → " + value_line
        st.caption(value_line)

    st.caption(f"Opened: {format_nyc(trade['time_opened'])} NYC" + (f" · Closed: {format_nyc(trade['time_closed'])} NYC" if trade["time_closed"] else " · Still open"))

    st.info(
        "ℹ️ Trigger reasoning and lessons-learned aren't shown here yet — that text only "
        "exists in the agent's own log on the deployment VM, which this hosted dashboard "
        "doesn't have a live connection to. This section shows real market data and actual "
        "fill details only."
    )

    # ---- Price chart around entry ----
    st.subheader(f"Market Context — {trade['underlying']}")
    try:
        entry_dt = datetime.fromisoformat(trade["time_opened"].replace("Z", "+00:00"))
    except Exception:
        entry_dt = datetime.now(timezone.utc)

    exit_dt = None
    if trade["time_closed"]:
        try:
            exit_dt = datetime.fromisoformat(trade["time_closed"].replace("Z", "+00:00"))
        except Exception:
            exit_dt = None

    span_end = exit_dt or datetime.now(timezone.utc)
    span = span_end - entry_dt

    # Padding scales with how long the trade was actually open, so a quick
    # same-day trade gets a tight, readable window and a multi-day trade
    # doesn't get compressed into an unreadable sliver.
    padding = max(timedelta(minutes=30), span * 0.15)
    padding = min(padding, timedelta(hours=6))

    if span <= timedelta(hours=6):
        bar_timeframe = "5Min"
    elif span <= timedelta(days=2):
        bar_timeframe = "15Min"
    else:
        bar_timeframe = "1Hour"

    start = (entry_dt - padding).isoformat()
    end = (span_end + padding).isoformat()
    bars_resp = fetch(DATA_URL, f"/v2/stocks/{trade['underlying']}/bars", {
        "timeframe": bar_timeframe, "start": start, "end": end, "limit": 300,
    })
    bars = bars_resp.get("bars", []) if isinstance(bars_resp, dict) else []

    if bars:
        fig = go.Figure(data=[go.Candlestick(
            x=[b["t"] for b in bars],
            open=[b["o"] for b in bars],
            high=[b["h"] for b in bars],
            low=[b["l"] for b in bars],
            close=[b["c"] for b in bars],
            increasing_line_color="#22D3A8", decreasing_line_color="#EF4444",
        )])
        fig.add_vline(x=entry_dt.isoformat(), line_dash="dash", line_color="#F59E0B",
                       annotation_text="Entry", annotation_font_color="#F59E0B")
        if exit_dt:
            fig.add_vline(x=exit_dt.isoformat(), line_dash="dash", line_color="#0EA5E9",
                           annotation_text="Exit", annotation_font_color="#0EA5E9")
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#151B2B",  # deliberately lighter than the page background (#0A0E17)
            plot_bgcolor="#151B2B",   # so the chart's own boundary is visually obvious, not blending in
            height=450, margin=dict(l=10, r=10, t=30, b=10),
            xaxis_rangeslider_visible=False,
            dragmode=False,  # a scroll gesture starting on the chart was being captured as a
                              # zoom-select drag instead of scrolling the page — this was the
                              # actual cause of "touching the chart resizes it" while scrolling.
        )
        # Force the modebar (autoscale/reset button included) to always be visible rather than
        # only appearing on hover, which doesn't work on a touch screen at all.
        chart_config = {
            "scrollZoom": False,      # pinch/wheel no longer zooms the chart unexpectedly
            "displayModeBar": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        }
        with st.container(border=True):  # gives the chart a real, visible boundary on the page
            st.plotly_chart(fig, use_container_width=True, config=chart_config)
        st.caption("Chart is zoom/pan-locked by default so it doesn't interfere with scrolling — use the toolbar above the chart (visible on tap) to zoom or reset the view.")
    else:
        st.info(f"No bar data available for {trade['underlying']} in this window.")

    st.divider()

    # ---- Per-leg purchase / current-or-exit / profit breakdown ----
    st.subheader("Per-Leg Breakdown")
    breakdown = compute_leg_breakdown(trade, positions)
    if breakdown["legs"]:
        leg_rows = [{
            "Type": l["type"],
            "Strike": l["strike"],
            "Expiry": l["expiry"],
            "Side": l["side"],
            "Contracts": l["qty"],
            "Purchase $": f"${l['purchase_price']:.2f}",
            ("Exit $" if trade["status"] == "closed" else "Current $"): (f"${l['current_or_exit_price']:.2f}" if l["current_or_exit_price"] is not None else "—"),
            "P/L per Ctr": f"${l['profit_per_contract']:.2f}" if l["profit_per_contract"] is not None else "—",
            "Leg Total P/L": f"${l['leg_total_profit']:.2f}" if l["leg_total_profit"] is not None else "—",
        } for l in breakdown["legs"]]
        st.dataframe(leg_rows, use_container_width=True, hide_index=True)

        gc1, gc2, gc3 = st.columns(3)
        with gc1:
            st.metric("Total Purchase", f"${breakdown['grand_purchase']:.2f}")
        with gc2:
            st.metric("Total Current/Exit", f"${breakdown['grand_current']:.2f}" if breakdown["grand_current"] is not None else "—")
        with gc3:
            st.metric("Total P/L (all legs)", f"${breakdown['grand_profit']:.2f}")
        st.caption("Purchase/Current/Total figures here are per-contract price × contract count (not the full ×100 options multiplier used elsewhere in this app).")
    else:
        st.caption("No leg data available for this trade.")

    st.divider()

    st.subheader("Opening trade details")
    render_leg_table(trade["initial_open_events"], "No opening legs recorded.")

    if trade["modification_events"]:
        st.subheader("Modified trade details")
        st.caption("Additional opening orders placed on this same position after the initial entry.")
        render_leg_table(trade["modification_events"], "")

    if trade["close_events"]:
        st.subheader("Closing trade details")
        render_leg_table(trade["close_events"], "No closing legs recorded.")
    else:
        st.subheader("Closing trade details")
        st.caption("Still open — no closing legs yet.")

    st.stop()

# =================================================================
# DASHBOARD VIEW
# =================================================================
st.title("⚡ Circuit Breaker")
st.caption("An autonomous AI trading agent, built on Alpaca — live paper account status")

funds_committed = abs(sum(float(p.get("cost_basis", 0) or 0) for p in positions))
current_equity = float(account.get("equity", 0))
cash_available = current_equity - funds_committed
open_unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)

# Mobile-friendly: 4 + 3 instead of 7 across one row, which was forcing
# heavy horizontal squeeze/scroll on phone screens.
row1 = st.columns(4)
with row1[0]:
    st.metric("Equity", f"${current_equity:,.2f}", help="Total account value, including the current market value of open positions.")
with row1[1]:
    st.metric("Cash Available", f"${cash_available:,.2f}", help="Equity minus capital currently committed to open positions.")
with row1[2]:
    st.metric("Buying Power", f"${float(account.get('buying_power', 0)):,.2f}")
with row1[3]:
    st.metric("Funds Committed", f"${funds_committed:,.2f}", help="How much was actually paid into currently open positions (net cost basis).")

row2 = st.columns(3)
with row2[0]:
    st.metric("Unrealized P&L", f"${open_unrealized:,.2f}", help="Live floating gain/loss on currently open positions — changes constantly while a position is open.")
with row2[1]:
    st.metric("Options Level", account.get("options_trading_level", "—"))
with row2[2]:
    st.metric("Status", account.get("status", "—"))

st.divider()

# ---- Trades summary (open/closed at the TRADE level, not raw order status) ----
st.subheader("Trades Summary")
open_trade_count = len([t for t in trades if t["status"] == "open"])
closed_trade_count = len([t for t in trades if t["status"] == "closed"])
cancelled_order_count = sum(1 for o in all_orders if o.get("status") in ("canceled", "expired"))
rejected_order_count = sum(1 for o in all_orders if o.get("status") == "rejected")

tcols = st.columns(4)
with tcols[0]:
    st.metric("Open Trades", open_trade_count)
with tcols[1]:
    st.metric("Closed Trades", closed_trade_count)
with tcols[2]:
    st.metric("Cancelled Orders", cancelled_order_count)
with tcols[3]:
    st.metric("Rejected Orders", rejected_order_count)

st.divider()

# ---- Win / Loss ----
# Realized P&L uses actual account equity, not reconstructed fill
# prices, so it's fee-inclusive to the cent. This is correctly stable
# by construction: subtracting the currently-open positions' own live
# unrealized P&L exactly cancels out their fluctuation, leaving only
# the fixed, already-settled result of closed trades. Any tiny jitter
# seen is just sub-second timing between two separate API calls
# (positions vs account), not a real drift in the number itself.
st.subheader("Win / Loss")
closed_trades = [t for t in trades if t["status"] == "closed"]
wins = [t for t in closed_trades if t["profit_loss"] == "win"]
losses = [t for t in closed_trades if t["profit_loss"] == "loss"]

STARTING_EQUITY = 100000.0  # matches the hackathon's required starting balance
total_realized = (current_equity - STARTING_EQUITY) - open_unrealized

wl1, wl2, wl3, wl4 = st.columns(4)
with wl1:
    st.metric("Realized P&L", f"${total_realized:,.2f}", help="From actual account equity — includes exchange/regulatory fees. Mathematically excludes any currently-open position's fluctuation.")
with wl2:
    st.metric("Wins", len(wins))
with wl3:
    st.metric("Losses", len(losses))
with wl4:
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0
    st.metric("Win Rate", f"{win_rate:.0f}%" if closed_trades else "—")

if not closed_trades:
    st.info("No closed trades yet — win/loss populates once a position is opened and closed.")

st.divider()

# ---- Open positions ----
st.subheader("Open Positions")
open_trades_indexed = [(i, t) for i, t in enumerate(trades) if t["status"] == "open"]

if open_trades_indexed:
    rows = [{
        "Underlying": t["underlying"],
        "Entered (NYC)": format_nyc(t["time_opened"]),
        "Class": t["class"],
        "Qty": t["qty"],
        "Entry $/Ctr": f"${t['purchase_price_per_contract']:.2f}" if t["purchase_price_per_contract"] is not None else "—",
        "Now $/Ctr": f"${t['current_value_per_contract']:.2f}" if t["current_value_per_contract"] is not None else "—",
        "Unrealized P/L": f"${t['outcome']:,.2f}",
    } for _, t in open_trades_indexed]

    event = st.dataframe(
        rows, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="open_positions_table",
        column_config={
            "Entry $/Ctr": st.column_config.TextColumn(width="small"),
            "Now $/Ctr": st.column_config.TextColumn(width="small"),
        },
    )
    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    if selected_rows:
        position_in_filtered_list = selected_rows[0]
        original_idx, selected_trade = open_trades_indexed[position_in_filtered_list]
        st.write(f"Selected: **{selected_trade['underlying']}** — opened {rows[position_in_filtered_list]['Entered (NYC)']}")
        if st.button("🔍 View trade detail & chart", type="primary", key="open_position_detail_btn"):
            go_to_detail(original_idx)
else:
    st.info("No open positions right now.")

st.divider()

# ---- Trade History — CLOSED trades only, one row per TRADE ----
# Open positions already have their own section above (Open Positions);
# including them here too was redundant and confusing — found via user
# report: a single completed trade appeared to show as "two lines"
# because a still-open, unrelated position was also being listed here
# with status='open' the moment it was placed, before it had any
# actual outcome yet.
st.subheader("Trade History")
st.caption("Completed trades only — click a row, then use the button below to open its detail page with a price chart. Still-open positions are in Open Positions, above.")

closed_trades_indexed = [(i, t) for i, t in enumerate(trades) if t["status"] == "closed"]

if closed_trades_indexed:
    trade_rows = [{
        "Underlying": t["underlying"],
        "Time Opened (NYC)": format_nyc(t["time_opened"]),
        "Class": t["class"],
        "Status": t["status"],
        "Outcome": f"${t['outcome']:,.2f}",
        "Time Closed (NYC)": format_nyc(t["time_closed"]) if t["time_closed"] else "—",
        "Profit/Loss": t["profit_loss"] if t["profit_loss"] else "—",
    } for _, t in closed_trades_indexed]

    event = st.dataframe(
        trade_rows, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="trade_table",
    )

    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    if selected_rows:
        position_in_filtered_list = selected_rows[0]
        original_idx, selected_trade = closed_trades_indexed[position_in_filtered_list]
        st.write(f"Selected: **{selected_trade['underlying']}** — {selected_trade['status']} — {trade_rows[position_in_filtered_list]['Time Opened (NYC)']}")
        if st.button("🔍 View trade detail & chart", type="primary"):
            go_to_detail(original_idx)
else:
    st.info("No completed trades yet. Check Open Positions above if something is currently active.")

st.divider()

st.subheader("What makes this different")
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**🎯 Genuinely autonomous**")
    st.write("Claude originates every trade decision directly via Alpaca's MCP server — no rules engine pre-filtering candidates.")
with c2:
    st.markdown("**🛡️ Three hard backstops**")
    st.write("Defined-risk-only, spread-economics-sane, and a 15% per-trade sizing cap — enforced in code, not by prompt.")
with c3:
    st.markdown("**🔄 Self-assessing**")
    st.write("Reviews its own recent activity each cycle and adjusts its own approach — not a fixed script.")

now_nyc = datetime.now(timezone.utc).astimezone(NYC_TZ)
st.caption(f"Last refreshed: {now_nyc.strftime('%d %m %Y %H:%M:%S')} NYC · [View source on GitHub](https://github.com/irishkiwi007/alpaca-options-agent)")

if st.button("🔄 Refresh"):
    st.rerun()
