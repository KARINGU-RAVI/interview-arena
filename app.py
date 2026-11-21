"""Interview Arena — Streamlit UI.  Run:  streamlit run app.py
Design: docs/ui_design_spec.md (calm practice coach: Where am I? What now? What next?).

How the UI and LangGraph fit together:
- One graph (+ InMemorySaver checkpointer) per server process, shared by all users;
  each user is a separate thread_id.
- A refresh starts a NEW Streamlit session, so the session id lives in the URL (?sid=...).
  Live per-session objects (LLM router, RAG store) live in a server-side registry keyed by it.
- The screen comes ONLY from the graph state: no state -> Upload; paused at start_gate ->
  Skill gaps; paused at a question -> Interview; paused at send_gate / finished -> Report.
- Buttons hand a step (Command(resume=...)) to graph/worker.py, which runs it in a background
  thread that Streamlit reruns/refreshes can't interrupt; the page polls and shows each
  finished node. One step per session at a time (double clicks are ignored).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import streamlit as st
from langgraph.types import Command

import config
from crew.extraction_crew import ExtractionError
from graph import worker
from graph.build_graph import get_shared_graph
from graph.state import SessionContext
from llm.router import AllProvidersFailedError, BudgetExceededError, LLMRouter
from pdf_parser import PdfTooLargeError, ScannedPdfError, parse_resume_pdf
from ui import components as ui
from ui.styles import apply_styles
from ui.validation import EMPTY_ANSWER_ERROR, clean_answer, clean_block, validate_inputs, word_count

st.set_page_config(page_title="Interview Arena", page_icon="🎯", layout="centered")

# Debug log (off by default): INTERVIEW_ARENA_DEBUG=1  or  streamlit run app.py -- --debug
DEBUG = os.getenv("INTERVIEW_ARENA_DEBUG") == "1" or "--debug" in sys.argv
dlog = logging.getLogger("interview_arena.app")
_root = logging.getLogger("interview_arena")  # app + graph worker logs
if DEBUG and not _root.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(name)s %(message)s", "%H:%M:%S"))
    _root.addHandler(_h)
    _root.setLevel(logging.DEBUG)
    _root.propagate = False
apply_styles()

SAMPLE_RESUME = config.PROJECT_ROOT / "data" / "resume_2_ananya_frontend.txt"  # fictional
SAMPLE_JD = config.PROJECT_ROOT / "data" / "jd_2_frontend_developer.txt"
SAMPLE_EMAIL = "ananya.test@example.com"
STATUS_ORDER = {"missing": 0, "partial": 1, "matched": 2}
ROUND_SIZE = {"technical": 4, "project": 3, "hr": 3}

TIPS = {
    "technical": "**Concept → why → trade-off.** Say what it is, why it's used, and one trade-off or limit.",
    "project": (
        "**What you built → how → result.** Name your part, the key decision, and a measurable result."
    ),
    "hr": "**STAR:** Situation, Task, Action, Result. Keep it to one real example and end with the result.",
}


# ---------------------------------------------------------------- shared objects + session


@st.cache_resource
def get_graph():
    return get_shared_graph()


@st.cache_resource
def get_registry() -> tuple[dict, threading.Lock]:
    """sid -> {"ctx": SessionContext, "seen": time}. Shared by all sessions, so guarded by a lock."""
    return {}, threading.Lock()


def get_context(sid: str) -> SessionContext:
    registry, lock = get_registry()
    now = time.time()
    with lock:
        for old_sid, entry in list(registry.items()):  # clean up abandoned sessions
            if now - entry["seen"] > config.SESSION_TTL_SECONDS:
                if entry["ctx"].store is not None:
                    entry["ctx"].store.close()
                del registry[old_sid]
        if sid not in registry:
            registry[sid] = {"ctx": SessionContext(router=LLMRouter()), "seen": now}
        registry[sid]["seen"] = now
        return registry[sid]["ctx"]


def forget_session(sid: str) -> None:
    registry, lock = get_registry()
    with lock:
        entry = registry.pop(sid, None)
    if entry and entry["ctx"].store is not None:
        entry["ctx"].store.close()
    worker.forget(sid)


def is_registered(sid: str) -> bool:
    registry, lock = get_registry()
    with lock:
        return sid in registry


url_sid = st.query_params.get("sid")
sid = url_sid or uuid.uuid4().hex
if "_session_started" not in st.session_state:  # first run of a new Streamlit session
    st.session_state["_session_started"] = True
    dlog.debug(
        "new Streamlit session | sid %s", f"{sid[:8]} restored from URL" if url_sid else f"{sid[:8]} created"
    )
st.query_params["sid"] = sid  # keeps the session across a browser refresh
graph = get_graph()
known_session = is_registered(sid) or worker.is_known(sid)  # before get_context() registers it
ctx = get_context(sid)
CFG = {"configurable": {"thread_id": sid}}

FRIENDLY = {
    AllProvidersFailedError: (
        "Our AI providers are busy right now. Your progress is saved — wait a moment and press **Try again**."
    ),
    ExtractionError: (
        "We couldn't read your resume and the job description just now (AI providers busy). "
        "Your inputs are saved — press **Try again**."
    ),
    BudgetExceededError: (
        "This session reached its AI limit. Your progress is saved; "
        "the report will mark anything that couldn't be scored."
    ),
}


def run_graph(payload, label: str, toast: str | None = None) -> None:
    """Start a graph step in the background worker and redraw. The worker can't be cancelled
    by Streamlit reruns/refreshes, and refuses a second step while one runs (double clicks)."""
    if worker.submit(sid, graph, payload, CFG, ctx, label):
        dlog.debug("click accepted | sid %s | %r", sid[:8], label)
        if toast:
            st.session_state["toast_after_step"] = toast
    else:
        dlog.debug("click IGNORED (step already running) | sid %s | %r", sid[:8], label)
    st.rerun()


def wait_for_step() -> None:
    """Shown while a step runs: a spinner with elapsed time, then one redraw when it's done.
    Safe to be stopped by a rerun/refresh: the graph runs in the worker, not in this script,
    and nothing here touches the graph."""
    step = worker.status(sid)
    with st.spinner(step.label, show_time=True):
        deadline = time.time() + config.MAX_WAIT_PER_RUN_SECONDS
        while worker.is_busy(sid) and time.time() < deadline:
            time.sleep(0.2)
    st.rerun()


def collect_step_result() -> None:
    """After a step: turn its error (if any) into one friendly message; show the toast."""
    err = worker.take_error(sid)
    if err is not None:
        msg = next((m for cls, m in FRIENDLY.items() if isinstance(err, cls)), None)
        st.session_state["error"] = msg or (
            f"Something went wrong ({type(err).__name__}). Your progress is saved — press **Try again**."
        )
        st.session_state.pop("toast_after_step", None)
    elif "toast_after_step" in st.session_state:
        st.toast(st.session_state.pop("toast_after_step"), icon="✅")


def start_over(prefill: dict | None = None) -> None:
    """New session (new sid). Optional prefill keeps the user's inputs ("Change inputs")."""
    forget_session(sid)
    st.query_params.clear()
    st.session_state.clear()
    if prefill:
        st.session_state.update(prefill)
    st.rerun()


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown("### 🎯 Interview Arena")
    st.caption("Your AI interview practice coach")
    if st.button("Start a new interview", width="stretch"):
        start_over()
    ui.how_it_works()
    with st.expander("Session details", expanded=False):
        s = ctx.router.summary()
        st.caption(f"AI calls this session: **{s['calls']}** / {s['target']}")
        st.caption("Model: " + (", ".join(s["models_used"]) or "—"))
        repeats = worker.status(sid).repeats
        if repeats:
            st.caption(f"Repeated AI steps (retries): {repeats}")
        st.caption(f"Mode: `{config.APP_MODE}` · Session: `{sid[:8]}`")


# ---------------------------------------------------------------- screen 1: upload


def fill_sample() -> None:
    """'Try with sample data': fictional pair-2 resume + JD (never real personal data)."""
    st.session_state.update(
        {
            "in_paste": True,
            "in_resume": SAMPLE_RESUME.read_text(encoding="utf-8"),
            "in_jd": SAMPLE_JD.read_text(encoding="utf-8"),
            "in_email": SAMPLE_EMAIL,
        }
    )


def screen_upload() -> None:
    ui.stepper(0)
    st.title("Practice an interview for any job", anchor=False)
    st.caption(
        "Upload your resume and paste a job description. We'll find your skill gaps "
        "and interview you on them."
    )
    st.button(
        "✨ Try with sample data",
        type="tertiary",
        on_click=fill_sample,
        help="Fills a fictional resume and job description so you can try the app.",
    )

    left, right = st.columns(2)
    with left, st.container(border=True):
        st.markdown("**Your resume**")
        pasting = st.toggle("Paste text instead of a PDF", key="in_paste")
        pdf = None
        if pasting:
            st.text_area(
                "Resume text",
                key="in_resume",
                height=220,
                max_chars=config.MAX_RESUME_CHARS,
                placeholder="Paste your resume here…",
            )
        else:
            pdf = st.file_uploader("Resume PDF (max 2 MB)", type=["pdf"])
        resume_error = st.empty()
    with right, st.container(border=True):
        st.markdown("**Job description**")
        st.text_area(
            "Job description text",
            key="in_jd",
            height=220 if pasting else 260,
            max_chars=config.MAX_JD_CHARS,
            placeholder="Paste the full job description…",
        )
        jd_len = len(st.session_state.get("in_jd") or "")
        st.caption(f"{jd_len:,} / {config.MAX_JD_CHARS:,} characters")
        jd_error = st.empty()

    optional = " (optional in cloud mode)" if config.APP_MODE == "cloud" else ""
    st.text_input(f"Email for your report{optional}", key="in_email", placeholder="name@example.com")
    email_error = st.empty()
    st.caption(
        "🔒 Use a resume without phone number or address. Free AI providers may use "
        "inputs to improve their models."
    )

    if not st.button("Analyse my fit →", type="primary"):
        return
    # B2/B3: trim first, then validate each field and show the error next to it.
    try:
        resume = clean_block(
            parse_resume_pdf(pdf.getvalue())
            if (pdf and not pasting)
            else st.session_state.get("in_resume", "")
            if pasting
            else ""
        )
        parse_error = ""
    except (ScannedPdfError, PdfTooLargeError, ValueError) as exc:
        resume, parse_error = "", str(exc)
    jd = clean_block(st.session_state.get("in_jd", ""))
    email = (st.session_state.get("in_email") or "").strip()
    errors = validate_inputs(resume, jd, email, config.APP_MODE)
    if parse_error:
        errors["resume"] = parse_error
    for slot, field in ((resume_error, "resume"), (jd_error, "jd"), (email_error, "email")):
        if field in errors:
            slot.error(errors[field], icon="⚠️")
    if errors:
        return
    run_graph(
        {"resume_text": resume, "jd_text": jd, "candidate_email": email},
        "Reading your resume and the job description, then finding your skill gaps…",
    )


# ---------------------------------------------------------------- screen 2: skill gaps


def screen_gaps(p: dict, values: dict) -> None:
    ui.stepper(1)
    jd = values["jd_profile"]
    st.title("Skill gap analysis", anchor=False)
    st.markdown(f"{jd['role_title']} &nbsp; {ui.badge(jd['level'].title(), 'blue')}")

    required = [g for g in p["gaps"] if g["importance"] == "required"]
    n = {s: sum(g["status"] == s for g in required) for s in STATUS_ORDER}
    c1, c2, c3, c4 = st.columns([1.4, 1, 1, 1])
    with c1, st.container(border=True):
        st.metric("Skill match", f"{p['match_percent']}%")
        st.caption(f"{n['matched']} of {len(required)} required skills")
    for col, status in ((c2, "matched"), (c3, "partial"), (c4, "missing")):
        color, label = ui.STATUS_BADGE[status]
        with col, st.container(border=True):
            st.markdown(ui.badge(label, color))
            st.markdown(f"### {n[status]}")

    st.subheader("Your biggest gaps", anchor=False)
    gaps = [g for g in required if g["status"] != "matched"]
    gaps.sort(key=lambda g: STATUS_ORDER[g["status"]])
    if gaps:
        st.markdown(
            " ".join(
                ui.badge(
                    f"{ui.STATUS_BADGE[g['status']][1][0]} {g['skill']}", ui.STATUS_BADGE[g["status"]][0]
                )
                for g in gaps[:6]
            )
        )
        st.caption("The interview focuses on these.")
    else:
        st.markdown(ui.badge("✅ No required gaps", "green"))
        st.caption("You match every required skill — the interview will test depth.")

    with st.expander("See all skills and evidence", expanded=False):
        rows = sorted(p["gaps"], key=lambda g: (g["importance"] != "required", STATUS_ORDER[g["status"]]))
        st.table(
            [
                {
                    "Skill": g["skill"],
                    "Importance": g["importance"].replace("_", " "),
                    "Status": ui.STATUS_BADGE[g["status"]][1],
                    "Evidence from your resume": g["evidence"] or "—",
                }
                for g in rows
            ],
            hide_index=True,
        )  # every row visible; long evidence wraps
    st.caption("10 questions · about 15 minutes · 4 Technical, 3 Project, 3 HR. You can skip any question.")

    with st.container(horizontal=True):
        if st.button("Start interview →", type="primary"):
            run_graph(Command(resume=True), "Preparing your 10 interview questions…")
        if st.button("Change inputs"):
            start_over(
                {
                    "in_paste": True,
                    "in_resume": values.get("resume_text", ""),
                    "in_jd": values.get("jd_text", ""),
                    "in_email": values.get("candidate_email", ""),
                }
            )


# ---------------------------------------------------------------- screen 3: interview


def round_progress(values: dict, p: dict) -> None:
    """Segmented progress for the 3 rounds + 'Question n of 10'."""
    answered = {e["question_id"] for e in values.get("evaluations", [])}
    rounds = [r for r in ROUND_SIZE if any(q["round"] == r for q in values["questions"])]
    cols = st.columns(len(rounds) or 1)
    # only rounds that have questions (no divide-by-zero); one spare column if there are none
    for col, round_ in zip(cols, rounds, strict=False):
        ids = [q["id"] for q in values["questions"] if q["round"] == round_]
        done = sum(i in answered for i in ids)
        col.progress(done / len(ids), text=f"{ui.ROUND_NAME[round_]} {done}/{len(ids)}")
    if p["type"] == "question":
        st.caption(f"Question {p['number']} of {p['total']}")
    else:
        st.caption(f"Second chance · follow-up on {p['of']}")


def screen_question(p: dict, values: dict) -> None:
    ui.stepper(2)
    q = p["question"]
    round_progress(values, p)
    if values.get("evaluations"):
        ui.result_card(values["evaluations"][-1])

    focus = q.get("target_skill") or ""
    st.markdown(
        ui.badge(q["round"].upper(), ui.ROUND_COLOR[q["round"]])
        + (f" &nbsp; {ui.badge('Focus: ' + focus, 'gray')}" if focus else "")
    )
    with st.container(border=True):
        st.markdown(f"#### {q['text']}")
    with st.expander("Tips for this answer", expanded=False):
        st.markdown(TIPS[q["round"]])

    key = f"ans_{q['id']}"
    # clear_on_submit=False + a per-question key: typed text survives reruns and errors.
    with st.form(f"form_{q['id']}", clear_on_submit=False, border=False):
        st.text_area(
            "Your answer",
            key=key,
            height=170,
            max_chars=config.MAX_ANSWER_CHARS,
            placeholder="Explain your thinking in 3–6 sentences…",
        )
        error_slot = st.empty()
        words = word_count(st.session_state.get(key))
        st.caption(
            (f"{words} words · " if words else "")
            + "Good answers are usually 60–150 words. Press Ctrl+Enter to submit."
        )
        with st.container(horizontal=True, horizontal_alignment="distribute"):
            skip = st.form_submit_button("Skip question", type="tertiary")
            submit = st.form_submit_button("Submit answer →", type="primary")
    if skip:
        run_graph(Command(resume={"skipped": True}), "Moving to the next question…", toast="Question skipped")
    elif submit:
        answer = clean_answer(st.session_state.get(key))  # B1/B2: trimmed before anything else
        if not answer:
            error_slot.error(EMPTY_ANSWER_ERROR, icon="✍️")  # do NOT resume the graph
            return
        # The follow-up is the last answer, so its step also writes the report.
        label = (
            "Reviewing your answer and writing your report…"
            if p["type"] == "follow_up"
            else "Reviewing your answer and preparing feedback…"
        )
        run_graph(Command(resume=answer), label, toast="Answer saved")


# ---------------------------------------------------------------- screen 4: report


def incomplete_reasons(values: dict) -> list[str]:
    reasons = [
        f"{e['question_id']} couldn't be scored" for e in values.get("evaluations", []) if e.get("error")
    ]
    if "report_summary" in values and not values["report_summary"]:
        reasons.append("the AI summary couldn't be written")
    reasons += values.get("delivery", {}).get("errors", [])
    return reasons


def strengths_and_gaps(values: dict) -> tuple[list[str], list[str]]:
    """Strengths from the best answers, 'to improve' from the weakest (deduplicated, max 4)."""
    scored = [e for e in values["evaluations"] if not e["skipped"] and not e.get("error")]
    best = sorted(scored, key=lambda e: -e["score"])
    worst = sorted(scored, key=lambda e: e["score"])

    def pick(evals, field):
        out = []
        for e in evals:
            for item in e.get(field, []):
                if item not in out:
                    out.append(item)
        return out[:4]

    return pick(best, "strengths"), pick(worst, "weaknesses")


def screen_report(values: dict, at_send_gate: bool) -> None:
    ui.stepper(3)
    o, summary = values["overall"], values.get("report_summary") or {}
    st.title("Your interview report", anchor=False)

    with st.container(border=True):  # hero
        c1, c2, c3 = st.columns([1.2, 1.2, 1])
        c1.metric("Overall score", f"{o['score']} / 10")
        with c2:
            st.caption("Recommendation")
            st.markdown(f"### {ui.badge(o['recommendation'], ui.REC_COLOR[o['recommendation']])}")
        c3.metric("Skill match", f"{values['match_percent']}%")
        first = (summary.get("summary") or "").split(". ")[0].strip()
        line = (first + ".") if first and not first.endswith(".") else first
        if o["recommendation"] in ("No Hire", "Lean No"):
            line = (line + " " if line else "") + "**Here's how to close the gap ↓**"
        if line:
            st.markdown(line)

    cols = st.columns(3)
    for col, (round_, avg) in zip(cols, o["by_round"].items(), strict=False):  # a round may be missing
        with col, st.container(border=True):
            st.caption(f"{ui.ROUND_NAME[round_]} · weight {ui.ROUND_WEIGHT[round_]}")
            st.markdown(f"{ui.badge(f'{avg:.1f}/10', ui.score_color(avg))}")
            st.progress(min(avg / 10, 1.0))

    # Actions bar: Download and Send to my Gmail both stay available.
    d = values.get("delivery", {})
    with st.container(horizontal=True):
        if d.get("pdf_path") and Path(d["pdf_path"]).exists():
            st.download_button(
                "⬇️ Download PDF",
                data=Path(d["pdf_path"]).read_bytes(),
                file_name=Path(d["pdf_path"]).name,
                mime="application/pdf",
                type="primary",
                on_click="ignore",
            )
        if config.APP_MODE == "local":
            if d.get("draft_id"):
                st.button(
                    "✅ Draft created in Gmail",
                    disabled=True,
                    help="One draft per session — check Gmail → Drafts. Nothing was sent.",
                )
            elif at_send_gate and st.button(
                "📧 Send to my Gmail", help="Creates a Gmail DRAFT with the PDF attached — nothing is sent."
            ):
                run_graph(
                    Command(resume="gmail"),
                    "Creating your Gmail draft…",
                    toast="Draft created in Gmail — nothing was sent",
                )
        if st.button("Start a new interview", type="tertiary"):
            if at_send_gate:  # finish this session cleanly in the background (deletes its RAG data)
                worker.submit(sid, graph, Command(resume="finish"), CFG, ctx, "Finishing")
            start_over()

    reasons = incomplete_reasons(values)
    if reasons:
        st.warning("**Incomplete** — " + "; ".join(reasons), icon="⚠️")

    if summary.get("top_actions"):
        st.subheader("Your top 3 next steps", anchor=False)
        for i, action in enumerate(summary["top_actions"], 1):
            st.checkbox(action, key=f"step_{i}")  # just for the user's own tracking

    good, improve = strengths_and_gaps(values)
    if good or improve:
        left, right = st.columns(2)
        with left:
            st.subheader("Strengths", anchor=False)
            st.markdown("\n".join(f"- ✅ {s}" for s in good) or "_Answer more questions to see strengths._")
        with right:
            st.subheader("To improve", anchor=False)
            st.markdown("\n".join(f"- 🔸 {w}" for w in improve) or "—")

    st.subheader("Question by question", anchor=False)
    lowest_first = st.toggle("Lowest scores first", value=False)
    questions = {q["id"]: q for q in values["questions"]}
    answers = {a["question_id"]: a for a in values["answers"]}
    samples = {s["question_id"]: s["improved_answer"] for s in summary.get("sample_answers", [])}
    evals = sorted(values["evaluations"], key=lambda e: e["score"]) if lowest_first else values["evaluations"]
    for e in evals:
        q = questions[e["question_id"]]
        if e["skipped"]:
            title = f"{q['id']} · {ui.ROUND_NAME[q['round']]} · Skipped :gray[●]"
        else:
            title = (
                f"{q['id']} · {ui.ROUND_NAME[q['round']]} · {e['score']}/10 :{ui.score_color(e['score'])}[●]"
            )
        with st.expander(title, expanded=False):
            st.markdown(f"**Question:** {q['text']}")
            a = answers.get(q["id"], {})
            st.markdown("**Your answer:** " + ("_Skipped_" if a.get("skipped") else a.get("answer", "")))
            st.markdown(f"**Feedback:** {e.get('error') or e['feedback']}")
            if q["id"] in samples:
                st.markdown(f"**Stronger answer:** {samples[q['id']]}")

    fu = values.get("follow_up") or {}
    if fu.get("evaluation"):
        before = next(e["score"] for e in values["evaluations"] if e["question_id"] == fu["of"])
        after = fu["evaluation"]["score"]
        st.markdown(f"**Second chance on {fu['of']}:** {before} → {after} ({after - before:+.1f})")
        st.caption("The follow-up is shown separately and doesn't change the overall score.")

    s = ctx.router.summary()
    models = (
        ", ".join(sorted({e["evaluator_model"] for e in values["evaluations"] if e.get("evaluator_model")}))
        or "none (all skipped)"
    )
    st.divider()
    st.caption(
        "Scores come from a fixed scoring guide, the same for every answer · "
        f"AI calls {values.get('llm_calls', s['calls'])} · scoring model {models}"
    )


# ---------------------------------------------------------------- router: state -> screen

if worker.is_busy(sid):
    # A rerun, extra click or refresh while a step runs lands here: the step is NOT affected.
    dlog.debug("script run while a step is running (rerun/click/refresh) | sid %s", sid[:8])
    wait_for_step()
    st.stop()
collect_step_result()

if st.session_state.get("error"):
    st.error(st.session_state["error"], icon="⚠️")
    if st.button("Try again", type="primary"):
        st.session_state.pop("error")
        snap = graph.get_state(CFG)
        if snap.next and not snap.interrupts:  # a step failed: resume from the last checkpoint
            run_graph(None, "Trying again…")
        st.rerun()
    st.stop()

snap = graph.get_state(CFG)
pending = snap.interrupts[0].value if snap.interrupts else None

if not snap.values and url_sid and not known_session:
    # The URL names a session this server doesn't have (restarted, or idle > 3 h).
    dlog.debug("EXPIRED session | sid %s | no state on this server", sid[:8])
    st.warning(
        "This interview session has ended or expired (the server restarted, or it was "
        "idle for more than 3 hours). Your answers can't be recovered.",
        icon="⌛",
    )
    if st.button("Start a new interview", type="primary", key="expired_new"):
        start_over()
elif not snap.values:
    screen_upload()
elif pending and pending["type"] == "start_gate":
    screen_gaps(pending, snap.values)
elif pending and pending["type"] in ("question", "follow_up"):
    screen_question(pending, snap.values)
elif pending and pending["type"] == "send_gate":
    screen_report(snap.values, at_send_gate=True)
elif snap.values.get("finished"):
    screen_report(snap.values, at_send_gate=False)
elif worker.mark_auto_resumed(sid):
    # A step that genuinely didn't finish (it raised, or ran in a process that stopped):
    # resume it once, silently, from the last checkpoint — no Continue screen.
    cause = (
        "previous step raised an error"
        if worker.status(sid).done_nodes
        else "no running step on this server for this session"
    )
    dlog.warning(
        "UNFINISHED step detected | sid %s | next %s | cause: %s | auto-resuming once",
        sid[:8],
        snap.next,
        cause,
    )
    run_graph(None, "Picking up where you left off…")
else:
    dlog.warning("RECOVERY screen (auto-resume already tried) | sid %s | next %s", sid[:8], snap.next)
    st.warning("We couldn't finish your last step automatically. Your progress is saved.", icon="⚠️")
    with st.container(horizontal=True):
        if st.button("Try again", type="primary", key="recover_retry"):
            run_graph(None, "Trying again…")
        if st.button("Start a new interview", key="recover_new"):
            start_over()
