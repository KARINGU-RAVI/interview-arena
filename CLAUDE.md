# CLAUDE.md — Interview Arena

Guidance for AI coding assistants working on this repository. Read it fully before every
session, then read the docs in §4 that the task needs. Clarity beats cleverness: every change
should be easy to explain line by line.

---

## 1. What this project is

A Streamlit app: the user uploads a **resume** (PDF/text) and pastes a **job description**. A
**CrewAI** crew extracts both into JSON profiles. A **LangGraph** graph finds **skill gaps** (with
RAG evidence), plans **10 gap-targeted questions** (4 Technical, 3 Project, 3 HR), runs the
interview **one question at a time** (pausing for each answer), scores every answer with a **fixed
rubric**, asks **1 follow-up** on the weakest attempted answer, computes the **overall score +
recommendation in Python**, writes a **report + PDF**, and — when the user asks — uses its **own
MCP server** to create a **Gmail draft** (local mode only). Target role used for the sample data:
Agentic AI Engineer.

---

## 2. Hard rules

1. **Free tier only.** No paid services, no paid-only models, never suggest enabling billing.
2. **Secrets:** read keys only from `.env` via `python-dotenv`. Never print, log, echo or commit
   keys, and never ask for them in chat. `.env`, `credentials.json`, `token.json` and
   `tests/local/` stay git-ignored; check `git status` before every commit.
3. **One task at a time.** Do only what was asked (phases: `docs/archive/07_phase_prompts.md`).
4. **Follow the docs.** `docs/decisions.md` and `docs/architecture.md` describe the agreed design.
   If a doc looks wrong or a library API has changed, stop and explain the problem and options
   before changing the design.
5. **No scope creep:** no voice, login, database, multi-user history, AutoGen/Agent Framework or
   Bedrock in v1.
6. **Budget per interview session: target 18 LLM calls, hard cap 24.** Count every call; never add
   hidden ones (CrewAI delegation, reflection loops). Retries are logged and shown in the report.
7. **Deterministic where possible:** match %, answer scores, overall score and recommendation are
   computed in Python, not by the LLM.
8. **No heavy dependencies:** no PyTorch / `sentence-transformers` (hosting has ~1 GB RAM);
   embeddings use FastEmbed.
9. **Verify library APIs against the installed versions** before writing code (§9).
10. **After each task:** run the checks, then report what was built, how to run it, a plain-English
    explanation of the key code, and anything that couldn't be done.

---

## 3. Environment

| Item | Value |
|---|---|
| OS / shell | Windows (PowerShell commands); hosting is Linux |
| Python | 3.12, virtual env in `.venv` |
| Main LLM | `gemini-3.5-flash-lite` (Google AI Studio free tier: 15 RPM, 250K TPM, 500 RPD). The router limits itself to **12 RPM**, shared by the whole process (limits are per Google Cloud project) |
| Backup 1 / 1B | Groq `openai/gpt-oss-120b` / `qwen/qwen3.8-27b` (free: 30 RPM, 1K RPD, 8K TPM, 200K TPD each) |
| Backup 2 | An OpenRouter `:free` model (`BACKUP_MODEL_2`; free: 20 RPM, 50 RPD) |
| `.env` keys | `GOOGLE_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `MAIN_MODEL`, `BACKUP_MODEL_1`, `BACKUP_MODEL_1B`, `BACKUP_MODEL_2`, `APP_MODE` (`local`/`cloud`), `EVALUATOR_PROVIDER` (`gemini`/`groq`) |
| Run | `streamlit run app.py` (dev) · `scripts/run_prod.ps1` (prod: no file watcher, no rerun-on-save) · `python cli.py --jd … --resume … --email …` |
| Debug log | `INTERVIEW_ARENA_DEBUG=1` or `streamlit run app.py -- --debug` |
| Dependencies | `requirements.txt` (runtime) · `requirements-dev.txt` (+ pytest, ruff, vulture) · `requirements.lock.txt` (pinned, Linux-safe; generated on Windows with Windows-only packages removed) |

Free-tier prompts may be used for training by providers → test only with fictional data.

---

## 4. Docs map

| File | Contents |
|---|---|
| `docs/architecture.md` | Mermaid diagrams: data flow, layers, the 12-node graph, one answer end to end, data model, call budget |
| `docs/decisions.md` | Key design decisions and trade-offs |
| `docs/ui_design_spec.md` | UI/UX spec: tokens, layout, the 4 screens, PDF, accessibility |
| `docs/test_results.md` | Test metrics (filled in Phase 9) |
| `docs/archive/` | Original planning docs: brief, research, PRD, orchestration review, blueprint, phase prompts, prerequisites |
| `data/` | Fictional sample JDs and resumes, `rubric.md` (loaded by the evaluator) |
| `tests/expected_gaps.json`, `tests/golden_answers.json` | Ground truth for gap accuracy and evaluator tests |

---

## 5. Folder structure

```
app.py                  Streamlit UI (4 screens); graph steps run in graph/worker.py
cli.py                  terminal runner
config.py               .env loading, model IDs, limits and all tunables
schemas.py              Pydantic models for every LLM output
scoring.py              rubric weights, answer / match / overall scores, recommendation
report.py               Report Writer (1 batched call) + markdown report
evaluator.py            per-criterion scoring; skip / "don't know" handling
gap_analysis.py         RAG evidence + 1 LLM call → GapItems + match %
question_planner.py     10 questions (4/3/3) + rule checks + 1 retry
pdf_parser.py           resume PDF → text
mcp_client.py           langchain-mcp-adapters client (one long-lived stdio session)
crew/extraction_crew.py CrewAI JD + Resume Analysts (exactly 2 calls)
graph/                  state.py, nodes.py (12 nodes), build_graph.py, worker.py
llm/router.py           Gemini → Groq → Groq qwen → OpenRouter, retry/backoff, rate limiter, structured output
rag/store.py            FastEmbed + per-session in-memory Chroma
ui/                     styles.py (only CSS), components.py, validation.py
mcp_server/server.py    FastMCP: save_report_pdf, create_gmail_draft (no send tool)
prompts/                7 prompt files
scripts/                test_providers.py, try_app.py, try_mcp.py, run_prod.ps1
tests/                  pytest suite + fixtures (tests/local/ is private and git-ignored)
```

---

## 6. Key design facts

- **7 LLM roles:** JD Analyst, Resume Analyst (CrewAI) · Gap Analyst, Question Planner,
  Evaluator, Follow-up Interviewer, Report Writer (LangGraph nodes).
- **12 nodes:** extract → gap_analysis → start_gate⏸ → plan_questions → ask_question⏸ →
  evaluate_answer → `route_next` (↻ ×10) → follow_up → evaluate_follow_up⏸ → final_evaluation →
  build_report (+PDF) → send_gate⏸ → deliver → (Gmail draft: back to send_gate | Finish: END).
- **Interrupt rule:** LangGraph re-runs a node from its start on resume, so no LLM call may come
  before `interrupt()` in a node.
- **Web app:** graph steps run in a background worker thread (`graph/worker.py`), never in the
  Streamlit script (Streamlit stops a running script on refresh/rerun). One step per session at a
  time; the page shows a spinner; an unfinished checkpoint is resumed once, silently. The session id
  lives in the URL (`?sid=`) so a refresh reconnects.
- **CrewAI:** own `LLM(...)`, `allow_delegation=False`, `max_iter=1`, `max_retry_limit=0`, no
  tools, Pydantic output; on failure rebuilt with the Groq model and retried once.
- **Evaluator:** rubric from `data/rubric.md`; one schema field per criterion (0–10 + evidence
  quote); the answer score is the weighted sum in Python; answer wrapped in tags and treated as
  data; empty / "don't know" / < 3 words = skipped (0, no LLM call); model locked per session to
  `EVALUATOR_PROVIDER`. Gemini 3.5 Flash-Lite ignores temperature — consistency comes from the
  rubric, schema and the ±1 consistency test.
- **Scoring (Python):** overall = Technical 40% + Project 30% + HR 30%; ≥ 8 Strong Hire ·
  6.5–7.9 Hire · 5–6.4 Lean No · < 5 No Hire. Match % = (matched + 0.5 × partial) / required.
  Question difficulty follows the JD's level.
- **Follow-up:** weakest attempted answer (score > 0); none → skipped; reported separately.
- **Report:** `final_evaluation` is Python only; one batched Report Writer call (summary, top 3
  actions, sample answers for scores < 6; HR samples are STAR templates with placeholders).
- **Gmail:** scope `gmail.compose` only, no send tool; drafts go to the mailbox that authorised
  OAuth → `APP_MODE=local` only. PDF download always available.
- **Limits:** resume ≤ 12,000 chars, JD ≤ 8,000, answer ≤ 2,000; PDF ≤ 2 MB; PDF text < 200
  chars → ask the user to paste text.

---

## 7. Coding conventions

- Small modules, type hints everywhere, Pydantic for all LLM outputs (validate + 1 retry).
- Every LLM call goes through `llm/router.py` (except CrewAI, which has its own fallback and is
  added to the session count).
- All tunables live in `config.py` (exception: `mcp_server/server.py` runs as a separate program).
- Log per call: node, provider/model, latency, token estimate — never prompt contents or keys.
  No `print()` outside `cli.py` and `scripts/`.
- Comments explain *why*, in plain English.
- Lint: `ruff format . && ruff check .` and `vulture .` (settings in `pyproject.toml`; dev tools
  in `requirements-dev.txt`).
- Tests: `python -m pytest -q` (offline tests need no keys; live tests use `.env`).
- Commit after each phase: `Phase N: <summary>`.

---

## 8. Status

- [x] Phases 0–8: skeleton, LLM router, CrewAI extraction, RAG + gap analysis, planner + evaluator,
  LangGraph + CLI, report, own MCP server, Streamlit UI (background worker, design spec)
- [x] Cleanup: docs reorganised, dead code removed, ruff/vulture clean, Linux-safe lock file
- [ ] Phase 9 — testing & metrics (`docs/test_results.md`)
- [ ] Phase 10 — ship (README, deploy)

---

## 9. Code quality rule: no guessing, always check the docs

These libraries change their APIs often (LangGraph, CrewAI, MCP, langchain-google-genai,
Streamlit). Never write code from memory alone.

1. **Check the installed version first** (`pip show <package>`) and use the API for that version.
2. **Read the official documentation** (below) for the exact functions you will use. If a URL has
   moved, search the official site; don't rely on random blogs.
3. **Confirm in the installed package source** when the docs are unclear (`inspect.signature`, or
   open the file in `.venv/Lib/site-packages/...`).
4. **Write a tiny proof first** — a 5–10 line script that proves the API works.
5. **Cite your source:** list the doc page or source file and version for each important API.
6. **If you're not sure, say so** — "couldn't confirm X in the docs for version Y", with options.
   Never invent function names, parameters, model IDs or config keys.

| Library / service | Official docs |
|---|---|
| LangGraph | https://docs.langchain.com/oss/python/langgraph/overview |
| LangChain | https://docs.langchain.com/oss/python/langchain/overview |
| langchain-google-genai | https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai |
| Gemini API | https://ai.google.dev/gemini-api/docs (redirects to https://aistudio.google.com/docs) |
| Groq | https://console.groq.com/docs |
| OpenRouter | https://openrouter.ai/docs |
| CrewAI | https://docs.crewai.com |
| MCP (spec) | https://modelcontextprotocol.io |
| MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk |
| langchain-mcp-adapters | https://github.com/langchain-ai/langchain-mcp-adapters |
| FastEmbed | https://qdrant.github.io/fastembed/ |
| Chroma | https://docs.trychroma.com |
| Pydantic | https://docs.pydantic.dev |
| pypdf | https://pypdf.readthedocs.io |
| fpdf2 | https://py-pdf.github.io/fpdf2/ |
| Gmail API | https://developers.google.com/workspace/gmail/api |
| Streamlit | https://docs.streamlit.io |

Checklist for every change:

- [ ] Every library call matches the installed version (docs or source checked)
- [ ] Proof script run before the full module
- [ ] Type hints and Pydantic models for all LLM outputs
- [ ] Errors handled with clear messages (no silent `except: pass`)
- [ ] No hard-coded keys, model IDs or limits — they come from `.env` and `config.py`
