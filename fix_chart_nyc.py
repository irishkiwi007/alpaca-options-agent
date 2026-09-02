"""
Two changes to the price chart on the trade detail page:
1. Removes the "zoom/pan-locked" caption, per user request.
2. Converts the chart's time axis to NYC — previously the candlestick
   bars used Alpaca's raw UTC timestamps directly, while the entry/exit
   vlines used UTC-based datetimes too, so the whole chart was in UTC
   even though every other timestamp on this page is already shown in
   NYC. Converts to NYC and strips the timezone offset before
   formatting, so Plotly renders the literal NYC wall-clock time.
"""
import sys

PATH = "streamlit_app.py"

EDITS = [
    (
        '        st.caption("Chart is zoom/pan-locked by default so it doesn\'t interfere with scrolling — use the toolbar above the chart (visible on tap) to zoom or reset the view.")\n',
        '',
    ),
    (
        '''        fig = go.Figure(data=[go.Candlestick(
            x=[b["t"] for b in bars],
            open=[b["o"] for b in bars],
            high=[b["h"] for b in bars],
            low=[b["l"] for b in bars],
            close=[b["c"] for b in bars],
            increasing_line_color="#22D3A8", decreasing_line_color="#EF4444",
        )])
        fig.add_vline(x=entry_dt.isoformat(), line_dash="dash", line_color="#F59E0B",
                       annotation_text="Entry", annotation_font_color="#F59E0B")
        if exit_dt:
            fig.add_vline(x=exit_dt.isoformat(), line_dash="dash", line_color="#0EA5E9",
                           annotation_text="Exit", annotation_font_color="#0EA5E9")''',
        '''        def to_nyc_naive(dt_or_iso):
            if isinstance(dt_or_iso, str):
                dt = datetime.fromisoformat(dt_or_iso.replace("Z", "+00:00"))
            else:
                dt = dt_or_iso
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(NYC_TZ).replace(tzinfo=None).isoformat()

        fig = go.Figure(data=[go.Candlestick(
            x=[to_nyc_naive(b["t"]) for b in bars],
            open=[b["o"] for b in bars],
            high=[b["h"] for b in bars],
            low=[b["l"] for b in bars],
            close=[b["c"] for b in bars],
            increasing_line_color="#22D3A8", decreasing_line_color="#EF4444",
        )])
        fig.add_vline(x=to_nyc_naive(entry_dt), line_dash="dash", line_color="#F59E0B",
                       annotation_text="Entry", annotation_font_color="#F59E0B")
        if exit_dt:
            fig.add_vline(x=to_nyc_naive(exit_dt), line_dash="dash", line_color="#0EA5E9",
                           annotation_text="Exit", annotation_font_color="#0EA5E9")''',
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

    print("Both fixes applied successfully.")


if __name__ == "__main__":
    main()
