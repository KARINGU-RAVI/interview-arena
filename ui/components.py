"""Small reusable UI pieces built from Streamlit's own components (no JS).

Colour meaning (spec §1) — always colour + icon/word, never colour alone:
  green  = matched / Hire / score >= 7
  orange = partial / Lean No / score 5–6.9   (theme "orangeColor" = spec Warning)
  red    = missing / No Hire / score < 5
"""

from __future__ import annotations

import streamlit as st

STEPS = ["Upload", "Skill gaps", "Interview", "Report"]
CIRCLED = ["①", "②", "③", "④"]

STATUS_BADGE = {
    "matched": ("green", "✅ Matched"),
    "partial": ("orange", "🟡 Partial"),
    "missing": ("red", "❌ Missing"),
}
REC_COLOR = {"Strong Hire": "green", "Hire": "green", "Lean No": "orange", "No Hire": "red"}
ROUND_COLOR = {"technical": "blue", "project": "violet", "hr": "gray"}
ROUND_NAME = {"technical": "Technical", "project": "Project", "hr": "HR"}
ROUND_WEIGHT = {"technical": "40%", "project": "30%", "hr": "30%"}


def score_color(score: float) -> str:
    return "green" if score >= 7 else "orange" if score >= 5 else "red"


def badge(text: str, color: str) -> str:
    """Inline coloured badge (markdown directive), e.g. :green-badge[✅ Matched]."""
    return f":{color}-badge[{text.replace(']', ')')}]"


def stepper(current: int) -> None:
    """Top progress: ✓ done steps, current step bold blue, later steps grey.
    One line of text, so it wraps naturally on a phone (no columns needed)."""
    parts = []
    for i, name in enumerate(STEPS):
        if i < current:
            parts.append(f":green[✓ {name}]")
        elif i == current:
            parts.append(f":blue[**{CIRCLED[i]} {name}**]")
        else:
            parts.append(f":gray[{CIRCLED[i]} {name}]")
    st.markdown(" → ".join(parts))


def result_card(e: dict) -> None:
    """Previous answer's result: score pill + one-line feedback (skipped = neutral pill)."""
    with st.container(border=True):
        if e["skipped"]:
            st.markdown(f"{badge('Skipped', 'gray')} &nbsp; **{e['question_id']}** — {e['feedback']}")
        elif e.get("error"):
            st.markdown(f"{badge('Not scored', 'gray')} &nbsp; **{e['question_id']}** — {e['error']}")
        else:
            st.markdown(
                f"{badge(f'{e["score"]}/10', score_color(e['score']))} &nbsp; "
                f"**{e['question_id']}** — {e['feedback']}"
            )


def how_it_works() -> None:
    with st.expander("How it works", expanded=False):
        st.markdown(
            "1. **Upload** your resume and paste a job description.\n"
            "2. We find your **skill gaps** and ask **10 questions** focused on them.\n"
            "3. Every answer is **scored the same fair way** and you get feedback, a report and a PDF."
        )
