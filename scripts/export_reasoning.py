"""
Extracts the agent's own trade-decision reasoning from logs/events.jsonl
into a small, git-trackable JSON file (logs/reasoning_export.json) that
the hosted Streamlit dashboard can read over raw.githubusercontent.com.

Why this exists: the dashboard runs on Streamlit Cloud and only talks to
Alpaca's REST API — it has no live connection to this VM. The agent's
own written rationale for each trade only exists in this VM's local
events.jsonl. Committing a compact export to the repo is the simplest
bridge between the two, without standing up new infrastructure.

Run manually, or on a cron schedule via deploy/sync_reasoning.sh.
Safe to run repeatedly — it's a full re-derivation from events.jsonl
each time, not an incremental append, so it can never drift out of
sync or double-count.
"""
import json
import os

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "events.jsonl")
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "reasoning_export.json")

# Keep the export small — only what the dashboard actually needs to
# render per-trade reasoning.
MAX_RECORDS = 500


def load_events():
    if not os.path.exists(LOG_PATH):
        return []
    events = []
    with open(LOG_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def extract_reasoning(events):
    records = []
    for e in events:
        if e.get("event_type") != "agent_order_submit":
            continue
        p = e.get("payload", {})
        # NOTE: the deployed tools.py on this VM predates GitHub's version
        # and never wrote "action" into this log line. Every real
        # rationale-bearing order_submit is functionally an "open" —
        # closes never call place_spread_order with a rationale — so
        # defaulting missing action to "open" is correct for now.
        # Root cause fix (adding "action": action to tools.py's
        # log_event call) is tracked separately, deferred post-hackathon.
        action = p.get("action") or "open"
        records.append({
            "timestamp": e.get("timestamp"),
            "action": action,
            "underlying": p.get("underlying"),
            "buy_symbol": p.get("buy_symbol"),
            "sell_symbol": p.get("sell_symbol"),
            "contracts": p.get("contracts"),
            "limit_price": p.get("limit_price"),
            "max_loss_per_contract": p.get("max_loss_per_contract"),
            "rationale": p.get("rationale"),
            "setup_type": p.get("setup_type"),  # None for trades predating this field — handled as "untagged" elsewhere
        })
    return records[-MAX_RECORDS:]


def main():
    events = load_events()
    records = extract_reasoning(events)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({"records": records}, f, indent=2)
    print(f"Wrote {len(records)} reasoning records to {OUT_PATH}")


if __name__ == "__main__":
    main()
