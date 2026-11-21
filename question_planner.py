"""Question Planner: 10 gap-targeted questions (PRD FR-4), ONE LLM call (+1 retry if needed).

The schema (QuestionPlan) already forces 10 questions split 4/3/3 with no duplicates.
Python then checks the rules a schema can't:
  - at least 3 technical questions target a missing/partial skill (FR-4.2)
  - every project question is about a REAL resume project (FR-4.3)
If a rule fails, we ask once more with the problems listed (PRD: validate + 1 retry).
Difficulty comes from the JD's level — the job's bar, not the candidate's years (FR-4.4).
In Phase 5 the LangGraph `plan_questions` node will call plan_questions().
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import config
from gap_analysis import normalize_skill
from llm.router import LLMRouter
from rag.store import SessionStore
from schemas import GapItem, JDProfile, QuestionPlan, ResumeProfile

log = logging.getLogger("interview_arena.planner")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "question_planner.md"

# What "difficulty" means at each JOB level (JDProfile.level).
LEVEL_GUIDE = {
    "intern": "basics and definitions; simple 'what is / how do you' questions.",
    "junior": "fundamentals and practical use: how something works, when to use it, "
    "simple debugging scenarios. No system-design questions.",
    "mid": "design decisions and trade-offs in real features; debugging and testing choices.",
    "senior": "architecture, scaling, reliability and trade-offs across systems.",
    "lead": "architecture plus leading people: technical direction, reviews, mentoring.",
}


@dataclass
class PlanResult:
    plan: QuestionPlan
    warnings: list[str] = field(default_factory=list)  # rules still broken after the retry


def _matches(name: str, options: list[str]) -> bool:
    """Same name after normalising, or a very close spelling."""
    key, keys = normalize_skill(name), [normalize_skill(o) for o in options]
    return key in keys or bool(difflib.get_close_matches(key, keys, n=1, cutoff=0.85))


def check_plan(
    plan: QuestionPlan,
    gap_skills: list[str],
    project_names: list[str],
    section_projects: list[str] | None = None,
) -> list[str]:
    """Rules the JSON schema can't enforce. Returns a list of problems (empty = OK).

    section_projects = projects from the resume's PROJECTS section; they are preferred,
    so at least min(3, how many there are) project questions must use them.
    """
    problems = []
    technical = [q for q in plan.questions if q.round == "technical"]
    on_gaps = sum(_matches(q.target_skill, gap_skills) for q in technical)
    needed = min(config.MIN_GAP_QUESTIONS, len(gap_skills))
    if on_gaps < needed:
        problems.append(
            f"only {on_gaps} technical questions target a skill gap; need {needed}. Gap skills: {gap_skills}"
        )
    projects = [q for q in plan.questions if q.round == "project"]
    unknown = [q.target_skill for q in projects if not _matches(q.target_skill, project_names)]
    if unknown:
        problems.append(
            f"project questions must use real project names exactly; "
            f"not found: {unknown}. Real projects: {project_names}"
        )
    if len({normalize_skill(q.target_skill) for q in projects}) < len(projects):
        problems.append("each project question must be about a different project")
    if section_projects:
        used = sum(_matches(q.target_skill, section_projects) for q in projects)
        needed = min(3, len(section_projects))
        if used < needed:
            problems.append(
                f"use the PROJECTS-section projects first: {used} used, need {needed}. "
                f"PROJECTS section: {section_projects}"
            )
    return problems


def build_prompt(jd: JDProfile, resume: ResumeProfile, gaps: list[GapItem], store: SessionStore) -> str:
    # Gaps: required first (they matter more), then nice-to-have.
    gap_items = sorted((g for g in gaps if g.status != "matched"), key=lambda g: g.importance != "required")
    gap_lines = [f'- "{g.skill}" ({g.importance}, {g.status})' for g in gap_items]
    matched = [f'- "{g.skill}"' for g in gaps if g.status == "matched"]

    # RAG: one resume excerpt per project gives concrete details to ask about.
    # Projects-section items first (preferred for project questions), then work products.
    project_lines = []
    for p in sorted(resume.projects, key=lambda p: p.kind != "project"):
        hits = store.retrieve_evidence(f"{p.name} {' '.join(p.tech)}", k=1, source="resume")
        excerpt = hits[0].text.replace("\n", " ") if hits else ""
        tag = "PROJECTS section" if p.kind == "project" else "work product"
        project_lines.append(f'- [{tag}] "{p.name}" — {", ".join(p.tech)} — {p.impact} — excerpt: {excerpt}')

    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{ROLE_TITLE}}", jd.role_title)
        .replace("{{LEVEL}}", jd.level)
        .replace("{{LEVEL_GUIDE}}", LEVEL_GUIDE[jd.level])
        .replace("{{RESPONSIBILITIES}}", "\n".join(f"- {r}" for r in jd.responsibilities))
        .replace("{{GAP_SKILLS}}", "\n".join(gap_lines) or "- (none)")
        .replace("{{MATCHED_SKILLS}}", "\n".join(matched) or "- (none)")
        .replace("{{PROJECTS}}", "\n".join(project_lines) or "- (no projects listed)")
    )


def _renumber(plan: QuestionPlan) -> QuestionPlan:
    """Give ids T1–T4, P1–P3, H1–H3 in a fixed order (don't trust the LLM's ids)."""
    out = []
    for prefix, round_ in (("T", "technical"), ("P", "project"), ("H", "hr")):
        qs = [q for q in plan.questions if q.round == round_]
        out += [q.model_copy(update={"id": f"{prefix}{i}"}) for i, q in enumerate(qs, 1)]
    return QuestionPlan(questions=out)


def plan_questions(
    jd: JDProfile, resume: ResumeProfile, gaps: list[GapItem], store: SessionStore, router: LLMRouter
) -> PlanResult:
    gap_skills = [g.skill for g in gaps if g.status != "matched"]
    project_names = [p.name for p in resume.projects]
    section_projects = [p.name for p in resume.projects if p.kind == "project"]
    prompt = build_prompt(jd, resume, gaps, store)

    plan = router.invoke_structured(prompt, QuestionPlan, node="plan_questions")
    problems = check_plan(plan, gap_skills, project_names, section_projects)
    if problems:
        log.warning("plan broke %d rule(s); retrying once: %s", len(problems), problems)
        retry_prompt = (
            prompt + "\n\nYour previous plan broke these rules — fix them:\n- " + "\n- ".join(problems)
        )
        plan = router.invoke_structured(retry_prompt, QuestionPlan, node="plan_questions_retry")
        problems = check_plan(plan, gap_skills, project_names, section_projects)
        if problems:
            log.warning("plan still breaks rules after retry (kept, reported): %s", problems)
    return PlanResult(plan=_renumber(plan), warnings=problems)
