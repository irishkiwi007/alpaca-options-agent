"""
Structured JSONL logger. Every fast-layer signal, agent decision, risk
governor verdict, and order event gets a line here. This is what the
demo dashboard reads to show the agent's reasoning trail — the thing
hackathon judges actually want to see, not just a PnL number.
"""
import json
import os
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "events.jsonl")


def log_event(event_type: str, payload: dict):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_events(limit: int = 200) -> list:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r") as f:
        lines = f.readlines()[-limit:]
    return [json.loads(line) for line in lines]
