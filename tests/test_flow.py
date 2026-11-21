"""Offline tests (no LLM calls) for interview-flow rules:
1. "Don't know" / very short answers count as skipped: score 0, no LLM call.
2. The follow-up targets the weakest ATTEMPTED answer; if there is none, it is skipped.

Run:  python -m pytest -q tests/test_flow.py
"""

import pytest

from evaluator import evaluate, is_non_answer
from graph.nodes import _clean_reply, _weakest, route_next
from llm.router import LLMRouter, Provider
from schemas import Question

Q = Question(id="T1", round="technical", text="What is RAG?", target_skill="RAG")


def _router() -> LLMRouter:
    # A provider that would fail if ever called — proves no LLM call happens.
    return LLMRouter(providers=[Provider("groq", "groq", "not-a-model", "GROQ_API_KEY", "json_schema")])


@pytest.mark.parametrize(
    "answer",
    [
        None,
        "",
        "   ",
        "skip",
        "Skip",
        "dont know",
        "Don't know",
        "I don’t know",
        "idk",
        "IDK.",
        "no idea",
        "No idea, sorry",
        "Sorry I really do not know this",
        "RAG retrieval",
    ],
)
def test_non_answers_score_zero_without_llm_call(answer):
    router = _router()
    ev = evaluate(Q, answer, [], router)
    assert ev.skipped and ev.score == 0.0 and ev.feedback == "No answer given"
    assert router.call_count == 0 and router.history == []


@pytest.mark.parametrize(
    "answer",
    [
        "RAG retrieves relevant chunks and adds them to the prompt.",
        "Use React Query",  # 3 words: short but a real answer -> scored
        "I don't know the exact flag, but Docker caches each layer so rebuilds are fast.",
    ],
)
def test_real_answers_are_not_skipped(answer):
    assert not is_non_answer(answer)


def test_graph_records_dont_know_as_skipped():
    assert _clean_reply("dont know") is None
    assert _clean_reply({"answer": "idk"}) is None
    assert _clean_reply({"skipped": True, "answer": "anything"}) is None
    assert _clean_reply("RAG retrieves relevant chunks and adds them to the prompt.") is not None


def _ev(qid: str, score: float, skipped: bool = False, error: str = "") -> dict:
    return {
        "question_id": qid,
        "round": "technical",
        "score": score,
        "skipped": skipped,
        "error": error,
        "weaknesses": [],
    }


def test_follow_up_picks_weakest_attempted_answer():
    state = {
        "evaluations": [
            _ev("T1", 0, skipped=True),  # skipped: ignored
            _ev("T2", 0.0),  # attempted but scored 0: ignored
            _ev("T3", 4.2),
            _ev("T4", 2.5),  # <- weakest attempted
            _ev("P1", 1.0, error="Not scored"),
        ],  # unscored: ignored
        "questions": [{}] * 5,
        "current_index": 5,
    }
    assert _weakest(state)["question_id"] == "T4"
    assert route_next(state) == "follow_up"


def test_no_follow_up_when_nothing_was_attempted():
    state = {
        "evaluations": [_ev("T1", 0, skipped=True), _ev("T2", 0.0)],
        "questions": [{}] * 2,
        "current_index": 2,
    }
    assert _weakest(state) is None
    assert route_next(state) == "final_evaluation"  # follow-up skipped -> 0 LLM calls


def test_more_questions_left_goes_back_to_ask():
    state = {"evaluations": [_ev("T1", 5.0)], "questions": [{}] * 3, "current_index": 1}
    assert route_next(state) == "ask_question"
