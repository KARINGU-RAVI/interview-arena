"""Manual check: call each LLM provider once (text + structured) and print OK/FAIL.

Run from the project folder:
    python scripts/test_providers.py              # test all 3 providers
    python scripts/test_providers.py --fallback   # check Gemini -> Groq fallback

Never prints API keys. Uses tiny prompts (~6 calls total, well inside free limits).
"""

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

# Let this script import project modules when run as `python scripts/test_providers.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from llm.router import LLMRouter, default_providers  # noqa: E402


class Capital(BaseModel):
    country: str
    capital: str


TEXT_PROMPT = "Reply with exactly one word: OK"
STRUCT_PROMPT = "What is the capital of France? Give the country and capital."


def test_each_provider() -> bool:
    providers = default_providers()
    print(f"Configured providers: {[f'{p.name}:{p.model}' for p in providers]}\n")
    all_ok = True
    for p in providers:
        # A router with ONLY this provider, so a failure can't hide behind a fallback.
        router = LLMRouter(providers=[p])
        for label, run in (
            # r=router binds this loop's router (a plain lambda would see the last one)
            ("text", lambda r=router: r.invoke_text(TEXT_PROMPT, node="test")),
            ("structured", lambda r=router: r.invoke_structured(STRUCT_PROMPT, Capital, node="test")),
        ):
            try:
                out = run()
                shown = out.model_dump() if isinstance(out, BaseModel) else out.strip()[:40]
                rec = router.last_call
                print(f"OK    {p.name:<10} {label:<10} {rec.latency_s:5.2f}s  -> {shown}")
            except Exception as exc:
                all_ok = False
                print(f"FAIL  {p.name:<10} {label:<10} {type(exc).__name__}: {str(exc)[:120]}")
        print(f"      calls counted: {router.call_count}")
    return all_ok


def test_fallback() -> bool:
    """Point Gemini at a model that does not exist; the router must fall back to Groq."""
    providers = default_providers()
    broken = [
        dataclasses.replace(p, model="gemini-does-not-exist") if p.name == "gemini" else p for p in providers
    ]
    router = LLMRouter(providers=broken)
    try:
        router.invoke_text(TEXT_PROMPT, node="fallback-test")
    except Exception as exc:
        print(f"FAIL  fallback: {type(exc).__name__}: {str(exc)[:120]}")
        return False
    answered_by = router.last_call.provider
    ok = answered_by == "groq"
    print(f"{'OK  ' if ok else 'FAIL'}  fallback: invalid Gemini model -> answered by {answered_by}")
    print(f"      summary: {router.summary()}")
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fallback", action="store_true", help="only run the fallback test")
    parser.add_argument("-v", "--verbose", action="store_true", help="show router logs")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )

    ok = test_fallback() if args.fallback else test_each_provider()
    sys.exit(0 if ok else 1)
