"""Pydantic models for every LLM output (and the data built from them).

Why Pydantic: the LLM must return JSON in a fixed shape. Pydantic checks the
shape and types; if a model returns something else we get a clear error and
can retry, instead of a crash somewhere later in the graph.

Rule for free-model compatibility: every field is required (lists may be empty).
Optional fields and complex unions break structured output on some providers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Level = Literal["intern", "junior", "mid", "senior", "lead"]
Round = Literal["technical", "project", "hr"]


# ------------------------------------------------------------ extraction (Phase 2)


class JDProfile(BaseModel):
    """What the JD Analyst extracts from a job description."""

    role_title: str = Field(description="Job title, e.g. 'Frontend Developer'")
    level: Level = Field(description="Seniority implied by the JD")
    required_skills: list[str] = Field(description="Must-have skills, one skill per item")
    nice_to_have: list[str] = Field(description="Optional / preferred skills, one per item")
    responsibilities: list[str] = Field(description="Main duties, short phrases")


class Project(BaseModel):
    name: str
    kind: Literal["project", "work"] = Field(
        description="'project' = from the resume's PROJECTS section; 'work' = a product built at a job"
    )
    tech: list[str] = Field(description="Technologies used in the project")
    impact: str = Field(description="What it achieved; numbers if the resume gives them, else ''")


class ResumeProfile(BaseModel):
    """What the Resume Analyst extracts from a resume."""

    name: str
    skills: list[str] = Field(description="Languages, frameworks, concepts")
    tools: list[str] = Field(description="Tools/platforms, e.g. Git, Jira, Figma, Docker")
    years_experience: float = Field(ge=0, description="Total professional years; internships count")
    projects: list[Project]
    education: list[str]
    certifications: list[str]


# ------------------------------------------------------------ gap analysis (Phase 3)


class GapItem(BaseModel):
    skill: str
    importance: Literal["required", "nice_to_have"]
    status: Literal["matched", "partial", "missing"]
    evidence: str = Field(description="Resume text that supports the status, or '' if missing")


class GapAnalysis(BaseModel):
    """Gap Analyst output: one item per JD skill (required + nice-to-have)."""

    items: list[GapItem]


# ------------------------------------------------------------ questions (Phase 4)


class Question(BaseModel):
    id: str = Field(description="T1–T4, P1–P3, H1–H3")
    round: Round
    text: str
    target_skill: str


class QuestionPlan(BaseModel):
    questions: list[Question]

    @model_validator(mode="after")
    def check_split(self) -> QuestionPlan:
        """PRD FR-4.1: exactly 10 questions — 4 technical, 3 project, 3 HR — no duplicates."""
        counts = {r: sum(q.round == r for q in self.questions) for r in ("technical", "project", "hr")}
        if counts != {"technical": 4, "project": 3, "hr": 3}:
            raise ValueError(f"need 4 technical / 3 project / 3 hr, got {counts}")
        texts = [q.text.strip().lower() for q in self.questions]
        if len(set(texts)) != len(texts):
            raise ValueError("duplicate question text")
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate question id")
        return self


# ------------------------------------------------------------ evaluation (Phase 4)


class CriterionScore(BaseModel):
    """One rubric criterion as stored in an Evaluation (name added by Python)."""

    criterion: str = Field(description="Rubric criterion name, e.g. 'Correctness'")
    score: int = Field(ge=0, le=10)
    evidence: str = Field(description="Short quote from the answer that justifies the score")


class ScoredCriterion(BaseModel):
    """What the Evaluator returns for ONE criterion."""

    score: int = Field(ge=0, le=10, description="0–10 using the rubric bands")
    evidence: str = Field(description="Short quote copied from the answer ('' if none)")


# The Evaluator LLM returns one of these two. There is one FIELD per rubric criterion,
# so the JSON schema itself forces the model to score every criterion (it can't skip
# or rename one). Note: NO overall score — Python computes it from the weights.


class TechnicalAssessment(BaseModel):
    """For technical AND project questions."""

    correctness: ScoredCriterion
    depth: ScoredCriterion
    clarity: ScoredCriterion
    jd_relevance: ScoredCriterion
    strengths: list[str]
    weaknesses: list[str]
    feedback: str = Field(description="One sentence on how to improve")


class HRAssessment(BaseModel):
    """For HR / behavioural questions."""

    star_completeness: ScoredCriterion
    impact: ScoredCriterion
    clarity_structure: ScoredCriterion
    relevance: ScoredCriterion
    strengths: list[str]
    weaknesses: list[str]
    feedback: str = Field(description="One sentence on how to improve")


class Evaluation(BaseModel):
    """Stored result for one answer (LLM assessment + values computed in Python)."""

    question_id: str
    round: Round
    score: float = Field(ge=0, le=10, description="Weighted rubric score, computed in Python")
    criteria: list[CriterionScore] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    feedback: str = ""
    skipped: bool = False
    evaluator_model: str = ""
    error: str = ""  # set when the answer could not be scored (provider/budget problem)


class FollowUpQuestion(BaseModel):
    """Follow-up Interviewer output: one probing question on the weakest answer."""

    text: str = Field(description="One follow-up question, one or two sentences")


# ------------------------------------------------------------ report (Phase 6)


class SampleAnswer(BaseModel):
    question_id: str
    improved_answer: str


class ReportSummary(BaseModel):
    summary: str
    top_actions: list[str] = Field(description="Exactly 3 concrete next steps")
    sample_answers: list[SampleAnswer]

    @model_validator(mode="after")
    def check_actions(self) -> ReportSummary:
        if len(self.top_actions) != 3:
            raise ValueError(f"need exactly 3 top_actions, got {len(self.top_actions)}")
        return self
