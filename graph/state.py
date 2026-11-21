"""LangGraph state + per-session runtime objects.

InterviewState holds only plain JSON-like data (dicts, lists, numbers, strings), so the
checkpointer (InMemorySaver) can save it at every step and at every pause.

SessionContext holds the LIVE objects a session needs — the LLM router (call counter,
evaluator lock) and the RAG store (in-memory Chroma). They can't be saved in a checkpoint,
so they are passed to the graph as runtime `context` on EVERY invoke/resume
(LangGraph does not remember context between invokes — checked in the Phase 5 proof).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from llm.router import LLMRouter
from rag.store import SessionStore


class InterviewState(TypedDict, total=False):
    # inputs
    resume_text: str
    jd_text: str
    candidate_email: str
    # extract + gap_analysis
    jd_profile: dict
    resume_profile: dict
    extract_model: str
    extract_attempts: list[str]  # e.g. ['gemini: failed (ServerError) after 2 call(s)', ...]
    gaps: list[dict]  # GapItem dicts
    match_percent: float
    # interview
    started: bool
    questions: list[dict]  # Question dicts, T1–T4, P1–P3, H1–H3
    current_index: int  # index of the next question to ask
    answers: list[dict]  # {"question_id", "answer", "skipped"}
    evaluations: list[dict]  # Evaluation dicts
    follow_up: dict  # {"of", "question", "answer", "evaluation"} or {} if none
    # results
    overall: dict  # {"score", "by_round", "recommendation"}
    report_summary: dict  # ReportSummary dict (summary, top_actions, sample_answers) or {}
    report_markdown: str
    report_path: str  # outputs/report_<name>_<time>.md (PDF in Phase 7)
    send_action: str  # "gmail" | "finish" (last choice at send_gate)
    finished: bool  # True after Finish -> graph ends
    delivery: dict  # {"pdf_path", "draft_id", "gmail", "errors"} — PDF is made in build_report
    llm_calls: int  # router.call_count after the last LLM node (for the report)


@dataclass
class SessionContext:
    router: LLMRouter
    store: SessionStore | None = None  # created by the extract node once texts are known
