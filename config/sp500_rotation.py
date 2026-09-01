"""
Persists which position in the S&P 500 ticker list the rotation is
currently at, so successive cycles see different batches rather than
always the same first N tickers. State lives in a small JSON file,
gitignored, similar to config/dynamic_overrides.json.
"""
import json
import os

STATE_PATH = os.path.join(os.path.dirname(__file__), "sp500_rotation_state.json")


def get_next_batch(all_tickers: list, batch_size: int) -> list:
    """
    Returns the next `batch_size` tickers from the rotation, advancing
    and persisting the position for next time. Wraps around to the
    start once it reaches the end, so coverage is continuous over time
    rather than stopping after one pass.
    """
    if not all_tickers:
        return []

    position = 0
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r") as f:
                position = json.load(f).get("position", 0)
        except (json.JSONDecodeError, OSError):
            position = 0

    position = position % len(all_tickers)
    batch = []
    for i in range(batch_size):
        batch.append(all_tickers[(position + i) % len(all_tickers)])

    new_position = (position + batch_size) % len(all_tickers)
    try:
        with open(STATE_PATH, "w") as f:
            json.dump({"position": new_position}, f)
    except OSError:
        pass  # non-critical if this fails to persist; worst case, next batch overlaps this one

    return batch
