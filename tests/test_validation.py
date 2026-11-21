"""Offline tests for input cleaning/validation (spec bugs B1–B3).

Run:  python -m pytest -q tests/test_validation.py
"""

from graph.nodes import _clean_reply
from ui.validation import clean_answer, clean_block, validate_inputs, word_count


def test_b2_answers_are_trimmed_and_collapsed():
    assert clean_answer("   React   Query \t caches  ") == "React Query caches"
    assert clean_answer("line one  \n\n\n\n   line two ") == "line one\n\nline two"
    assert clean_answer("   \n\t ") == "" and clean_answer(None) == ""


def test_b2_graph_stores_the_trimmed_answer():
    assert _clean_reply("   React   Query caches server state   ") == "React Query caches server state"
    assert _clean_reply("   ") is None  # whitespace only = no answer


def test_b2_blocks_keep_lines_but_lose_padding():
    assert clean_block("  SKILLS  \n\n\n\n  - Python  \n  - React ") == "SKILLS\n\n- Python\n- React"


def test_b3_whitespace_fields_get_their_own_errors():
    errors = validate_inputs(clean_block("  "), clean_block(" \n "), "", "local")
    assert set(errors) == {"resume", "jd", "email"}


def test_b3_email_optional_only_in_cloud_mode():
    assert "email" not in validate_inputs("r", "j", "", "cloud")
    assert "email" in validate_inputs("r", "j", "bad", "cloud")
    assert validate_inputs("r", "j", "a@b.co", "local") == {}


def test_word_count():
    assert word_count("  one two   three ") == 3 and word_count("") == 0
