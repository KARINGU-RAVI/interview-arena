"""Offline UI tests (Streamlit AppTest — no LLM, no Gmail).

Sessions are placed directly in the shared graph (paused at a question or at send_gate);
a fake MCP toolbox stands in for the real server. Then the real app.py is driven by clicks.

Run:  python -m pytest -q tests/test_app.py
"""

import uuid

import pytest
from streamlit.testing.v1 import AppTest

import config
from graph.build_graph import get_shared_graph
from graph.state import SessionContext
from llm.router import LLMRouter, Provider
from ui.validation import EMPTY_ANSWER_ERROR

APP = str(config.PROJECT_ROOT / "app.py")


class FakeToolbox:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, **args):
        self.calls.append(name)
        return {"draft_id": "d-test-1"}


def _ctx() -> SessionContext:
    # A provider that would fail if ever called: proves no LLM call happens in these tests.
    return SessionContext(
        router=LLMRouter(providers=[Provider("groq", "groq", "x", "GROQ_API_KEY", "json_schema")])
    )


def _ev(qid, rnd, score, skipped=False):
    return {
        "question_id": qid,
        "round": rnd,
        "score": score,
        "skipped": skipped,
        "error": "",
        "feedback": "tip",
        "weaknesses": ["be specific"],
        "criteria": [],
        "strengths": ["clear"],
        "evaluator_model": "gemini:m",
    }


BASE = {
    "candidate_email": "c@example.com",
    "match_percent": 75.0,
    "resume_text": "fictional resume",
    "jd_text": "fictional jd",
    "jd_profile": {"role_title": "Frontend Developer", "level": "junior"},
    "resume_profile": {"name": "TEST PERSON"},
    "gaps": [{"skill": "React", "importance": "required", "status": "matched", "evidence": "React"}],
    "questions": [
        {"id": "T1", "round": "technical", "text": "What is SSR?", "target_skill": "Next.js"},
        {"id": "H1", "round": "hr", "text": "A time you learned fast", "target_skill": "learning"},
    ],
}


def _seed(values: dict, as_node: str) -> str:
    sid = "test" + uuid.uuid4().hex
    cfg = {"configurable": {"thread_id": sid}}
    graph = get_shared_graph()
    graph.update_state(cfg, values, as_node=as_node)
    graph.invoke(None, cfg, context=_ctx())  # runs to the next pause
    return sid


def _open(sid: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    if sid:
        at.query_params["sid"] = sid
    return at.run()


def _button(at: AppTest, label: str):
    for b in list(at.button) + list(at.get("form_submit_button")):
        if b.label == label:
            return b
    raise AssertionError(f"{label!r} not in {[b.label for b in at.button]}")


# ---------------------------------------------------------------- B1: empty answers


def test_b1_whitespace_answer_is_blocked_and_graph_not_resumed():
    sid = _seed(
        {**BASE, "current_index": 0, "answers": [], "evaluations": [], "follow_up": {}},
        as_node="plan_questions",
    )
    at = _open(sid)
    assert any("What is SSR?" in m.value for m in at.markdown)
    at.text_area[0].input("   \n\t   ")
    at = _button(at, "Submit answer →").click().run()
    assert [e.value for e in at.error] == [EMPTY_ANSWER_ERROR]
    snap = get_shared_graph().get_state({"configurable": {"thread_id": sid}})
    assert snap.interrupts[0].value["number"] == 1 and snap.values["answers"] == []  # not resumed


# ---------------------------------------------------------------- B3: per-field validation


def test_b3_screen1_shows_one_error_per_field(monkeypatch):
    monkeypatch.setattr(config, "APP_MODE", "local")
    at = _open()
    assert at.title[0].value == "Practice an interview for any job"
    at.toggle[0].set_value(True).run()  # paste mode
    at.text_area[0].input("   ")  # resume: whitespace only
    at.text_area[1].input(" \n  \t ")  # JD: whitespace only
    at.text_input[0].input("  not-an-email  ")
    at = _button(at, "Analyse my fit →").click().run()
    errors = [e.value for e in at.error]
    assert len(errors) == 3
    assert any("resume" in e for e in errors) and any("job description" in e for e in errors)
    assert any("email" in e for e in errors)
    assert not at.status  # nothing was sent to the AI


def test_try_with_sample_data_fills_fictional_inputs():
    at = _open()
    at = _button(at, "✨ Try with sample data").click().run()
    assert at.toggle[0].value is True
    assert "ANANYA REDDY (fictional test candidate)" in at.text_area[0].value
    assert "Frontend Developer" in at.text_area[1].value
    assert at.text_input[0].value.endswith("@example.com")


# ---------------------------------------------------------------- report: download + Gmail


@pytest.fixture
def report_session(tmp_path, monkeypatch):
    pdf = tmp_path / "report_test.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    fake = FakeToolbox()
    monkeypatch.setattr("mcp_client.get_toolbox", lambda: fake)
    values = {
        **BASE,
        "answers": [
            {"question_id": "T1", "answer": "server renders", "skipped": False},
            {"question_id": "H1", "answer": "", "skipped": True},
        ],
        "evaluations": [_ev("T1", "technical", 4.0), _ev("H1", "hr", 0.0, skipped=True)],
        "follow_up": {
            "of": "T1",
            "question": {"id": "F1", "text": "Why?"},
            "evaluation": {"score": 6.0, "feedback": "better", "error": ""},
        },
        "overall": {"score": 5.2, "by_round": {"technical": 4.0, "hr": 0.0}, "recommendation": "Lean No"},
        "report_summary": {
            "summary": "You explained SSR. Practise trade-offs.",
            "top_actions": ["a", "b", "c"],
            "sample_answers": [{"question_id": "T1", "improved_answer": "SSR renders on the server."}],
        },
        "report_markdown": "# Interview Report\n",
        "delivery": {"pdf_path": str(pdf), "draft_id": "", "gmail": "not requested", "errors": []},
    }
    return _seed(values, as_node="build_report"), fake


def test_download_first_then_gmail_still_available(report_session, monkeypatch):
    sid, fake = report_session
    monkeypatch.setattr(config, "APP_MODE", "local")
    at = _open(sid)
    assert [d.label for d in at.get("download_button")] == ["⬇️ Download PDF"]
    assert "📧 Send to my Gmail" in [b.label for b in at.button]
    assert any(":orange-badge[Lean No]" in m.value for m in at.markdown)  # amber badge
    assert any("Here's how to close the gap" in m.value for m in at.markdown)  # coach tone
    assert any("Second chance on T1" in m.value for m in at.markdown)
    titles = [e.label for e in at.expander]
    assert any(t.startswith("T1 · Technical · 4.0/10") for t in titles)
    assert any("Skipped" in t for t in titles)

    at = _button(at, "📧 Send to my Gmail").click().run()
    _wait_idle(sid)  # the draft is created by the background worker
    at = at.run()
    assert fake.calls == ["create_gmail_draft"]
    assert [d.label for d in at.get("download_button")] == ["⬇️ Download PDF"]  # still there
    done = _button(at, "✅ Draft created in Gmail")
    assert done.disabled  # visible, but can't create a duplicate draft


def test_cloud_mode_hides_gmail(report_session, monkeypatch):
    sid, fake = report_session
    monkeypatch.setattr(config, "APP_MODE", "cloud")
    at = _open(sid)
    assert [d.label for d in at.get("download_button")] == ["⬇️ Download PDF"]
    assert not any("Gmail" in b.label for b in at.button)


# ---------------------------------------------------------------- graph steps run in a worker
import threading  # noqa: E402
import time  # noqa: E402

import graph.nodes as nodes  # noqa: E402
from graph import worker  # noqa: E402
from schemas import Evaluation  # noqa: E402

ANSWER = "Server side rendering builds the HTML on the server so the first paint is faster."


@pytest.fixture
def gated_evaluator(monkeypatch):
    """Fake evaluator that waits for gate.set() and counts calls (no LLM)."""
    gate, calls = threading.Event(), []

    def fake_evaluate(question, answer, evidence, router):
        calls.append(question.id)
        gate.wait(10)
        return Evaluation(
            question_id=question.id,
            round=question.round,
            score=6.0,
            feedback="fake",
            evaluator_model="fake:m",
        )

    monkeypatch.setattr(nodes, "evaluate", fake_evaluate)
    monkeypatch.setattr(nodes, "evidence_for", lambda q, store: [])
    return gate, calls


def _wait_idle(sid: str, seconds: float = 10) -> None:
    end = time.time() + seconds
    while worker.is_busy(sid) and time.time() < end:
        time.sleep(0.05)
    assert not worker.is_busy(sid)


def _question_session() -> str:
    return _seed(
        {**BASE, "current_index": 0, "answers": [], "evaluations": [], "follow_up": {}},
        as_node="plan_questions",
    )


def _later(seconds: float, fn) -> threading.Thread:
    th = threading.Timer(seconds, fn)
    th.start()
    return th


def test_double_click_during_scoring_runs_the_step_once(gated_evaluator):
    gate, calls = gated_evaluator
    sid = _question_session()
    cfg = {"configurable": {"thread_id": sid}}
    from langgraph.types import Command

    second = {}

    def second_click():
        # Wait until the first step is really running, then click again (double click /
        # Ctrl+Enter), and only after that let the fake AI answer.
        end = time.time() + 10
        while not worker.is_busy(sid) and time.time() < end:
            time.sleep(0.01)
        second["ok"] = worker.submit(
            sid, get_shared_graph(), Command(resume=ANSWER), cfg, _ctx(), "second click"
        )
        gate.set()

    threading.Thread(target=second_click, daemon=True).start()
    at = _open(sid)
    at.text_area[0].input(ANSWER)
    at = _button(at, "Submit answer →").click().run()  # page shows the spinner, then redraws
    assert second == {"ok": False}  # the second submit was refused
    assert calls == ["T1"]  # scored exactly once
    assert any("A time you learned fast" in m.value for m in at.markdown)  # next question
    snap = get_shared_graph().get_state(cfg)
    assert len(snap.values["answers"]) == 1 and len(snap.values["evaluations"]) == 1


def test_rerun_during_scoring_does_not_cancel_the_step(gated_evaluator):
    gate, calls = gated_evaluator
    sid = _question_session()
    cfg = {"configurable": {"thread_id": sid}}
    from langgraph.types import Command

    assert worker.submit(sid, get_shared_graph(), Command(resume=ANSWER), cfg, _ctx(), "Scoring")
    assert worker.is_busy(sid)
    _later(1.0, gate.set)
    at = _open(sid)  # a refresh = brand-new app session, same ?sid=, while the step runs
    assert not any("couldn't finish" in w.value for w in at.warning)  # no recovery screen
    assert any("A time you learned fast" in m.value for m in at.markdown)  # waited, then Q2
    assert calls == ["T1"] and worker.status(sid).repeats == 0


def test_unfinished_step_is_resumed_silently(gated_evaluator):
    gate, calls = gated_evaluator
    gate.set()
    sid = "test" + uuid.uuid4().hex
    cfg = {"configurable": {"thread_id": sid}}
    # A checkpoint left half-way: T1 answered and scored, but ask_question never ran
    # (exactly what an interrupted run used to leave behind).
    get_shared_graph().update_state(
        cfg,
        {
            **BASE,
            "current_index": 1,
            "follow_up": {},
            "answers": [{"question_id": "T1", "answer": ANSWER, "skipped": False}],
            "evaluations": [_ev("T1", "technical", 6.0)],
        },
        as_node="evaluate_answer",
    )
    assert get_shared_graph().get_state(cfg).next == ("ask_question",)
    assert not get_shared_graph().get_state(cfg).interrupts
    at = _open(sid)
    _wait_idle(sid)
    at = at.run()
    assert any("A time you learned fast" in m.value for m in at.markdown)  # resumed to Q2
    assert not any("couldn't finish" in w.value for w in at.warning)  # no recovery screen
    assert calls == []  # resuming ask_question needs no LLM call


def test_unknown_session_shows_expired_message():
    at = _open("expired-" + uuid.uuid4().hex)
    assert any("expired" in w.value for w in at.warning)
    assert "Start a new interview" in [b.label for b in at.button]
