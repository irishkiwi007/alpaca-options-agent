# Alpaca Options Agent

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (28 Aug – 4 Sep 2026).

An options trading agent that separates **speed** from **judgment**:

- A deterministic **fast layer** scans market regime (VIX level, term structure, IV rank) and proposes 0DTE credit spread candidates on a short interval — no LLM in this path, so it's not gated by API latency or cost.
- An **agent layer** (Claude) reviews each candidate with visible, logged reasoning before it's allowed forward — approve, resize, or reject, with a rationale a human could audit afterward.
- A **portfolio governor** has final veto power over every trade, checking account-wide exposure (position count, notional, delta, daily loss) regardless of what the fast layer or the agent decided.
- **Exit management** stays rule-based (profit target / stop-loss / time-stop), never waiting on an LLM call to close a losing position.

This mirrors a pattern already proven out across a fleet of deterministic trend-following bots — the addition here is a legible, LLM-reviewed decision layer sitting on top of it, specifically for options.

## Why split it this way

Calling an LLM on every price tick is neither fast nor cheap enough for intraday options. So the agent is only invoked at discrete decision points — when the fast layer has already found a candidate worth a second opinion — capped at `agent_max_calls_per_session` per session (see `config/settings.py`). Everything time-critical (exits, risk limits) is plain code.

## Self-tuning: rules assessment and modification

A third, separate agent — `agent_layer/rules_review_agent.py` — runs once per session, before any trading. It reads recent activity from `logs/events.jsonl` (how many candidates were found, why others were rejected, what regime conditions looked like) and decides whether a strategy parameter should change.

This is deliberately narrow and bounded, not free-form self-modification:

- It can **only** adjust four whitelisted fields — `vix_entry_threshold`, `min_iv_rank`, `target_short_delta`, `profit_take_pct` — each with a hard min/max enforced in code (`config/dynamic_overrides.py`), regardless of what the agent argues for.
- It **cannot** touch anything in `RiskConfig` (position limits, notional caps, delta caps, the daily-loss kill switch) or the stop-loss multiple. The system prompt tells it this explicitly, and the enforcement is structural, not just instructional — `apply_override()` raises on any non-whitelisted field.
- Every review is logged — including when it decides *not* to change anything — so "the agent looked and left it alone" is as visible as an actual change.
- Changes persist in `config/dynamic_overrides.json` (gitignored, runtime state) and are layered onto the base config for that session via `effective_strategy_config()`, never mutating the source-of-truth config file itself.

The practical motivation: this strategy's original entry thresholds were calibrated generically, not against live conditions. Rather than hand-tune them, the agent assesses whether current thresholds are filtering out everything indiscriminately versus reflecting a genuine absence of opportunity, and adjusts within safe bounds if warranted — with its reasoning on record either way.

## Architecture

```
fast_layer/       Market data + indicators + signal generation (pure, testable, no orders)
agent_layer/       Claude review of candidates (approve/resize/reject) AND the separate rules-review agent (bounded self-tuning)
risk/              Portfolio governor (final veto) + rule-based exit/stop-loss state machine
execution/         Alpaca order placement via MCP (isolated from read-only market data) + structured event logging
config/            All tunable parameters, plus the whitelisted/bounded override mechanism the rules-review agent writes to
main.py            Orchestrator / entrypoint — the pipeline described above, end to end
tests/             Unit tests for indicators, signal gating, risk governor, and override bounds (28 passing, no network needed)
```

## Status

- Core logic — indicators, signal gating, risk governor, exit rules, and override bounds — is implemented and unit-tested (28/28 passing).
- End-to-end async pipeline (`main.py`) runs clean in `--dry-run` mode, including graceful, logged failure when live data or the Anthropic key isn't available.
- **All trading and market data access goes through Alpaca's official MCP server** (`execution/mcp_client.py`, spawned as a local stdio subprocess), not the raw `alpaca-py` SDK — verified working: the MCP handshake succeeds and exposes all 72 tools before any network call is attempted.
- **Live network access to Alpaca's API hosts is pending** in the dev environment this was built in — confirmed by testing that the MCP subprocess and tool-calling machinery work correctly, and the failure is isolated to the final network hop. No code changes are needed once that access is available; `account_fetch_failed` / `market_data_fetch_failed` log events will simply stop appearing.
- **`ANTHROPIC_API_KEY` must be set** for both Claude-driven agents (trade review, rules review) to function — verified that omitting it produces a clean, logged fail-closed behavior rather than a crash or unreviewed trading.
- Order execution (`execution/alpaca_client.py::submit_vertical_spread`) is written and tested against the MCP tool interface but has not yet fired a real order against the paper account — intentionally held until confirmed against live data first.
- Entry thresholds (`vix_entry_threshold: 12.0`, `min_iv_rank: 20.0`) were calibrated against the actual low-volatility conditions observed in late Aug 2026 (VIX near 2026 lows, ~14-15), not a generic "safe" default — see the self-tuning section below for how the agent can adjust these further based on what it observes.

## Setup

```bash
cp .env.example .env
# fill in ALPACA_API_KEY, ALPACA_SECRET_KEY (paper account), ANTHROPIC_API_KEY

pip install -r requirements.txt
python3 -m pytest tests/ -v         # verify core logic first (no network needed)
python3 main.py --dry-run --once    # single pass, logs decisions, places no orders
python3 main.py --live-paper        # continuous loop, places real paper orders
```

No separate MCP server install step is needed — `alpaca-mcp-server` is a pip dependency and `execution/mcp_client.py` spawns it automatically as a subprocess per call.

## Safety notes

- `AlpacaExecutionClient` asserts `paper=True` at construction — this repo cannot place live orders even if the base URL were misconfigured.
- `.env` is gitignored; only `.env.example` (no real secrets) is committed.
- Every decision at every layer — fast-layer gate, agent verdict, risk verdict, order event — is written to `logs/events.jsonl` as structured JSON, giving a full audit trail of *why* each trade did or didn't happen.

## Hackathon requirements checklist

- [x] Uses Alpaca's Trading API via its official MCP server (`alpacahq/alpaca-mcp-server`, not called directly via `alpaca-py`)
- [x] Strategy incorporates options trading (0DTE credit verticals on SPY + QQQ)
- [x] New dedicated paper trading account, one per email, $100,000 starting balance, Options Level 3
- [x] One-page write-up covering AI logic, risk gates, and infrastructure — see `docs/SUBMISSION.md`
- [x] Demonstrates rules assessment and modification — see "Self-tuning" section above
- [ ] Live-paper order actually placed and confirmed — pending network access to Alpaca's API hosts from the dev environment; everything up to that point is built and tested
- [ ] Video pitch (MP4) and slide deck (PDF), if required by the actual submission form — not yet started; confirm against the hackathon's submission page
