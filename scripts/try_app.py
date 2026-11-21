"""End-to-end UI check: drive app.py through all 4 screens with Streamlit's official
AppTest (headless, real LLM calls ~15), using "Try with sample data" (fictional pair 2).

    python scripts/try_app.py            # report: Download available, Gmail available, start new
    python scripts/try_app.py --gmail    # also clicks "Send to my Gmail" (creates one draft)

Also checks live: blank answer is blocked (B1), and a browser REFRESH mid-interview
(new app session, same ?sid=) shows the same question.
"""

import argparse
import json
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 240  # seconds per script run (a graph step can take ~20-60 s)
BANK = json.loads((ROOT / "scripts" / "demo_answers_pair2.json").read_text(encoding="utf-8"))


def button(at: AppTest, label: str):
    for b in list(at.button) + list(at.get("form_submit_button")):
        if b.label == label:
            return b
    raise AssertionError(f"button {label!r} not found; have {[b.label for b in at.button]}")


def click(at: AppTest, label: str) -> AppTest:
    return button(at, label).click().run(timeout=TIMEOUT)


def question_text(at: AppTest) -> str:
    return next(m.value[5:] for m in at.markdown if m.value.startswith("#### "))


def pick_answer(question: str, number: int | None) -> str:
    if number == 4:
        return "I don't know"  # counts as skipped: 0, no LLM call
    q = question.lower()
    for entry in BANK["bank"]:
        if not entry.get("used") and any(k in q for k in entry["keywords"]):
            entry["used"] = True
            return entry["answer"]
    return BANK["default"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gmail", action="store_true")
    args = ap.parse_args()

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT).run()
    sid = at.query_params["sid"]
    print(f"[1 Upload] stepper: {at.markdown[1].value!r}")
    print(f"           title: {at.title[0].value!r} | caption: {at.caption[0].value[:60]!r}…")
    at = click(at, "✨ Try with sample data")
    print(
        f"           sample data -> paste mode {at.toggle[0].value}, "
        f"resume {len(at.text_area[0].value)} chars, "
        f"JD {len(at.text_area[1].value)} chars, email {at.text_input[0].value}"
    )
    at = click(at, "Analyse my fit →")
    assert not at.error, [e.value for e in at.error]

    print(
        f"[2 Gaps] {at.title[0].value} | "
        f"subtitle: {next(m.value for m in at.markdown if '-badge[' in m.value)}"
    )
    print(f"         {at.metric[0].label}: {at.metric[0].value} | {[c.value for c in at.caption][:2]}")
    print(
        f"         expander: {[e.label for e in at.expander if 'skills' in e.label]} | "
        f"table rows: {len(at.table[0].value)}"
    )
    at = click(at, "Start interview →")

    refreshed = blank_checked = False
    # The interview screen shows 'Question n of 10' or 'Second chance…'; the report doesn't.
    while any(c.value.startswith(("Question", "Second chance")) for c in at.caption):
        bars = [p.proto.text for p in at.get("progress")]
        where = next((c.value for c in at.caption if c.value.startswith(("Question", "Second chance"))), "")
        number = int(where.split()[1]) if where.startswith("Question") else None
        question = question_text(at)
        chips = next((m.value for m in at.markdown if "-badge[" in m.value and "Focus" in m.value), "")
        card = next((m.value for m in at.markdown if "-badge[" in m.value and " — " in m.value), "")
        print(f"[3 Interview] {where} | bars {bars} | {chips[:70]}")
        print(f"      Q: {question[:85]}")
        if card:
            print(f"      result card: {card[:100]}")
        if number == 2 and not blank_checked:  # B1 live: blank answer must be blocked
            at.text_area[0].input("    ")
            at = click(at, "Submit answer →")
            print(
                f"      blank submit -> error: {[e.value for e in at.error]} | "
                f"still Q2: {question_text(at) == question}"
            )
            blank_checked = True
        if number == 3 and not refreshed:  # simulate a browser refresh mid-question
            at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
            at2.query_params["sid"] = sid
            at2.run(timeout=TIMEOUT)
            print(f"      [refresh] new session, same sid -> same question: {question_text(at2) == question}")
            refreshed = True
        if number == 6:
            at = click(at, "Skip question")
        else:
            at.text_area[0].input(pick_answer(question, number))
            at = click(at, "Submit answer →")
        assert not at.error, [e.value for e in at.error]

    m = {x.label: x.value for x in at.metric}
    badge = next((x.value for x in at.markdown if x.value.startswith("### :")), "")
    print(f"[4 Report] {m} | recommendation: {badge}")
    hero = next((x.value for x in at.markdown if "close the gap" in x.value or x.value.endswith(".")), "")
    print(f"   hero line: {hero[:110]}")
    print(
        f"   actions: download {[d.label for d in at.get('download_button')]} | buttons "
        f"{[b.label for b in at.button if b.label not in ('Start a new interview',) or True]}"
    )
    print(
        f"   next steps (checkboxes): {len(at.checkbox)} | question expanders: "
        f"{[e.label for e in at.expander if '·' in e.label][:4]}…"
    )
    print(
        f"   {[x.value for x in at.markdown if x.value.startswith('**Second chance')]} | footer: "
        f"{at.caption[-1].value}"
    )
    if args.gmail:
        at = click(at, "📧 Send to my Gmail")
        print(
            f"   after Gmail -> download {[d.label for d in at.get('download_button')]} | "
            f"{[(b.label, b.disabled) for b in at.button if 'Draft' in b.label]}"
        )
    sys.exit(0)
