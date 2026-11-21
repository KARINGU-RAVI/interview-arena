"""Phase 9 metrics: run every live measurement and save the numbers for docs/test_results.md.

    python scripts/run_metrics.py                 # everything (~50 LLM calls, ~8-10 min)
    python scripts/run_metrics.py --only golden,consistency

Sections: gaps, golden, consistency, sessions (one full session on fictional pair 2).
All run in ONE process, so the shared rate limiter (Gemini 12 RPM) covers them all.
Results: outputs/metrics/<section>.json (git-ignored). The optional private pair
(tests/local/) is written only to outputs/metrics/private_gaps.json — never into docs/.
Only fictional resumes are used for the full sessions.
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import shutil
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.types import Command  # noqa: E402

from cli import ScriptedAnswers  # noqa: E402
from crew.extraction_crew import extract_profiles  # noqa: E402
from evaluator import evaluate, evidence_for  # noqa: E402
from gap_analysis import analyze_gaps, normalize_skill  # noqa: E402
from graph.build_graph import build_graph  # noqa: E402
from graph.state import SessionContext  # noqa: E402
from llm.router import LLMRouter  # noqa: E402
from pdf_parser import parse_resume_pdf  # noqa: E402
from rag.store import build_session_store  # noqa: E402
from schemas import Question  # noqa: E402

OUT = ROOT / "outputs" / "metrics"
EXPECTED = json.loads((ROOT / "tests" / "expected_gaps.json").read_text(encoding="utf-8"))
PRIVATE = ROOT / "tests" / "local" / "expected_gaps_private.json"
GOLDEN = json.loads((ROOT / "tests" / "golden_answers.json").read_text(encoding="utf-8"))["items"]
ANSWERS = str(ROOT / "scripts" / "demo_answers_pair2.json")
STORE_FILES = {  # RAG context for each golden item's JD (fictional resumes)
    "jd_1": ("data/jd_1_agentic_ai_engineer.txt", "data/resume_2_ananya_frontend.txt"),
    "jd_2": ("data/jd_2_frontend_developer.txt", "data/resume_2_ananya_frontend.txt"),
    "jd_3": ("data/jd_3_fullstack_developer.txt", "data/resume_3_karthik_backend.txt"),
    "any": ("data/jd_1_agentic_ai_engineer.txt", "data/resume_2_ananya_frontend.txt"),
}
SESSIONS = [  # (label, JD, fictional resume)
    ("jd2_frontend", "data/jd_2_frontend_developer.txt", "data/resume_2_ananya_frontend.pdf"),
]


def read(rel: str) -> str:
    path = ROOT / rel
    return parse_resume_pdf(path) if path.suffix == ".pdf" else path.read_text(encoding="utf-8")


def save(name: str, data) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def accuracy(pair: dict, gaps) -> dict:
    by_name = {normalize_skill(g.skill): g for g in gaps}
    expected = {**pair["required"], **pair["nice_to_have"]}
    rows, correct = [], 0
    for skill, want in expected.items():
        key = normalize_skill(skill)
        if key not in by_name:
            prefix = [k for k in by_name if key.startswith(k) or k.startswith(key)]
            close = prefix or difflib.get_close_matches(key, by_name.keys(), n=1, cutoff=0.6)
            key = close[0] if close else None
        got = by_name[key].status if key else "NOT FOUND"
        correct += got == want
        rows.append({"skill": skill, "expected": want, "got": got})
    return {
        "correct": correct,
        "total": len(expected),
        "accuracy": round(correct / len(expected), 3),
        "rows": rows,
    }


def run_gap_pair(pair: dict) -> dict:
    jd, resume = read(pair["jd"]), read(pair["resume"])
    router = LLMRouter()
    t = time.perf_counter()
    ex = extract_profiles(jd, resume)
    router.add_external_calls(ex.llm_calls, node="extract")
    store = build_session_store(resume, jd)
    try:
        result = analyze_gaps(ex.jd_profile, ex.resume_profile, store, router)
    finally:
        store.close()
    low, high = pair["expected_match_percent_range"]
    return {
        **accuracy(pair, result.gaps),
        "match_percent": result.match_percent,
        "match_range": [low, high],
        "match_ok": low <= result.match_percent <= high,
        "llm_calls": router.call_count,
        "seconds": round(time.perf_counter() - t, 1),
    }


# ---------------------------------------------------------------- sections


def section_gaps() -> None:
    out = {name: run_gap_pair(EXPECTED[name]) for name in ("pair_2", "pair_3")}
    save("gaps", out)
    for name, r in out.items():
        print(
            f"gaps {name}: {r['correct']}/{r['total']} = {r['accuracy']:.0%} | match {r['match_percent']}% "
            f"(range {r['match_range']}) | calls {r['llm_calls']}"
        )
    if PRIVATE.exists():  # private pair: chat output + git-ignored file only
        r = run_gap_pair(json.loads(PRIVATE.read_text(encoding="utf-8")))
        save("private_gaps", r)
        print(
            f"gaps PRIVATE: {r['correct']}/{r['total']} = {r['accuracy']:.0%} | match {r['match_percent']}% "
            f"(range {r['match_range']}) | calls {r['llm_calls']}  [not written to docs]"
        )


def _stores() -> dict:
    return {k: build_session_store(read(r), read(j)) for k, (j, r) in STORE_FILES.items()}


def _q(item: dict) -> Question:
    return Question(id=item["id"], round=item["round"], text=item["question"], target_skill="")


def section_golden() -> None:
    stores, router, rows = _stores(), LLMRouter(), []
    try:
        for item in GOLDEN:
            q = _q(item)
            ev = evidence_for(q, stores[item["jd"]])
            t = time.perf_counter()
            strong = evaluate(q, item["strong"], ev, router).score
            weak = evaluate(q, item["weak"], ev, router).score
            rows.append(
                {
                    "id": item["id"],
                    "strong": strong,
                    "weak": weak,
                    "ok": strong >= 7 and weak < 5,
                    "seconds_per_answer": round((time.perf_counter() - t) / 2, 1),
                }
            )
            print(f"golden {item['id']}: strong {strong} weak {weak} {'OK' if rows[-1]['ok'] else 'FAIL'}")
    finally:
        for s in stores.values():
            s.close()
    save(
        "golden",
        {
            "rows": rows,
            "passed": sum(r["ok"] for r in rows),
            "total": len(rows),
            "llm_calls": router.call_count,
            "model": router.summary()["models_used"],
        },
    )


def section_consistency() -> None:
    picks = [("T2", "strong"), ("P1", "weak"), ("H1", "strong")]
    stores, router, rows = _stores(), LLMRouter(), []
    try:
        for item_id, kind in picks:
            item = next(i for i in GOLDEN if i["id"] == item_id)
            q = _q(item)
            ev = evidence_for(q, stores[item["jd"]])
            scores = [evaluate(q, item[kind], ev, router).score for _ in range(3)]
            rows.append(
                {
                    "id": item_id,
                    "answer": kind,
                    "scores": scores,
                    "max_diff": round(max(scores) - min(scores), 1),
                }
            )
            print(f"consistency {item_id} {kind}: {scores} max diff {rows[-1]['max_diff']}")
    finally:
        for s in stores.values():
            s.close()
    save("consistency", {"rows": rows, "llm_calls": router.call_count})


def _technical_on_gaps(result: dict) -> str:
    gap_skills = {normalize_skill(g["skill"]) for g in result.get("gaps", []) if g["status"] != "matched"}
    tech = [q for q in result.get("questions", []) if q["round"] == "technical"]
    on_gaps = sum(normalize_skill(q["target_skill"]) in gap_skills for q in tech)
    return f"{on_gaps}/{len(tech)}"


def run_session(label: str, jd_rel: str, resume_rel: str) -> dict:
    """One full scripted session through the real graph (same flow as cli.py)."""
    graph, ctx = build_graph(), SessionContext(router=LLMRouter())
    cfg = {"configurable": {"thread_id": f"metrics-{label}-{uuid.uuid4().hex[:6]}"}}
    scripted = ScriptedAnswers(ANSWERS)
    payload = {
        "resume_text": read(resume_rel),
        "jd_text": read(jd_rel),
        "candidate_email": "test@example.com",
    }
    steps, kind, started = [], "analyse", time.perf_counter()
    try:
        while True:
            t = time.perf_counter()
            result = graph.invoke(payload, cfg, context=ctx)
            steps.append({"step": kind, "seconds": round(time.perf_counter() - t, 2)})
            if "__interrupt__" not in result:
                break
            p = result["__interrupt__"][0].value
            kind = {
                "start_gate": "plan",
                "question": "answer",
                "follow_up": "follow_up_answer",
                "send_gate": "finish",
            }[p["type"]]
            reply = (
                True
                if p["type"] == "start_gate"
                else "finish"
                if p["type"] == "send_gate"
                else scripted.answer(p)
            )
            payload = Command(resume=reply)
    finally:
        if ctx.store is not None:
            ctx.store.close()
    s = ctx.router.summary()
    total = round(time.perf_counter() - started, 1)
    pdf = (result.get("delivery") or {}).get("pdf_path", "")
    out = {
        "label": label,
        "jd": jd_rel,
        "resume": resume_rel,
        "seconds_total": total,
        "steps": steps,
        "calls": s["calls"],
        "calls_by_node": s["calls_by_node"],
        "failed_requests": s["failed_requests"],
        "models": s["models_used"],
        "extract_model": result.get("extract_model"),
        "overall": result.get("overall"),
        "match_percent": result.get("match_percent"),
        "skipped": sum(e["skipped"] for e in result.get("evaluations", [])),
        "pdf": pdf,
        "blocked_by_cap": False,
        # PRD acceptance: >= 3 of 4 technical questions target a missing/partial skill
        "technical_on_gaps": _technical_on_gaps(result),
    }
    print(
        f"session {label}: {s['calls']} calls in {total}s | by node {s['calls_by_node']} | "
        f"failed requests {s['failed_requests']} | overall {out['overall']}"
    )
    return out


def section_sessions() -> None:
    out = [run_session(*spec) for spec in SESSIONS]
    save("sessions", out)
    pdf = Path(out[0]["pdf"])  # fictional pair 2 (Ananya) -> sample for the README
    if pdf.exists():
        shutil.copyfile(pdf, ROOT / "docs" / "sample_report.pdf")
        print(f"sample report -> docs/sample_report.pdf (from {pdf.name})")


SECTIONS = {
    "gaps": section_gaps,
    "golden": section_golden,
    "consistency": section_consistency,
    "sessions": section_sessions,
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated sections: " + ",".join(SECTIONS))
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    chosen = args.only.split(",") if args.only else list(SECTIONS)
    t0 = time.perf_counter()
    for name in chosen:
        print(f"\n===== {name} =====", flush=True)
        try:
            SECTIONS[name]()
        except Exception as exc:  # record it and carry on with the other sections
            print(f"SECTION FAILED {name}: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
            save(f"{name}_error", {"error": type(exc).__name__, "message": str(exc)[:500]})
    print(f"\nall done in {time.perf_counter() - t0:.0f}s | results in outputs/metrics/")
