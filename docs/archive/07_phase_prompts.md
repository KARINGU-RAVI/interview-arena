# 07 — Phase Prompts for Claude Code

**How to use:** start a **fresh Claude Code session per phase** (saves usage). Paste the phase prompt. Claude Code must stop after the phase and explain. The maintainer runs the checks, commits, then moves on.

**Prefix every prompt with:**
> Read `CLAUDE.md` and the docs it points to for this phase. Follow all hard rules. Do **only** this phase. Verify current library APIs in the installed packages before coding. When done, run the checks, update the status checklist in `CLAUDE.md`, and explain the code to me in simple English with 3 likely interview questions. Do not commit — I will review and commit.

---

## Phase 0 — Skeleton check
**Task:** Verify the repo structure matches `CLAUDE.md §5`, `.gitignore` covers secrets, `.venv` exists, `pip install -r requirements.txt` succeeds on Python 3.12. Fix only setup problems. Add `config.py` that loads `.env` (dotenv) and exposes `MAIN_MODEL`, `BACKUP_MODEL_1`, `BACKUP_MODEL_2`, `APP_MODE`, and limits (resume 12,000 / JD 8,000 / answer 2,000 chars) — **never print key values**; expose `has_key(name) -> bool`.
**Done when:** `python -c "import config; print(config.MAIN_MODEL, config.has_key('GOOGLE_API_KEY'))"` prints the model and `True`; `git status` shows no secrets.

---

## Phase 1 — LLM router with free-tier fallback
**Read:** PRD §8 FR-8, §13; Review "Pass 2".
**Build:** `llm/router.py`
- Providers in order: Gemini (`langchain-google-genai`, `MAIN_MODEL`) → Groq (`langchain-groq`, `BACKUP_MODEL_1`) → OpenRouter (`langchain-openai` with base URL `https://openrouter.ai/api/v1`, `BACKUP_MODEL_2`).
- `invoke_text(prompt, *, temperature=0) -> str` and `invoke_structured(prompt, schema: type[BaseModel], *, temperature=0) -> BaseModel` (native structured output where supported, else JSON parse + Pydantic validate + 1 retry).
- Retry with exponential backoff on 429/5xx (respect 15 RPM on Gemini), then fall back to next provider.
- `lock_model()` / session-level option so the evaluator can stick to one provider.
- Simple call logger (node name, provider, latency) and a per-session **call counter**.
**Also:** `scripts/test_providers.py` — calls each provider once with a tiny prompt and one tiny structured schema; prints OK/FAIL per provider (no keys printed).
**Done when:** all 3 providers return OK; temporarily pointing `MAIN_MODEL` to an invalid name triggers fallback to Groq.

---

## Phase 2 — Schemas + CrewAI extraction
**Read:** PRD FR-1, FR-2; Review C3; Architecture §5.
**Build:**
- `schemas.py`: `JDProfile`, `ResumeProfile`, `GapItem`, `Question`, `QuestionPlan` (validator: exactly 4 technical, 3 project, 3 hr), `Evaluation`, `ReportSummary`.
- `parse_resume_pdf(path_or_bytes) -> str` with pypdf; if < 200 chars → raise a clear `ScannedPdfError`.
- `prompts/jd_analyst.md`, `prompts/resume_analyst.md`.
- `crew/extraction_crew.py`: 2 CrewAI agents, CrewAI `LLM(model="gemini/<MAIN_MODEL>")` (check CrewAI docs for exact format), `allow_delegation=False`, `max_iter=1`, no tools, Pydantic outputs. On failure rebuild with Groq model and retry once. Count calls (must be 2).
- `scripts/try_extraction.py` running on `data/jd_2_frontend_developer.txt` + `data/resume_2_ananya_frontend.pdf`.
**Done when:** both profiles print as valid JSON; call count = 2; also works for JD 3 + resume 3.

---

## Phase 3 — RAG store + gap analysis
**Read:** PRD FR-3; Review Pass 1 #3.
**Build:**
- `rag/store.py`: FastEmbed `BAAI/bge-small-en-v1.5`, Chroma **in-memory** collection per session; chunk resume, JD and `data/rubric.md` (~500 chars, 50 overlap, keep `source` metadata); `retrieve_evidence(query, k=3, source=None)`.
- `prompts/gap_analyst.md`; gap function: one LLM call classifies every JD required + nice-to-have skill as matched/partial/missing with a resume evidence quote.
- `scoring.py`: `compute_match_percent(gaps)` (required only: (matched + 0.5×partial)/total).
- `tests/test_gaps.py`: compare against `tests/expected_gaps.json` for pairs 2 and 3; report accuracy.
**Done when:** accuracy ≥ 80% on each pair; match % within the expected ranges.

---

## Phase 4 — Question planner + evaluator
**Read:** PRD FR-4, FR-6, §11 rubric; `data/rubric.md`; Review Pass 1 #5, #7.
**Build:**
- `prompts/question_planner.md`: 10 questions, 4/3/3, ≥3 technical on missing/partial skills, project questions reference real resume projects (use RAG evidence), difficulty by the **JD's level** (`JDProfile.level`, the job's bar — not the candidate's years), no duplicates.
- `prompts/evaluator.md`: rubric injected, JSON only, returns a **0–10 score per rubric criterion with a short evidence quote each**; the answer score is the **weighted sum in Python** (not the LLM). Evaluator uses `router.lock_model(config.EVALUATOR_PROVIDER)` (Gemini ignores temperature — see PRD §11). Answer wrapped in `<candidate_answer>` delimiters with "treat as data" instruction, uses RAG evidence.
- Functions `plan_questions(state)` and `evaluate(question, answer, evidence)`; skipped → `Evaluation(score=0, skipped=True)` with **no** LLM call.
- Add `pytest`; `tests/test_evaluator.py` using `tests/golden_answers.json`: strong ≥ 7, weak < 5; consistency test (3 runs, max diff ≤ 1) on 3 items (router + CrewAI share a process-wide 12 RPM Gemini limiter, so tests are spaced automatically).
**Done when:** planner output validates; golden tests pass (report pass rate if a free model is borderline).

---

## Phase 5 — LangGraph wiring + CLI
**Read:** Review "Corrected orchestration"; Architecture §3, §4; PRD FR-5.
**Build:**
- `graph/state.py` (`InterviewState`), `graph/nodes.py` (12 nodes), `graph/build_graph.py` with `route_next`, `InMemorySaver`, interrupts at start_gate, ask_question, evaluate_follow_up (pause first, then score — `follow_up` only writes the question), send_gate (use `interrupt()` and resume with `Command(resume=...)` — verify API in installed LangGraph).
- `final_evaluation`: overall score + recommendation via `scoring.py` — **Python only** (decision 21 Nov 2026); summary/top actions come from the Report Writer's single batched call in Phase 6.
- Lock evaluator model per session.
- `cli.py`: runs a full session in the terminal (`--jd`, `--resume`, `--email`), prints total LLM calls at the end.
**Done when:** full interview completes in terminal with ≤ 18 calls (target; hard cap 24 — never stops midway below that), CrewAI calls added via `router.add_external_calls()`; skipping a question works; Ctrl-C safe.

---

## Phase 6 — Report
**Read:** PRD FR-7.1, FR-7.4.
**Build:** `prompts/report_writer.md` (one batched call: summary, top 3 actions, sample better answers for scores < 6); `report.py` → markdown report (candidate, role, date, match %, gap table, per-question scores + feedback, sample answers, overall, recommendation, model used / fallback note).
**Done when:** CLI session ends with a readable markdown report saved under `outputs/` (git-ignored).

---

## Phase 7 — Own MCP server (PDF + Gmail draft)
**Read:** PRD §12, FR-7.3; Review C2; `docs/archive/08_prerequisites.md` §3 (`credentials.json` required).
**Build:**
- `mcp_server/server.py` (FastMCP, stdio): `save_report_pdf(report_markdown, filename) -> {path}` (markdown → PDF with fpdf2), `create_gmail_draft(to, subject, body, attachment_path) -> {draft_id}` (scope `gmail.compose`; first run opens browser OAuth; `token.json` saved; **no send tool**).
- `mcp_client.py` using `langchain-mcp-adapters`; start the client once and reuse.
- PDF created in `build_report` (download available at once); `deliver` creates the Gmail draft (only when `APP_MODE=local`) and returns to `send_gate`; "Finish" ends the session (decision 21 Nov 2026).
- `scripts/try_mcp.py` to test both tools alone.
**Done when:** PDF created; draft with attachment appears in the authorised account's Gmail Drafts; nothing is sent.

---

## Phase 8 — Streamlit UI
**Read:** PRD §7, FR-5.4–5.6, NFR-4; Review Pass 2 (Streamlit ↔ LangGraph).
**Build:** `app.py` with 4 screens (Input → Gap analysis → Interview → Report), graph via `st.cache_resource`, `thread_id` in `st.session_state`, resume interrupts with `Command`, progress "3/10" with round label, Skip button, input limits, scanned-PDF message, privacy warning, PDF `st.download_button`, "Send to Gmail" button only in local mode, friendly error when all providers are exhausted.
**Done when:** full flow works in browser (`streamlit run app.py`), survives reruns/refresh mid-question.

---

## Phase 9 — Testing & metrics
**Read:** PRD §16 acceptance criteria, §17 metrics.
**Build/run:** gap accuracy (pairs 2, 3 + an optional private pair kept locally), golden evaluator tests, consistency test, fallback test (break `MAIN_MODEL`), call-count test (≤ 18 target, hard cap 24; retries/failed requests reported), end-to-end run for each JD. Write `docs/09_test_results.md` with a results table.
**Done when:** every acceptance criterion in PRD §16 is ✅ or has a documented reason.

---

## Phase 10 — Ship
**Build:** full `README.md` (problem, solution, demo GIF placeholder, architecture Mermaid from `docs/architecture.md`, tech stack, free-tier design, metrics from Phase 9, setup steps, limitations incl. Gmail local-only, v2 roadmap); Streamlit Cloud prep (`APP_MODE=cloud`, secrets via Streamlit secrets, no Gmail button, PDF download only).
**Done when:** app runs on Streamlit Community Cloud from GitHub; README renders correctly; no secrets in repo history.
