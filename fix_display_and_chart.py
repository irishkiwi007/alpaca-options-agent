"""
Applies six fixes to streamlit_app.py:
1-5. Removes five caption lines per user request (redundant/cluttering
     explanatory text under tables and on the detail page).
6. Changes "Still open — no closing legs yet." to "Still open".
7. Reformats rationale text: splits on sentence boundaries and puts
   each sentence on its own line instead of running together with
   periods (in addition to the earlier dollar-sign escaping fix).
8. Adds feed=iex to the bars request. Root cause of "No bar data
   available" persisting even after the future-timestamp fix: with no
   feed specified, Alpaca defaults to the SIP feed, which free/paper
   accounts aren't authorized for real-time/recent data on — the
   entire request comes back empty rather than partially. IEX is
   available to paper accounts without a real-time data subscription
   and covers the current session, so it's the correct default here.
"""
import re
import sys

PATH = "streamlit_app.py"

CAPTIONS_TO_REMOVE = [
    '    st.caption("\'Current/Last\' reflects the most recently quoted price whether the market is open or closed right now — it is not necessarily a live, updating quote outside market hours. Totals include the standard \u00d7100 options multiplier.")\n',
    '    st.caption("$/Ctr figures are the per-contract premium quote. Totals include the standard \u00d7100 options multiplier — real account dollars, live and updating.")\n',
    'st.caption("Completed trades only — click a row, then use the button below to open its detail page with a price chart. Still-open positions are in Open Positions, above.")\n',
    '    st.caption("$/Ctr figures are the per-contract premium quote. Totals include the standard \u00d7100 options multiplier — real account dollars, same as Outcome.")\n',
    '        st.caption("\'$/Ctr\' figures are per-contract premium quotes. All totals (here and above) include the standard \u00d7100 options multiplier — real account dollars throughout this page.")\n',
]

SIMPLE_EDITS = [
    (
        '        st.caption("Still open — no closing legs yet.")',
        '        st.caption("Still open")',
    ),
    (
        '''    bars_resp = fetch(DATA_URL, f"/v2/stocks/{trade['underlying']}/bars", {
        "timeframe": bar_timeframe, "start": start, "end": end, "limit": 300,
    })''',
        '''    bars_resp = fetch(DATA_URL, f"/v2/stocks/{trade['underlying']}/bars", {
        "timeframe": bar_timeframe, "start": start, "end": end, "limit": 300,
        "feed": "iex",  # SIP (the default) isn't authorized for recent/real-time
                        # data on paper/free accounts and returns nothing for the
                        # whole request rather than just the recent tail; IEX covers
                        # the current session without needing a real-time subscription.
    })''',
    ),
]

RATIONALE_FUNC_OLD = '''def render_rationale_text(text: str) -> str:
    """
    Escapes literal "$" before markdown rendering. Streamlit's markdown
    treats a pair of "$" as inline LaTeX/KaTeX math delimiters by
    default; the agent's rationale text is full of dollar amounts, so
    without this, arbitrary spans between dollar signs get rendered as
    garbled math notation in a different font.
    """
    if not text:
        return "\u2014"
    return text.replace("$", "\\\\$")'''

RATIONALE_FUNC_NEW = '''def render_rationale_text(text: str) -> str:
    """
    Two fixes for the agent's rationale text, which is written by the
    LLM as dense run-on prose:
    1. Escapes literal "$" before markdown rendering — Streamlit's
       markdown treats a pair of "$" as inline LaTeX/KaTeX math
       delimiters by default, and this text is full of dollar amounts,
       so without this, arbitrary spans between dollar signs render as
       garbled math notation in a different font.
    2. Splits on sentence boundaries (period + whitespace) and puts
       each sentence on its own line, dropping the period. Safe
       against decimals like "$2.01" since a decimal point is never
       followed by whitespace.
    """
    if not text:
        return "\u2014"
    text = text.replace("$", "\\\\$")
    sentences = re.split(r"\\.\\s+", text.strip())
    sentences = [s.rstrip(".").strip() for s in sentences if s.strip()]
    return "  \\n".join(sentences)'''


def main():
    with open(PATH, "r") as f:
        content = f.read()

    removed = 0
    for line in CAPTIONS_TO_REMOVE:
        count = content.count(line)
        if count != 1:
            print(f"FAILED removing caption (found {count}, expected 1): {line[:70]}...")
            sys.exit(1)
        content = content.replace(line, "")
        removed += 1

    for i, (old, new) in enumerate(SIMPLE_EDITS, 1):
        count = content.count(old)
        if count != 1:
            print(f"FAILED on simple edit {i}: found {count} occurrences (expected 1).")
            sys.exit(1)
        content = content.replace(old, new)

    count = content.count(RATIONALE_FUNC_OLD)
    if count != 1:
        print(f"FAILED updating render_rationale_text: found {count} occurrences (expected 1).")
        sys.exit(1)
    content = content.replace(RATIONALE_FUNC_OLD, RATIONALE_FUNC_NEW)

    if "\nimport re\n" not in content:
        content = content.replace(
            "import streamlit as st\n",
            "import streamlit as st\nimport re\n",
            1,
        )

    with open(PATH, "w") as f:
        f.write(content)

    print(f"All fixes applied successfully. Removed {removed} captions, "
          f"{len(SIMPLE_EDITS)} simple edits, rationale formatting updated.")


if __name__ == "__main__":
    main()
