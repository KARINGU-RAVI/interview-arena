"""Evaluator: score one answer with the fixed rubric (PRD FR-6).

- The LLM scores each rubric criterion 0–10 with an evidence quote (one LLM call).
- Python computes the answer score from the rubric weights (scoring.compute_answer_score).
- The answer is wrapped in <candidate_answer> tags and treated as data (prompt-injection guard).
- A skipped / empty answer scores 0 with NO LLM call.
- The evaluator model is locked per session to config.EVALUATOR_PROVIDER, so every answer
  is scored by the same model (Gemini ignores temperature; Groq is the consistency fallback).
In Phase 5 the `evaluate_answer` / `evaluate_follow_up` nodes will call evaluate().
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import config
from llm.router import LLMRouter
from rag.store import Evidence, SessionStore
from schemas import CriterionScore, Evaluation, HRAssessment, Question, TechnicalAssessment
from scoring import compute_answer_score, criteria_for

log = logging.getLogger("interview_arena.evaluator")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "evaluator.md"
ROUND_RUBRIC = {
    "technical": "Technical and Project",
    "project": "Technical and Project",
    "hr": "HR / behavioural",
}


def evidence_for(
    question: Question, store: SessionStore, k: int = config.EVALUATOR_EVIDENCE_CHUNKS
) -> list[Evidence]:
    """RAG context for scoring: project answers are checked against the RESUME (do they
    match what the candidate claimed?); technical/HR answers against the JD (relevance)."""
    source = "resume" if question.round == "project" else "jd"
    return store.retrieve_evidence(question.text, k=k, source=source)


def _escape_answer(answer: str) -> str:
    """Limit length (PRD: 2,000 chars) and stop the answer from closing our tag early."""
    answer = answer[: config.MAX_ANSWER_CHARS]
    return answer.replace("</candidate_answer>", "[/candidate_answer]")


def _ensure_lock(router: LLMRouter) -> None:
    """Lock the evaluator to EVALUATOR_PROVIDER once per session (if that provider is set up)."""
    if router.locked_provider:
        return
    try:
        router.lock_model(config.EVALUATOR_PROVIDER)
    except ValueError:
        fallback = router.lock_model()  # e.g. no Groq key: use the first available provider
        log.warning(
            "EVALUATOR_PROVIDER=%s not available; locked to %s instead", config.EVALUATOR_PROVIDER, fallback
        )


# Phrases that mean "no answer". Checked only on SHORT answers (<= 8 words), so a real
# answer like "I don't know the exact flag, but Docker caches each layer..." still counts.
NON_ANSWER_PHRASES = ("dont know", "do not know", "idk", "no idea", "skip")


def is_non_answer(answer: str | None) -> bool:
    """True for empty answers, fewer than 3 words, or a short "don't know" style reply.

    These score 0 with NO LLM call (saves budget; nothing to evaluate).
    """
    if answer is None:
        return True
    words = re.sub(r"[^a-z0-9 ]+", "", answer.lower().replace("’", "'").replace("'", "")).split()
    if len(words) < config.MIN_ANSWER_WORDS:
        return True
    text = " ".join(words)
    return len(words) <= 8 and any(re.search(rf"\b{p}\b", text) for p in NON_ANSWER_PHRASES)


def skipped_evaluation(question: Question) -> Evaluation:
    return Evaluation(
        question_id=question.id, round=question.round, score=0.0, skipped=True, feedback="No answer given"
    )


def evaluate(
    question: Question, answer: str | None, evidence: list[Evidence], router: LLMRouter
) -> Evaluation:
    if is_non_answer(answer):
        return skipped_evaluation(question)  # no LLM call (PRD FR-8.6)

    _ensure_lock(router)
    schema = HRAssessment if question.round == "hr" else TechnicalAssessment
    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{RUBRIC}}", config.RUBRIC_PATH.read_text(encoding="utf-8"))
        .replace("{{ROUND_RUBRIC}}", ROUND_RUBRIC[question.round])
        .replace("{{ROUND}}", question.round)
        .replace("{{QUESTION}}", question.text)
        .replace("{{EVIDENCE}}", "\n---\n".join(e.text for e in evidence) or "(none)")
        .replace("{{ANSWER}}", _escape_answer(answer))
    )  # last: answer text can't inject placeholders

    assessment = router.invoke_structured(
        prompt, schema, temperature=0, node=f"evaluate_{question.id}", use_lock=True
    )

    criteria = [
        CriterionScore(
            criterion=label,
            score=getattr(assessment, field).score,
            evidence=getattr(assessment, field).evidence,
        )
        for field, (label, _) in criteria_for(question.round).items()
    ]
    last = router.last_call
    return Evaluation(
        question_id=question.id,
        round=question.round,
        score=compute_answer_score(assessment, question.round),
        criteria=criteria,
        strengths=assessment.strengths,
        weaknesses=assessment.weaknesses,
        feedback=assessment.feedback,
        evaluator_model=f"{last.provider}:{last.model}" if last else "",
    )
