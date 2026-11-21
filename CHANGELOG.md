# Changelog

All notable changes to Interview Arena. The project was built in phases; this file summarises them
(the detailed phase plans are in `docs/archive/07_phase_prompts.md`).

## v1.0.0 — 2026-11-21 · Initial release

### Build phases

- **Phase 0 — Skeleton.** Python 3.12 virtual environment, dependencies, `config.py` (loads `.env`,
  exposes model IDs and input limits; key values are never printed).
- **Phase 1 — LLM router.** Gemini → Groq (gpt-oss-120b, then qwen) → OpenRouter fallback; retry with
  backoff on 429/5xx; structured output with Pydantic validation and one retry; per-session call
  counter; evaluator model lock. Decision: Gemini 3.5 Flash-Lite ignores temperature, so scoring
  consistency relies on the rubric, a strict schema and a consistency test.
- **Phase 2 — Extraction.** Pydantic schemas; resume PDF parser (scanned-PDF and size checks); CrewAI
  crew with a JD Analyst and a Resume Analyst, locked to exactly two LLM calls (counted via CrewAI
  events) and rebuilt with the Groq model on failure.
- **Phase 3 — RAG + gap analysis.** FastEmbed + in-memory Chroma per session; one LLM call classifies
  every JD skill as matched / partial / missing with resume evidence; match % in Python. Budget set to
  a target of 18 calls with a hard cap of 24; question difficulty follows the JD's level.
- **Phase 4 — Question planner + evaluator.** 10 questions (4 Technical, 3 Project, 3 HR) with rule
  checks and one retry; evaluator returns a 0–10 score per rubric criterion with evidence quotes and
  Python computes the weighted score; prompt-injection guard; process-wide rate limiter (Gemini 12 RPM).
- **Phase 5 — LangGraph + CLI.** 12-node graph with `interrupt()` / `Command(resume=...)`,
  `InMemorySaver`, conditional `route_next`; "don't know" / very short answers count as skipped with no
  LLM call; follow-up on the weakest attempted answer; terminal runner.
- **Phase 6 — Report.** One batched Report Writer call (summary, top 3 actions, sample answers for
  scores below 6; HR samples are STAR templates); markdown report with gap table, per-question scores
  and model / fallback notes.
- **Phase 7 — Own MCP server.** FastMCP (stdio) tools `save_report_pdf` (Unicode-safe PDF with page
  footer) and `create_gmail_draft` (`gmail.compose` only, no send tool); one long-lived MCP client
  session; PDF always, Gmail draft only in local mode.
- **Phase 8 — Streamlit UI.** Four screens (Upload, Skill gaps, Interview, Report) per
  `docs/ui_design_spec.md`: theme tokens, step bar, "Try with sample data", per-field validation,
  segmented progress, result cards, report actions (Download PDF and Send to my Gmail both available).
- **Phase 9 — Testing & metrics.** Live measurements (`scripts/run_metrics.py`,
  `docs/test_results.md`): gap accuracy 93% / 100%, golden evaluator set 10 / 10, 16 LLM calls and
  ~92 s per session, scoring drift up to 1.1 (accepted: Gemini kept as the scorer for capacity;
  Groq available as an option). Sample report PDF from a fictional session.
- **Phase 10 — README.** Recruiter-friendly README (architecture diagram, results, engineering
  highlights, quick start) with four app screenshots taken on fictional sample data. Public deployment is left as future work.

### Fixes

- PDF download and Gmail draft no longer exclude each other: the PDF is created with the report and
  the graph stays paused on the report screen until the user finishes.
- Empty or whitespace-only answers are blocked with an inline message; all inputs are trimmed.
- Graph steps now run in a background worker thread. A browser refresh or rerun used to stop the
  Streamlit script between two graph nodes ("session stopped part-way"); steps now always finish at a
  checkpointed pause, double submits are refused, and an unfinished step is resumed once, silently.
- CrewAI extraction now retries busy providers (429 / 5xx) with backoff, like the LLM router.
- Planner prefers projects from the resume's projects section; gap analysis treats everyday developer
  tools (Git, Jira, …) listed by a working developer as matched.

### Cleanup

- Docs reorganised: `architecture.md`, `decisions.md`, `ui_design_spec.md`, `test_results.md`;
  planning documents moved to `docs/archive/` and made neutral.
- All tunables moved to `config.py`; dead state fields removed; clearer names; ruff and vulture clean.
- `requirements-dev.txt` for dev tools; Linux-safe `requirements.lock.txt` (generated on Windows with
  Windows-only packages removed); MIT licence.
