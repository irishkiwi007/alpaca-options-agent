"""
Wires the performance reflection mechanism (agent_layer/performance_reflection.py,
created separately) into the two files that make up the live loop:

1. agent_layer/autonomous_agent.py -- reads the latest reflection and
   injects it into each cycle's opening message, same channel already
   used for manual operator notes.
2. main_autonomous.py -- triggers PerformanceReflectionAgent every 8
   cycles, after the trading cycle completes, wrapped so a failure
   here can never affect trading.

Run this AFTER creating agent_layer/performance_reflection.py.
This script only edits the two files above; it does not touch
agent_layer/tools.py, risk/, or execution/ at all.
"""
import sys

AGENT_PATH = "agent_layer/autonomous_agent.py"
MAIN_PATH = "main_autonomous.py"

AGENT_EDITS = [
    (
        'OPERATOR_NOTE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "OPERATOR_NOTE")',
        '''OPERATOR_NOTE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "OPERATOR_NOTE")
PERFORMANCE_REFLECTION_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PERFORMANCE_REFLECTION")''',
    ),
    (
        '''def _consume_operator_note() -> str:
    """
    If a note has been left (see deploy/DEPLOY.md), read it, delete the
    file so it's only injected once, and return its text. Lets the
    operator correct a factual error or flag something without needing
    to stop and restart the whole process — the note becomes part of
    the very next cycle's opening message.
    """
    if not os.path.exists(OPERATOR_NOTE_PATH):
        return ""
    with open(OPERATOR_NOTE_PATH, "r") as f:
        note = f.read().strip()
    os.remove(OPERATOR_NOTE_PATH)
    return note''',
        '''def _consume_operator_note() -> str:
    """
    If a note has been left (see deploy/DEPLOY.md), read it, delete the
    file so it's only injected once, and return its text. Lets the
    operator correct a factual error or flag something without needing
    to stop and restart the whole process — the note becomes part of
    the very next cycle's opening message.
    """
    if not os.path.exists(OPERATOR_NOTE_PATH):
        return ""
    with open(OPERATOR_NOTE_PATH, "r") as f:
        note = f.read().strip()
    os.remove(OPERATOR_NOTE_PATH)
    return note


def _read_performance_reflection() -> str:
    """
    Reads the most recent auto-generated performance reflection (see
    agent_layer/performance_reflection.py), if one exists. Unlike the
    operator note, this is NOT deleted after reading — a reflection on
    real trading outcomes is meant to inform judgment across many
    cycles until a newer one supersedes it, not just the next one.
    """
    if not os.path.exists(PERFORMANCE_REFLECTION_PATH):
        return ""
    with open(PERFORMANCE_REFLECTION_PATH, "r") as f:
        return f.read().strip()''',
    ),
    (
        '''        operator_note = _consume_operator_note()
        opening_text = (
            "Begin this decision cycle. Check whatever account, position, and market information "
            "you need, decide whether to act, and act if warranted within your limits. End with "
            "your summary and the NEXT_CHECK_MINUTES line."
        )
        if operator_note:
            log_event("operator_note_injected", {"note": operator_note})
            opening_text = (
                f"OPERATOR NOTE (read this first, it may correct something you previously assumed): "
                f"{operator_note}\\n\\n{opening_text}"
            )''',
        '''        operator_note = _consume_operator_note()
        performance_reflection = _read_performance_reflection()
        opening_text = (
            "Begin this decision cycle. Check whatever account, position, and market information "
            "you need, decide whether to act, and act if warranted within your limits. End with "
            "your summary and the NEXT_CHECK_MINUTES line."
        )
        if performance_reflection:
            log_event("performance_reflection_injected", {"reflection": performance_reflection})
            opening_text = (
                f"YOUR OWN RECENT PERFORMANCE (a self-generated reflection on real, closed trades "
                f"and their actual outcomes — context to weigh as you judge fit, not an instruction): "
                f"{performance_reflection}\\n\\n{opening_text}"
            )
        if operator_note:
            log_event("operator_note_injected", {"note": operator_note})
            opening_text = (
                f"OPERATOR NOTE (read this first, it may correct something you previously assumed): "
                f"{operator_note}\\n\\n{opening_text}"
            )''',
    ),
]

MAIN_EDITS = [
    (
        'from agent_layer.autonomous_agent import AutonomousTradingAgent',
        '''from agent_layer.autonomous_agent import AutonomousTradingAgent
from agent_layer.performance_reflection import PerformanceReflectionAgent

REFLECTION_EVERY_N_CYCLES = 8  # not precisely tuned -- a starting cadence''',
    ),
    (
        '''async def main_loop():
    config = CONFIG
    agent = AutonomousTradingAgent(config)

    log_event("autonomous_runner_start", {})

    while True:''',
        '''async def main_loop():
    config = CONFIG
    agent = AutonomousTradingAgent(config)
    reflection_agent = PerformanceReflectionAgent(config)
    cycle_count = 0

    log_event("autonomous_runner_start", {})

    while True:''',
    ),
    (
        '''        try:
            next_check_minutes = await agent.run_cycle()
        except Exception as e:
            log_event("autonomous_cycle_failed", {"error": str(e)})
            next_check_minutes = 15  # conservative fallback if a cycle errors out

        log_event("sleeping_until_next_cycle", {"minutes": next_check_minutes})
        time.sleep(next_check_minutes * 60)''',
        '''        try:
            next_check_minutes = await agent.run_cycle()
        except Exception as e:
            log_event("autonomous_cycle_failed", {"error": str(e)})
            next_check_minutes = 15  # conservative fallback if a cycle errors out

        cycle_count += 1
        if cycle_count % REFLECTION_EVERY_N_CYCLES == 0:
            try:
                reflection_agent.maybe_generate_reflection()
            except Exception as e:
                log_event("performance_reflection_trigger_failed", {"error": str(e)})

        log_event("sleeping_until_next_cycle", {"minutes": next_check_minutes})
        time.sleep(next_check_minutes * 60)''',
    ),
]


def apply_edits(path, edits, label):
    with open(path, "r") as f:
        content = f.read()

    for i, (old, new) in enumerate(edits, 1):
        count = content.count(old)
        if count != 1:
            print(f"FAILED on {label} edit {i}: found {count} occurrences (expected 1).")
            print(f"Nothing written to {path}. Paste this back for help.")
            sys.exit(1)
        content = content.replace(old, new)

    with open(path, "w") as f:
        f.write(content)
    print(f"{label}: all edits applied successfully.")


def main():
    apply_edits(AGENT_PATH, AGENT_EDITS, "autonomous_agent.py")
    apply_edits(MAIN_PATH, MAIN_EDITS, "main_autonomous.py")
    print("Done. Both files updated.")


if __name__ == "__main__":
    main()
