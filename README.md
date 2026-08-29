# Alpaca Options Agent

Built for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (28 Aug – 4 Sep 2026).

## Primary mode: fully autonomous agent (`main_autonomous.py`)

This is the architecture used for the actual submission. Claude has direct, unmediated authority over strategy, timing, and trade origination via Alpaca's MCP server — no pre-filtering formula decides what counts as a candidate. Each cycle, Claude decides what to check, whether to act, what to trade, and when to check again; it also assesses its own recent activity and can change its own approach based on what it observes.

**Two hard backstops, enforced in code, not prompt** — the only limits Claude cannot reason around:
1. **Defined risk only** — every position must be a genuine two-leg spread (one leg bought, one sold). There is no tool for placing a naked/undefined-risk position; `place_spread_order` structurally requires both legs.
2. **15% per-trade sizing cap** — no single trade may risk more than 15% of current account equity. Enforced by `risk/hard_backstops.py` before any order reaches Alpaca.

**One automatic safety net:** if account equity ever drops 15% from that day's starting baseline, the agent flattens all positions and stops itself — automatically, without requiring a human to notice (`risk/drawdown_monitor.py`). Two manual stop mechanisms are also available — see `deploy/DEPLOY.md`.

Everything else — what to trade, when, how much (within the 15% cap), what strategy structure, when to exit — is Claude's judgment, made fresh each cycle via direct tool calls to Alpaca's MCP server. See `agent_layer/autonomous_agent.py`, `agent_layer/tools.py`, and `agent_layer/autonomous_prompts.py`.

Deployment: this needs to run continuously, so it's deployed as a systemd service on a VM with confirmed Alpaca network access — see `deploy/DEPLOY.md` for the full setup.

## Alternate mode: reviewed/deterministic pipeline (`main.py`)

An earlier, more constrained architecture is also in this repo and fully functional — useful for comparison, or as a more conservative fallback. It separates speed from judgment differently:

- A deterministic **fast layer** scans market regime (VIX level, term structure, IV rank) and proposes 0DTE credit spread candidates on a short interval — no LLM in this path.
- An **agent layer** (Claude) reviews each candidate with visible, logged reasoning — approve, resize, or reject.
- A **portfolio governor** has final veto power over every trade (position count, notional, delta, daily loss).
- A separate **rules-review agent** can adjust four whitelisted, bounded strategy parameters based on observed activity (see "Self-tuning" below).
- **Exit management** stays rule-based, never waiting on an LLM call.

This mode is what's described in the rest of this README below. Both modes share the same MCP client, trade logger, and Alpaca account.

## Why split it this way (reviewed mode)

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

**Autonomous mode (`main_autonomous.py`), verified with a real, live Claude API call:** the full agentic loop was run end-to-end — Claude independently decided to check account info, positions, and its own activity log simultaneously; when calls failed (the dev sandbox can't reach Alpaca's network), it retried, correctly diagnosed the failures as a persistent infrastructure issue rather than transient, and **explicitly declined to trade** — "acting blindly without knowing my equity, existing positions, or market prices would be irresponsible, so I will stand down" — then chose a 5-minute retry interval on its own. No part of that sequence was scripted; it's genuine tool-use-driven reasoning. 51/51 unit tests pass, including 20 covering the two hard backstops and the drawdown monitor specifically.

**Reviewed mode (`main.py`), also fully tested:**
- Core logic — indicators, signal gating, risk governor, exit rules, and override bounds — is implemented and unit-tested.
- End-to-end async pipeline runs clean in `--dry-run` mode, including graceful, logged failure when live data or the Anthropic key isn't available.
- **All trading and market data access, in both modes, goes through Alpaca's official MCP server** (`execution/mcp_client.py`, spawned as a local stdio subprocess), not the raw `alpaca-py` SDK — verified working: the MCP handshake succeeds and exposes all 72 tools before any network call is attempted.
- **Live network access to Alpaca's API hosts is pending** in the dev environment this was built in — confirmed by testing that the MCP subprocess and tool-calling machinery work correctly, and the failure is isolated to the final network hop. No code changes are needed once that access is available; both modes already handle it gracefully rather than crashing.
- **`ANTHROPIC_API_KEY` is confirmed working** — verified with a real API call, and this testing surfaced and fixed a real bug (Claude occasionally wrapping JSON in markdown code fences despite instructions not to, breaking strict parsing in the reviewed-mode agents).
- Order execution is written and tested against the MCP tool interface in both modes but has not yet fired a real order against the paper account — pending the VM deployment step in `deploy/DEPLOY.md`.
- Entry thresholds in reviewed mode (`vix_entry_threshold: 12.0`, `min_iv_rank: 20.0`) were calibrated against the actual low-volatility conditions observed in late Aug 2026 (VIX near 2026 lows, ~14-15), not a generic "safe" default.

## Setup

```bash
cp .env.example .env
# fill in ALPACA_API_KEY, ALPACA_SECRET_KEY (paper account), ANTHROPIC_API_KEY

pip install -r requirements.txt
python3 -m pytest tests/ -v              # verify core logic first (51 tests, no network needed)

# Reviewed mode (deterministic + agent review):
python3 main.py --dry-run --once         # single pass, logs decisions, places no orders
python3 main.py --live-paper             # continuous loop, places real paper orders

# Autonomous mode (Claude decides everything, within the two hard backstops):
python3 main_autonomous.py               # runs continuously; see deploy/DEPLOY.md for VM deployment
```

No separate MCP server install step is needed — `alpaca-mcp-server` is a pip dependency and `execution/mcp_client.py` spawns it automatically as a subprocess per call.

For continuous unattended operation (the intended mode for the actual submission), see `deploy/DEPLOY.md` — it needs to run on a host with confirmed Alpaca network access, deployed as a systemd service.

## Safety notes

- In reviewed mode, `AlpacaExecutionClient` asserts `paper=True` at construction — this repo cannot place live orders even if the base URL were misconfigured.
- In autonomous mode, every order passes through two hard backstops (`risk/hard_backstops.py`) enforced in code before reaching Alpaca — defined-risk-only positions, 15% per-trade sizing cap — regardless of what Claude reasons in any given cycle. A catastrophic drawdown monitor (`risk/drawdown_monitor.py`) auto-flattens and stops the process if equity ever falls 15% from the day's starting baseline, without requiring a human to notice.
- `.env` is gitignored; only `.env.example` (no real secrets) is committed.
- Every decision at every layer — in both modes — is written to `logs/events.jsonl` as structured JSON, giving a full audit trail of *why* each trade did or didn't happen.

## Hackathon requirements checklist

- [x] Uses Alpaca's Trading API via its official MCP server (`alpacahq/alpaca-mcp-server`, not called directly via `alpaca-py`) — in autonomous mode, Claude calls MCP tools directly as part of its own reasoning loop, not through a human-built pipeline
- [x] Strategy incorporates options trading (0DTE credit verticals; autonomous mode is not limited to this structure)
- [x] Autonomous agent — Claude originates trade decisions directly via tool use, not reviewing pre-filtered candidates
- [x] New dedicated paper trading account, one per email, $100,000 starting balance, Options Level 3
- [x] One-page write-up covering AI logic, risk gates, and infrastructure — see `docs/SUBMISSION.md`
- [x] Demonstrates rules assessment and modification — both via the reviewed-mode rules-review agent (bounded, whitelisted) and via autonomous mode's own self-assessment each cycle (unbounded strategy judgment, within the two hard backstops)
- [x] Verified with a real, live autonomous cycle — see "Status" above
- [ ] Live-paper order actually placed and confirmed on the VM — pending the deployment step in `deploy/DEPLOY.md`
- [ ] Video pitch (MP4) and slide deck (PDF), if required by the actual submission form — not yet started; confirm against the hackathon's submission page
