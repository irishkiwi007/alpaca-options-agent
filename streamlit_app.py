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


def build_trade_records(orders, live_positions):
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

            if is_open:
                outcome = sum(
                    float(p.get("unrealized_pl", 0) or 0)
                    for p in live_positions if p.get("symbol") in symbols_involved
                )
                pl_word = None
                status = "open"
            else:
                outcome = pending_pnl
                pl_word = "win" if outcome > 0 else ("loss" if outcome < 0 else "flat")
                status = "closed"

            trades.append({
                "underlying": root_symbol_from_group(gkey),
                "group_key": gkey,
                "time_opened": time_opened,
                "time_closed": time_closed,
                "class": trade_class,
                "status": status,
                "outcome": outcome,
                "profit_loss": pl_word,
                "initial_open_events": initial_open_events,
                "modification_events": modification_events,
                "close_events": close_events,
            })

        for e in events:
            current_events.append(e)
            symbol, intent, qty, price = e["symbol"], e["intent"], e["qty"], e["price"]

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
    rows = [{
        "Time (NYC)": format_nyc(e["ts"]),
        "Symbol": e["symbol"],
        "Side": e["side"],
        "Intent": e["intent"],
        "Qty": e["qty"],
        "Fill Price": f"${e['price']:.2f}",
    } for e in events]
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

trades = build_trade_records(all_orders, positions)

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

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Class", trade["class"])
    with c2:
        st.metric("Status", trade["status"])
    with c3:
        st.metric("Outcome", f"${trade['outcome']:,.2f}")
    with c4:
        st.metric("Result", trade["profit_loss"].upper() if trade["profit_loss"] else "—")

    st.caption(f"Opened: {format_nyc(trade['time_opened'])} NYC" + (f" · Closed: {format_nyc(trade['time_closed'])} NYC" if trade["time_closed"] else " · Still open"))

    st.info(
        "ℹ️ Trigger reasoning and lessons-learned aren't shown here yet — that text only "
        "exists in the agent's own log on the deployment VM, which this hosted dashboard "
        "doesn't have a live connection to. This section shows real market data and actual "
        "fill details only."
    )

    # ---- Price chart around entry ----
    st.subheader(f"Market Context at Entry — {trade['underlying']}")
    try:
        entry_dt = datetime.fromisoformat(trade["time_opened"].replace("Z", "+00:00"))
    except Exception:
        entry_dt = datetime.now(timezone.utc)

    start = (entry_dt - timedelta(hours=3)).isoformat()
    end = (entry_dt + timedelta(hours=3)).isoformat()
    bars_resp = fetch(DATA_URL, f"/v2/stocks/{trade['underlying']}/bars", {
        "timeframe": "5Min", "start": start, "end": end, "limit": 200,
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
                       annotation_text="Trade opened", annotation_font_color="#F59E0B")
        fig.update_layout(
            template="plotly_dark", paper_bgcolor="#0A0E17", plot_bgcolor="#0A0E17",
            height=450, margin=dict(l=10, r=10, t=30, b=10),
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(f"No bar data available for {trade['underlying']} in this window.")

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

col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    st.metric("Equity", f"${current_equity:,.2f}", help="Total account value, including the current market value of open positions.")
with col2:
    st.metric("Cash Available", f"${cash_available:,.2f}", help="Equity minus capital currently committed to open positions.")
with col3:
    st.metric("Buying Power", f"${float(account.get('buying_power', 0)):,.2f}")
with col4:
    st.metric("Funds Committed", f"${funds_committed:,.2f}", help="Net cost basis of open positions (long minus short).")
with col5:
    st.metric("Options Level", account.get("options_trading_level", "—"))
with col6:
    st.metric("Status", account.get("status", "—"))

st.divider()

# ---- Order status breakdown ----
st.subheader("Order Status Breakdown")
status_counts = defaultdict(int)
for o in all_orders:
    status_counts[o.get("status", "unknown")] += 1

STATUS_GROUPS = {
    "Filled": ["filled"],
    "Open / Pending": ["new", "accepted", "pending_new", "accepted_for_bidding", "held", "partially_filled"],
    "Cancelled": ["canceled", "expired"],
    "Rejected": ["rejected"],
}
bcols = st.columns(len(STATUS_GROUPS))
for i, (label, keys) in enumerate(STATUS_GROUPS.items()):
    count = sum(status_counts.get(k, 0) for k in keys)
    with bcols[i]:
        st.metric(label, count)

st.divider()

# ---- Win / Loss ----
st.subheader("Win / Loss")
closed_trades = [t for t in trades if t["status"] == "closed"]
wins = [t for t in closed_trades if t["profit_loss"] == "win"]
losses = [t for t in closed_trades if t["profit_loss"] == "loss"]

starting_equity = 100000.0  # matches the hackathon's required starting balance
open_unrealized = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
total_realized = (current_equity - starting_equity) - open_unrealized

wl1, wl2, wl3, wl4 = st.columns(4)
with wl1:
    st.metric("Realized P&L", f"${total_realized:,.2f}", help="From actual account equity — includes exchange/regulatory fees, not just fill prices.")
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
if positions:
    rows = [{
        "Symbol": p.get("symbol"),
        "Side": p.get("side"),
        "Qty": p.get("qty"),
        "Avg Entry": f"${float(p.get('avg_entry_price', 0)):.2f}",
        "Cost Basis": f"${abs(float(p.get('cost_basis', 0))):.2f}",
        "Unrealized P/L": f"${float(p.get('unrealized_pl', 0)):.2f}",
    } for p in positions]
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No open positions right now.")

st.divider()

# ---- Trade History — one row per TRADE, click through for detail ----
st.subheader("Trade History")
st.caption("One row per trade — click a row, then use the button below to open its detail page with a price chart.")

if trades:
    trade_rows = [{
        "Underlying": t["underlying"],
        "Time Opened (NYC)": format_nyc(t["time_opened"]),
        "Class": t["class"],
        "Status": t["status"],
        "Outcome": f"${t['outcome']:,.2f}",
        "Time Closed (NYC)": format_nyc(t["time_closed"]) if t["time_closed"] else "—",
        "Profit/Loss": t["profit_loss"] if t["profit_loss"] else "—",
    } for t in trades]

    event = st.dataframe(
        trade_rows, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="trade_table",
    )

    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    if selected_rows:
        idx = selected_rows[0]
        st.write(f"Selected: **{trades[idx]['underlying']}** — {trades[idx]['status']} — {trade_rows[idx]['Time Opened (NYC)']}")
        if st.button("🔍 View trade detail & chart", type="primary"):
            go_to_detail(idx)
else:
    st.info("No trades yet.")

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
