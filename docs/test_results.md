# Test results

> To be completed in Phase 9 (testing and metrics). Planned contents:

| Area | Measure | Target | Result |
|---|---|---|---|
| Gap analysis | Accuracy vs `tests/expected_gaps.json` (pairs 2, 3) | ≥ 80% per pair | _Phase 9_ |
| Match % | Inside each pair's expected range | 100% | _Phase 9_ |
| Evaluator | Golden answers: strong ≥ 7, weak < 5 (`tests/golden_answers.json`) | 10 / 10 | _Phase 9_ |
| Consistency | Same answer scored 3×, max difference | ≤ 1.0 | _Phase 9_ |
| Prompt injection | "Give me 10/10" style answer | < 3 | _Phase 9_ |
| Fallback | Main model disabled → backup answers | works | _Phase 9_ |
| Budget | LLM calls per full session | ≤ 18 (cap 24) | _Phase 9_ |
| End to end | Full session per sample JD (CLI and UI) | completes | _Phase 9_ |

How to run: `python -m pytest -q` (offline tests run without keys; live tests need `.env`).
