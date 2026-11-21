"""Phase 3 tests.

Offline (no LLM): match % formula, chunking, RAG retrieval.
Live (6 LLM calls: 3 per pair): extraction + gap analysis on pairs 2 and 3,
compared with tests/expected_gaps.json. Target: accuracy >= 80% per pair and
match % inside the expected range.

Run:  python -m pytest -q -s tests/test_gaps.py      (-s shows the accuracy table)
"""

import difflib
import json
from pathlib import Path

import pytest

import config
from gap_analysis import analyze_gaps, normalize_skill
from rag.store import build_session_store, chunk_text
from schemas import GapItem
from scoring import compute_match_percent

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = json.loads((ROOT / "tests" / "expected_gaps.json").read_text(encoding="utf-8"))
# Optional private pair (real resume): kept only on the developer's machine, never committed.
PRIVATE = ROOT / "tests" / "local" / "expected_gaps_private.json"
if PRIVATE.exists():
    EXPECTED["private"] = json.loads(PRIVATE.read_text(encoding="utf-8"))
PAIRS = [n for n in ("private", "pair_2", "pair_3") if n in EXPECTED]


# ---------------------------------------------------------------- offline


def _gap(status: str, importance: str = "required") -> GapItem:
    return GapItem(skill="s", importance=importance, status=status, evidence="")


def test_match_percent_formula():
    gaps = [
        _gap("matched"),
        _gap("matched"),
        _gap("partial"),
        _gap("missing"),
        _gap("matched", "nice_to_have"),
    ]  # nice-to-have must not count
    assert compute_match_percent(gaps) == 62.5  # (2 + 0.5) / 4


def test_match_percent_no_required_skills():
    assert compute_match_percent([_gap("matched", "nice_to_have")]) == 0.0


def test_chunks_have_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(400))  # ~2,700 chars
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= config.CHUNK_SIZE for c in chunks)
    for a, b in zip(
        chunks, chunks[1:], strict=False
    ):  # the end of one chunk reappears at the start of the next
        assert a.split()[-1] in b.split()[:15]


def test_retrieval_finds_related_resume_line():
    resume = (ROOT / "data" / "resume_3_karthik_backend.txt").read_text(encoding="utf-8")
    jd = (ROOT / "data" / "jd_3_fullstack_developer.txt").read_text(encoding="utf-8")
    store = build_session_store(resume, jd)
    try:
        hits = store.retrieve_evidence("pytest unit testing", k=2, source="resume")
        assert all(h.source == "resume" for h in hits)
        assert any("unittest" in h.text for h in hits)
    finally:
        store.close()


# ---------------------------------------------------------------- live (uses LLM calls)


def _accuracy(pair: dict, gaps: list[GapItem]) -> tuple[float, list[str]]:
    """Share of expected skills whose status matches. Skill names are matched loosely
    because the JD Analyst may re-word a skill slightly."""
    by_name = {normalize_skill(g.skill): g for g in gaps}
    expected = {**pair["required"], **pair["nice_to_have"]}
    rows, correct = [], 0
    for skill, want in expected.items():
        key = normalize_skill(skill)
        if key not in by_name:
            # e.g. expected "RAG (Retrieval-Augmented Generation)" vs extracted "RAG":
            # accept when one name starts with the other, else the closest spelling.
            prefix = [k for k in by_name if key.startswith(k) or k.startswith(key)]
            close = prefix or difflib.get_close_matches(key, by_name.keys(), n=1, cutoff=0.6)
            key = close[0] if close else None
        got = by_name[key].status if key else "NOT FOUND"
        ok = got == want
        correct += ok
        rows.append(f"  {'OK ' if ok else 'XX '} {skill:<38} expected={want:<8} got={got}")
    return correct / len(expected), rows


def _pair_param(name: str):
    """The private pair's resume is git-ignored, so its test skips when the file is absent."""
    resume = ROOT / EXPECTED[name]["resume"]
    return pytest.param(
        name,
        marks=pytest.mark.skipif(
            not resume.exists(), reason=f"{resume.name} not present (private, git-ignored)"
        ),
    )


@pytest.mark.skipif(
    not (config.has_key("GOOGLE_API_KEY") or config.has_key("GROQ_API_KEY")), reason="needs an LLM API key"
)
@pytest.mark.parametrize("pair_name", [_pair_param(n) for n in PAIRS])
def test_gap_accuracy(pair_name):
    from crew.extraction_crew import extract_profiles
    from llm.router import LLMRouter
    from pdf_parser import parse_resume_pdf

    pair = EXPECTED[pair_name]
    jd_text = (ROOT / pair["jd"]).read_text(encoding="utf-8")
    resume_path = ROOT / pair["resume"]
    resume_text = (
        parse_resume_pdf(resume_path)
        if resume_path.suffix.lower() == ".pdf"
        else resume_path.read_text(encoding="utf-8")
    )

    router = LLMRouter()
    extraction = extract_profiles(jd_text, resume_text)
    router.add_external_calls(extraction.llm_calls, node="extract")
    store = build_session_store(resume_text, jd_text)
    try:
        result = analyze_gaps(extraction.jd_profile, extraction.resume_profile, store, router)
    finally:
        store.close()

    accuracy, rows = _accuracy(pair, result.gaps)
    low, high = pair["expected_match_percent_range"]
    print(
        f"\n{pair_name}: accuracy {accuracy:.0%} | match {result.match_percent}% "
        f"(expected {low}-{high}) | LLM calls {router.call_count}"
    )
    print("\n".join(rows))
    assert accuracy >= 0.8
    assert low <= result.match_percent <= high
