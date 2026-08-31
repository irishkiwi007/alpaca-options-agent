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
around the decision) but not the agent's narrated thought process.
"""
import streamlit as st
import requests
from datetime import datetime, timedelta, timezone
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
if "selected_order_id" not in st.session_state:
    st.session_state.selected_order_id = None


def go_to_detail(order_id: str):
    st.session_state.view = "detail"
    st.session_state.selected_order_id = order_id
    st.rerun()


def go_to_dashboard():
    st.session_state.view = "dashboard"
    st.session_state.selected_order_id = None
    st.rerun()


# =================================================================
# DETAIL VIEW
# =================================================================
if st.session_state.view == "detail" and st.session_state.selected_order_id:
    order_id = st.session_state.selected_order_id
    order = fetch(BASE_URL, f"/v2/orders/{order_id}")

    if st.button("← Back to dashboard"):
        go_to_dashboard()

    if "error" in order:
        st.error(f"Could not load order {order_id}: {order['error']}")
        st.stop()

    legs = order.get("legs") or [order]
    underlying = parse_underlying(legs[0].get("symbol", "")) if legs else "—"

    st.title(f"Trade Detail — {underlying}")
    st.caption(f"Order ID: {order_id}")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Status", order.get("status", "—"))
    with c2:
        st.metric("Limit Price", f"${float(order.get('limit_price', 0) or 0):.2f}")
    with c3:
        st.metric("Contracts", order.get("qty", "—"))
    with c4:
        submitted = (order.get("submitted_at") or "")[:19].replace("T", " ")
        st.metric("Submitted", submitted or "—")

    st.subheader("Legs")
    leg_rows = [{
        "Symbol": l.get("symbol"),
        "Side": l.get("side"),
        "Intent": l.get("position_intent"),
        "Qty": l.get("qty"),
        "Status": l.get("status"),
        "Filled Avg Price": l.get("filled_avg_price") or "—",
    } for l in legs]
    st.dataframe(leg_rows, use_container_width=True, hide_index=True)

    # ---- Price chart: what the market looked like around this order ----
    st.subheader(f"Market Context — {underlying}")
    st.caption("Price action around the time this trade was submitted (not the agent's written reasoning — see note below).")

    try:
        submitted_at = datetime.strptime(submitted, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        submitted_at = datetime.now(timezone.utc)

    start = (submitted_at - timedelta(hours=3)).isoformat()
    end = (submitted_at + timedelta(hours=3)).isoformat()

    bars_resp = fetch(DATA_URL, f"/v2/stocks/{underlying}/bars", {
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
        fig.add_vline(x=submitted_at.isoformat(), line_dash="dash", line_color="#F59E0B",
                       annotation_text="Order submitted", annotation_font_color="#F59E0B")
        fig.update_layout(
            template="plotly_dark", paper_bgcolor="#0A0E17", plot_bgcolor="#0A0E17",
            height=450, margin=dict(l=10, r=10, t=30, b=10),
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(
            f"No bar data available for {underlying} in this window — likely outside market "
            "hours, or the underlying couldn't be parsed correctly from the option symbol."
        )

    st.divider()
    st.caption(
        "ℹ️ This chart shows real market price action around the order, pulled live from Alpaca. "
        "It does not show the agent's own written rationale for the trade — that lives in the "
        "structured event log on the deployment VM, which this hosted dashboard doesn't have "
        "access to. See the GitHub repo's logs/events.jsonl for the full reasoning trail."
    )

    with st.expander("Raw order JSON"):
        st.json(order)

    st.stop()

# =================================================================
# DASHBOARD VIEW
# =================================================================
st.title("⚡ Circuit Breaker")
st.caption("An autonomous AI trading agent, built on Alpaca — live paper account status")

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

# ---- Top metrics ----
funds_committed = sum(abs(float(p.get("cost_basis", 0) or 0)) for p in positions)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Equity", f"${float(account.get('equity', 0)):,.2f}")
with col2:
    st.metric("Buying Power", f"${float(account.get('buying_power', 0)):,.2f}")
with col3:
    st.metric("Funds Committed", f"${funds_committed:,.2f}", help="Total cost basis of all open positions right now.")
with col4:
    st.metric("Options Level", account.get("options_trading_level", "—"))
with col5:
    st.metric("Status", account.get("status", "—"))

st.divider()

# ---- Order status breakdown ----
st.subheader("Order Status Breakdown")
status_counts = {}
for o in all_orders:
    s = o.get("status", "unknown")
    status_counts[s] = status_counts.get(s, 0) + 1

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

# ---- Win / Loss (realized P&L via position_intent-aware FIFO on order legs) ----
st.subheader("Win / Loss")

from collections import defaultdict


def compute_realized_from_orders(orders):
    """
    Matches closing legs (sell_to_close / buy_to_close) against their
    corresponding opening legs (buy_to_open / sell_to_open) using
    position_intent directly, rather than inferring direction from side
    alone. Short legs (sell_to_open, then buy_to_close) are tracked
    separately from long legs — a plain buy/sell FIFO model silently
    drops short-leg fills entirely, which is exactly what this strategy
    does on every spread's short leg. Verified against real fill data.
    Returns one realized P&L entry per closing leg-fill (summed across
    whatever opening lots it matched against), not per matched lot pair,
    so a single close isn't double-counted as multiple wins/losses.
    """
    long_lots = defaultdict(list)
    short_lots = defaultdict(list)
    realized = []

    leg_events = []
    for o in orders:
        if o.get("status") != "filled":
            continue
        legs = o.get("legs") or [o]
        ts = o.get("filled_at") or o.get("submitted_at") or ""
        for leg in legs:
            leg_events.append((ts, leg))
    leg_events.sort(key=lambda x: x[0])

    for ts, leg in leg_events:
        symbol = leg.get("symbol")
        intent = leg.get("position_intent") or ""
        qty = float(leg.get("qty", 0) or 0)
        price = float(leg.get("filled_avg_price", 0) or 0)
        if not symbol or qty == 0 or price == 0:
            continue

        if intent == "buy_to_open":
            long_lots[symbol].append([qty, price])
        elif intent == "sell_to_open":
            short_lots[symbol].append([qty, price])
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
            if remaining < qty:
                realized.append((symbol, leg_pnl))
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
            if remaining < qty:
                realized.append((symbol, leg_pnl))
    return realized


realized = compute_realized_from_orders(all_orders)

wins = [p for _, p in realized if p > 0]
losses = [p for _, p in realized if p < 0]
total_realized = sum(p for _, p in realized)

wl1, wl2, wl3, wl4 = st.columns(4)
with wl1:
    st.metric("Realized P&L", f"${total_realized:,.2f}")
with wl2:
    st.metric("Wins", len(wins))
with wl3:
    st.metric("Losses", len(losses))
with wl4:
    win_rate = (len(wins) / len(realized) * 100) if realized else 0
    st.metric("Win Rate", f"{win_rate:.0f}%" if realized else "—")

if not realized:
    st.info("No closed round-trips yet — win/loss populates once positions are opened and closed.")

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

# ---- Trade history — click a row to see detail + chart ----
st.subheader("Trade History")
st.caption("Click a row, then use the button below to open its detail page with a price chart.")

if all_orders:
    order_rows = []
    for o in all_orders:
        legs = o.get("legs") or [o]
        underlying = parse_underlying(legs[0].get("symbol", "")) if legs else "—"
        order_rows.append({
            "Submitted": (o.get("submitted_at") or "")[:19].replace("T", " "),
            "Underlying": underlying,
            "Class": o.get("order_class", o.get("type", "—")),
            "Status": o.get("status"),
            "Qty": o.get("qty"),
            "Limit": f"${float(o.get('limit_price', 0) or 0):.2f}",
            "_order_id": o.get("id"),
        })

    event = st.dataframe(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in order_rows],
        use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="trade_table",
    )

    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    if selected_rows:
        idx = selected_rows[0]
        chosen_id = order_rows[idx]["_order_id"]
        st.write(f"Selected: **{order_rows[idx]['Underlying']}** — {order_rows[idx]['Status']} — {order_rows[idx]['Submitted']}")
        if st.button("🔍 View trade detail & chart", type="primary"):
            go_to_detail(chosen_id)
else:
    st.info("No orders yet.")

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

st.caption(f"Last refreshed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC · [View source on GitHub](https://github.com/irishkiwi007/alpaca-options-agent)")

if st.button("🔄 Refresh"):
    st.rerun()
