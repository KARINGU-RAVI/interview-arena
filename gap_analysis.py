"""Gap analysis: which JD skills does the candidate have? (PRD FR-3)

Steps:
1. RAG (local, free): for every JD skill, find the 2 most related resume chunks.
2. ONE LLM call (Gap Analyst) classifies every skill as matched / partial / missing,
   quoting resume evidence. One call for all skills keeps us inside the budget.
3. Python cleans up: exact JD skill names, importance from the JD (not the LLM),
   required skills first (FR-3.3), and the match % (FR-3.2).

Why an LLM at all: plain string matching fails on synonyms and near-misses
("React.js" vs "React", Flask vs "FastAPI or Django" = partial).
In Phase 5 the LangGraph `gap_analysis` node will call analyze_gaps().
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import config
from llm.router import LLMRouter
from rag.store import SessionStore
from schemas import GapAnalysis, GapItem, JDProfile, ResumeProfile
from scoring import compute_match_percent

log = logging.getLogger("interview_arena.gaps")

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "gap_analyst.md"


@dataclass
class GapResult:
    gaps: list[GapItem]  # required skills first, then nice-to-have
    match_percent: float
    omitted_by_llm: list[str] = field(default_factory=list)  # skills the LLM skipped


def normalize_skill(skill: str) -> str:
    """Make names comparable: lowercase, letters/digits only, and drop extras the LLM
    sometimes copies from the prompt line (leading "3.", trailing "(required)")."""
    s = skill.lower()
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)  # "3. Python" -> "python"
    s = re.sub(r"\((required|nice[_ ]to[_ ]have)\)", "", s)  # "Python (required)"
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _job_skills(jd: JDProfile) -> list[tuple[str, str]]:
    """All JD skills in a fixed order: required first, then nice-to-have (FR-3.3)."""
    return [(s, "required") for s in jd.required_skills] + [(s, "nice_to_have") for s in jd.nice_to_have]


def build_prompt(jd: JDProfile, resume: ResumeProfile, store: SessionStore) -> str:
    # RAG: collect the most relevant resume chunks per skill, numbered once (R1, R2, ...)
    excerpt_ids: dict[str, str] = {}
    skill_lines = []
    for i, (skill, importance) in enumerate(_job_skills(jd), 1):
        refs = []
        for ev in store.retrieve_evidence(skill, k=config.GAP_EVIDENCE_PER_SKILL, source="resume"):
            if ev.text not in excerpt_ids:
                excerpt_ids[ev.text] = f"R{len(excerpt_ids) + 1}"
            refs.append(excerpt_ids[ev.text])
        skill_lines.append(
            f'{i}. skill="{skill}" | importance={importance} | excerpts: {", ".join(refs) or "none"}'
        )

    excerpts = "\n\n".join(f"[{rid}] {text}" for text, rid in excerpt_ids.items())
    profile = resume.model_dump(exclude={"name", "education"})  # not needed to judge skills

    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{RESUME_PROFILE}}", json.dumps(profile, ensure_ascii=False))
        .replace("{{RESUME_EXCERPTS}}", excerpts)
        .replace("{{SKILLS}}", "\n".join(skill_lines))
    )


def analyze_gaps(jd: JDProfile, resume: ResumeProfile, store: SessionStore, router: LLMRouter) -> GapResult:
    prompt = build_prompt(jd, resume, store)
    result = router.invoke_structured(prompt, GapAnalysis, node="gap_analysis")

    # Match the LLM's items back to the JD skills: 1) same name, 2) close spelling,
    # 3) same position — the prompt asks for the same order, so when the counts match,
    # item i belongs to skill i even if the model re-worded the name.
    wanted = _job_skills(jd)
    by_name = {normalize_skill(item.skill): item for item in result.items}
    same_count = len(result.items) == len(wanted)
    gaps: list[GapItem] = []
    omitted: list[str] = []
    for pos, (skill, importance) in enumerate(wanted):
        item = by_name.get(normalize_skill(skill))
        if item is None:
            close = difflib.get_close_matches(normalize_skill(skill), by_name.keys(), n=1, cutoff=0.8)
            item = by_name.get(close[0]) if close else None
        if item is None and same_count:
            item = result.items[pos]
        if item is None:
            # The LLM skipped this skill. Count it as missing (no evidence) and log it,
            # rather than spending another LLM call.
            omitted.append(skill)
            item = GapItem(skill=skill, importance=importance, status="missing", evidence="")
        # Trust the JD for the name and importance; trust the LLM only for status + evidence.
        gaps.append(item.model_copy(update={"skill": skill, "importance": importance}))

    if omitted:
        log.warning(
            "gap analyst skipped %d skill(s), marked missing: %s | names it returned: %s",
            len(omitted),
            omitted,
            [i.skill for i in result.items],
        )
    return GapResult(gaps=gaps, match_percent=compute_match_percent(gaps), omitted_by_llm=omitted)
