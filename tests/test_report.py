"""Offline tests (no LLM) for the markdown report (PRD FR-7.1, FR-8.4).

Run:  python -m pytest -q tests/test_report.py
"""

import report


def _state(models=("gemini:m",)) -> dict:
    def ev(qid, rnd, score, skipped=False, error="", i=0):
        return {
            "question_id": qid,
            "round": rnd,
            "score": score,
            "skipped": skipped,
            "error": error,
            "feedback": f"tip {qid}",
            "weaknesses": ["w"],
            "evaluator_model": "" if skipped or error else models[i % len(models)],
        }

    return {
        "jd_profile": {"role_title": "Frontend Developer", "level": "junior"},
        "resume_profile": {
            "name": "Test Person",
            "skills": [],
            "tools": [],
            "years_experience": 2,
            "projects": [],
        },
        "match_percent": 75.0,
        "gaps": [
            {"skill": "Next.js", "importance": "nice_to_have", "status": "missing", "evidence": ""},
            {
                "skill": "React",
                "importance": "required",
                "status": "matched",
                "evidence": "Built UI | with React",
            },
        ],
        "questions": [
            {"id": "T1", "round": "technical", "text": "What is SSR?", "target_skill": "Next.js"},
            {"id": "P1", "round": "project", "text": "Tell me about X", "target_skill": "X"},
            {"id": "H1", "round": "hr", "text": "A time you learned fast", "target_skill": "learning"},
            {"id": "H2", "round": "hr", "text": "A conflict", "target_skill": "teamwork"},
        ],
        "answers": [
            {"question_id": "T1", "answer": "server renders", "skipped": False},
            {"question_id": "P1", "answer": "", "skipped": True},
            {"question_id": "H1", "answer": "long story", "skipped": False},
            {"question_id": "H2", "answer": "x", "skipped": False},
        ],
        "evaluations": [
            ev("T1", "technical", 4.5, i=0),
            ev("P1", "project", 0.0, skipped=True),
            ev("H1", "hr", 8.2, i=1),
            ev("H2", "hr", 0.0, error="Not scored: outage"),
        ],
        "follow_up": {
            "of": "T1",
            "question": {"id": "F1", "text": "Why SSR?"},
            "evaluation": {"score": 6.0, "feedback": "better", "error": ""},
        },
        "overall": {
            "score": 3.6,
            "by_round": {"technical": 4.5, "project": 0.0, "hr": 8.2},
            "recommendation": "No Hire",
        },
        "extract_model": "gemini:m",
        "extract_attempts": ["gemini:m: ok (2 calls)"],
    }


ROUTER = {"calls": 14, "target": 18}


def test_sample_answers_only_for_scored_below_six():
    # T1 (4.5) and skipped P1 (0) yes; H1 (8.2) no; H2 unscored (error) no.
    assert report.sample_ids(_state()) == ["T1", "P1"]


def test_report_has_every_fr_7_1_section():
    summary = {
        "summary": "Good start.",
        "top_actions": ["a1", "a2", "a3"],
        "sample_answers": [{"question_id": "T1", "improved_answer": "SSR renders on the server."}],
    }
    md = report.render_markdown(_state(), summary, ROUTER)
    for text in (
        "Test Person",
        "_Frontend Developer · Junior level_",
        "**Skill match:** 75.0%",
        "**Overall score:** 3.6/10",
        "**Recommendation:** No Hire",
        "## Summary",
        "1. a1",
        "3. a3",
        "## Skill gaps",
        "| Next.js |",
        "## Question by question",
        "| T1 | Technical |",
        "Not scored: outage",
        "Follow-up on T1",
        "not part of the overall",
        "## Sample better answers",
        "SSR renders on the server.",
        "### T1",
        "All answers scored by `gemini:m`",
        "Skipped / no answer (scored 0): P1",
    ):
        assert text in md, text
    assert "Built UI / with React" in md  # a "|" inside a cell can't break the table


def test_report_without_ai_summary_is_still_complete():
    md = report.render_markdown(_state(), None, ROUTER)
    assert "could not be generated" in md and "## Question by question" in md
    assert "## Sample better answers" not in md


def test_report_notes_evaluator_fallback():
    md = report.render_markdown(_state(models=("gemini:m", "groq:g")), None, ROUTER)
    assert "Evaluator fallback happened" in md and "H1: `groq:g`" in md


def test_save_report_uses_outputs_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "OUTPUT_DIR", tmp_path)
    path = report.save_report("# hi\n", "Ananya Reddy (test)")
    assert path.parent == tmp_path and path.name.startswith("report_ananya-reddy-test_")
    assert path.read_text(encoding="utf-8") == "# hi\n"


def test_names_get_normal_capitalisation():
    assert report.display_name("ANANYA REDDY") == "Ananya Reddy"
    assert report.display_name("karthik menon") == "Karthik Menon"
    assert report.display_name("Sam McDonald") == "Sam McDonald"  # mixed case kept


def test_report_explains_match_vs_overall_and_all_skipped():
    st = _state()
    for e in st["evaluations"]:
        e.update(skipped=True, score=0.0, evaluator_model="", error="")
    md = report.render_markdown(st, None, ROUTER)
    assert "Skill match compares the resume with the JD" in md
    assert "Answers scored by: none (all skipped)." in md
