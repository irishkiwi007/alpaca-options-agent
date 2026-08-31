"""
Streamlit demo dashboard for the hackathon's "Demo Application Platform"
requirement. Deployed on Streamlit Community Cloud, this queries Alpaca's
REST API directly — it doesn't depend on the VM running main_autonomous.py
being up, so it works as a standalone, always-available demo of the live
paper account's real state.

Reads credentials from Streamlit secrets (st.secrets), never hardcoded.
See README's "Demo dashboard" section for deployment steps.

Note: this app's dependencies live in the root requirements.txt
(lightweight — just streamlit + requests) so Streamlit Cloud's default
auto-detection picks them up with zero configuration. The trading
agent's own, much heavier dependencies live in requirements-agent.txt
instead — install that one on the VM, not this one.
"""
import streamlit as st
import requests
from datetime import datetime

st.set_page_config(page_title="Circuit Breaker — Live Demo", page_icon="⚡", layout="wide")

# ---- Theme (matches the project's cover image / deck brand) ----
st.markdown("""
<style>
    .stApp { background-color: #0A0E17; }
    h1, h2, h3, p, span, div { color: #F9FAFB; }
    .metric-card {
        background-color: #1F2937;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 10px;
    }
    .stDataFrame { background-color: #1F2937; }
</style>
""", unsafe_allow_html=True)

API_KEY = st.secrets.get("ALPACA_API_KEY", "")
SECRET_KEY = st.secrets.get("ALPACA_SECRET_KEY", "")
BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

HEADERS = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY,
}


def fetch(path: str):
    try:
        resp = requests.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


st.title("⚡ Circuit Breaker")
st.caption("An autonomous AI trading agent, built on Alpaca — live paper account status")

if not API_KEY or not SECRET_KEY:
    st.error(
        "Alpaca API credentials not configured. Add ALPACA_API_KEY and ALPACA_SECRET_KEY "
        "in this app's Streamlit Cloud secrets to connect to the live account."
    )
    st.stop()

account = fetch("/v2/account")

if "error" in account:
    st.warning(f"Could not reach Alpaca right now: {account['error']}")
else:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Equity", f"${float(account.get('equity', 0)):,.2f}")
    with col2:
        st.metric("Buying Power", f"${float(account.get('buying_power', 0)):,.2f}")
    with col3:
        st.metric("Options Level", account.get("options_trading_level", "—"))
    with col4:
        st.metric("Status", account.get("status", "—"))

st.divider()

left, right = st.columns([1, 1])

with left:
    st.subheader("Open Positions")
    positions = fetch("/v2/positions")
    if isinstance(positions, list) and positions:
        rows = [{
            "Symbol": p.get("symbol"),
            "Side": p.get("side"),
            "Qty": p.get("qty"),
            "Avg Entry": f"${float(p.get('avg_entry_price', 0)):.2f}",
            "Unrealized P/L": f"${float(p.get('unrealized_pl', 0)):.2f}",
        } for p in positions]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    elif isinstance(positions, list):
        st.info("No open positions right now.")
    else:
        st.warning("Could not load positions.")

with right:
    st.subheader("Recent Orders")
    orders = fetch("/v2/orders?status=all&limit=10&direction=desc")
    if isinstance(orders, list) and orders:
        rows = []
        for o in orders:
            legs = o.get("legs") or [o]
            symbols = ", ".join(l.get("symbol", "") for l in legs if l.get("symbol"))
            rows.append({
                "Submitted": (o.get("submitted_at") or "")[:19].replace("T", " "),
                "Symbol(s)": symbols or o.get("symbol", "—"),
                "Type": o.get("order_class", o.get("type", "—")),
                "Status": o.get("status"),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    elif isinstance(orders, list):
        st.info("No orders yet.")
    else:
        st.warning("Could not load orders.")

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

st.caption(f"Last refreshed: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC · [View source on GitHub](https://github.com/irishkiwi007/alpaca-options-agent)")

if st.button("🔄 Refresh"):
    st.rerun()
