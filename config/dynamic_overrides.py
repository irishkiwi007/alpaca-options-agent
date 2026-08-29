"""
Persisted overrides for a whitelisted subset of StrategyConfig fields,
each with a hard min/max bound. This is the ONLY mechanism by which
the agent layer's rules-review process is allowed to change strategy
behavior at runtime.

Deliberately excluded from this whitelist: everything in RiskConfig
(position limits, notional caps, delta caps, daily-loss kill switch)
and StrategyConfig's exit rules (profit_take_pct is included as a
narrow exception below, but stop_loss_multiple and hard close timing
are not). The agent can make the strategy more or less willing to
*enter* a trade; it cannot make a trade, once entered, more dangerous.
That boundary is the point — see docs/SUBMISSION.md.
"""
import json
import os
from dataclasses import replace
from datetime import datetime, timezone

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "dynamic_overrides.json")

# field_name -> (min, max). Only fields listed here can ever be changed
# by the rules-review agent, and only within these bounds, regardless
# of what the agent's reasoning argues for.
ADJUSTABLE_BOUNDS = {
    "vix_entry_threshold": (10.0, 30.0),
    "min_iv_rank": (10.0, 80.0),
    "target_short_delta": (0.10, 0.30),
    "profit_take_pct": (0.25, 0.75),
}


def load_overrides() -> dict:
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    with open(OVERRIDES_PATH, "r") as f:
        return json.load(f)


def apply_override(field: str, new_value: float, reasoning: str) -> dict:
    """
    Validates field is whitelisted and value is within bounds, then
    persists it. Returns the record written (including whether it was
    clamped), so callers can log the actual outcome, not just the ask.
    Raises ValueError for a non-whitelisted field — this is a hard
    stop, not a clamp, because allowing arbitrary fields to be written
    would defeat the whitelist entirely.
    """
    if field not in ADJUSTABLE_BOUNDS:
        raise ValueError(
            f"'{field}' is not in the adjustable whitelist. "
            f"Only {list(ADJUSTABLE_BOUNDS.keys())} can be modified by the rules-review agent."
        )

    lo, hi = ADJUSTABLE_BOUNDS[field]
    clamped_value = max(lo, min(hi, new_value))
    was_clamped = clamped_value != new_value

    overrides = load_overrides()
    previous_value = overrides.get(field, {}).get("value")
    overrides[field] = {
        "value": clamped_value,
        "requested_value": new_value,
        "was_clamped": was_clamped,
        "previous_value": previous_value,
        "reasoning": reasoning,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    with open(OVERRIDES_PATH, "w") as f:
        json.dump(overrides, f, indent=2)

    return overrides[field]


def effective_strategy_config(base_config):
    """
    Returns a copy of the given StrategyConfig with any persisted,
    whitelisted overrides applied on top. Called once per session in
    main.py so a single review's changes are visible to that session's
    signal generation without mutating the frozen base CONFIG object.
    """
    overrides = load_overrides()
    changes = {field: rec["value"] for field, rec in overrides.items() if field in ADJUSTABLE_BOUNDS}
    return replace(base_config, **changes) if changes else base_config
