"""Report (PRD FR-7.1, FR-7.4): Report Writer LLM call + markdown report.

- Overall score and recommendation are already computed in Python (final_evaluation).
- The Report Writer makes ONE batched LLM call: summary, top 3 actions, and an improved
  sample answer for every question that scored below 6 (skipped ones included).
- If that call fails, the report is still written, just without the AI-written parts.
- The markdown is saved under outputs/ (git-ignored). Phase 7 turns it into a PDF.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import config
from llm.router import AllProvidersFailedError, BudgetExceededError, LLMRouter
from schemas import ReportSummary

log = logging.getLogger("interview_arena.report")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "report_writer.md"
OUTPUT_DIR = config.PROJECT_ROOT / "outputs"


def sample_ids(state: dict) -> list[str]:
    """Questions that get an improved sample answer: score < 6 (skipped = 0 included).
    Unscored answers (provider error) are left out — we don't know how good they were."""
    return [
        e["question_id"]
        for e in state["evaluations"]
        if e["score"] < config.SAMPLE_ANSWER_BELOW and not e.get("error")
    ]


def build_writer_prompt(state: dict) -> str:
    questions = {q["id"]: q for q in state["questions"]}
    answers = {a["question_id"]: a for a in state["answers"]}
    rows = []
    for e in state["evaluations"]:
        q, a = questions[e["question_id"]], answers.get(e["question_id"], {})
        answer = "(skipped)" if e["skipped"] else a.get("answer", "")[: config.REPORT_ANSWER_CHARS]
        answer = answer.replace("</results>", "[/results]")
        rows.append(
            f"[{q['id']} · {q['round']} · score {e['score']}] Q: {q['text']}\n"
            f"A: {answer}\nWeaknesses: {'; '.join(e.get('weaknesses', [])) or '-'}"
        )
    gaps = [
        f"{g['skill']} ({g['status']})"
        for g in state["gaps"]
        if g["status"] != "matched" and g["importance"] == "required"
    ]
    profile = {k: state["resume_profile"][k] for k in ("skills", "tools", "years_experience", "projects")}
    o = state["overall"]
    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{ROLE_TITLE}}", state["jd_profile"]["role_title"])
        .replace("{{LEVEL}}", state["jd_profile"]["level"])
        .replace("{{OVERALL}}", str(o["score"]))
        .replace("{{RECOMMENDATION}}", o["recommendation"])
        .replace("{{MATCH}}", str(state["match_percent"]))
        .replace("{{GAPS}}", ", ".join(gaps) or "none")
        .replace("{{PROFILE}}", json.dumps(profile, ensure_ascii=False))
        .replace("{{SAMPLE_IDS}}", ", ".join(sample_ids(state)) or "(none)")
        .replace("{{SKIPPED}}", str(sum(e["skipped"] for e in state["evaluations"])))
        .replace("{{TOTAL}}", str(len(state["evaluations"])))
        .replace("{{RESULTS}}", "\n\n".join(rows))
    )  # last: answers can't inject placeholders


def write_summary(state: dict, router: LLMRouter) -> dict | None:
    """The ONE Report Writer call. Returns ReportSummary as a dict, or None if it failed."""
    try:
        summary = router.invoke_structured(build_writer_prompt(state), ReportSummary, node="report_writer")
    except (AllProvidersFailedError, BudgetExceededError) as exc:
        log.warning("report writer failed (%s); report written without AI summary", type(exc).__name__)
        return None
    # Keep only sample answers for the ids we asked for (the model may add or miss some).
    wanted = set(sample_ids(state))
    data = summary.model_dump()
    data["sample_answers"] = [s for s in data["sample_answers"] if s["question_id"] in wanted]
    return data


# ---------------------------------------------------------------- markdown


def display_name(name: str) -> str:
    """'ANANYA REDDY' -> 'Ananya Reddy'. Names already in mixed case are left alone."""
    name = " ".join(name.split())
    return name.title() if name.isupper() or name.islower() else name


def _cell(text: str) -> str:
    """Make text safe inside a markdown table cell."""
    return " ".join(str(text).split()).replace("|", "/")


def render_markdown(state: dict, summary: dict | None, router_summary: dict) -> str:
    o, jd, rp = state["overall"], state["jd_profile"], state["resume_profile"]
    answers = {a["question_id"]: a for a in state["answers"]}
    questions = {q["id"]: q for q in state["questions"]}
    L: list[str] = []
    # Short title + role subtitle (spec §5: no 2-line titles).
    L += [
        "# Interview Report",
        "",
        f"_{jd['role_title']} · {jd['level'].title()} level_",
        "",
        f"**Candidate:** {display_name(rp['name'])}  ",
        f"**Date:** {datetime.now():%d %b %Y}  ",
        f"**Skill match:** {state['match_percent']}%  ",
        f"**Overall score:** {o['score']}/10  ",
        f"**Recommendation:** {o['recommendation']}",
        "",
    ]
    names = {"technical": "Technical", "project": "Project", "hr": "HR"}
    by_round = " · ".join(f"{names[r]} {v:.1f}" for r, v in o["by_round"].items())
    L += [
        f"_Round averages: {by_round} (weights: Technical 40%, Project 30%, HR 30%)_",
        "",
        "_Skill match compares the resume with the JD; the overall score measures the interview answers._",
        "",
    ]

    if summary:
        L += ["## Summary", "", summary["summary"], "", "## Top 3 actions", ""]
        L += [f"{i}. {a}" for i, a in enumerate(summary["top_actions"], 1)] + [""]
    else:
        L += [
            "## Summary",
            "",
            "_The AI summary could not be generated this time "
            "(provider unavailable). Scores and feedback below are complete._",
            "",
        ]

    L += ["## Skill gaps", "", "| Skill | Importance | Status | Evidence |", "|---|---|---|---|"]
    for g in state["gaps"]:
        L.append(
            f"| {_cell(g['skill'])} | {g['importance'].replace('_', ' ')} | {g['status']} "
            f"| {_cell(g['evidence']) or '—'} |"
        )

    L += [
        "",
        "## Question by question",
        "",
        "| # | Round | Question | Score | Feedback |",
        "|---|---|---|---|---|",
    ]
    for e in state["evaluations"]:
        q = questions[e["question_id"]]
        note = e.get("error") or e["feedback"]
        L.append(f"| {q['id']} | {names[q['round']]} | {_cell(q['text'])} | {e['score']} | {_cell(note)} |")

    fu = state.get("follow_up") or {}
    if fu.get("evaluation"):
        fe = fu["evaluation"]
        L += [
            "",
            f"**Follow-up on {fu['of']}** (second chance, not part of the overall score): "
            f"{fe['score']}/10 — {fe.get('error') or fe['feedback']}  ",
            f"_Q: {fu['question']['text']}_",
        ]

    if summary and summary["sample_answers"]:
        L += ["", "## Sample better answers (questions under 6/10)", ""]
        for s in summary["sample_answers"]:
            q = questions.get(s["question_id"])
            if not q:
                continue
            yours = answers.get(q["id"], {})
            L += [
                f"### {q['id']} — {q['text']}",
                "",
                f"_Your answer:_ {'(skipped)' if yours.get('skipped') else _cell(yours.get('answer', ''))}",
                "",
                f"**Stronger answer:** {s['improved_answer']}",
                "",
            ]

    # FR-8.4: say which model scored which answers, and note any fallback.
    scored_by = sorted({e["evaluator_model"] for e in state["evaluations"] if e.get("evaluator_model")})
    L += ["", "---", "", "### About this report", ""]
    if not scored_by:
        L.append("- Answers scored by: none (all skipped).")
    elif len(scored_by) == 1:
        L.append(f"- All answers scored by `{scored_by[0]}` (locked for the session).")
    elif scored_by:
        per = ", ".join(
            f"{e['question_id']}: `{e['evaluator_model']}`"
            for e in state["evaluations"]
            if e.get("evaluator_model")
        )
        L.append(f"- Evaluator fallback happened — answers were scored by different models: {per}.")
    L.append(
        f"- Profiles extracted by `{state.get('extract_model', '?')}`"
        + (" (after a fallback)" if len(state.get("extract_attempts", [])) > 1 else "")
        + "."
    )
    L.append(
        f"- LLM calls this session: {router_summary['calls']} "
        f"(target {router_summary['target']}); overall score and recommendation are "
        "calculated in Python from the rubric weights, not by the AI."
    )
    skipped = [e["question_id"] for e in state["evaluations"] if e["skipped"]]
    if skipped:
        L.append(f"- Skipped / no answer (scored 0): {', '.join(skipped)}.")
    return "\n".join(L) + "\n"


def save_report(markdown: str, candidate_name: str) -> Path:
    """Save under outputs/ (git-ignored) with a safe, unique file name."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", candidate_name.lower()).strip("-") or "candidate"
    path = OUTPUT_DIR / f"report_{slug}_{datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text(markdown, encoding="utf-8")
    return path
