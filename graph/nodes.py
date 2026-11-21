"""The 12 LangGraph nodes. Each node reads the state and returns ONLY the keys it changes.

Pauses (interrupt) and why they sit where they do:
LangGraph re-runs a node FROM THE START when it resumes after interrupt() (official docs,
confirmed in our proof). So a node must never make an LLM call BEFORE its interrupt(),
or that call would happen twice. Every pausing node below calls interrupt() first.
That is why `follow_up` only WRITES the question, and `evaluate_follow_up` pauses for the
answer first and evaluates after.

Pauses: start_gate, ask_question (x10), evaluate_follow_up (follow-up answer), send_gate = 13
(+1 if the user creates a Gmail draft: deliver returns to send_gate).
"""

from __future__ import annotations

import logging
from pathlib import Path

from langgraph.runtime import Runtime
from langgraph.types import interrupt

import config
import report
from crew.extraction_crew import extract_profiles
from evaluator import evaluate, evidence_for, is_non_answer
from gap_analysis import analyze_gaps
from graph.state import InterviewState, SessionContext
from llm.router import AllProvidersFailedError, BudgetExceededError
from question_planner import plan_questions
from rag.store import build_session_store
from schemas import Evaluation, FollowUpQuestion, GapItem, JDProfile, Question, ResumeProfile
from scoring import compute_overall_score
from ui.validation import clean_answer

log = logging.getLogger("interview_arena.graph")

FOLLOW_UP_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "follow_up.md"
# Errors that must NOT stop a session midway: we record them and carry on.
SOFT_ERRORS = (AllProvidersFailedError, BudgetExceededError)


def _calls(rt: Runtime[SessionContext]) -> int:
    return rt.context.router.call_count


# ---------------------------------------------------------------- 1. extract (CrewAI, 2 calls)


def extract(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    ctx = runtime.context
    result = extract_profiles(state["jd_text"], state["resume_text"])
    ctx.router.add_external_calls(result.llm_calls, node="extract")  # CrewAI counts toward the budget
    if ctx.store is None:
        ctx.store = build_session_store(state["resume_text"], state["jd_text"])
    return {
        "jd_profile": result.jd_profile.model_dump(),
        "resume_profile": result.resume_profile.model_dump(),
        "extract_model": result.model,
        "extract_attempts": result.attempts,
        "llm_calls": _calls(runtime),
    }


# ---------------------------------------------------------------- 2. gap_analysis (1 call)


def gap_analysis(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    ctx = runtime.context
    result = analyze_gaps(
        JDProfile(**state["jd_profile"]), ResumeProfile(**state["resume_profile"]), ctx.store, ctx.router
    )
    return {
        "gaps": [g.model_dump() for g in result.gaps],
        "match_percent": result.match_percent,
        "llm_calls": _calls(runtime),
    }


# ---------------------------------------------------------------- 3. start_gate (pause)


def start_gate(state: InterviewState) -> dict:
    reply = interrupt(
        {
            "type": "start_gate",
            "match_percent": state["match_percent"],
            "gaps": state["gaps"],
            "role": state["jd_profile"]["role_title"],
        }
    )
    return {"started": bool(reply)}


# ---------------------------------------------------------------- 4. plan_questions (1 call, +1 retry)


def plan_questions_node(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    ctx = runtime.context
    result = plan_questions(
        JDProfile(**state["jd_profile"]),
        ResumeProfile(**state["resume_profile"]),
        [GapItem(**g) for g in state["gaps"]],
        ctx.store,
        ctx.router,
    )
    return {
        "questions": [q.model_dump() for q in result.plan.questions],
        "current_index": 0,
        "answers": [],
        "evaluations": [],
        "follow_up": {},
        "llm_calls": _calls(runtime),
    }


# ---------------------------------------------------------------- 5. ask_question (pause, no LLM)


def _clean_reply(reply) -> str | None:
    """Resume value -> answer text, or None when it's no real answer
    (empty, "skip", "don't know", under 3 words, or {"skipped": True})."""
    if isinstance(reply, dict):
        reply = None if reply.get("skipped") else reply.get("answer")
    answer = clean_answer(str(reply)) if reply is not None else ""  # B2: always trimmed
    if is_non_answer(answer):
        return None
    return answer[: config.MAX_ANSWER_CHARS]


def ask_question(state: InterviewState) -> dict:
    i = state["current_index"]
    q = state["questions"][i]
    reply = interrupt({"type": "question", "number": i + 1, "total": len(state["questions"]), "question": q})
    answer = _clean_reply(reply)
    record = {"question_id": q["id"], "answer": answer or "", "skipped": answer is None}
    return {"answers": state["answers"] + [record]}


# ---------------------------------------------------------------- 6. evaluate_answer (1 call; skip = 0 calls)


def _safe_evaluate(question: Question, answer: str | None, ctx: SessionContext) -> Evaluation:
    """Evaluate, but never let a provider/budget problem stop the session."""
    try:
        return evaluate(question, answer, evidence_for(question, ctx.store), ctx.router)
    except SOFT_ERRORS as exc:
        log.warning("could not score %s: %s", question.id, type(exc).__name__)
        return Evaluation(
            question_id=question.id, round=question.round, score=0.0, error=f"Not scored: {exc}"
        )


def evaluate_answer(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    i = state["current_index"]
    q = Question(**state["questions"][i])
    record = state["answers"][-1]
    ev = _safe_evaluate(q, None if record["skipped"] else record["answer"], runtime.context)
    return {
        "evaluations": state["evaluations"] + [ev.model_dump()],
        "current_index": i + 1,
        "llm_calls": _calls(runtime),
    }


# ---------------------------------------------------------------- route_next (conditional edge, not a node)


def _weakest(state: InterviewState) -> dict | None:
    """Weakest ATTEMPTED answer (FR-5.3): answered, scored, and score > 0.

    Skipped / "don't know" answers and unscored ones (provider error) are left out.
    If nothing qualifies, route_next skips the follow-up entirely (0 LLM calls).
    """
    candidates = [
        e for e in state["evaluations"] if not e["skipped"] and not e.get("error") and e["score"] > 0
    ]
    return min(candidates, key=lambda e: e["score"]) if candidates else None


def route_next(state: InterviewState) -> str:
    if state["current_index"] < len(state["questions"]):
        return "ask_question"
    return "follow_up" if _weakest(state) else "final_evaluation"


# ---------------------------------------------------------------- 7. follow_up (1 call, NO pause)


def follow_up(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    ctx = runtime.context
    weak = _weakest(state)
    original = next(q for q in state["questions"] if q["id"] == weak["question_id"])
    answer = next(a["answer"] for a in state["answers"] if a["question_id"] == weak["question_id"])
    prompt = (
        FOLLOW_UP_PROMPT.read_text(encoding="utf-8")
        .replace("{{ROUND}}", original["round"])
        .replace("{{QUESTION}}", original["text"])
        .replace("{{WEAKNESSES}}", "\n".join(f"- {w}" for w in weak["weaknesses"]) or "- (none listed)")
        .replace("{{ANSWER}}", answer.replace("</candidate_answer>", "[/candidate_answer]"))
    )
    try:
        fq = ctx.router.invoke_structured(prompt, FollowUpQuestion, node="follow_up")
    except SOFT_ERRORS as exc:
        log.warning("no follow-up question: %s", type(exc).__name__)
        return {"follow_up": {}, "llm_calls": _calls(runtime)}
    question = Question(id="F1", round=original["round"], text=fq.text, target_skill=original["target_skill"])
    return {
        "follow_up": {"of": original["id"], "question": question.model_dump()},
        "llm_calls": _calls(runtime),
    }


# ------------------------------------------------ 8. evaluate_follow_up (pause FIRST, then 1 call)


def evaluate_follow_up(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    fu = state.get("follow_up") or {}
    if not fu.get("question"):
        return {}  # no follow-up was written (e.g. provider outage) -> nothing to ask
    reply = interrupt({"type": "follow_up", "question": fu["question"], "of": fu["of"]})
    answer = _clean_reply(reply)
    ev = _safe_evaluate(Question(**fu["question"]), answer, runtime.context)
    return {
        "follow_up": {**fu, "answer": answer or "", "evaluation": ev.model_dump()},
        "llm_calls": _calls(runtime),
    }


# ---------------------------------------------------------------- 9. final_evaluation (Python only)


def final_evaluation(state: InterviewState) -> dict:
    # Overall score + recommendation are deterministic Python (PRD FR-6.3/6.4, FR-7.4).
    return {"overall": compute_overall_score(state["evaluations"])}


# ---------------------------------------------------------------- 10. build_report (1 batched call + PDF)


def build_report(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    """Report Writer (ONE call: summary + top 3 actions + sample answers), then the markdown
    is saved under outputs/ and turned into a PDF straight away (MCP save_report_pdf), so the
    user can download it before deciding about Gmail. Failures never lose the report."""
    from mcp_client import get_toolbox  # imported here: the MCP server starts on first use

    router = runtime.context.router
    summary = report.write_summary(state, router)
    markdown = report.render_markdown(state, summary, router.summary())
    path = report.save_report(markdown, state["resume_profile"]["name"])
    delivery = {"pdf_path": "", "draft_id": "", "gmail": "not requested", "errors": []}
    try:
        pdf = get_toolbox().call_tool("save_report_pdf", report_markdown=markdown, filename=path.stem)
        delivery["pdf_path"] = pdf["path"]
    except Exception as exc:  # PDF problem: the markdown report is still shown
        log.warning("PDF problem: %s", type(exc).__name__)
        delivery["errors"].append(f"PDF: {type(exc).__name__}: {str(exc)[:200]}")
    return {
        "report_summary": summary or {},
        "report_markdown": markdown,
        "report_path": str(path),
        "delivery": delivery,
        "llm_calls": _calls(runtime),
    }


# ---------------------------------------------------------------- 11. send_gate (pause; may repeat)


def send_gate(state: InterviewState) -> dict:
    """Pause on the report. Resume with "gmail" (create a draft, then come back here)
    or "finish" (end the session). True/False are accepted as gmail/finish."""
    reply = interrupt(
        {"type": "send_gate", "email": state.get("candidate_email", ""), "app_mode": config.APP_MODE}
    )
    action = "gmail" if reply in (True, "gmail") else "finish"
    return {"send_action": action}


# ------------------------------------------------ 12. deliver (MCP Gmail draft / finish, no LLM)


def _draft_body(state: InterviewState) -> str:
    o, summary = state["overall"], state.get("report_summary") or {}
    lines = [
        f"Hi {report.display_name(state['resume_profile']['name'])},",
        "",
        f"Here is your Interview Arena practice report for {state['jd_profile']['role_title']}.",
        f"Overall: {o['score']}/10 ({o['recommendation']}) | Skill match: {state['match_percent']}%",
        "",
    ]
    if summary.get("summary"):
        lines += [summary["summary"], ""]
    if summary.get("top_actions"):
        lines += ["Top 3 actions:"] + [f"{i}. {a}" for i, a in enumerate(summary["top_actions"], 1)] + [""]
    lines += ["The full report is attached as a PDF.", "", "— Interview Arena"]
    return "\n".join(lines)


def deliver(state: InterviewState, runtime: Runtime[SessionContext]) -> dict:
    """send_action == "gmail": create ONE Gmail draft (APP_MODE=local only; never sends),
    then route_after_deliver goes back to send_gate so the user can still download / finish.
    send_action == "finish": delete the session's RAG data and end."""
    from mcp_client import get_toolbox

    delivery = {**state.get("delivery", {})}
    delivery["errors"] = list(delivery.get("errors", []))
    if state.get("send_action") == "finish":
        ctx = runtime.context
        if ctx.store is not None:
            ctx.store.close()  # session over: delete its in-memory vectors
            ctx.store = None
        return {"finished": True, "delivery": delivery}

    if delivery.get("draft_id"):
        pass  # one draft per session — never create duplicates
    elif config.APP_MODE != "local":
        delivery["gmail"] = "skipped (cloud mode: PDF download only)"
    elif not state.get("candidate_email"):
        delivery["gmail"] = "skipped (no email address)"
    elif not delivery.get("pdf_path"):
        delivery["gmail"] = "skipped (no PDF to attach)"
    else:
        try:
            draft = get_toolbox().call_tool(
                "create_gmail_draft",
                to=state["candidate_email"],
                subject=f"Your Interview Arena report — {state['jd_profile']['role_title']}",
                body=_draft_body(state),
                attachment_path=delivery["pdf_path"],
            )
            delivery["draft_id"], delivery["gmail"] = draft["draft_id"], "draft created (not sent)"
        except Exception as exc:  # Gmail problem: keep everything else, tell the user
            log.warning("Gmail draft problem: %s", type(exc).__name__)
            delivery["errors"].append(f"Gmail: {type(exc).__name__}: {str(exc)[:200]}")
    return {"delivery": delivery}


def route_after_deliver(state: InterviewState) -> str:
    """Conditional edge: after a Gmail draft, go back and wait at send_gate; after Finish, end."""
    return "end" if state.get("finished") else "send_gate"
