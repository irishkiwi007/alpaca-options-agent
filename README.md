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

## Architecture

```
fast_layer/       Market data + indicators + signal generation (pure, testable, no orders)
agent_layer/       Claude review of candidates — approve/resize/reject with reasoning
risk/              Portfolio governor (final veto) + rule-based exit/stop-loss state machine
execution/         Alpaca order placement (isolated from read-only market data) + structured event logging
config/            All tunable parameters in one place — regime thresholds, risk caps, agent budget
main.py            Orchestrator / entrypoint — the pipeline described above, end to end
tests/             Unit tests for indicators, signal gating, and risk governor (19 passing, no network needed)
```

## Status

- Core logic — indicators, signal gating, risk governor, exit rules — is implemented and unit-tested (19/19 passing).
- End-to-end pipeline (`main.py`) runs clean in `--dry-run` mode.
- **Live market/options chain data wiring is pending network access to Alpaca's data hosts** from the dev environment this was scaffolded in. The gating logic is final; only the live data-fetch calls in `fast_layer/market_data.py` need confirming against real responses once reachable.
- Order execution (`execution/alpaca_client.py`) is written against `alpaca-py`'s multi-leg order API but not yet fired against the paper account — intentionally held until the person running this confirms they want the first live-paper run.

## Setup

```bash
cp .env.example .env
# fill in ALPACA_API_KEY, ALPACA_SECRET_KEY (paper account), ANTHROPIC_API_KEY

pip install -r requirements.txt
python3 -m pytest tests/ -v   # verify core logic first
python3 main.py --dry-run --once   # single pass, logs decisions, places no orders
python3 main.py --live-paper        # continuous loop, places real paper orders
```

## Safety notes

- `AlpacaExecutionClient` asserts `paper=True` at construction — this repo cannot place live orders even if the base URL were misconfigured.
- `.env` is gitignored; only `.env.example` (no real secrets) is committed.
- Every decision at every layer — fast-layer gate, agent verdict, risk verdict, order event — is written to `logs/events.jsonl` as structured JSON, giving a full audit trail of *why* each trade did or didn't happen.

## Hackathon requirements checklist

- [x] Uses Alpaca's Trading API (`alpaca-py`)
- [x] Strategy incorporates options trading (0DTE credit verticals)
- [ ] Uses Alpaca's MCP server or CLI — currently wired via `alpaca-py` SDK directly; MCP wiring is the next step once live data access is confirmed
- [x] New dedicated paper trading account (separate from any pre-existing bots)
