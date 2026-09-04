# Submission Write-Up: Circuit Breaker

**Alpaca AI Trading Agents Hackathon — 28 Aug to 4 Sep 2026**
**Repo:** github.com/irishkiwi007/alpaca-options-agent
**Live account:** dedicated paper account, $100,000 starting balance, Options Level 3
**Live dashboard:** streamlit deployment (see repo README for current URL)

## AI Logic

Claude has direct, unmediated authority over what to trade, when, and how, via native tool-calling against Alpaca's official MCP server. There is no pre-filtering formula deciding what counts as a candidate trade — Claude decides what data to check, whether current conditions warrant action, what to trade if so, and when to check again, every cycle, with no separate rules engine in the loop.

Each cycle, Claude checks account state, positions, its own recent activity, and market data — then reasons in natural language about whether and how to act. Market discovery isn't limited to a fixed watchlist: alongside direct SPY/QQQ price action, Claude has tools for market movers, most-active stocks, and a rotating batch of real S&P 500 constituents (built specifically because raw volume/mover screeners skew heavily toward penny stocks and rarely surface genuine large-caps).

**This system has demonstrably self-corrected, not just followed a fixed script.** After an early loss, Claude independently formed and then followed its own trading rule mid-session — "when a spread reaches near-max value, close it immediately rather than hoping for the last few cents" — and explicitly referenced that principle again when acting on it later the same day. Separately, after a pattern of excessive multi-day caution following a loss was observed in the live logs, the system prompt was revised to address it directly; the very next live cycle showed Claude reasoning about and correctly applying the new guidance in real time.

## Risk Gates

Three hard constraints, enforced in code — not left to the agent's judgment or instructions it could reason around:

1. **Defined risk only.** Every position must be a genuine two-leg spread with opposite sides. No tool exists that can place a naked or undefined-risk position.
2. **Spread economics sanity check.** A trade's net debit/credit must be less than the spread's maximum possible value — added after live testing surfaced a real near-miss where a mispriced spread would otherwise have slipped through.
3. **15% per-trade sizing cap**, checked against Claude's own stated worst-case loss before an order is placed.

Above individual trades: an automatic daily drawdown monitor flattens all positions and halts the process if equity falls 15% from that day's starting baseline, with no human needing to notice. The service also runs only during actual market hours (cron-scheduled, timezone-aware), rather than continuously — reducing both unattended exposure and cost.

Everything else — strategy choice, timing, sizing within the cap, when to exit — is Claude's call.

## Alpaca Infrastructure

All trading and market data access goes through **Alpaca's official MCP server** (`alpacahq/alpaca-mcp-server`), run as a local subprocess. Claude's tool-use requests are dispatched directly to Alpaca's MCP tools — `get_account_info`, `get_option_chain`, `place_option_order` with `order_class="mleg"`, position closes, and expiration handling — with no custom trading-logic wrapper in between.

Deployed on a persistent cloud VM under systemd (auto-restarting, structured JSONL audit log of every reasoning step and tool call). A separate Streamlit dashboard queries Alpaca's REST API directly — decoupled from the VM, so it works independently of whether the agent is currently running — and reconstructs genuine round-trip trades from raw fills, orders, and expiration activity (rather than showing disconnected legs), including real per-contract entry/exit pricing and actual account fees pulled from Alpaca's activity feed.

## Self-Assessed Limitations

Asked directly, through the live app, "what do you need," the agent identified five concrete gaps in its own reasoning rather than deflecting — further evidence this is genuine reasoning under uncertainty, not a scripted persona. In its own words, the two it flagged as most consequential: no consolidated view of net delta/theta/vega across all open positions simultaneously (called out against a same-day concentration of multiple NVDA spreads that wasn't quantified portfolio-wide), and no structured record of historical win rate by setup type — "I could be systematically overconfident on certain trade types." It also named gaps in realized-vs-assumed fill pricing, visibility into why specific orders didn't fill, and real-time macro/VIX context beyond price action alone. None of these bear on the hard-coded risk gates above, which hold regardless of what the agent knows or doesn't know about its own performance — they're about the quality of its judgment inside those bounds, and the agent surfaced them unprompted.

## Status at Submission

Live and trading on the real paper account since 31 Aug 2026, with a mix of real wins and losses across multiple positions — see the live dashboard for current results, including full trade-by-trade detail and the underlying reasoning trail. The codebase carries 117 automated tests, the large majority written directly against real trade data pulled from the live account rather than synthetic examples, after specific bugs were found through actual use.
