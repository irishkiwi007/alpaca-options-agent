# Submission Write-Up: Alpaca Options Agent

**Alpaca AI Trading Agents Hackathon — 28 Aug to 4 Sep 2026**
**Repo:** github.com/irishkiwi007/alpaca-options-agent
**Competition account:** dedicated paper account, $100,000 starting balance, Options Level 3

## AI Logic

The agent is deliberately *not* an LLM that watches the market and decides what to trade. Calling an LLM on every price tick is too slow and too expensive for intraday options, and it invites the market-microstructure guesswork LLMs are worst at. Instead, the intelligence is split into three layers:

- **A deterministic fast layer** scans VIX level, VIX term structure (contango/backwardation), and IV rank on a short interval, and proposes a specific 0DTE bull-put-spread candidate — concrete strikes, concrete delta, concrete credit — only when the regime clearly supports selling premium. Most cycles it proposes nothing.
- **Claude reviews each candidate**, not the raw market. Given the specific trade and the rules engine's stated rationale, it returns a structured approve/resize/reject decision with 2–4 sentences of reasoning, checking whether the rationale actually holds up rather than just restating the filter that passed. This is capped at 12 calls per session, so the agent's role is bounded, auditable judgment on a short list of well-formed candidates — not unconstrained market commentary.
- **A separate rules-review agent assesses and modifies the strategy's own entry rules**, once per session, before trading begins — this is the piece that directly demonstrates the agent evaluating and adapting its own configuration rather than running a fixed, hand-tuned strategy indefinitely. It reads recent logged activity (how many candidates were found, why others were filtered out, what regime conditions looked like) and can propose a change to one of four whitelisted parameters (`vix_entry_threshold`, `min_iv_rank`, `target_short_delta`, `profit_take_pct`), each bounded by a hard min/max enforced in code. It is explicitly and structurally barred from touching any risk-limit parameter — position caps, notional caps, delta caps, the daily-loss kill switch, or the stop-loss multiple — so self-modification is confined to "how easily do I find a trade worth considering," never "how much can a single trade hurt the account." Every review is logged, including reviews that conclude no change is warranted.

Exit management is intentionally excluded from the LLM path entirely: profit-target, stop-loss, and time-stop rules fire from code, so closing a losing position never waits on an API round-trip.

**Concrete example of the assessment-and-modification loop in practice:** the strategy's initial entry thresholds (VIX > 20, IV rank > 40) were set generically and would have left the agent idle for the entire hackathon window, since VIX has been sitting near 2026 lows (~14–15) for weeks. Rather than simply hand-lowering the numbers, the thresholds were recalibrated to reflect the actual observed regime (VIX > 12, IV rank > 20) as a documented starting point, and the rules-review agent is positioned to continue that assessment autonomously each session going forward — evaluating whether the current thresholds are filtering out everything indiscriminately versus correctly reflecting a genuine absence of opportunity, and adjusting within the same hard bounds if the evidence supports it.

## Risk Gates

Three independent layers, each of which can veto a trade the others approved:

1. **Fast-layer gating** — no candidate is even proposed unless VIX is above threshold, term structure isn't inverted, and IV rank clears a minimum bar.
2. **Agent review** — Claude can reject or resize any candidate that passes the fast layer's mechanical filters but doesn't hold up on inspection.
3. **Portfolio governor** — final, account-wide veto: max concurrent positions, max notional per trade as a % of equity (with automatic contract-count scaling down rather than a hard reject where possible), a portfolio-level net delta cap, and a daily-loss kill switch that halts new entries for the session.

Every decision at every layer — including rejections — is written to `logs/events.jsonl` as structured JSON, giving a full audit trail of why each trade did or didn't happen.

## Alpaca Infrastructure

All trading and market data access goes through **Alpaca's official MCP server** (`alpacahq/alpaca-mcp-server`, v2.3.0), run as a local stdio subprocess and called via the standard `mcp` Python client — not the raw `alpaca-py` SDK. This was verified directly: the MCP handshake succeeds and correctly exposes all 72 tools (`place_option_order`, `get_option_chain`, `get_account_info`, etc.) before any network call is attempted. Order execution uses `place_option_order` with `order_class="mleg"` for the two-leg credit spread. The execution client is hard-asserted to `paper=True` at construction, so it cannot place a live order regardless of configuration.

## Status at Submission

Core logic (indicators, signal gating, risk governor, exit rules) is implemented and covered by 19 passing unit tests with no network dependency. The full pipeline runs end-to-end in `--dry-run` mode and fails gracefully — logging clear, catchable errors — when live market data isn't reachable, which is the state of the dev environment this was built in pending a final network configuration step. No code changes are required for those same paths to succeed once that access is confirmed.
