"""
Tracks account equity against a session-start baseline. If equity ever
falls past CATASTROPHIC_DRAWDOWN_PCT from that baseline, the caller
(main_autonomous.py) flattens all positions and stops the process —
automatically, without waiting for a human to notice. This is the one
safety mechanism designed to work even when nobody is watching, which
is the whole premise of running this unattended.

Baseline is read from a small state file on first run of each day
(reset daily) so a single bad day can't be measured against an
artificially low baseline from a prior bad day, and a good day can't
mask how bad the current day's swing has been.
"""
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone

CATASTROPHIC_DRAWDOWN_PCT = 0.15  # flatten and stop if equity falls 15% from today's starting baseline

BASELINE_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "daily_baseline.json")


@dataclass
class DrawdownCheck:
    triggered: bool
    current_equity: float
    baseline_equity: float
    drawdown_pct: float
    reason: str


def _load_baseline() -> dict:
    if not os.path.exists(BASELINE_PATH):
        return {}
    with open(BASELINE_PATH, "r") as f:
        return json.load(f)


def _save_baseline(record: dict):
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, "w") as f:
        json.dump(record, f, indent=2)


def check_drawdown(current_equity: float) -> DrawdownCheck:
    today = date.today().isoformat()
    baseline = _load_baseline()

    if baseline.get("date") != today:
        # First check of a new day — this equity becomes today's baseline.
        baseline = {
            "date": today,
            "equity": current_equity,
            "set_at": datetime.now(timezone.utc).isoformat(),
        }
        _save_baseline(baseline)
        return DrawdownCheck(
            triggered=False,
            current_equity=current_equity,
            baseline_equity=current_equity,
            drawdown_pct=0.0,
            reason="New daily baseline set; no drawdown to measure yet.",
        )

    baseline_equity = baseline["equity"]
    if baseline_equity <= 0:
        return DrawdownCheck(False, current_equity, baseline_equity, 0.0, "Invalid baseline equity; skipping check.")

    drawdown_pct = (baseline_equity - current_equity) / baseline_equity

    if drawdown_pct >= CATASTROPHIC_DRAWDOWN_PCT:
        return DrawdownCheck(
            triggered=True,
            current_equity=current_equity,
            baseline_equity=baseline_equity,
            drawdown_pct=drawdown_pct,
            reason=(
                f"Equity fell {drawdown_pct:.1%} from today's baseline (${baseline_equity:,.2f} -> "
                f"${current_equity:,.2f}), breaching the {CATASTROPHIC_DRAWDOWN_PCT:.0%} catastrophic "
                f"drawdown limit. Auto-flatten-and-stop triggered."
            ),
        )

    return DrawdownCheck(
        triggered=False,
        current_equity=current_equity,
        baseline_equity=baseline_equity,
        drawdown_pct=drawdown_pct,
        reason=f"Drawdown {drawdown_pct:.1%}, within the {CATASTROPHIC_DRAWDOWN_PCT:.0%} limit.",
    )
