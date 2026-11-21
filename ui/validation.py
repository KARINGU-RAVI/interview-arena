"""Input cleaning + validation (spec §0, bugs B1–B3). Plain functions: no Streamlit, easy to test.

B1: an empty / whitespace-only answer must not be submitted.
B2: answers, resume, JD and email are always trimmed before use.
B3: screen 1 validates AFTER trimming and reports one error per field.
"""

from __future__ import annotations

import re

import config

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMPTY_ANSWER_ERROR = "Write an answer, or choose Skip question."


def clean_answer(text: str | None) -> str:
    """Trim, and collapse runs of spaces/tabs and of blank lines (single line breaks are kept,
    so a list in an answer still reads as a list)."""
    if not text:
        return ""
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n\s*\n\s*", "\n\n", text)  # many blank lines -> one
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def clean_block(text: str | None) -> str:
    """Resume / JD text: trim every line and squeeze blank lines; keep the line structure
    (bullets and section headings help extraction)."""
    if not text:
        return ""
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def word_count(text: str | None) -> int:
    return len(clean_answer(text).split())


def validate_inputs(resume: str, jd: str, email: str, app_mode: str) -> dict[str, str]:
    """Return {field: error} for screen 1 (fields: "resume", "jd", "email"). Empty = valid.
    Pass already-cleaned values."""
    errors: dict[str, str] = {}
    if not resume:
        errors["resume"] = "Add your resume: upload a PDF or paste the text."
    elif len(resume) > config.MAX_RESUME_CHARS:
        errors["resume"] = f"Your resume is over {config.MAX_RESUME_CHARS:,} characters — please shorten it."
    if not jd:
        errors["jd"] = "Paste the job description."
    elif len(jd) > config.MAX_JD_CHARS:
        errors["jd"] = f"The job description is over {config.MAX_JD_CHARS:,} characters — please shorten it."
    if not email:
        if app_mode != "cloud":  # optional in cloud mode (no Gmail draft there)
            errors["email"] = "Enter your email so we can prepare your report."
    elif not EMAIL_RE.match(email):
        errors["email"] = "That email doesn't look right — check for typos (e.g. name@example.com)."
    return errors
