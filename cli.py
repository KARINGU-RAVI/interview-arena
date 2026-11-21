"""Run a full Interview Arena session in the terminal.

    python cli.py --jd data/jd_2_frontend_developer.txt \
        --resume data/resume_2_ananya_frontend.pdf --email test@example.com

Type your answer and press Enter. Type "skip" (or press Enter on an empty line) to skip.
Ctrl-C stops cleanly at any time.

For automated testing:  --answers scripts/demo_answers_pair2.json --yes
  (answers are picked from a keyword bank; --yes auto-confirms the start and send gates)

The loop: graph.invoke() runs until the next interrupt(); we show the pause to the user,
then resume with graph.invoke(Command(resume=reply), ...). The SAME thread_id and the SAME
SessionContext are passed every time (LangGraph doesn't store context in checkpoints).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

from langgraph.types import Command

from graph.build_graph import build_graph
from graph.state import SessionContext
from llm.router import LLMRouter
from pdf_parser import parse_resume_pdf
from ui.validation import EMAIL_RE


class ScriptedAnswers:
    """Answer bank for tests: first unused entry whose keyword appears in the question."""

    def __init__(self, path: str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.bank, self.default = data["bank"], data["default"]
        self.skip_number = data.get("skip_question_number")
        # e.g. {"3": "I don't know", "8": "idk"} -> fixed replies for those question numbers
        self.fixed = {int(k): v for k, v in data.get("fixed_replies", {}).items()}

    def answer(self, payload: dict) -> str:
        if self.skip_number is not None and payload.get("number") == self.skip_number:
            return "skip"
        if payload.get("number") in self.fixed:
            return self.fixed[payload["number"]]
        text = payload["question"]["text"].lower()
        for entry in self.bank:
            if not entry.get("used") and any(k in text for k in entry["keywords"]):
                entry["used"] = True
                return entry["answer"]
        return self.default


def read_resume(path: str) -> str:
    return parse_resume_pdf(path) if path.lower().endswith(".pdf") else Path(path).read_text(encoding="utf-8")


def show_gaps(payload: dict) -> None:
    print(f"\n=== {payload['role']} — match {payload['match_percent']}% ===")
    icon = {"matched": "[x]", "partial": "[~]", "missing": "[ ]"}
    for g in payload["gaps"]:
        print(f"  {icon[g['status']]} {g['skill']} ({g['importance']})")


def last_score_line(graph, config) -> str:
    evals = graph.get_state(config).values.get("evaluations") or []
    if not evals:
        return ""
    e = evals[-1]
    if e["skipped"]:
        return "   -> skipped: 0/10 (no LLM call)"
    if e.get("error"):
        return f"   -> {e['error']}"
    return f"   -> {e['score']}/10 · {e['feedback']}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Interview Arena (terminal)")
    ap.add_argument("--jd", required=True, help="job description .txt")
    ap.add_argument("--resume", required=True, help="resume .pdf or .txt")
    ap.add_argument("--email", required=True, help="candidate email (for the report)")
    ap.add_argument("--answers", help="JSON answer bank for scripted runs")
    ap.add_argument("--yes", action="store_true", help="auto-confirm the start gate; finish without email")
    ap.add_argument("--gmail", action="store_true", help="with --yes: also create a Gmail draft")
    ap.add_argument("-v", "--verbose", action="store_true", help="show info logs")
    ap.add_argument("--debug", action="store_true", help="show debug logs (incl. CrewAI token notes)")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not EMAIL_RE.match(args.email):
        print("Please give a valid email address.")
        return 2
    scripted = ScriptedAnswers(args.answers) if args.answers else None

    graph = build_graph()
    ctx = SessionContext(router=LLMRouter())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    state_in = {
        "jd_text": Path(args.jd).read_text(encoding="utf-8"),
        "resume_text": read_resume(args.resume),
        "candidate_email": args.email,
    }

    try:
        print("Reading the JD and resume (CrewAI) and finding skill gaps...")
        result = graph.invoke(state_in, config, context=ctx)
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            kind = payload["type"]
            if kind == "start_gate":
                show_gaps(payload)
                reply = True if args.yes else input("\nStart the interview? [Y/n] ").strip().lower() != "n"
                if not reply:
                    print("OK, stopped before the interview.")
                    return 0
            elif kind in ("question", "follow_up"):
                q = payload["question"]
                label = (
                    f"Q{payload['number']}/{payload['total']}"
                    if kind == "question"
                    else f"Follow-up on {payload['of']}"
                )
                print(f"\n[{label} · {q['round'].upper()}] {q['text']}")
                if scripted:
                    reply = scripted.answer(payload)
                    print(f"> {reply}")
                else:
                    reply = input("> ")
            elif kind == "send_gate":
                # The PDF already exists; the graph waits here until "finish". A Gmail draft
                # brings it back to this pause (with draft_id set), so we only offer it once.
                values = graph.get_state(config).values
                d = values.get("delivery", {})
                if not d.get("draft_id"):  # first time at this pause: show the report
                    print("\n" + values["report_markdown"])
                    print(f"PDF ready: {d.get('pdf_path') or '(not created)'}")
                wants_gmail = (
                    args.gmail
                    if args.yes
                    else payload["app_mode"] == "local"
                    and not d.get("draft_id")
                    and input("\nCreate a Gmail draft with the PDF? [y/N] ").strip().lower() == "y"
                )
                reply = "gmail" if wants_gmail and not d.get("draft_id") else "finish"
            else:
                raise RuntimeError(f"unknown pause type {kind}")

            result = graph.invoke(Command(resume=reply), config, context=ctx)
            if kind in ("question", "follow_up"):
                print(last_score_line(graph, config) if kind == "question" else "")
    except (KeyboardInterrupt, EOFError):  # Ctrl-C, or input closed (Ctrl-Z / piped stdin)
        print("\nStopped. Nothing was sent. Session data is discarded.")
        return 130
    finally:
        if ctx.store is not None:
            ctx.store.close()

    # ---- summary
    o, s = result["overall"], ctx.router.summary()
    fu = result.get("follow_up") or {}
    print(f"\n=== Overall {o['score']}/10 — {o['recommendation']} ===")
    print(f"By round: {o['by_round']}")
    if fu.get("evaluation"):
        print(f"Follow-up on {fu['of']}: {fu['evaluation']['score']}/10 (reported separately)")
    print(
        f"LLM calls: {s['calls']} (target {s['target']}, hard cap {s['hard_cap']}) | "
        f"failed requests retried: {s['failed_requests']} | evaluator locked to: "
        f"{s['locked_provider']} | models: {s['models_used']} + {result['extract_model']} (CrewAI)"
    )
    by_node: dict[str, int] = {}
    for node, n in s["calls_by_node"].items():  # evaluate_T1..H3 -> "evaluate"
        key = "evaluate (answers)" if node.startswith("evaluate_") and node != "evaluate_F1" else node
        by_node[key] = by_node.get(key, 0) + n
    print(f"Calls by node: {by_node}")
    print(f"CrewAI attempts: {result.get('extract_attempts')}")
    print(f"Report saved: {result['report_path']}")
    d = result["delivery"]
    print(
        f"PDF: {d.get('pdf_path') or '-'} | Gmail: {d.get('gmail')}"
        + (f" | draft id: {d['draft_id']}" if d.get("draft_id") else "")
        + (f" | errors: {d['errors']}" if d.get("errors") else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
