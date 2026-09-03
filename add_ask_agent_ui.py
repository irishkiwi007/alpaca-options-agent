"""
Adds an "Ask the Agent" section to the dashboard: a text box where the
person viewing the dashboard can ask a question about the agent's real
recent activity and get a real answer back, generated live.

Safety design (same guarantee as the standalone ask_agent.py CLI
version, adapted to run inside Streamlit Cloud instead of on the VM):
- Fresh, isolated Anthropic conversation per question -- never
  appended to the live trading loop's own conversation, which this
  process has no access to anyway (this runs on Streamlit Cloud, not
  the VM).
- NO tools passed to the API call at all -- it is architecturally
  incapable of placing, closing, or modifying any order or position.
- Answer is shown in the browser and kept only in that session's
  st.session_state -- never written anywhere the live agent (running
  on the VM) could ever read it back. There is no file, no repo commit,
  no path from this feature into the agent's own context, at all.
- Context is built only from data already fetched by this dashboard
  (account, positions, and the synced reasoning export) -- read-only,
  same data the person can already see elsewhere on the page.

Requires ANTHROPIC_API_KEY to be added to this app's Streamlit Cloud
secrets. If it's missing, the section explains that plainly instead of
erroring.
"""
import sys

PATH = "streamlit_app.py"

EDITS = [
    (
        "import streamlit as st\nimport re\nimport requests",
        "import streamlit as st\nimport re\nimport requests\nimport anthropic",
    ),
    (
        'BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")',
        '''BASE_URL = st.secrets.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")''',
    ),
    (
        '''trades = build_trade_records(all_orders, positions, expiry_activities)

# =================================================================
# DETAIL VIEW
# =================================================================''',
        '''trades = build_trade_records(all_orders, positions, expiry_activities)


def ask_agent_isolated(question: str, account: dict, positions: list, reasoning_records: list) -> str:
    """
    Answers a question about the agent's real recent activity, using
    only data this dashboard already has. No tools are passed to this
    API call -- there is nothing here that can place, close, or modify
    a trade, by construction, not by instruction alone.
    """
    recent_reasoning = reasoning_records[-15:] if reasoning_records else []
    reasoning_text = "\\n".join(
        f"[{r.get('timestamp')}] {r.get('action')} {r.get('underlying')}: {r.get('rationale', '')[:300]}"
        for r in recent_reasoning
    ) or "No recent reasoning synced yet."

    context = (
        f"Current account equity: ${account.get('equity', 'unknown')}\\n"
        f"Current open positions: {len(positions)} position(s)\\n\\n"
        f"Recent trade reasoning (most recent {len(recent_reasoning)} entries):\\n{reasoning_text}"
    )

    system_prompt = (
        "You are answering a question from someone viewing your public trading dashboard, "
        "about your own real recent trading activity. This conversation has no tools to "
        "place, close, or modify any order or position -- you are architecturally incapable "
        "of trading right now, so answer honestly and reflectively, not as if you're deciding "
        "anything. Nothing you say here will be shown to your trading-cycle self or affect "
        "what you do next cycle. Base your answer only on the real context provided; if you "
        "don't have enough information to answer confidently, say so rather than guessing. "
        "Keep it to a few sentences -- this is a dashboard, not a report."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"Context on your recent activity:\\n\\n{context}\\n\\nQuestion: {question}",
        }],
    )
    return "".join(b.text for b in response.content if hasattr(b, "text")).strip()


# =================================================================
# DETAIL VIEW
# =================================================================''',
    ),
    (
        '''st.subheader("What makes this different")''',
        '''st.subheader("Ask the Agent")
st.write("Feel free to ask my AI for information on its trading (note this does not influence its decision making)")

if "qa_history" not in st.session_state:
    st.session_state.qa_history = []

if not ANTHROPIC_API_KEY:
    st.info("Q&A isn't configured on this dashboard yet.")
else:
    question = st.text_input("Your question", key="qa_question", label_visibility="collapsed",
                              placeholder="e.g. Why did you hold NVDA overnight instead of taking profit?")
    if st.button("Ask", type="primary") and question.strip():
        with st.spinner("Thinking..."):
            try:
                reasoning_records = fetch_reasoning_export()
                answer = ask_agent_isolated(question.strip(), account, positions, reasoning_records)
                st.session_state.qa_history.insert(0, {"q": question.strip(), "a": answer})
            except Exception as e:
                st.error(f"Couldn't get an answer right now: {e}")

    for pair in st.session_state.qa_history:
        with st.container(border=True):
            st.markdown(f"**Q: {pair['q']}**")
            st.write(pair["a"])

st.divider()

st.subheader("What makes this different")''',
    ),
]


def main():
    with open(PATH, "r") as f:
        content = f.read()

    for i, (old, new) in enumerate(EDITS, 1):
        count = content.count(old)
        if count != 1:
            print(f"FAILED on edit {i}: found {count} occurrences (expected 1).")
            print("Nothing written. streamlit_app.py unchanged. Paste this back for help.")
            sys.exit(1)
        content = content.replace(old, new)

    with open(PATH, "w") as f:
        f.write(content)

    print("Ask the Agent feature added successfully.")


if __name__ == "__main__":
    main()
