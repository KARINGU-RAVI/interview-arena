"""Deterministic scoring in plain Python (no LLM), so the same input always gives
the same number and every number can be explained line by line.

Phase 3: compute_match_percent. Phase 6 adds compute_overall_score / get_recommendation.
"""

from __future__ import annotations

from schemas import GapItem, HRAssessment, TechnicalAssessment

# Rubric weights from data/rubric.md (PRD §11): field name -> (display name, weight).
# Technical and project questions share one set; HR has its own.
TECHNICAL_CRITERIA: dict[str, tuple[str, float]] = {
    "correctness": ("Correctness", 0.40),
    "depth": ("Depth", 0.25),
    "clarity": ("Clarity", 0.15),
    "jd_relevance": ("JD relevance", 0.20),
}
HR_CRITERIA: dict[str, tuple[str, float]] = {
    "star_completeness": ("STAR completeness", 0.40),
    "impact": ("Impact / measurable result", 0.25),
    "clarity_structure": ("Clarity and structure", 0.20),
    "relevance": ("Relevance", 0.15),
}


def criteria_for(round_: str) -> dict[str, tuple[str, float]]:
    return HR_CRITERIA if round_ == "hr" else TECHNICAL_CRITERIA


def compute_answer_score(assessment: TechnicalAssessment | HRAssessment, round_: str) -> float:
    """Answer score (0–10) = weighted sum of the criterion scores, rounded to 1 decimal.

    The LLM only scores each criterion; the weighting is done here, so the same
    criterion scores always give the same final score.
    """
    total = sum(
        getattr(assessment, field).score * weight for field, (_, weight) in criteria_for(round_).items()
    )
    return round(total, 1)


def compute_match_percent(gaps: list[GapItem]) -> float:
    """PRD FR-3.2: match % = (matched + 0.5 × partial) / total required × 100.

    Only REQUIRED skills count — nice-to-have skills are a bonus, not the job's bar.
    Returns 0.0 when the JD lists no required skills (avoid dividing by zero).
    """
    required = [g for g in gaps if g.importance == "required"]
    if not required:
        return 0.0
    matched = sum(g.status == "matched" for g in required)
    partial = sum(g.status == "partial" for g in required)
    return round((matched + 0.5 * partial) / len(required) * 100, 1)


# ------------------------------------------------------------ overall (PRD FR-6.3, FR-6.4)

ROUND_WEIGHTS: dict[str, float] = {"technical": 0.40, "project": 0.30, "hr": 0.30}


def compute_overall_score(evaluations: list[dict]) -> dict:
    """Overall = Technical avg × 40% + Project avg × 30% + HR avg × 30%.

    - Skipped answers count as 0 (they are part of the average).
    - Answers that could not be scored (provider/budget error) are left out, so the
      candidate isn't punished for our outage; if a whole round is missing, the other
      rounds' weights are scaled up to sum to 100%.
    - The follow-up answer is reported separately and not part of this score.
    """
    by_round: dict[str, float] = {}
    for round_ in ROUND_WEIGHTS:
        scores = [e["score"] for e in evaluations if e["round"] == round_ and not e.get("error")]
        if scores:
            by_round[round_] = round(sum(scores) / len(scores), 2)
    if not by_round:
        return {"score": 0.0, "by_round": {}, "recommendation": get_recommendation(0.0)}
    weight_sum = sum(ROUND_WEIGHTS[r] for r in by_round)
    score = round(sum(avg * ROUND_WEIGHTS[r] for r, avg in by_round.items()) / weight_sum, 1)
    return {"score": score, "by_round": by_round, "recommendation": get_recommendation(score)}


def get_recommendation(score: float) -> str:
    """PRD FR-6.4: ≥8 Strong Hire · 6.5–7.9 Hire · 5–6.4 Lean No · <5 No Hire."""
    if score >= 8:
        return "Strong Hire"
    if score >= 6.5:
        return "Hire"
    if score >= 5:
        return "Lean No"
    return "No Hire"
